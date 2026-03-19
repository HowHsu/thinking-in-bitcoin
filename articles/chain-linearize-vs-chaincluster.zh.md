# ChainLinearize vs ChainCluster：原始 Trace 对比

[English](chain-linearize-vs-chaincluster.en.md)

---

## 概述

本文在原始 4.5 天 TxGraph trace 上对比 **chain_linearize** 与 **chaincluster** 分支。chain_linearize 对所有 cluster（含链式）均使用 GenericClusterImpl；chaincluster 对链式 cluster 使用 ChainClusterImpl，提供 O(N) 快速路径和更低内存。

---

## Trace

**Trace 文件：** `txgraph.trace.4.5days.final`（原始 trace，约 552 MB）

- 总操作数：71,313,272
- COMMIT_STAGING：1,764,516
- 峰值：约 27,512 笔交易，约 8,596 个 cluster；889 个 size≥2 的 chain cluster

---

## 对比测试

对比脚本见 [bitcoin/bitcoin `chaincluster_time_mem_bench` 分支](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench)。

### 命令

```bash
bash /path/to/compare_before_vs_chaincluster.sh chain_linearize chaincluster time
```

### GetMainMemoryUsage（Cluster::TotalMemoryUsage）实测结果

GetMainMemoryUsage 基于 TxGraph 内部的 Cluster::TotalMemoryUsage，用于衡量 TxGraph 内存占用。测量方法详见 [ChainCluster 内存对比：三种测量方法](chain-cluster-memory.zh.md)。

| 分支 | Final (bytes) |
|------|---------------|
| chain_linearize | 1,273,368 |
| chaincluster | 1,232,424 |

**差异：** chaincluster 少用 **40,944 bytes**（约 3.2%）。

### 时间性能实测结果

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

上表为各 entry point 的 CPU 时间求和（μs）。**墙钟时间**（整段 replay 的实际耗时，含 I/O 等）单独测量：chain_linearize 8 s，chaincluster 7 s，**差异 -1 s（-12%）**。**CPU 时间**差异：chaincluster 快约 **15.5%**（TOTAL μs）。

---

## 分析

### 为何 chaincluster 快约 12–15%？

chaincluster 分支对链式 cluster（size≥2）使用 **ChainClusterImpl**，获得：

1. **O(N) 快速路径**：相比 GenericClusterImpl 的 O(N²) 线性化
2. **更少内存**：约 20 字节/交易 vs 约 40 字节/交易

主要加速体现在 **DoWork**（-21.1%）和 **GetMainStagingDiagrams**（-21.0%）。IsOversized 与 CountDistinctClusters 在 chaincluster 中略慢，源于实现取舍不同；对 TOTAL 的净影响仍是正向的。

### 原始 trace 上的 GetMainMemoryUsage

在原始 trace 上，chaincluster 的 TxGraph 内存（GetMainMemoryUsage）比 chain_linearize 少约 3.2%。峰值的 889 个 chain cluster 受益于 ChainClusterImpl 的紧凑表示（约 20 字节/交易 vs 约 40 字节/交易）。

---

## 总结

在原始 4.5 天 trace 上，**chaincluster** 比 **chain_linearize** 墙钟时间快约 12%，CPU 时间快约 15.5%。**GetMainMemoryUsage**：chaincluster 少用约 3.2% 的 TxGraph 内存。ChainClusterImpl 对链式 cluster 在时间和内存上均有收益。

---

## 测试环境

上述结果在测试机上测得。

### 硬件

| 项目 | 值 |
|------|-----|
| CPU | Intel Core i5-13600KF（14 核，20 线程） |
| 内存 | 32 GB |

### 宿主机系统

| 项目 | 值 |
|------|-----|
| 系统 | Ubuntu 24.04 LTS (Noble Numbat) |
| Docker | 28.4.0 |

### Docker 容器

| 项目 | 值 |
|------|-----|
| 基础系统 | Debian 12 (bookworm) |
| GCC | 12.2.0 |
| CMake | 3.25.1 |
