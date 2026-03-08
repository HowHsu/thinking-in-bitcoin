# 第 2 篇：TxGraph 接口 — 抽象层设计

[English](mempool-txgraph-02-txgraph-interface.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 2 篇。
> 上一篇：[第 1 篇：CTxMemPoolEntry](mempool-txgraph-01-entry.zh.md) | 下一篇：[第 3 篇：TxGraphImpl 数据结构](mempool-txgraph-03-impl-data.zh.md)

---

## 本篇聚焦

- 核心文件：`src/txgraph.h`
- 关键类/函数：TxGraph, TxGraph::Ref, TxGraph::BlockBuilder, Level, GraphIndex
- 前置阅读：第 1 篇

---

## 概述

TxGraph（`src/txgraph.h:47`）是一个纯虚类，定义了图引擎的完整公共 API。
它将 cluster 管理、线性化、staging、区块构建等复杂逻辑隐藏在一组清晰的虚函数接口之后。

这种设计允许 CTxMemPool 只通过接口与图引擎交互，便于测试（可注入 mock）和未来替换实现。

## 1. GraphIndex 与 Ref

（将讲解 `src/txgraph.h:51` 和 `:232-253`）

- `using GraphIndex = uint32_t;`（:51）：交易在图中的内部索引
- `Ref` 类（:232-253）：外部持有的交易句柄
  - `m_graph` 和 `m_index` 成员
  - 移动语义，不可复制
  - 析构时自动从图中移除交易
- Ref 的生命周期管理

## 2. Level 枚举

（将讲解 `src/txgraph.h:64-67`）

- `Level::TOP`：当 staging 存在时指向 staging 层，否则指向 main
- `Level::MAIN`：始终指向 main 层
- 为什么需要两个级别

## 3. Mutation 方法组

（将讲解 `src/txgraph.h:78-102`）

- `AddTransaction`（:78）：添加交易到图中
- `RemoveTransaction`（:93）：从图中移除交易
- `AddDependency`（:98）：添加父子依赖关系
- `SetTransactionFee`（:102）：更新交易费用

## 4. Work 与 Staging 方法组

（将讲解 `src/txgraph.h:108-120`）

- `DoWork`（:108）：执行延迟计算（cost budget 机制）
- `StartStaging`（:114）/ `AbortStaging`（:116）/ `CommitStaging`（:118）
- `HaveStaging`（:120）
- Lazy evaluation 的设计动机

## 5. Query 方法组

（将讲解 `src/txgraph.h:127-178`）

- 单交易查询：`Exists`（:130）、`GetIndividualFeerate`（:134）、`GetMainChunkFeerate`（:138）
- Cluster 查询：`GetCluster`（:142）、`GetAncestors`（:146）、`GetDescendants`（:150）
- 批量查询：`GetAncestorsUnion`（:154）、`GetDescendantsUnion`（:158）
- 全局查询：`GetTransactionCount`（:161）、`CountDistinctClusters`（:168）
- 排序：`CompareMainOrder`（:164）
- 诊断：`GetMainStagingDiagrams`（:173）
- 修剪：`Trim`（:178）

## 6. BlockBuilder 接口

（将讲解 `src/txgraph.h:181-196` 和 `:201`）

- `BlockBuilder` 内部类（:181-196）
- `GetCurrentChunk()`（:190）：获取当前最优 chunk
- `Include()`（:192）：将当前 chunk 纳入区块
- `Skip()`（:195）：跳过当前 chunk
- `GetBlockBuilder()`（:201）：工厂方法
- 迭代器模式的设计

## 7. 工厂函数与其他

（将讲解 `src/txgraph.h:206-271`）

- `GetWorstMainChunk`（:206）：获取最低费率 chunk（用于驱逐）
- `GetMainMemoryUsage`（:213）：内存使用统计
- `SanityCheck`（:216）：一致性检查
- `MakeTxGraph`（:266-271）：创建 TxGraphImpl 实例的工厂函数

---

## 小结

TxGraph 接口定义了图引擎的完整能力边界。理解这些 API 分类后，
下一篇我们将深入 TxGraphImpl，看看这些接口背后的数据结构是如何组织的。
