# 第 9 篇：测试与调试 — 质量保障

[English](mempool-txgraph-09-testing.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 9 篇。
> 上一篇：[第 8 篇：区块构建 — 从 Mempool 到区块模板](mempool-txgraph-08-block-building.zh.md)

---

## 本篇聚焦

- 核心文件：`src/test/txgraph_tests.cpp`, `src/test/fuzz/txgraph.cpp`
- 辅助文件：`src/txgraph.cpp`（SanityCheck 方法）
- 关键类/函数：SanityCheck, SimTxGraph, FUZZ_TARGET(txgraph)
- 前置阅读：第 4 篇（推荐）

---

## 概述

TxGraph 是一个复杂的状态机，正确性至关重要。Bitcoin Core 通过多层测试策略来保障质量：
从内部一致性检查（SanityCheck）到确定性单元测试，再到高覆盖率的 fuzz 测试。

本篇将展示这些测试手段的设计和使用方式。

## 1. SanityCheck — 内部一致性检查

（将讲解 `src/txgraph.cpp:2932`）

- SanityCheck 的检查项：
  - Cluster 内部一致性（DepGraph 与线性化的匹配）
  - Locator 的有效性（每个交易的位置信息是否正确）
  - ClusterSet 分桶的正确性（质量级别与实际状态一致）
  - ChunkIndex 的有序性
  - m_entries 与 Ref 的双向引用完整性
- 在测试中的调用模式
- CTxMemPool::check()（`src/txmempool.cpp:433`）中的调用

## 2. 单元测试：txgraph_tests

（将讲解 `src/test/txgraph_tests.cpp`，434 行）

- 测试套件结构（:14-434）：
  - `txgraph_trim_zigzag`（:29-90）：锯齿形依赖的超大图修剪
  - `txgraph_trim_flower`（:92-149）：花形拓扑的超大图修剪
  - `txgraph_trim_huge`（:151-263）：大规模（64,000 笔交易）修剪
  - `txgraph_trim_big_singletons`（:265-302）：超大单笔交易修剪
  - `txgraph_chunk_chain`（:304-379）：链式拓扑 chunk 费率验证
  - `txgraph_staging`（:381-432）：staging 创建/提交/回滚
- SanityCheck 的频繁调用（:67, :75, :81, :125, :131, :217, :247, :259, :286, :294, :368, :431）

## 3. Fuzz 测试：txgraph fuzzer

（将讲解 `src/test/fuzz/txgraph.cpp`，1396 行）

- 设计思路：differential testing（差分测试）
  - `SimTxGraph`（:37-301）：朴素的 TxGraph 模拟实现
  - 真实 `TxGraphImpl` 与 `SimTxGraph` 并行执行
  - 对比两者的输出是否一致
- `SimTxObject`（:26-32）：继承 TxGraph::Ref 的测试对象
- `FUZZ_TARGET(txgraph)`（:305-1396）：fuzz 目标入口
- 覆盖的操作：AddTransaction、RemoveTransaction、AddDependency、SetTransactionFee、StartStaging、CommitStaging、AbortStaging、GetCluster、GetAncestors、GetDescendants 等
- SanityCheck 调用（:1061, :1387）

## 4. TracingTxGraph — 装饰器模式

（将讲解 TracingTxGraph 的设计）

- 装饰器模式：包装 TxGraph 接口，录制所有操作
- Trace 文件格式
- 用于性能对比和回归测试
- 参考：[TxGraph Trace & Replay](txgraph-trace-replay.zh.md)

## 5. txgraph-replay 工具

（将讲解回放工具的使用方法）

- 从 trace 文件重放 TxGraph 操作
- A/B 性能对比：旧版 vs 新版实现
- 参考：[TxGraph Trace & Replay](txgraph-trace-replay.zh.md)

## 6. 常见调试技巧

（将收集 TxGraph 相关的调试经验）

- 如何定位 SanityCheck 失败
- 使用 `-debug=mempool` 日志
- 断点位置建议：ApplyDependencies、MakeAcceptable、Merge
- Fuzz corpus 最小化和复现

---

## 小结

测试是 TxGraph 可靠性的基石。差分 fuzz 测试的设计尤其精巧——
通过维护一个简单的参考实现来验证复杂实现的正确性。

本篇是 Mempool & TxGraph 代码导读系列的最后一篇。完整阅读本系列后，
你应该对 Bitcoin Core 的 mempool 子系统有了从架构到实现细节的全面理解。
