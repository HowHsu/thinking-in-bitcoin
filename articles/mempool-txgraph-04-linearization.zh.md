# 第 4 篇：聚类与线性化 — 核心算法

[English](mempool-txgraph-04-linearization.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 4 篇。
> 上一篇：[第 3 篇：TxGraphImpl 数据结构](mempool-txgraph-03-impl-data.zh.md) | 下一篇：[第 5 篇：Staging — 双图系统](mempool-txgraph-05-staging.zh.md)

---

## 本篇聚焦

- 核心文件：`src/cluster_linearize.h`, `src/txgraph.cpp`（算法部分）
- 关键类/函数：DepGraph, Linearize, PostLinearize, QualityLevel, ApplyDependencies, MakeAcceptable, DoWork
- 前置阅读：第 3 篇

---

## 概述

聚类（clustering）和线性化（linearization）是 TxGraph 的算法核心。当交易之间存在依赖关系时，
它们会被分组到同一个 cluster 中。每个 cluster 内部需要进行线性化——确定一个交易排列顺序，
使得费率图（feerate diagram）尽可能优化。

本篇将深入 DepGraph 模板类的设计和 TxGraphImpl 中的算法流程。

## 1. DepGraph 模板类

（将讲解 `src/cluster_linearize.h:29-357`）

- `DepGraph<SetType>` 的模板参数：BitSet 类型
- Entry 内部结构（:33-49）：`feerate`、`ancestors`、`descendants`
- ancestors/descendants 的 BitSet 表示
- 添加交易、添加依赖的接口

## 2. SetInfo 辅助结构

（将讲解 `src/cluster_linearize.h:360-427`）

- `SetInfo<SetType>`：交易集合 + 聚合费率
- 用于 chunk 计算和线性化决策

## 3. Linearize 函数

（将讲解 `src/cluster_linearize.h:1798-1805`）

- 输入：DepGraph + 已有线性化（可选）
- 输出：新的线性化 + 剩余工作量
- 算法策略：分支定界（branch-and-bound）
- 迭代次数限制（cost budget）

## 4. PostLinearize 函数

（将讲解 `src/cluster_linearize.h:1854-1855`）

- PostLinearize 的作用：优化已有线性化
- 与 Linearize 的关系：先粗排再精调
- 参考：[PostLinearize 算法详解](post_linearize.md)

## 5. QualityLevel 状态机

（将讲解 `src/txgraph.cpp:38-58`）

- 状态定义：
  - `OVERSIZED_SINGLETON`：超大单笔交易
  - `NEEDS_SPLIT_FIX` / `NEEDS_SPLIT`：需要拆分
  - `NEEDS_FIX` / `NEEDS_RELINEARIZE`：需要重新线性化
  - `ACCEPTABLE`：可接受但可能非最优
  - `OPTIMAL`：已达最优
  - `NONE`：无效状态
- 状态转换触发条件

## 6. ApplyDependencies

（将讲解 `src/txgraph.cpp:2114`）

- 从 pending 队列应用依赖关系到 cluster
- 依赖传播导致的 cluster 合并
- 与 GroupClusters（:1856）和 Merge（:2068）的协作

## 7. MakeAcceptable 与线性化触发

（将讲解 `src/txgraph.cpp:2207-2215`）

- `MakeAcceptable`（:2207）：使单个 cluster 达到 ACCEPTABLE 质量
- `MakeAllAcceptable`（:2215）：使所有 cluster 达到 ACCEPTABLE
- Split（:1829）/ SplitAll（:1841）：拆分不连通的 cluster

## 8. Chunk 的定义与 ChunkIndex

（将讲解 chunk 在线性化结果中的角色）

- Chunk：线性化中费率单调递减的连续段
- ChunkIndex（`src/txgraph.cpp:544`）：按费率排序的 chunk 索引
- chunk 与区块构建的关系

## 9. DoWork 的 cost budget 机制

（将讲解 `src/txgraph.cpp:3113`）

- `DoWork(max_cost)` 的语义
- 如何分配计算预算给不同操作
- lazy evaluation 的实际工作分配

---

## 小结

聚类与线性化是理解 TxGraph 的关键。掌握了 DepGraph、QualityLevel 状态机和
lazy evaluation 机制后，下一篇我们将看 staging 双层架构如何支持原子性的
RBF 评估。
