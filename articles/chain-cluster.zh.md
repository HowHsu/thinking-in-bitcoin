# ChainClusterImpl：链形拓扑的优化集群实现

[English](chain-cluster.en.md)

---

## 动机

TxGraph 根据依赖关系将交易分组为集群。通用实现（`GenericClusterImpl`）使用 `DepGraph` 加 `m_linearization` 表示任意拓扑。对于**链形**集群（A→B→C→D…），这是过度设计：拓扑固定，唯一的拓扑序就是链顺序。

对真实 mempool 数据的 trace 分析表明，**约 99% 的集群为链形**（见下文 [Trace 分析](#trace-分析)）。专用的 `ChainClusterImpl` 用紧凑的线性 `m_txdata` 向量和向后吸收的 chunk 算法替代通用表示，在主流场景下降低内存并提升性能。

---

## 设计概览

### 集群类型层次

| 类型 | 拓扑 | 表示 | 用途 |
|------|------|------|------|
| `SingletonClusterImpl` | 单笔 | `m_graph_index`、`m_feerate` | 单笔交易 |
| `ChainClusterImpl` | A→B→C→… | `m_txdata`（链顺序） | 线性链 |
| `GenericClusterImpl` | 任意 | `DepGraph` + `m_linearization` | 菱形、分叉等 |

`ChainClusterImpl` 用于大小 ≥ 2 的集群。大小为 1 的链使用 `SingletonClusterImpl`。

### 核心思路

对链而言，线性化恒为 `[0, 1, …, n−1]`，无需 `m_linearization` 向量。chunk 边界通过对 feerate 的向后吸收扫描计算。位置 `i` 的祖先为 `[0..i]`，后代为 `[i..n−1]`。这些操作均为 O(1) 或 O(n)，无需 DepGraph 机制。

---

## 实现细节

### 1. m_maybe_chain 标志

在合并前对集群分组时，`GroupData` 记录待合并的集群。`GroupEntry` 上的 `m_maybe_chain` 标志提示合并结果可能为链形（例如两条链通过单条依赖连接）。这样 `ApplyDependencies` 可在回退到通用合并前尝试快速路径。

### 2. ChainClusterImpl 数据结构

```cpp
struct TxData {
    GraphIndex graph_index;
    FeeFrac feerate;  // fee + size，用于 chunk 计算
};

std::vector<TxData> m_txdata;           // 链顺序 [根..尾] 的交易
std::vector<DepGraphIndex> m_split_segments;  // 删除后的连续段大小
```

- **m_txdata**：按链顺序存储 `(graph_index, feerate)`。下标 `i` 表示从根到尾的第 i 笔交易。
- **m_split_segments**：由 `ApplyRemovals` 在删除中间交易时填充。每个元素为一段连续剩余交易的大小。供 `Split()` 正确分区。

### 3. TryChainMerge

在 `ApplyDependencies` 中，当合并可能形成链的集群时：

1. 检查合并后的依赖图是否为单条链（除根和尾外，每笔交易最多一个父、一个子）。
2. 若是，创建 `ChainClusterImpl` 并按链顺序追加交易。
3. 若否（菱形、分叉等），回退到 `GenericClusterImpl` 合并。

这是**乐观路径**：先尝试链表示，仅在失败时使用通用路径。

### 4. ApplyRemovals 与 Split

**问题**：删除中间交易（如从 A→B→C→D 中删除 B）会断开链。若简单将 `m_txdata` 重建为 [A,C,D]，会错误地暗示 C 依赖 A。

**解决**：`ApplyRemovals` 在重建前计算 `m_split_segments`：

1. 用 `removed[]` 标记被删位置。
2. 扫描剩余位置得到连续段大小，例如 [1, 2] 表示 {A} 和 {C,D}。
3. 重建不含被删交易的 `m_txdata`。
4. 将质量设为 `NEEDS_SPLIT`。

`Split()` 使用 `m_split_segments`：

- 若仅一段且大小 ≥ 2：保留集群，清空 segments，设为 OPTIMAL。
- 否则：对每段创建新集群——大小 ≥ 2 用 `ChainClusterImpl`，大小为 1 用 `SingletonClusterImpl`。

### 5. ComputeChunks

向后吸收的 chunk 算法集中在 `ComputeChunks()`：

```cpp
for (DepGraphIndex i = 0; i < m_txdata.size(); ++i) {
    ChunkBound c{i, i+1, m_txdata[i].feerate};
    while (!chunks.empty() && c.feerate >> chunks.back().feerate) {
        c.feerate += chunks.back().feerate;
        c.start = chunks.back().start;
        chunks.pop_back();
    }
    chunks.push_back(c);
}
```

被 `Updated()`、`AppendTrimData()` 和 `SanityCheck()` 使用。

### 6. SetFee 与 Relinearize（bug 修复）

**Bug**：当对某笔交易调用 `SetTransactionFee`，而其兄弟交易的 Ref 已被销毁（待 `ApplyRemovals` 处理）时，`ChainClusterImpl::Updated()` 在 chunk 计算中解引用 `m_ref` 导致崩溃。

**修复**：与 `GenericClusterImpl::SetFee` 一致——在 fee 变更时降级质量为 `NEEDS_RELINEARIZE`，从而跳过 chunk 计算。`Relinearize()` 在 removals 处理完成后提升回 OPTIMAL 并重新计算 chunks。对链而言，`Relinearize()` 在拓扑上无操作，仅更新质量和 chunk 数据。

---

## Trace 分析

对真实 signet 数据的 trace 分析（`analyze_trace.py`）显示：

- **峰值状态**：27,512 笔交易，8,596 个集群。**99.1%** 的集群为链形。
- **最终状态**：6,079 笔交易，4,665 个集群。**98.9%** 为链形。
- **排除 size=1**：峰值时 23.8% 的多交易交易在链中；最终时 42.2%。

出现 size > 64 的集群是因为 trace 在离散快照处捕获状态；Split 由 DoWork 惰性触发。峰值状态是交易数最多的快照，不一定是链集群最多的快照。

---

## 性能

### Trace 回放对比

同一 trace 在 baseline 与 ChainCluster 分支上回放。参数：`max_cluster_count=64`，`max_cluster_size=404000`，`acceptable_cost=75000`。

| 入口点 | Baseline (μs) | ChainCluster (μs) |
|--------|---------------|---------------------|
| DoWork | 3,595,581 | 1,297,506 |
| **TOTAL** | **3,682,890** | **1,444,911** |

ChainCluster 整体约 2.5× 加速，主要由 DoWork 贡献。

### 复现

- Trace 与 replay 代码：[`github.com/HowHsu/bitcoin`](https://github.com/HowHsu/bitcoin) 分支 `before_chaincluster`
- 使用说明：[TxGraph Trace & Replay](https://howhsu.github.io/thinking-in-bitcoin/articles/txgraph-trace-replay.zh.html)

---

## Fuzz 测试

| 检查项 | 结果 |
|--------|------|
| Crash 文件 | 无 |
| Workers | 8 个全部完成 |
| 总执行次数 | ~3960 万 |
| 时长 | 10 小时 |

环境：Debian 12，Clang 20.1.8，libFuzzer，AddressSanitizer，UndefinedBehaviorSanitizer。

---

## 相关

- [链形集群的 O(N) 快速路径](chain-cluster-optimization.zh.md) — GenericCluster 内的 `TryLinearizeChain`（不同优化层）
- [TxGraph Trace & Replay](txgraph-trace-replay.zh.md) — 可复现性能对比工具
