# ChainClusterImpl 的 IsOversized 开销：Chunk Computation 的时机问题

[English](chain-isoversized-chunk-overhead.en.md)

---

## 问题

在 4.5 天 mainnet trace 的重放测试中，ChainClusterImpl 让 `DoWork` 快了 2.8×，但
`IsOversized` 反而慢了 2.1×。整体仍然快 2.5×，但 IsOversized 的回退值得深究。

---

## 测量方法

使用 `txgraph-replay` 重放真实 mainnet trace（4.5 天，71M 操作），在
`IsOversized` 内部插桩测量 `ApplyRemovals`、`GroupClusters`、`SplitAll` 及
`cluster->Split()` 各阶段的时间。

对比两个分支：
- **before\_chaincluster**：所有 cluster 使用 `GenericClusterImpl`
- **chaincluster**：链状 cluster 使用 `ChainClusterImpl`

---

## 数据

### 入口点计时

| 入口点 | before\_chaincluster | chaincluster | 倍率 |
|--------|----:|----:|----:|
| DoWork | 3,607,902 us | 1,295,222 us | **2.8× 快** |
| IsOversized | 65,717 us | 140,463 us | **2.1× 慢** |
| Diagrams | 35,752 us | 16,146 us | 2.2× 快 |
| **TOTAL** | **3,729,140 us** | **1,474,694 us** | **2.5× 快** |

### IsOversized 内部分解

| | before\_chaincluster | chaincluster |
|---|----:|----:|
| cached 返回 | 1,099,671 calls | 1,099,671 calls |
| ApplyRemovals | 226 us | 417 us |
| GroupClusters | 34,806 us | 111,926 us |
| 　└─ SplitAll | 9,408 us | 91,798 us |

### SplitAll（GroupClusters 的第一步）中 cluster→Split 的分类

**before\_chaincluster**（无 ChainClusterImpl）：

| 类型 | 调用次数 | 总耗时 | 每次 |
|------|------:|------:|------:|
| generic | 155,174 | 4,188 us | **27 ns** |
| singleton | 89,721 | 5 us | ~0 ns |

**chaincluster**（有 ChainClusterImpl）：

| 类型 | 调用次数 | 总耗时 | 每次 |
|------|------:|------:|------:|
| generic | 4,298 | 502 us | 117 ns |
| **chain** | **146,765** | **85,246 us** | **581 ns** |
| singleton | 93,832 | 0 us | ~0 ns |

其中 chain 的 85,246 us 中，**early\_exit 路径占 77,753 us**（123,392 次调用，
630 ns/次）。

---

## 根因

差异的根源在于 `Split` 设置的 `QualityLevel` 不同，进而决定 `Updated()` 是否
执行 chunk computation。

### QualityLevel 与 Updated() 的关系

`Updated()` 分两个阶段：

1. **Locator rebuild** — 遍历所有 tx，更新 `SetPresent` 和 `ClearChunkData`。O(n)。
2. **Chunk computation** — 计算每个 chunk 的 feerate 并写入 entry。O(n)。
   **仅在 `IsAcceptable()` 为 true 时执行**（即 quality 为 `OPTIMAL` 或 `ACCEPTABLE`）。

```
enum class QualityLevel {
    OVERSIZED_SINGLETON,
    NEEDS_SPLIT_FIX,
    NEEDS_SPLIT,
    NEEDS_FIX,
    NEEDS_RELINEARIZE,   ← 不是 Acceptable
    ACCEPTABLE,          ← 是 Acceptable → 触发 chunk computation
    OPTIMAL,             ← 是 Acceptable → 触发 chunk computation
    NONE,
};
```

### GenericClusterImpl 路径

```
ApplyRemovals:
  设 NEEDS_SPLIT
  Updated(): locator O(n) ✓, chunks 跳过 (NEEDS_SPLIT 不是 Acceptable)

Split (单连通分量, 无空洞):
  设 NEEDS_RELINEARIZE
  Updated(): locator O(n) ✓, chunks 跳过 (NEEDS_RELINEARIZE 不是 Acceptable)

→ Split 总开销: 2 × O(n) locator only = 27 ns/call
→ Chunks 在 DoWork 中计算 (re-linearize → 设 ACCEPTABLE → Updated() 做 chunks)
```

### ChainClusterImpl 路径

```
ApplyRemovals:
  设 NEEDS_SPLIT
  Updated(): locator O(n) ✓, chunks 跳过

Split early_exit (单段链, 大小 ≥ 2):
  设 OPTIMAL                          ← 直接跳到最高质量
  Updated(): locator O(n) ✓, chunks O(n) ✓  ← 触发了 chunk computation!

→ Split 总开销: 2 × O(n) locator + 1 × O(n) chunks = 630 ns/call
→ DoWork 中不需要做任何事 (已经是 OPTIMAL)
```

### 为什么 ChainCluster 设 OPTIMAL？

链的 linearization 天然就是最优的——沿链序依次就是 optimal order。不需要
re-linearize，所以可以直接设 `OPTIMAL`。

Generic 则不能这样做——删除交易后 linearization 可能不再最优，必须设
`NEEDS_RELINEARIZE`，让 DoWork 后续重新线性化。

### 工作总量相同，时机不同

