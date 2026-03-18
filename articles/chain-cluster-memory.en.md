# ChainCluster Memory Comparison: Three Measurement Methods

[中文版](chain-cluster-memory.zh.md)

---

## Overview

This article compares memory usage between the `before_chaincluster` and `chaincluster` branches using three methods: RSS (process resident set size), Valgrind massif (heap profiling), and TxGraph's internal `GetMainMemoryUsage()` (based on `Cluster::TotalMemoryUsage()`).

**Trace file:** `txgraph.trace.4.5days.final` (~552 MB, 4.5 days of mempool activity)

---

## 1. RSS (Resident Set Size)

### Method

1. Build `txgraph-replay` on each branch with `-DWITH_TXGRAPH_TRACING=ON`
2. Run `txgraph-replay <trace_file>` in the background
3. Sample `/proc/<pid>/status` VmRSS every second
4. Record the maximum RSS during the run

### Results

| Branch              | Peak RSS (kB) |
|---------------------|---------------|
| before_chaincluster | 11,412        |
| chaincluster        | 10,984        |

**Difference:** chaincluster uses **428 kB less** (~3.7% reduction).

---

## 2. Massif (Heap Profiling)

### Method

1. Build Debug with `-DCMAKE_BUILD_TYPE=Debug -DWITH_TXGRAPH_TRACING=ON`
2. Run `valgrind --tool=massif --massif-out-file=out.out --stacks=no txgraph-replay <trace_file>`
3. Use `ms_print out.out` to extract peak heap snapshot

### Results

| Branch              | Peak heap (bytes) | Peak heap (MB) |
|---------------------|-------------------|----------------|
| before_chaincluster | 7,925,912         | 7.56           |
| chaincluster        | 7,605,304         | 7.25           |

**Difference:** chaincluster uses **320 kB less** heap (~4.0% reduction).

---

## 3. GetMainMemoryUsage (Cluster::TotalMemoryUsage)

### Method

1. Run `txgraph-replay --memory-csv=mem.csv <trace_file>`
2. The tool calls `GetMainMemoryUsage()` after every 100th COMMIT_STAGING (~17.6k samples)
3. CSV format: `commit_index,usage_bytes`
4. Plot with `python3 contrib/txgraph_tracing/plot_memory_curve.py mem_before.csv mem_chain.csv -o compare.png`

### Results

| Metric                    | before_chaincluster | chaincluster | Difference      |
|---------------------------|--------------------|--------------|-----------------|
| **Peak** (commit 1,290,200) | 4.61 MB            | 4.22 MB      | **402 KB less** (~8.3%) |
| **Final** (end of trace)  | 1.21 MB            | 1.18 MB      | **41 KB less** (~3.2%)  |
| **Average** over replay   | 2.37 MB            | 2.22 MB      | **155 KB less** (~6.5%) |

Peak occurs mid-trace (commit ~1.29M of 1.76M), not at the end.

---

## Theoretical Analysis

### Data Structure Comparison

| Component | GenericClusterImpl (before) | ChainClusterImpl (after) |
|-----------|-----------------------------|---------------------------|
| Per-tx data | DepGraph::Entry + m_mapping + m_linearization | TxData (GraphIndex + FeeFrac) |
| Per-tx bytes | ~40 | ~20 |
| Ancestor/descendant | Explicit BitSets per tx | Implicit (chain order) |
| Linearization | Explicit vector | Implicit [0,1,…,n−1] |

**GenericClusterImpl** for an n-tx cluster: ~40n bytes.

**ChainClusterImpl** for an n-tx chain: ~20n bytes.

**Savings per chain cluster of size n:** 20n bytes.

### Trace Characteristics (analyze_trace.py on txgraph.trace.4.5days.final)

**Peak state** (27,512 transactions, 8,596 clusters):

- Size 1: 7,628 clusters → **SingletonClusterImpl** (single-tx; not ChainClusterImpl)
- Size ≥2, chain-shaped: 889 clusters → **ChainClusterImpl** in chaincluster branch
- Size ≥2, non-chain: 79 clusters → **GenericClusterImpl** in both branches
- 99.1% of clusters are chain-shaped (including singletons)
- Transactions in chain clusters of size ≥2: 12,355 − 7,628 = **4,727 txs**

**Theoretical savings** (peak): 4,727 × 20 ≈ **94 KB** from cluster representation. Measured savings (320–428 KB) are larger; the difference likely comes from DepGraph/chunk overhead and allocator behavior.

---

## Summary

| Method | chaincluster reduction |
|--------|------------------------|
| RSS | ~3.7% |
| Massif (heap) | ~4.0% |
| GetMainMemoryUsage peak | ~8.3% |
| GetMainMemoryUsage final | ~3.2% |
| GetMainMemoryUsage average | ~6.5% |
