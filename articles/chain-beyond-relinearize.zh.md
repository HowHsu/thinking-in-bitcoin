# Relinearize 之外的 O(N²) 瓶颈：链式 Cluster 在 TxGraph 中的完整代价

[English](chain-beyond-relinearize.en.md)

---

## 动机

`TryLinearizeChain` 将链式 cluster 的 `Linearize()` 步骤从 O(N²) 降到 O(N)。然而，
`Relinearize()` 只是整条流水线的最后一步。每一条到达 `Relinearize()` 的代码路径都会先
经过 `ApplyDependencies()`，其内部执行 cluster 的 Split、Merge 和依赖应用——由于
DepGraph 传递闭包的存在，这些操作在链式 cluster 上都是 O(N²) 的。

本文追踪所有到达 `Relinearize()` 的入口点，逐一标出路径上的 O(N²) 操作，并评估
`TryLinearizeChain` 实际覆盖了多少总代价。

---

## 到达 Relinearize 的入口点

所有路径都遵循相同的两阶段模式：

```
入口点
  → ApplyDependencies(level)     ← 第一阶段：结构性变更（Split / Merge / 依赖应用）
    → Relinearize()              ← 第二阶段：线性化
```

`TryLinearizeChain` 优化的是第二阶段。第一阶段未受影响。

### 第一阶段：ApplyDependencies 流水线

`ApplyDependencies(level)` 是所有待处理结构变更的统一入口，在任何线性化之前执行：

```
ApplyDependencies(level)
  → GroupClusters(level)
    → SplitAll(level)
      → ApplyRemovals(level)          每个 cluster O(N)
      → Split(cluster)                每个 cluster O(N²)  ← 瓶颈
    → [union-find 分组]               O(M α(M))
  → Merge(cluster_span)              每个分组 O(N²)       ← 瓶颈
  → cluster->ApplyDependencies(deps)  每个 cluster O(N²)  ← 瓶颈
```

### 第二阶段：Relinearize

```
Relinearize()
  → Linearize()
    → TryLinearizeChain             链式 O(N)    ✓ 已优化
    → 或 SPF 回退                    非链式 O(N²)
  → PostLinearize()                 链式跳过      ✓ 已优化
  → Updated()                      O(N)
```

---

## 入口点列表

### A. `MakeAllAcceptable(level)` — 同步按需处理

当消费方需要所有 cluster 具有有效线性化时调用：

| 调用方 | 触发时机 |
|--------|---------|
| `BlockBuilderImpl()` (txgraph.cpp:3208) | 构建区块模板 |
| `GetWorstMainChunk()` (txgraph.cpp:3260) | Mempool 驱逐（查找最低 feerate chunk） |
| `GetMainStagingDiagrams()` (txgraph.cpp:2811,2813) | RBF 评估（同时处理 main 和 staging） |

调用链：
```
MakeAllAcceptable(level)
  → ApplyDependencies(level)              ← 完整第一阶段流水线，O(N²)
  → [处理 NEEDS_FIX 队列]
    → MakeAcceptable(cluster)
      → Relinearize(acceptable_iters)     ← 第二阶段，链式 O(N)
  → [处理 NEEDS_RELINEARIZE 队列]
    → MakeAcceptable(cluster)
      → Relinearize(acceptable_iters)     ← 第二阶段，链式 O(N)
```

### B. `DoWork(iters)` — 有限预算的后台工作

每次 mempool 变更后调用，执行延迟优化：

| 调用方 | 触发时机 |
|--------|---------|
| `CTxMemPool::Apply()` (txmempool.cpp:224) | 新交易入池或 RBF 替换生效 |
| `CTxMemPool::removeForReorg()` (txmempool.cpp:381) | 链重组 |
| `CTxMemPool::removeForBlock()` (txmempool.cpp:424) | 新块确认（移除已确认交易） |

预算：`POST_CHANGE_WORK = 5 × ACCEPTABLE_ITERS = 8500` 次迭代。

调用链：
```
DoWork(iters)
  → [对每个质量级别：NEEDS_FIX, NEEDS_RELINEARIZE, ACCEPTABLE]
    → [对每个层级：staging, main]
      → ApplyDependencies(level)          ← 完整第一阶段流水线，O(N²)
      → [对队列中的每个 cluster]
        → Relinearize(iters_now)          ← 第二阶段，链式 O(N)
```

### C. `ApplyDependencies` + 单个 `MakeAcceptable` — 按需单 cluster 查询

