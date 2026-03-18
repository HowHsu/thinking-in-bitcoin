# ChainCluster 内存对比：三种测量方法

[English](chain-cluster-memory.en.md)

---

## 概述

本文使用三种方法对比 `before_chaincluster` 与 `chaincluster` 分支的内存占用：RSS（进程常驻集）、Valgrind massif（堆分析）、以及 TxGraph 内部的 `GetMainMemoryUsage()`（基于 `Cluster::TotalMemoryUsage()`）。

**Trace 文件：** `txgraph.trace.4.5days.final`（约 552 MB，4.5 天 mempool 活动）

---

## 1. RSS（常驻集大小）

### 方法步骤

1. 在各分支用 `-DWITH_TXGRAPH_TRACING=ON` 构建 `txgraph-replay`
2. 后台运行 `txgraph-replay <trace_file>`
3. 每秒采样 `/proc/<pid>/status` 的 VmRSS
4. 记录运行期间的最大 RSS

### 结果

| 分支               | 峰值 RSS (kB) |
|--------------------|----------------|
| before_chaincluster | 11,412         |
| chaincluster        | 10,984         |

**差异：** chaincluster 少用 **428 kB**（约 3.7%）。

---

## 2. Massif（堆分析）

### 方法步骤

1. 使用 `-DCMAKE_BUILD_TYPE=Debug -DWITH_TXGRAPH_TRACING=ON` 构建 Debug 版本
2. 运行 `valgrind --tool=massif --massif-out-file=out.out --stacks=no txgraph-replay <trace_file>`
3. 用 `ms_print out.out` 提取峰值堆快照

### 结果

| 分支               | 峰值堆 (bytes) | 峰值堆 (MB) |
|--------------------|----------------|-------------|
| before_chaincluster | 7,925,912      | 7.56        |
| chaincluster        | 7,605,304      | 7.25        |

**差异：** chaincluster 少用 **320 kB** 堆（约 4.0%）。

---

## 3. GetMainMemoryUsage（Cluster::TotalMemoryUsage）

### 方法步骤

1. 运行 `txgraph-replay --memory-csv=mem.csv <trace_file>`
2. 工具在每 100 次 COMMIT_STAGING 后调用 `GetMainMemoryUsage()`（约 1.76 万次采样）
3. CSV 格式：`commit_index,usage_bytes`
4. 绘图：`python3 contrib/txgraph_tracing/plot_memory_curve.py mem_before.csv mem_chain.csv -o compare.png`

### 结果

| 指标                     | before_chaincluster | chaincluster | 差异              |
|--------------------------|---------------------|--------------|-------------------|
| **峰值**（commit 1,290,200） | 4.61 MB             | 4.22 MB      | **少 402 KB**（约 8.3%） |
| **最终**（trace 结束）   | 1.21 MB             | 1.18 MB      | **少 41 KB**（约 3.2%）  |
| **平均**（整个回放）     | 2.37 MB             | 2.22 MB      | **少 155 KB**（约 6.5%） |

峰值出现在回放中段（约第 129 万次 commit，共 176 万次），而非 trace 结束。

---

## 理论分析

### 数据结构对比

| 组件       | GenericClusterImpl（之前） | ChainClusterImpl（之后） |
|------------|----------------------------|---------------------------|
| 每笔交易数据 | DepGraph::Entry + m_mapping + m_linearization | TxData (GraphIndex + FeeFrac) |
| 每笔交易字节 | ~40 | ~20 |
| 祖先/后代   | 显式 BitSet 每笔 | 隐式（链序） |
| 线性化     | 显式 vector | 隐式 [0,1,…,n−1] |

**GenericClusterImpl** 对 n 笔交易的 cluster：约 40n 字节。

**ChainClusterImpl** 对 n 笔交易的链：约 20n 字节。

**每个链式 cluster（规模 n）的节省：** 20n 字节。

### Trace 特征（analyze_trace.py 对 txgraph.trace.4.5days.final）

**峰值状态**（27,512 笔交易，8,596 个 cluster）：

- 规模 1：7,628 个 cluster → **SingletonClusterImpl**（单笔交易，非 ChainClusterImpl）
- 规模 ≥2、链式：889 个 cluster → chaincluster 分支使用 **ChainClusterImpl**
- 规模 ≥2、非链式：79 个 cluster → 两分支均使用 **GenericClusterImpl**
- 99.1% 的 cluster 为链式（含单例）
- 规模 ≥2 的链式 cluster 中的交易数：12,355 − 7,628 = **4,727 笔**

**理论节省**（峰值）：4,727 × 20 ≈ **94 KB**（仅 cluster 表示）。实测节省（320–428 KB）更大，差异可能来自 DepGraph/chunk 开销和分配器行为。

---

## 总结

| 方法 | chaincluster 减少幅度 |
|------|------------------------|
| RSS | 约 3.7% |
| Massif（堆） | 约 4.0% |
| GetMainMemoryUsage 峰值 | 约 8.3% |
| GetMainMemoryUsage 最终 | 约 3.2% |
| GetMainMemoryUsage 平均 | 约 6.5% |
