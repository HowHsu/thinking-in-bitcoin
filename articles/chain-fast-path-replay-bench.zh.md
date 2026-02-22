# 重放 Benchmark：TryLinearizeChain 在真实 Mempool 数据上的效果

[English](chain-fast-path-replay-bench.en.md)

---

## 动机

合成 benchmark（如 `LinearizeOptimallyMonotoneChainTotal`）显示链式 cluster 上单次调用有
20–36× 的加速，但 reviewer 可能会问：*这在实际运行中到底节省了多少时间？*

本 benchmark 用真实 mainnet 数据回答两个问题：

1. **mempool 中链式 cluster 的占比是多少？**
2. **重放真实线性化工作负载时，整体加速比是多少？**

---

## 方法

### 数据采集

在 `txgraph.cpp` 的 `GenericClusterImpl::Relinearize()` 中添加临时插桩代码，
在每次调用 `Linearize()` 前，将以下输入序列化到
[`mempool_clusters.txt`](mempool_clusters.txt)：

| 字段 | 说明 |
|------|------|
| DepGraph (hex) | Cluster 拓扑和费率，通过 `DepGraphFormatter` 序列化 |
| is_topological | 旧线性化是否拓扑有效 |
| max_iters | SPF 迭代预算 |
| rng_seed | 传给 SPF 的随机数种子 |
| old_linearization | 现有线性化序列（SPF 的起点） |

这完整捕获了 `Linearize()` 收到的所有输入，足以离线忠实重放。

### 采集时间段

| | |
|---|---|
| 开始 | 2026-02-21 14:19:57 UTC |
| 结束 | 2026-02-22 02:38:09 UTC |
| 持续 | 约 12.3 小时 |

### 重放

对每个采集到的 cluster，分别走两条代码路径：

- **有快速路径**：`Linearize()` → `TryLinearizeChain` 命中则 O(N) 返回；未命中则走 SPF。
  仅在走 SPF 时调用 `PostLinearize()`。
- **无快速路径**：对所有 cluster 强制走 `LinearizeSPF()` + `PostLinearize()`。

两条路径收到完全相同的 DepGraph、旧线性化和 RNG 种子，唯一区别是是否先尝试
`TryLinearizeChain`。`fallback_order` 统一用 `IndexTxOrder`（按 index 比较），
这是所有现有 benchmark 和测试的标准做法。

---

## 数据集

| 指标 | 数值 |
|------|-----:|
| `Relinearize()` 总调用次数 | 115,370 |
| 总交易数 | 1,304,654 |
| 链式 cluster | 110,982 (96.2%) |
| 非链式 cluster | 4,388 (3.8%) |
| 平均 cluster 大小 | 11.3 tx |

**12 小时 mainnet 窗口内 96.2% 的 cluster 是链式的**，与 CPFP 费率提升在实际 Bitcoin
流量中的主导地位一致。

原始数据：[`mempool_clusters.txt`](mempool_clusters.txt)

---

## 结果

### 整体

| | 有快速路径 | 无快速路径 | 比值 |
|---|---:|---:|---:|
| 时间 (ns/重放) | 14,626,254 | 247,187,979 | **16.9×** |
| 指令数 | 181,197,637 | 2,652,277,302 | 14.6× |
| 周期数 | 50,972,740 | 861,302,943 | 16.9× |
| 分支数 | 23,610,416 | 345,208,836 | 14.6× |
| IPC | 3.555 | 3.080 | |

一次完整重放 = 处理全部 115,370 个 cluster。

### 每 cluster

| | 有快速路径 | 无快速路径 |
|---|---:|---:|
| ns / cluster | 127 | 2,143 |
| ns / 交易 | 11.2 | 189.5 |

---

## 要点

1. **链式 cluster 占绝对多数。** 12 小时 mainnet 窗口内 96.2% 的 `Relinearize()` 调用
   作用于链式 cluster。链式拓扑的专用快速路径具有很高的实际收益。

2. **整体加速 16.9×。** 完整重放 115k cluster 的总线性化时间从 ~247 ms 降至 ~15 ms。

3. **指令数减少 14.6×。** 加速来自算法层面（O(N) vs SPF `MakeTopological` 的 O(N²)），
   而非缓存或分支预测效应。

4. **对非链式 cluster 的额外开销可忽略。** `TryLinearizeChain` 仅做一次 O(N) 的祖先集
   大小扫描，对 3.8% 的非链式 cluster 立即返回空。这个开销远小于紧随其后的 SPF 初始化。

---

## 相关文章

- [链式 Cluster 的 O(N) 快速路径优化](chain-cluster-optimization.zh.md)
- [SPF 算法在链式 cluster 上的复杂度分析](spf-chain-complexity.zh.md)
