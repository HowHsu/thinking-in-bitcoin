# 第 3 篇：TxGraphImpl 数据结构 — 内部表示

[English](mempool-txgraph-03-impl-data.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 3 篇。
> 上一篇：[第 2 篇：TxGraph 接口](mempool-txgraph-02-txgraph-interface.zh.md) | 下一篇：[第 4 篇：聚类与线性化](mempool-txgraph-04-linearization.zh.md)

---

## 本篇聚焦

- 核心文件：`src/txgraph.cpp`（前半部分，数据结构定义）
- 关键类/结构：TxGraphImpl, Entry, Cluster, SingletonClusterImpl, GenericClusterImpl, Locator, ClusterSet
- 前置阅读：第 2 篇

---

## 概述

TxGraphImpl（`src/txgraph.cpp:390`）是 TxGraph 接口的唯一实现。它管理着一个庞大的内部状态：
交易数组、cluster 集合、chunk 索引，以及可选的 staging 层。

本篇重点是数据结构的"静态"视角——各个类和结构体是什么、怎么组织的，
暂不深入算法流程（那是第 4-5 篇的内容）。

## 1. Cluster 抽象基类

（将讲解 `src/txgraph.cpp:102-248`）

- Cluster 基类的职责：封装一组相互关联的交易
- 关键虚方法：依赖关系管理、线性化状态
- QualityLevel 枚举（:38-58）：从 OVERSIZED_SINGLETON 到 OPTIMAL 的状态

## 2. SingletonClusterImpl vs GenericClusterImpl

（将讲解 `src/txgraph.cpp:311-365` 和 `:252-309`）

- `SingletonClusterImpl`（:311-365）：仅包含一笔交易的特化实现
  - 为什么要特化？性能和内存优势
  - 无需 DepGraph，无需线性化
- `GenericClusterImpl`（:252-309）：多交易 cluster 的通用实现
  - 内部持有 DepGraph
  - 线性化状态管理

## 3. Entry 结构

（将讲解 `src/txgraph.cpp:601-625`）

- `Entry` 结构：TxGraphImpl 中每笔交易的内部表示
- 关键字段：`m_ref`（指向外部 Ref）、`m_locator`（在 cluster 中的位置）
- 与 CTxMemPoolEntry 的关系

## 4. Locator 结构

（将讲解 `src/txgraph.cpp:580-599`）

- `Locator`：(cluster_idx, pos_in_cluster) 二元组
- 如何从 GraphIndex 定位到具体 cluster 中的具体位置
- Locator 的状态变化（参见第 5 篇 staging 部分）

## 5. ClusterSet 结构

（将讲解 `src/txgraph.cpp:431-458`）

- `ClusterSet`：按 QualityLevel 分桶管理 cluster
- 每个桶的含义：NEEDS_SPLIT、NEEDS_RELINEARIZE、ACCEPTABLE、OPTIMAL 等
- 为什么按质量分级管理

## 6. TxGraphImpl 的顶层结构

（将讲解 `src/txgraph.cpp:390-850`）

- `m_entries` 数组（:628）：GraphIndex → Entry 的映射
- `m_unlinked`（:631）：已释放的 GraphIndex 列表
- `m_main_clusterset`（:461）：主 cluster 集合
- `m_staging_clusterset`（:463）：可选的 staging cluster 集合
- `m_main_chunkindex`（:544）：chunk 费率索引
- Compact 机制（:1775）：压缩 m_entries 数组中的空洞

## 7. BlockBuilderImpl

（将讲解 `src/txgraph.cpp:852-879`）

- BlockBuilderImpl 类：BlockBuilder 接口的实现
- 如何遍历 ChunkIndex 生成区块模板
- 与 TxGraphImpl 的交互

---

## 小结

本篇展示了 TxGraphImpl 的内部数据组织方式。理解这些数据结构后，
下一篇我们将进入算法核心——聚类与线性化，看看 cluster 是如何被拆分、
合并和排序的。
