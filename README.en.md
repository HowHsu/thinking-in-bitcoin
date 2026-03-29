# thinking-in-bitcoin

[中文版](README.md)

## Cluster Mempool Linearization Algorithms

- [Cluster Mempool Linearization: SFP (Spanning-Forest)](articles/spf.md)
- [Cluster Mempool PostLinearize Algorithm](articles/post_linearize.en.md) · [中文](articles/post_linearize.md)
- [Why PostLinearize Is Needed After SFP](articles/why-postlinearize.en.md) · [中文](articles/why-postlinearize.zh.md)

## Chain Cluster Optimization

- [Complexity Analysis of the SFP Algorithm on Chain Clusters](articles/spf-chain-complexity.en.md) · [中文](articles/spf-chain-complexity.zh.md)
- [O(N) Fast Path for Chain-Shaped Clusters](articles/chain-cluster-optimization.en.md) · [中文](articles/chain-cluster-optimization.zh.md)
- [O(N²) Bottlenecks Beyond Relinearize: The Full Cost of Chain Clusters in TxGraph](articles/chain-beyond-relinearize.en.md) · [中文](articles/chain-beyond-relinearize.zh.md)
- [ChainClusterImpl: Optimised Cluster for Chain-Shaped Topologies](articles/chain-cluster.en.md) · [中文](articles/chain-cluster.zh.md)
- [Replay Benchmark: TryLinearizeChain on Real Mempool Data](articles/chain-fast-path-replay-bench.en.md) · [中文](articles/chain-fast-path-replay-bench.zh.md)
- [TxGraph Trace & Replay: Reproducible Performance Comparison Tool](articles/txgraph-trace-replay.en.md) · [中文](articles/txgraph-trace-replay.zh.md)
- [ChainCluster Memory Comparison: RSS, Massif, GetMainMemoryUsage](articles/chain-cluster-memory.en.md) · [中文](articles/chain-cluster-memory.zh.md)
- [ChainCluster No-Large-Chain Trace Baseline](articles/chain-cluster-nochain-baseline.en.md) · [中文](articles/chain-cluster-nochain-baseline.zh.md)
- [ChainLinearize vs ChainCluster: Original Trace Comparison](articles/chain-linearize-vs-chaincluster.en.md) · [中文](articles/chain-linearize-vs-chaincluster.zh.md)
- [ChainClusterImpl's IsOversized Overhead: The Chunk Computation Timing Problem](articles/chain-isoversized-chunk-overhead.en.md) · [中文](articles/chain-isoversized-chunk-overhead.zh.md)

## Mempool Empirical Data

- [Full-Year 2023 Mempool Measurement: Cluster Size and Topology Distribution](articles/mempool-cluster-distribution-2023.en.md) · [中文](articles/mempool-cluster-distribution-2023.zh.md)
- [2025 Mempool Measurement: Cluster Size and Topology Distribution](articles/mempool-cluster-distribution-2025.en.md) · [中文](articles/mempool-cluster-distribution-2025.zh.md)

## Testing

- [Bitcoin Core Fuzz Testing Practical Guide](articles/fuzz-testing.en.md) · [中文](articles/fuzz-testing.zh.md)
- [One-Click Fuzz Script Guide](articles/fuzz-script.en.md) · [中文](articles/fuzz-script.zh.md)

## Mempool & TxGraph Code Walkthrough

- [Part 0: Architecture Overview](articles/mempool-txgraph-00-architecture.en.md) · [中文](articles/mempool-txgraph-00-architecture.zh.md)
- [Part 1: CTxMemPoolEntry — Transaction Representation in the Mempool](articles/mempool-txgraph-01-entry.en.md) · [中文](articles/mempool-txgraph-01-entry.zh.md)
- [Part 2: TxGraph Interface — Abstraction Layer Design](articles/mempool-txgraph-02-txgraph-interface.en.md) · [中文](articles/mempool-txgraph-02-txgraph-interface.zh.md)
- [Part 3: TxGraphImpl Data Structures — Internal Representation](articles/mempool-txgraph-03-impl-data.en.md) · [中文](articles/mempool-txgraph-03-impl-data.zh.md)
- [Part 4: Clustering and Linearization — Core Algorithms](articles/mempool-txgraph-04-linearization.en.md) · [中文](articles/mempool-txgraph-04-linearization.zh.md)
- [Part 5: Staging — The Dual-Graph System](articles/mempool-txgraph-05-staging.en.md) · [中文](articles/mempool-txgraph-05-staging.zh.md)
- [Part 6: CTxMemPool — Core Mempool Operations](articles/mempool-txgraph-06-ctxmempool.en.md) · [中文](articles/mempool-txgraph-06-ctxmempool.zh.md)
- [Part 7: Transaction Validation and Acceptance — The ATMP Flow](articles/mempool-txgraph-07-atmp.en.md) · [中文](articles/mempool-txgraph-07-atmp.zh.md)
- [Part 8: Block Building — From Mempool to Block Template](articles/mempool-txgraph-08-block-building.en.md) · [中文](articles/mempool-txgraph-08-block-building.zh.md)
- [Part 9: Testing and Debugging — Quality Assurance](articles/mempool-txgraph-09-testing.en.md) · [中文](articles/mempool-txgraph-09-testing.zh.md)

## Storage & Indexes

- [PR #34897: Indexes Must Not Commit Ahead of the Flushed Chainstate](articles/index-flush-pr34897.en.md) · [中文](articles/index-flush-pr34897.zh.md)
- [Bitcoin Core Data Persistence: Flush Timing and Ordering](articles/index-flush-ordering.en.md) · [中文](articles/index-flush-ordering.zh.md)
- [Bitcoin Core Disk Storage Layout: Data Classification and Relationships](articles/disk-storage-layout.en.md) · [中文](articles/disk-storage-layout.zh.md)
- [Bitcoin Core Storage Architecture: Complete ASCII Reference](articles/storage-architecture-ascii.en.md) · [中文](articles/storage-architecture-ascii.zh.md)

## Consensus & Activation

- [Bitcoin Soft Fork Activation: From BIP9 Signaling to BuriedDeployment](articles/soft-fork-activation.en.md) · [中文](articles/soft-fork-activation.zh.md)

## USDT Tracing

- [Bitcoin Core USDT Tracing: Internals and Implementation](articles/usdt-tracing.en.md) · [中文](articles/usdt-tracing.zh.md)

## Other

- [Bitcoin Core Fee Estimation Algorithm](articles/fee-estimation-notes.en.md) · [中文](articles/fee-estimation-notes.zh.md)