|  | Generic | Chain |
|---|---|---|
| Split 中做的事 | locator only (便宜) | **locator + chunks (贵)** |
| DoWork 中做的事 | **re-linearize + chunks (贵)** | 什么都不做 |
| IsOversized 耗时 | 65,717 us | 140,463 us |
| DoWork 耗时 | 3,607,902 us | 1,295,222 us |
| **两者之和** | **3,673,619 us** | **1,435,685 us** |

Chain 把 chunk computation 从 DoWork **提前**到了 Split 中。从 DoWork 的角度看，
这是 2.8× 的加速；从 IsOversized 的角度看，这是 2.1× 的回退。但整体是 2.6× 的
净收益。

---

## 核心问题

IsOversized **根本不需要 chunks**。它只需要判断合并后的 cluster 是否超过大小限制。
但因为 Split 设了 `OPTIMAL`，`Updated()` 作为副作用计算了 chunks。

这 77,753 us（123,392 次 × 630 ns）全部花在了「IsOversized 不需要但 OPTIMAL 状态
强制触发」的 chunk computation 上。

### 受影响的不止 IsOversized

任何经过 `GroupClusters → SplitAll → Split` 路径的入口点都会触发同样的开销。
`CountDistinctClusters` 就是一个例子：

```
CountDistinctClusters
  → ApplyDependencies
    → GroupClusters
      → SplitAll
        → ChainClusterImpl::Split → OPTIMAL → Updated() 做 chunk computation
```

| 入口点 | before\_chaincluster | chaincluster | 倍率 |
|--------|----:|----:|----:|
| IsOversized | 65,717 us | 140,463 us | 2.1× 慢 |
| CountDistinctClusters | 575 us | 2,474 us | 4.3× 慢 |

`CountDistinctClusters` 的 4.3× 回退比 IsOversized 的 2.1× 更严重，因为
IsOversized 有大量 cached 返回（1,099,671 次）稀释了平均开销，而
`CountDistinctClusters` 每次调用都会走完整路径。

### 潜在优化方向

如果能让 Split 在这些路径中避免触发 chunk computation——例如引入一个
「linearization 正确但 chunks 未计算」的中间状态——就能把 chain Split 的单次开销
从 630 ns 降到接近 generic 的 27 ns，使这些入口点不再是 ChainClusterImpl 的
回退点。

---

## 附录：完整 profiling 输出

### before\_chaincluster

```
Timed entry points:
  Entry point                         Calls     Total (us)       Avg (us)
  ---                                   ---            ---            ---
  DoWork                            1764078        3607902           2.05
  CompareMainOrder                 54285313           1391           0.00
  GetAncestors                        38823              1           0.00
  GetDescendants                      16761           8509           0.51
  CountDistinctClusters               16636            603           0.04
  GetMainMemoryUsage                3553929            130           0.00
  GetMainStagingDiagrams              31129          35752           1.15
  IsOversized                       1857503          65717           0.04
  StartStaging                      1764603             86           0.00
  AbortStaging                         1087              0           0.00
  CommitStaging                     1763516           9049           0.01
                                        ---            ---
  TOTAL                            65093378        3729140

[IsOversized profile] cached returns: 1099671 calls
[IsOversized profile] ApplyRemovals: total=226 us, calls=757832
[IsOversized profile] GroupClusters: total=34806 us, calls=757832
[IsOversized profile] GroupClusters breakdown:
  SplitAll:    total=9408 us, calls=757832
    ApplyRemovals (top-level):       total=11 us
    ApplyRemovals (per-cluster):     total=3 us
    cluster->Split:    generic=4188 us (155174 calls), singleton=5 us (89721 calls)
  build_an:    total=68 us, calls=757832
  union_find:  total=32 us, calls=757832
  translate:   total=54 us, calls=757832
  compact:     total=16 us, calls=757832
```

### chaincluster

```
Timed entry points:
  Entry point                         Calls     Total (us)       Avg (us)
  ---                                   ---            ---            ---
  DoWork                            1764078        1295222           0.73
  CompareMainOrder                 54285313           1441           0.00
  GetAncestors                        38823              1           0.00
  GetDescendants                      16761           8333           0.50
  CountDistinctClusters               16636           2520           0.15
  GetMainMemoryUsage                3553929            181           0.00
  GetMainStagingDiagrams              31129          16146           0.52
  IsOversized                       1857503         140463           0.08
  StartStaging                      1764603             85           0.00
  AbortStaging                         1087              1           0.00
  CommitStaging                     1763516          10301           0.01
                                        ---            ---
  TOTAL                            65093378        1474694

[IsOversized profile] cached returns: 1099671 calls
[IsOversized profile] ApplyRemovals: total=417 us, calls=757832
[IsOversized profile] GroupClusters: total=111926 us, calls=757832
[IsOversized profile] GroupClusters breakdown:
  SplitAll:    total=91798 us, calls=757832
    ApplyRemovals (top-level):       total=7 us
    ApplyRemovals (per-cluster):     total=8 us
    cluster->Split:    generic=502 us (4298 calls), chain=85246 us (146765 calls),
                       singleton=0 us (93832 calls)
    ChainClusterImpl::Split breakdown:
      early_exit=77753 us (123392 calls), append=0 us, insert=0 us,
      updated=3910 us, compact=11 us, other=0 us, singleton_seg=1 us
  build_an:    total=182 us, calls=757832
  union_find:  total=36 us, calls=757832
  translate:   total=60 us, calls=757832
  compact:     total=11 us, calls=757832
```