当需要特定 cluster 的线性化时调用：

| 调用方 | 触发时机 |
|--------|---------|
| `GetBestChunkData()` (txgraph.cpp:2542+2549) | 查询某交易的 chunk feerate |
| `CompareMainOrder()` (txgraph.cpp:2766+2775) | 比较两个交易的挖矿优先级 |
| `TrimToLimit()` (txgraph.cpp:3395) | Cluster 超过 size/count 限制时裁剪 |
| `PullIn()` (txgraph.cpp:1703) | 将 main cluster 拉入 staging |

调用链：
```
ApplyDependencies(level)                  ← 完整第一阶段流水线，O(N²)
→ MakeAcceptable(cluster)
  → Relinearize(acceptable_iters)         ← 第二阶段，链式 O(N)
```

---

## 第一阶段的三个 O(N²) 瓶颈

### 1. `Split()` — 为每个连通分量重建 DepGraph

**触发时机**：`RemoveTransaction` 后（如区块确认移除了链中的交易），`ApplyRemovals`
标记 cluster 为 `NEEDS_SPLIT`，`SplitAll` 调用 `Split()`。

**代码**（txgraph.cpp:1493–1499）：
```cpp
for (auto i : m_linearization) {
    SetType new_parents;
    for (auto par : m_depgraph.GetReducedParents(i))
        new_parents.Set(remap[par].second);
    new_cluster->AddDependencies(new_parents, remap[i].second);
}
```

**复杂度分析**：

- `GetReducedParents(i)` 遍历 `Ancestors(i)`。链中第 k 个交易有 k 个祖先。
  总遍历量：1 + 2 + ··· + N = **O(N²)**。
- 每个 `AddDependencies()` 在新 DepGraph 中更新传递闭包：每次 O(N)，共 N 次 → **O(N²)**。

**发生频率**：每个区块（约 10 分钟）移除已确认交易，对受影响的链式 cluster 触发 Split。

### 2. `Merge()` — 从零构建 DepGraph

**触发时机**：`AddDependency` 连接了不同 cluster 中的交易。`GroupClusters` 确定需要合并的
cluster 组，然后 `Merge()` 将它们合为一体。

**代码**（txgraph.cpp:1536–1542）：
```cpp
other.ExtractTransactions(
    [&](DepGraphIndex pos, GraphIndex idx, FeePerWeight feerate) {
        auto new_pos = m_depgraph.AddTransaction(feerate);
        // ...
    },
    [&](DepGraphIndex pos, GraphIndex idx, SetType other_parents) {
        SetType parents;
        for (auto par : other_parents) parents.Set(remap[par]);
        m_depgraph.AddDependencies(parents, remap[pos]);
    });
```

**复杂度**：N 个交易 × 每个 `AddDependencies` O(N) = **O(N²)**。

**发生频率**：每个创建了对已有 cluster 依赖的新交易都可能触发合并。

### 3. `cluster->ApplyDependencies()` — 更新传递闭包

**触发时机**：`Merge()` 将所有交易放入同一 cluster 后，应用实际的依赖边。

**代码**（txgraph.cpp:1580–1583）：
```cpp
// "this is O(N) in the size of the cluster, regardless of the
// number of parents being added"
m_depgraph.AddDependencies(parents, child_idx);
```

**复杂度**：每次调用 O(N)，最多 N 个不同 child → 最坏 **O(N²)**。

`DepGraph::AddDependencies` 内部（cluster_linearize.h:179–200）：
```cpp
// 对每个新祖先，添加 child 的后裔
for (auto anc_of_par : par_anc) {
    entries[anc_of_par].descendants |= chl_des;   // BitSet<64> OR: O(1)
}
// 对 child 的每个后裔，添加新祖先
for (auto dec_of_chl : Descendants(child)) {
    entries[dec_of_chl].ancestors |= par_anc;      // BitSet<64> OR: O(1)
}
```

单个 BitSet 操作是 O(1)（一条 64 位 OR 指令），但循环次数为 O(N)，所有边累计为 **O(N²)**。

---

## 覆盖范围的可视化

```
入口点
  │
  ▼
  ApplyDependencies
  ├── SplitAll → Split()              O(N²) ← TryLinearizeChain 未覆盖
  ├── Merge()                          O(N²) ← TryLinearizeChain 未覆盖
  └── cluster->ApplyDependencies()     O(N²) ← TryLinearizeChain 未覆盖
  │
  ▼
  Relinearize
  ├── Linearize → TryLinearizeChain    O(N)  ✓ 已优化
  ├── PostLinearize                    跳过  ✓ 已优化（链式 cluster 跳过）
  └── Updated                          O(N)    始终 O(N)
```

