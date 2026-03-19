# ChainLinearize vs ChainCluster: Original Trace Comparison

[中文版](chain-linearize-vs-chaincluster.zh.md)

---

## Overview

This article compares the **chain_linearize** and **chaincluster** branches on the original 4.5-day TxGraph trace. The chain_linearize branch uses GenericClusterImpl for all clusters (including chain-shaped ones); the chaincluster branch uses ChainClusterImpl for chain-shaped clusters, yielding O(N) fast paths and lower memory.

---

## Trace

**Trace file:** `txgraph.trace.4.5days.final` (original trace, ~552 MB)

- Total ops replayed: 71,313,272
- COMMIT_STAGING: 1,764,516
- At peak: ~27,512 transactions, ~8,596 clusters; 889 chain clusters of size≥2

---

## Comparison Test

The comparison script is available in the [bitcoin/bitcoin `chaincluster_time_mem_bench` branch](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench).

### Command

```bash
bash /path/to/compare_before_vs_chaincluster.sh chain_linearize chaincluster time
```

### GetMainMemoryUsage (Cluster::TotalMemoryUsage) Results

GetMainMemoryUsage is based on TxGraph's internal Cluster::TotalMemoryUsage and measures TxGraph memory. See [ChainCluster Memory Comparison: Three Methods](chain-cluster-memory.en.md) for measurement details.

| Branch | Final (bytes) |
|--------|---------------|
| chain_linearize | 1,273,368 |
| chaincluster | 1,232,424 |

**Difference:** chaincluster uses **40,944 bytes less** (~3.2%).

### Time Performance Measured Results

| Entry point | chain_linearize Total (μs) | chaincluster Total (μs) |
|-------------|----------------------------|--------------------------|
| DoWork | 1,637,936 | 1,293,445 |
| CompareMainOrder | 1,600 | 1,553 |
| GetAncestors | 1 | 0 |
| GetDescendants | 8,459 | 8,405 |
| CountDistinctClusters | 471 | 2,467 |
| GetMainMemoryUsage | 151 | 154 |
| GetMainStagingDiagrams | 20,079 | 15,866 |
| IsOversized | 25,534 | 107,668 |
| StartStaging | 83 | 51 |
| AbortStaging | 0 | 0 |
| CommitStaging | 9,563 | 10,305 |
| **TOTAL** | **1,703,877** | **1,439,914** |

The table sums CPU time (μs) per entry point. **Wall-clock time** (actual elapsed time for the full replay, including I/O) is measured separately: chain_linearize 8 s, chaincluster 7 s, **difference -1 s (-12%)**. **CPU time** difference: chaincluster is **~15.5% faster** (TOTAL μs).

---

## Analysis

### Why Is chaincluster ~12–15% Faster?

The chaincluster branch uses **ChainClusterImpl** for chain-shaped clusters (size≥2), which provides:

1. **O(N) fast path:** vs GenericClusterImpl's O(N²) linearization
2. **Lower memory:** ~20 bytes/tx vs ~40 bytes/tx

The main speedups appear on **DoWork** (-21.1%) and **GetMainStagingDiagrams** (-21.0%). IsOversized and CountDistinctClusters are slower in chaincluster due to different implementation trade-offs; the net effect on TOTAL is favorable.

### GetMainMemoryUsage on Original Trace

On the original trace, chaincluster uses ~3.2% less TxGraph memory (GetMainMemoryUsage) than chain_linearize. The 889 chain clusters at peak benefit from ChainClusterImpl's compact representation (~20 bytes/tx vs ~40 bytes/tx).

---

## Summary

On the original 4.5-day trace, **chaincluster** is ~12% faster in wall-clock time and ~15.5% faster in CPU time than **chain_linearize**. **GetMainMemoryUsage:** chaincluster uses ~3.2% less TxGraph memory. ChainClusterImpl provides both time and memory benefits for chain-shaped clusters.

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
