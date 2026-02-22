# Replay Benchmark: TryLinearizeChain on Real Mempool Data

[中文版](chain-fast-path-replay-bench.zh.md)

---

## Motivation

Synthetic benchmarks (e.g., `LinearizeOptimallyMonotoneChainTotal`) demonstrate per-call speedups
of 20–36× on chain clusters, but the reader may ask: *how much does this matter in practice?*

This benchmark answers two questions with real mainnet data:

1. **What fraction of clusters are chains?**
2. **What is the aggregate speedup when replaying a real linearization workload?**

---

## Methodology

### Capture

Temporary instrumentation was added to `GenericClusterImpl::Relinearize()` in `txgraph.cpp`.
Before each `Linearize()` call, the following inputs were serialized to
[`mempool_clusters.txt`](mempool_clusters.txt):

| Field | Description |
|-------|-------------|
| DepGraph (hex) | Cluster topology and feerates, serialized via `DepGraphFormatter` |
| is_topological | Whether the old linearization is topologically valid |
| max_iters | SPF iteration budget |
| rng_seed | The RNG seed passed to SPF |
| old_linearization | The existing linearization (SPF's starting point) |

This captures everything `Linearize()` receives — enough for a faithful offline replay.

### Capture period

| | |
|---|---|
| Start | Sat Feb 21 14:19:57 UTC 2026 |
| End | Sun Feb 22 02:38:09 UTC 2026 |
| Duration | ~12.3 hours |

### Replay

Each captured cluster was replayed through two code paths:

- **With fast path**: `Linearize()` → `TryLinearizeChain` hit → O(N) return; miss → SPF fallback.
  `PostLinearize()` called only when SPF is used.
- **Without fast path**: `LinearizeSPF()` + `PostLinearize()` on every cluster (forced SPF).

Both paths received the identical DepGraph, old linearization, and RNG seed.
The only difference is whether `TryLinearizeChain` is tried first.
`fallback_order` was replaced with `IndexTxOrder` (index-based comparison), which is standard
practice in all existing benchmarks and tests.

---

## Dataset

| Metric | Value |
|--------|------:|
| Total `Relinearize()` calls | 115,370 |
| Total transactions | 1,304,654 |
| Chain clusters | 110,982 (96.2%) |
| Non-chain clusters | 4,388 (3.8%) |
| Average cluster size | 11.3 tx |

**96.2% of all clusters seen in a 12-hour mainnet window are chains**, consistent with the
dominance of CPFP fee-bumping in real Bitcoin traffic.

Raw data: [`mempool_clusters.txt`](mempool_clusters.txt)

---

## Results

### Aggregate

| | With fast path | Without fast path | Ratio |
|---|---:|---:|---:|
| Time (ns/replay) | 14,626,254 | 247,187,979 | **16.9×** |
| Instructions | 181,197,637 | 2,652,277,302 | 14.6× |
| Cycles | 50,972,740 | 861,302,943 | 16.9× |
| Branches | 23,610,416 | 345,208,836 | 14.6× |
| IPC | 3.555 | 3.080 | |

One full replay = processing all 115,370 clusters once.

### Per-cluster

| | With fast path | Without fast path |
|---|---:|---:|
| ns / cluster | 127 | 2,143 |
| ns / transaction | 11.2 | 189.5 |

---

## Key Takeaways

1. **Chain clusters dominate.** 96.2% of `Relinearize()` calls during a 12-hour mainnet window
   operate on chain-shaped clusters. This makes a chain-specific fast path high-impact.

2. **16.9× aggregate speedup.** Total linearization time drops from ~247 ms to ~15 ms per
   replay of the full 115k-cluster workload.

3. **14.6× instruction reduction.** The speedup is algorithmic (O(N) vs O(N²) in SPF's
   `MakeTopological`), not a cache or branch-prediction artefact.

4. **Negligible overhead on non-chains.** `TryLinearizeChain` performs a single O(N) scan
   of ancestor-set sizes and returns empty immediately for the 3.8% of clusters that are not
   chains. The cost is dwarfed by the SPF setup that follows.

---

## Reproduce

To reproduce these results on your own node:

1. **Capture**: Apply the [mempool cluster data dump](https://github.com/HowHsu/bitcoin/commit/3f98387b123a6670deb569485b9863b6dd9e55ad) commit on top of `master`, build and run your node. Cluster data will be written to `/tmp/mempool_clusters.txt`.
2. **Replay**: Apply the [replay benchmark](https://github.com/HowHsu/bitcoin/commit/443d5240bb913fca574277aca28fe6b944cdf018) commit on top of the `chain_linearize` branch, build and run:
   ```bash
   ./build/src/bench/bench_bitcoin --filter='ReplayLinearize*'
   ```

---

## Related Articles

- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
- [Complexity Analysis of the SPF Algorithm on Chain Clusters](spf-chain-complexity.en.md)
