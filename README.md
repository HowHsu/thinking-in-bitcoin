# thinking-in-bitcoin

[English](README.en.md)

## Cluster Mempool 线性化算法

- [Cluster Mempool的交易线性化算法：SFP(Spanning-forest)](articles/spf.md)
- [Cluster Mempool的PostLinearize算法](articles/post_linearize.md) · [EN](articles/post_linearize.en.md)
- [为什么 SFP 之后需要 PostLinearize](articles/why-postlinearize.zh.md) · [EN](articles/why-postlinearize.en.md)

## 链式 Cluster 优化

- [SFP 算法在链式 cluster 上的复杂度分析](articles/spf-chain-complexity.zh.md) · [EN](articles/spf-chain-complexity.en.md)
- [链式 Cluster 的 O(N) 快速路径优化](articles/chain-cluster-optimization.zh.md) · [EN](articles/chain-cluster-optimization.en.md)
- [Relinearize 之外的 O(N²) 瓶颈：链式 Cluster 在 TxGraph 中的完整代价](articles/chain-beyond-relinearize.zh.md) · [EN](articles/chain-beyond-relinearize.en.md)
- [ChainClusterImpl：链形拓扑的优化集群实现](articles/chain-cluster.zh.md) · [EN](articles/chain-cluster.en.md)
- [重放 Benchmark：TryLinearizeChain 在真实 Mempool 数据上的效果](articles/chain-fast-path-replay-bench.zh.md) · [EN](articles/chain-fast-path-replay-bench.en.md)
- [TxGraph Trace & Replay：可复现的性能对比工具](articles/txgraph-trace-replay.zh.md) · [EN](articles/txgraph-trace-replay.en.md)
- [ChainCluster 内存对比：RSS、Massif、GetMainMemoryUsage 三种测量](articles/chain-cluster-memory.zh.md) · [EN](articles/chain-cluster-memory.en.md)
- [ChainCluster 无大链 Trace 基线](articles/chain-cluster-nochain-baseline.zh.md) · [EN](articles/chain-cluster-nochain-baseline.en.md)
- [ChainLinearize vs ChainCluster：原始 Trace 对比](articles/chain-linearize-vs-chaincluster.zh.md) · [EN](articles/chain-linearize-vs-chaincluster.en.md)
- [ChainClusterImpl 的 IsOversized 开销：Chunk Computation 的时机问题](articles/chain-isoversized-chunk-overhead.zh.md) · [EN](articles/chain-isoversized-chunk-overhead.en.md)

## Mempool 实测数据

- [2023 年全年 Mempool 实测：Cluster 大小与拓扑分布](articles/mempool-cluster-distribution-2023.zh.md) · [EN](articles/mempool-cluster-distribution-2023.en.md)
- [2025 年 Mempool 实测：Cluster 大小与拓扑分布](articles/mempool-cluster-distribution-2025.zh.md) · [EN](articles/mempool-cluster-distribution-2025.en.md)

## 测试

- [Bitcoin Core Fuzz 测试实践指南](articles/fuzz-testing.zh.md) · [EN](articles/fuzz-testing.en.md)
- [一键 Fuzz 脚本使用指南](articles/fuzz-script.zh.md) · [EN](articles/fuzz-script.en.md)

## Mempool & TxGraph 代码导读

- [第 0 篇：架构概览](articles/mempool-txgraph-00-architecture.zh.md) · [EN](articles/mempool-txgraph-00-architecture.en.md)
- [第 1 篇：CTxMemPoolEntry — 交易在内存池中的表示](articles/mempool-txgraph-01-entry.zh.md) · [EN](articles/mempool-txgraph-01-entry.en.md)
- [第 2 篇：TxGraph 接口 — 抽象层设计](articles/mempool-txgraph-02-txgraph-interface.zh.md) · [EN](articles/mempool-txgraph-02-txgraph-interface.en.md)
- [第 3 篇：TxGraphImpl 数据结构 — 内部表示](articles/mempool-txgraph-03-impl-data.zh.md) · [EN](articles/mempool-txgraph-03-impl-data.en.md)
- [第 4 篇：聚类与线性化 — 核心算法](articles/mempool-txgraph-04-linearization.zh.md) · [EN](articles/mempool-txgraph-04-linearization.en.md)
- [第 5 篇：Staging — 双图系统](articles/mempool-txgraph-05-staging.zh.md) · [EN](articles/mempool-txgraph-05-staging.en.md)
- [第 6 篇：CTxMemPool — 内存池核心操作](articles/mempool-txgraph-06-ctxmempool.zh.md) · [EN](articles/mempool-txgraph-06-ctxmempool.en.md)
- [第 7 篇：交易验证与入池 — ATMP 流程](articles/mempool-txgraph-07-atmp.zh.md) · [EN](articles/mempool-txgraph-07-atmp.en.md)
- [第 8 篇：区块构建 — 从 Mempool 到区块模板](articles/mempool-txgraph-08-block-building.zh.md) · [EN](articles/mempool-txgraph-08-block-building.en.md)
- [第 9 篇：测试与调试 — 质量保障](articles/mempool-txgraph-09-testing.zh.md) · [EN](articles/mempool-txgraph-09-testing.en.md)

## 其他

- [Bitcoin Core的交易费率估算算法](articles/fee-estimation-notes.zh.md) · [EN](articles/fee-estimation-notes.en.md)
