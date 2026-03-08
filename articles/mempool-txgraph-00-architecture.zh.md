# 第 0 篇：架构概览

[English](mempool-txgraph-00-architecture.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 0 篇。
> 下一篇：[第 1 篇：CTxMemPoolEntry — 交易在内存池中的表示](mempool-txgraph-01-entry.zh.md)

---

## 本篇聚焦

- 核心文件：全局视角，不聚焦单一文件
- 关键类/函数：CTxMemPool, TxGraph, Cluster, DepGraph, BlockAssembler, MemPoolAccept
- 前置阅读：无（系列起点）

---

## 概述

本篇从全局视角介绍 Bitcoin Core 的 mempool 子系统架构。我们将建立一张"文件地图"，
理清核心数据流（交易提交 → 验证 → 入池 → 构建区块模板），并展示关键类之间的关系。

理解架构全貌是深入源码的前提。本篇不涉及具体实现细节，而是为后续 9 篇文章提供导航框架。

## 1. 文件地图

Mempool 子系统的源码分布在以下关键文件中：

| 层级 | 文件 | 职责 |
|------|------|------|
| 入口层 | `src/validation.cpp` | 交易验证与入池（MemPoolAccept） |
| 内存池 | `src/txmempool.h/cpp` | CTxMemPool：交易存储、索引、ChangeSet |
| 交易表示 | `src/kernel/mempool_entry.h` | CTxMemPoolEntry：单笔交易的池内表示 |
| 图引擎 | `src/txgraph.h` | TxGraph 纯虚接口 |
| 图实现 | `src/txgraph.cpp` | TxGraphImpl：cluster 管理、线性化、staging |
| 线性化 | `src/cluster_linearize.h` | DepGraph、Linearize、PostLinearize 算法 |
| 费率 | `src/util/feefrac.h` | FeeFrac 费率表示与比较 |
| 区块构建 | `src/node/miner.h/cpp` | BlockAssembler：区块模板生成 |

## 2. 核心数据流

（将详细展示交易从 RPC/P2P 进入到区块模板的完整路径）

- 交易提交：`AcceptToMemoryPool` → `MemPoolAccept::AcceptSingleTransactionInternal`
- 验证流水线：`PreChecks` → `PolicyScriptChecks` → `ConsensusScriptChecks` → `FinalizeSubpackage`
- 入池：`ChangeSet::Apply` → `CTxMemPool::addNewTransaction` → `TxGraph::AddTransaction`
- 区块构建：`BlockAssembler::CreateNewBlock` → `addChunks` → `TxGraph::BlockBuilder`

## 3. 关键类关系

（将绘制类关系图）

```
CTxMemPool
  ├── mapTx: boost::multi_index<CTxMemPoolEntry>
  ├── m_txgraph: unique_ptr<TxGraph>
  └── ChangeSet (staging 管理)

TxGraph (纯虚接口)
  └── TxGraphImpl (实现)
        ├── ClusterSet (m_main / m_staging)
        │     └── Cluster (SingletonClusterImpl / GenericClusterImpl)
        │           └── DepGraph<BitSet> (依赖图 + 线性化)
        ├── Entry[] (交易数据)
        ├── ChunkIndex (chunk 费率索引)
        └── BlockBuilderImpl

CTxMemPoolEntry : public TxGraph::Ref
  └── 每个 entry 同时是 TxGraph 中的一个 Ref
```

## 4. 分层架构

（将详细解释 validation → txmempool → txgraph → cluster_linearize 的分层设计）

- **validation 层**：策略检查、脚本验证、RBF 评估
- **txmempool 层**：交易存储与索引（mapTx, mapNextTx, mapDeltas）
- **txgraph 层**：依赖关系图、cluster 管理、staging
- **cluster_linearize 层**：纯算法层，无副作用

## 5. 设计哲学

（将讨论以下设计决策背后的动机）

- 为什么要将 TxGraph 从 CTxMemPool 中分离？
- 为什么 TxGraph 使用纯虚接口？
- 为什么采用 lazy evaluation（DoWork 机制）？
- 为什么需要 staging 双层架构？

---

## 小结

本篇建立了 mempool 子系统的全局认知框架。下一篇我们将从最基本的构建单元开始——
CTxMemPoolEntry，了解一笔交易在内存池中是如何被表示的。
