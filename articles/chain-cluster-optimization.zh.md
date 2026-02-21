# 链式 Cluster 的 O(N) 快速路径优化

[English](chain-cluster-optimization.en.md)

---

## 背景

`Linearize()` 使用的 SPF 算法在严格递增费率的链式 cluster 上会产生 **O(N²)** 的复杂度
（详见[SPF 算法在链式 cluster 上的复杂度分析](spf-chain-complexity.zh.md)）。
本文介绍 `TryLinearizeChain` 优化——对链式 cluster 完全绕过 SPF，直接以 O(N) 得到最优线性化，
并给出实测 benchmark 数据。

节点插桩观测发现，**mempool 中几乎所有 cluster 都是链式的**——每笔交易花费前一笔的输出。
这与 CPFP（Child-Pays-For-Parent）费率提升的广泛使用一致：低费率的父交易配合花费其输出的
高费率子交易，构成两笔交易的链；多次叠加 CPFP 或批量支付则形成更长的链。
由于链式 cluster 在实际中占主导，针对链式拓扑的 O(N) 快速路径具有远超其他拓扑优化的收益。

---

## 算法

### 检测

当且仅当一个 N 笔交易的 cluster 中所有交易的祖先集大小恰好构成 {1, 2, ..., N} 时，它是链式的。
使用布尔存在数组 O(N) 判断；`Ancestors(i).Count()` 在固定大小的 `BitSet<64>` 下为 O(1)。

### 拓扑序恢复

祖先数为 k 的节点唯一占据拓扑序的第 k−1 位。一次映射扫描即可 O(N) 恢复。

### 为什么不需要 PostLinearize

链式 cluster 的拓扑序是唯一的，且每对相邻交易都有依赖，
因此 `ChunkLinearizationInfo` 的无条件合并和 `PostLinearize` 的 merge/swap 行为完全一致
（swap 分支永远不触发）。拓扑序本身就是最优的线性化结果，可以安全跳过 `PostLinearize`。
详细原理见[为什么 SPF 之后需要 PostLinearize](why-postlinearize.zh.md)。

### 集成到 `Linearize()`

`TryLinearizeChain` 作为 `Linearize()` 的早退出，在 SPF 初始化前执行：

```cpp
// 在 Linearize() 中：
if (auto chain_lin = TryLinearizeChain(depgraph); !chain_lin.empty()) {
    return {std::move(chain_lin), /*optimal=*/true, /*cost=*/0, /*is_chain=*/true};
}
// 通用路径：SPF 算法（LinearizeSPF）
```

第四个返回值 `is_chain` 通知调用方（`Relinearize()`）结果已经最优且连通，
可以完全跳过 `PostLinearize()`。

---

## Benchmark：`LinearizeOptimallyMonotoneChainTotal`

`MakeMonotoneChainGraph` 构造费率 fee_i = i+1、大小 size_i = 1 的链式图——
这是 SPF 算法的最坏费率分布。不做优化时 `MakeTopological` 会触发 O(N²) 级联合并；
有了 `TryLinearizeChain` 则 O(N) 直接得出结果。

每个 benchmark op 是对给定规模链式 cluster 调用一次 `Linearize()` 的完整耗时。

| 规模 | 优化前 (ns/op) | 优化后 (ns/op) | 加速比 |
|------|--------------|--------------|--------|
| 9 tx | 973 | 48 | **20×** |
| 16 tx | 1,772 | 72 | **25×** |
| 32 tx | 3,880 | 129 | **30×** |
| 48 tx | 6,205 | 185 | **34×** |
| 63 tx | 8,551 | 238 | **36×** |

优化后每次调用的指令数：

| N | 总指令数 | 指令数/tx |
|---|---------|---------|
| 9 | 954 | 106 |
| 16 | 1,360 | 85 |
| 32 | 2,295 | 72 |
| 48 | 3,230 | 67 |
| 63 | 4,100 | 65 |

每笔交易的指令数随 N 增大略有下降（常数收敛），印证了 O(N) 行为。
加速比随 N 增大持续增长，因为优化前趋向 O(N²) 而优化后始终保持 O(N)。