`TryLinearizeChain` 消除了 `Relinearize()` 内部的 O(N²)，但 `ApplyDependencies` 中的三个
O(N²) 操作未受影响。

---

## 定量视角

对于大小为 N 的链式 cluster，使用 BitSet<64>（每个 BitSet 操作为单条 64 位 OR 指令）：

| 操作 | 循环次数 | 每次迭代代价 | 估算耗时 (N=11) |
|------|:---:|:---:|:---:|
| Split (GetReducedParents + AddDeps) | ~N² | ~1 cycle (BitSet OR) | ~50 ns |
| Merge (AddDependencies per tx) | ~N² | ~1 cycle (BitSet OR) | ~50 ns |
| SPF MakeTopological（无 TryLinearizeChain） | ~N² | ~30 cycles（FeeFrac 比较 + 交换） | ~2100 ns |
| TryLinearizeChain | ~N | ~3 cycles (popcount) | ~10 ns |

在 N=11（真实 mempool 数据中观察到的平均链式 cluster 大小）时，`Relinearize()` 中 SPF 的
O(N²) 占主导地位——每次迭代比 Split/Merge 中基于 BitSet 的 O(N²) 贵约 35–100 倍。这就是
`TryLinearizeChain` 在重放 benchmark 中能带来 16.9× 整体加速的原因。

但随着 N 增长，这个平衡会改变。在 N=25（最大的常见链式 cluster）时：

| 操作 | 估算耗时 (N=25) |
|------|:---:|
| Split (GetReducedParents + AddDeps) | ~250 ns |
| Merge (AddDependencies per tx) | ~250 ns |
| SPF MakeTopological | ~18,750 ns |
| TryLinearizeChain | ~25 ns |

非 Relinearize 的 O(N²) 随 N 二次增长。如果 `MAX_CLUSTER_COUNT` 增大到超过 64（需要
多字 BitSet），常数因子也会显著增加，因为 BitSet 操作将不再是单条机器指令。

---

## 根本原因：DepGraph 的传递闭包

三个 O(N²) 瓶颈共享同一根因：**DepGraph 存储并维护完整的祖先/后裔传递闭包**。

对于 N 个交易的链：

```
ancestors[0] = {0}
ancestors[1] = {0, 1}
ancestors[2] = {0, 1, 2}
...
ancestors[N-1] = {0, 1, ..., N-1}
```

总存储信息量：N × (N+1) / 2 bits = O(N²)，而链的实际结构信息仅为 N−1 条边（O(N)）。

每个创建、销毁或修改 DepGraph 的操作都必须重建或更新这个传递闭包，本质上就是 O(N²)。

---

## 更完整的解决方向

`TryLinearizeChain` 是针对线性化步骤的有效优化，但它受限于链式 cluster 仍以
`GenericClusterImpl`（底层为完整 DepGraph）表示这一事实。

更彻底的方案是实现专用的 `ChainClusterImpl`，将链存储为简单的有序交易数组——完全避免
DepGraph（及其 O(N²) 传递闭包）：

| 操作 | GenericClusterImpl (DepGraph) | ChainClusterImpl (数组) |
|------|:---:|:---:|
| Split | O(N²) | O(N) — 数组切片 |
| Merge（两条链） | O(N²) | O(N) — 数组拼接 |
| ApplyDependencies | O(N²) | O(N) — 更新线性序 |
| Relinearize | O(N)（经由 TryLinearizeChain） | O(N) — 按祖先 feerate 排序 |
| 内存 | O(N²) bits | O(N) |

这延续了 `SingletonClusterImpl` 的先例——它对单交易 cluster 避免使用 `DepGraph`。当链式
cluster 收到破坏链式拓扑的依赖（产生分叉或菱形）时，将其升级为 `GenericClusterImpl`——
与 singleton 首次收到依赖时的处理模式相同。

---

## 相关文章

- [链式 Cluster 的 O(N) 快速路径优化](chain-cluster-optimization.zh.md)
- [SPF 算法在链式 Cluster 上的复杂度分析](spf-chain-complexity.zh.md)
- [重放 Benchmark：TryLinearizeChain 在真实 Mempool 数据上的效果](chain-fast-path-replay-bench.zh.md)
