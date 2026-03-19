# ChainCluster No-Large-Chain Trace Baseline

[中文版](chain-cluster-nochain-baseline.zh.md)

---

## Overview

To evaluate the ChainCluster optimization when there are no large chain clusters, we generated a no-large-chain trace. This trace is derived from the original by adding extra `ADD_DEP` edges to eliminate all **size ≥ 3** chain-shaped clusters, leaving only size-2 chains and singleton clusters.

The rewrite script only eliminates chains of size≥3; **size-2 chains are left unchanged**. Therefore the trace still contains hundreds of size-2 chain clusters, from which the chaincluster branch still benefits.

---

## Generation Steps

### 1. Tool

Use the script `contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py` in the [bitcoin/bitcoin `chaincluster_time_mem_bench` branch](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench).

### 2. Commands

```bash
# Single process (default)
python3 contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py \
  <input_trace> <output_trace>

# Multi-process (recommended for 15–20 cores)
python3 contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py \
  --jobs 15 \
  <input_trace> <output_trace>
```

### 3. Rewrite Strategy

- **Chain clusters of size ≥ 3**: Add `first → third` edge (e.g. A→B→C becomes A→B, A→C, B→C) to break the chain
- **Chains of size 2**: Left unchanged

### 4. This Run Result

**Input:** Original trace (~552 MB, 1,763,516 commits)

**Output:** Rewritten trace

**Extra ADD_DEP edges added:** **43,074**

**Verification:** `python3 contrib/txgraph_tracing/analyze_trace.py <trace_file>`

---

## Cluster Distribution (analyze_trace.py)

### Peak State (27,512 transactions, 8,582 clusters)

| Size | Clusters | Chains | Non-chain | Chain% |
|------|----------|--------|-----------|--------|
| 1 | 7,606 | 7,606 | 0 | 100.0% |
| 2 | 582 | 582 | 0 | 100.0% |
| 3 | 104 | 0 | 104 | 0.0% |
| 4 | 74 | 0 | 74 | 0.0% |
| 5 | 21 | 0 | 21 | 0.0% |
| 6 | 10 | 0 | 10 | 0.0% |
| 7 | 12 | 0 | 12 | 0.0% |
| 8 | 4 | 0 | 4 | 0.0% |
| 9 | 31 | 0 | 31 | 0.0% |
| 10 | 25 | 0 | 25 | 0.0% |
| … | … | … | … | … |
| **TOTAL** | **8,582** | **8,188** | **394** | **95.4%** |

- **Transactions in chain clusters:** 8,770 (31.9%)
- **Transactions in non-chain clusters:** 18,742 (68.1%)

### Peak Summary

- **582 size-2 chain clusters** (1,164 transactions) still use ChainClusterImpl
- All clusters of size≥3 are non-chain (rewrite succeeded)

---

## Comparison Test

The comparison script is available in the [bitcoin/bitcoin `chaincluster_time_mem_bench` branch](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench).

### Command

```bash
TXGRAPH_TRACE_FILE=/path/to/txgraph.trace.4.5days.final.nochain.copy \
  ./contrib/compare_before_vs_chaincluster.sh getmain
```

### GetMainMemoryUsage (Cluster::TotalMemoryUsage) Results

GetMainMemoryUsage is based on TxGraph's internal Cluster::TotalMemoryUsage and measures TxGraph memory. See [ChainCluster Memory Comparison: Three Methods](chain-cluster-memory.en.md) for measurement details.

| Branch | Final (bytes) |
|--------|---------------|
| before_chaincluster | 2,936,296 |
| chaincluster | 2,843,648 |

**Difference:** chaincluster uses **92,648 bytes less** (~3.2%).

### Time Performance Measured Results

| Entry point | Baseline Total (μs) | ChainCluster Total (μs) |
|-------------|---------------------|--------------------------|
| DoWork | 3,637,704 | 2,816,609 |
| CompareMainOrder | 1,536 | 1,464 |
| GetAncestors | 0 | 0 |
| GetDescendants | 8,533 | 8,508 |
| CountDistinctClusters | 546 | 1,113 |
| GetMainMemoryUsage | 154 | 168 |
| GetMainStagingDiagrams | 35,717 | 29,033 |
| IsOversized | 31,423 | 62,107 |
| StartStaging | 69 | 70 |
| AbortStaging | 0 | 1 |
| CommitStaging | 9,397 | 9,671 |
| **TOTAL** | **3,725,079** | **2,928,744** |

The table sums CPU time (μs) per entry point. **Wall-clock time** (actual elapsed time for the full replay, including I/O) is measured separately: Baseline 10 s, ChainCluster 10 s, **difference 0 s (0%)**. **CPU time** difference: chaincluster is **~21% faster** (TOTAL μs).

---

## Analysis

### Why Is There Still ~21% CPU Time Improvement?

The rewrite script **only eliminates chains of size≥3**; **size-2 chains are left unchanged**. The 582 size-2 chains use **ChainClusterImpl** in the chaincluster branch, yielding:

1. **O(N) fast path:** vs GenericClusterImpl's O(N²) linearization
2. **Lower memory:** ~20 bytes/tx vs ~40 bytes/tx

Clear speedups appear on DoWork, CommitStaging, IsOversized, and other paths.

### GetMainMemoryUsage on No-Large-Chain Trace

On the no-large-chain trace, chaincluster uses ~3.2% less TxGraph memory (GetMainMemoryUsage) than before_chaincluster. The 582 size-2 chains benefit from ChainClusterImpl's compact representation (~20 bytes/tx vs ~40 bytes/tx).

### Comparison with Original Trace

The original trace has **889 chain clusters of size≥2** at peak; the no-large-chain trace retains only **582 size-2 chains**. Eliminating size-2 chains as well would yield a "fully no-chain" baseline.

---

## Summary

The no-large-chain trace eliminates all size≥3 chain clusters by adding 43,074 extra ADD_DEP edges, leaving 582 size-2 chains. **Time performance:** chaincluster is ~21% faster in CPU time (wall-clock similar); **GetMainMemoryUsage:** chaincluster uses ~3.2% less TxGraph memory. Size-2 chains provide both time and memory benefits.

---

## Test Environment

The above results were obtained on the test machine.

### Hardware

| Item | Value |
|------|-------|
| CPU | Intel Core i5-13600KF (14 cores, 20 threads) |
| Memory | 32 GB |

### Host OS

| Item | Value |
|------|-------|
| OS | Ubuntu 24.04 LTS (Noble Numbat) |
| Docker | 28.4.0 |

### Docker Container

| Item | Value |
|------|-------|
| Base OS | Debian 12 (bookworm) |
| GCC | 12.2.0 |
| CMake | 3.25.1 |
