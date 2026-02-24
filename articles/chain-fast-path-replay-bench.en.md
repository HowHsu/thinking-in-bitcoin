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
| max_iters | SFP iteration budget |
| rng_seed | The RNG seed passed to SFP |
| old_linearization | The existing linearization (SFP's starting point) |

This captures everything `Linearize()` receives — enough for a faithful offline replay.

### Capture period

| | |
|---|---|
| Start | Sat Feb 21 14:19:57 UTC 2026 |
| End | Sun Feb 22 02:38:09 UTC 2026 |
| Duration | ~12.3 hours |

### Replay

Each captured cluster was replayed through two code paths:

- **With fast path**: `Linearize()` → `TryLinearizeChain` hit → O(N) return; miss → SFP fallback.
  `PostLinearize()` called only when SFP is used.
- **Without fast path**: `LinearizeSFP()` + `PostLinearize()` on every cluster (forced SFP).

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

### Chain cluster size distribution

| tx count | chains | non-chain |    all | chain% | total txs |
|---------:|-------:|----------:|-------:|-------:|----------:|
|        2 | 14,885 |         0 | 14,885 | 100.0% |    29,770 |
|        3 |  7,183 |     1,039 |  8,222 |  87.4% |    24,666 |
|        4 |  5,251 |       710 |  5,961 |  88.1% |    23,844 |
|        5 |  4,935 |       501 |  5,436 |  90.8% |    27,180 |
|        6 |  4,714 |       383 |  5,097 |  92.5% |    30,582 |
|        7 |  4,504 |       284 |  4,788 |  94.1% |    33,516 |
|        8 |  4,415 |       211 |  4,626 |  95.4% |    37,008 |
|        9 |  4,392 |       185 |  4,577 |  96.0% |    41,193 |
|       10 |  4,260 |       145 |  4,405 |  96.7% |    44,050 |
|       11 |  4,186 |       118 |  4,304 |  97.3% |    47,344 |
|       12 |  4,091 |       104 |  4,195 |  97.5% |    50,340 |
|       13 |  4,064 |        86 |  4,150 |  97.9% |    53,950 |
|       14 |  4,036 |        63 |  4,099 |  98.5% |    57,386 |
|       15 |  4,015 |        57 |  4,072 |  98.6% |    61,080 |
|       16 |  3,981 |        53 |  4,034 |  98.7% |    64,544 |
|       17 |  3,960 |        53 |  4,013 |  98.7% |    68,221 |
|       18 |  3,908 |        48 |  3,956 |  98.8% |    71,208 |
|       19 |  3,871 |        39 |  3,910 |  99.0% |    74,290 |
|       20 |  3,822 |        39 |  3,861 |  99.0% |    77,220 |
|       21 |  3,495 |        46 |  3,541 |  98.7% |    74,361 |
|       22 |  3,326 |        26 |  3,352 |  99.2% |    73,744 |
|       23 |  3,334 |        25 |  3,359 |  99.3% |    77,257 |
|       24 |  3,024 |        28 |  3,052 |  99.1% |    73,248 |
|       25 |  3,330 |        23 |  3,353 |  99.3% |    83,825 |
|    26–64 |      0 |       270 |    270 |   0.0% |     4,827 |

Clusters with ≥26 transactions (270 total) are all non-chains. Chain clusters naturally top
out around 25 transactions in practice: CPFP chains are typically short, and longer unconfirmed
chains are uncommon in normal traffic.

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

3. **14.6× instruction reduction.** The speedup is algorithmic (O(N) vs O(N²) in SFP's
   `MakeTopological`), not a cache or branch-prediction artefact.

4. **Negligible overhead on non-chains.** `TryLinearizeChain` performs a single O(N) scan
   of ancestor-set sizes and returns empty immediately for the 3.8% of clusters that are not
   chains. The cost is dwarfed by the SFP setup that follows.

---

## Reproduce

To reproduce these results on your own node:

1. **Capture**: Apply the [mempool cluster data dump](https://github.com/HowHsu/bitcoin/commit/3f98387b123a6670deb569485b9863b6dd9e55ad) commit on top of `master`, build and run your node. Cluster data will be written to `/tmp/mempool_clusters.txt`.
2. **Analyze**: Use [`scripts/analyze_clusters.py`](../scripts/analyze_clusters.py) to compute chain/non-chain statistics from the dump:
   ```bash
   python3 scripts/analyze_clusters.py /tmp/mempool_clusters.txt
   ```
3. **Replay**: Apply the [replay benchmark](https://github.com/HowHsu/bitcoin/commit/443d5240bb913fca574277aca28fe6b944cdf018) commit on top of the `chain_linearize` branch, build and run:
   ```bash
   ./build/src/bench/bench_bitcoin --filter='ReplayLinearize*'
   ```

---

## Related Articles

- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
- [Complexity Analysis of the SFP Algorithm on Chain Clusters](spf-chain-complexity.en.md)
