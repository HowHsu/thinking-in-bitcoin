# ChainCluster 无大链 Trace 基线

[English](chain-cluster-nochain-baseline.en.md)

---

## 概述

为评估 ChainCluster 优化在「无大 chain cluster」场景下的表现，我们生成了无大链 trace。该 trace 在原始 trace 基础上，通过添加额外的 `ADD_DEP` 边，消除了所有 **size ≥ 3** 的 chain-shaped cluster，仅保留 size=2 的 chain 和单例 cluster。

改写脚本仅消除 size≥3 的 chain，**size=2 的 chain 被保留**。因此 trace 中仍有数百个 size-2 chain cluster，chaincluster 分支仍能从中获益。

---

## 生成 trace 的步骤

### 1. 工具

使用脚本 `contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py`，位于 [bitcoin/bitcoin `chaincluster_time_mem_bench` 分支](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench)。

### 2. 命令

```bash
# 单进程（默认）
python3 contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py \
  <input_trace> <output_trace>

# 多进程加速（推荐 15–20 核）
python3 contrib/txgraph_tracing/rewrite_trace_no_chain_clusters.py \
  --jobs 15 \
  <input_trace> <output_trace>
```

### 3. 改写策略

- **size ≥ 3 的 chain cluster**：添加 `first → third` 边（如 A→B→C 变为 A→B、A→C、B→C），打破链式结构
- **size = 2 的 chain**：保持不变

### 4. 本次生成结果

**输入：** 原始 trace（约 552 MB，1,763,516 个 commits）

**输出：** 改写后的 trace

**额外 ADD_DEP 边数：** **43,074**

**验证：** `python3 contrib/txgraph_tracing/analyze_trace.py <trace_file>`

---

## Cluster 分布（analyze_trace.py）

### 峰值状态（27,512 笔交易，8,582 个 cluster）

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

- **Chain cluster 中的交易数：** 8,770（31.9%）
- **Non-chain cluster 中的交易数：** 18,742（68.1%）

### 峰值要点

- **582 个 size-2 chain cluster**（1,164 笔交易）仍在使用 ChainClusterImpl
- Size≥3 的 cluster 均为 non-chain（改写成功）

---

## 对比测试

对比脚本见 [bitcoin/bitcoin `chaincluster_time_mem_bench` 分支](https://github.com/bitcoin/bitcoin/tree/chaincluster_time_mem_bench)。

### 命令

```bash
TXGRAPH_TRACE_FILE=/path/to/txgraph.trace.4.5days.final.nochain.copy \
  ./contrib/compare_before_vs_chaincluster.sh getmain
```

### GetMainMemoryUsage（Cluster::TotalMemoryUsage）实测结果

GetMainMemoryUsage 基于 TxGraph 内部的 Cluster::TotalMemoryUsage，用于衡量 TxGraph 内存占用。测量方法详见 [ChainCluster 内存对比：三种测量方法](chain-cluster-memory.zh.md)。

| 分支 | Final (bytes) |
|------|---------------|
| before_chaincluster | 2,936,296 |
| chaincluster | 2,843,648 |

**差异：** chaincluster 少用 **92,648 bytes**（约 3.2%）。

### 时间性能实测结果

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

上表为各 entry point 的 CPU 时间求和（μs）。**墙钟时间**（整段 replay 的实际耗时，含 I/O 等）单独测量：Baseline 10 s，ChainCluster 10 s，**差异 0 s（0%）**。**CPU 时间**差异：chaincluster 快约 **21%**（TOTAL μs）。

---

## 分析

### 为何仍有约 21% CPU 时间提升？

改写脚本**仅消除 size≥3 的 chain**，**size=2 的 chain 被保留**。582 个 size-2 chain 在 chaincluster 分支中使用 **ChainClusterImpl**，获得：

1. **O(N) 快速路径**：相比 GenericClusterImpl 的 O(N²) 线性化
2. **更少内存**：约 20 字节/交易 vs 约 40 字节/交易

DoWork、CommitStaging、IsOversized 等路径上均有明显加速。

### 无大链 trace 上的 GetMainMemoryUsage

在无大链 trace 上，chaincluster 的 TxGraph 内存（GetMainMemoryUsage）比 before_chaincluster 少约 3.2%。582 个 size-2 chain 受益于 ChainClusterImpl 的紧凑表示（约 20 字节/交易 vs 约 40 字节/交易）。

### 与原始 trace 的对比

原始 trace 峰值有 **889 个 size≥2 的 chain cluster**；无大链 trace 仅保留 **582 个 size-2 chain**。若进一步消除 size-2 chain，可得到「完全无 chain cluster」的基线。

---

## 总结

无大链 trace 通过 43,074 条额外 ADD_DEP 边消除了所有 size≥3 的 chain cluster，仅保留 582 个 size-2 chain。**时间性能**：chaincluster 的 CPU 时间快约 21%（墙钟相近）；**GetMainMemoryUsage**：chaincluster 少用约 3.2% 的 TxGraph 内存。size-2 chain 在时间和内存上均有收益。

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
| 容器 ID | `acad37791c08` |
| 镜像 | `bitcoin:latest` |
| 基础系统 | Debian 12 (bookworm) |
| GCC | 12.2.0 |
| CMake | 3.25.1 |
