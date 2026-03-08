# 第 1 篇：CTxMemPoolEntry — 交易在内存池中的表示

[English](mempool-txgraph-01-entry.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 1 篇。
> 上一篇：[第 0 篇：架构概览](mempool-txgraph-00-architecture.zh.md) | 下一篇：[第 2 篇：TxGraph 接口 — 抽象层设计](mempool-txgraph-02-txgraph-interface.zh.md)

---

## 本篇聚焦

- 核心文件：`src/kernel/mempool_entry.h`
- 辅助文件：`src/util/feefrac.h`
- 关键类/函数：CTxMemPoolEntry, TxGraph::Ref, FeeFrac, FeePerWeight, FeePerVSize
- 前置阅读：第 0 篇

---

## 概述

CTxMemPoolEntry 是一笔交易进入内存池后的完整表示。它不仅包含交易本身（CTransactionRef），
还记录了费用、权重、入池时间、入池高度等元数据。

一个关键设计决策是 CTxMemPoolEntry 继承自 TxGraph::Ref（`src/kernel/mempool_entry.h:65`），
这使得每个 mempool entry 同时作为 TxGraph 中的引用句柄，无需额外的映射表。

## 1. 类定义与继承关系

（将讲解 `src/kernel/mempool_entry.h:65` 的类定义）

- `class CTxMemPoolEntry : public TxGraph::Ref`
- 为什么选择继承而非组合？
- TxGraph::Ref 的语义：移动语义、不可复制

## 2. 核心字段逐一解读

（将逐一讲解 `src/kernel/mempool_entry.h:73-83` 的字段）

| 字段 | 行号 | 类型 | 用途 |
|------|------|------|------|
| `tx` | :73 | `CTransactionRef` | 交易本体（共享指针） |
| `nFee` | :74 | `CAmount` | 原始交易费 |
| `nTxWeight` | :75 | `int32_t` | 交易权重（witness 折扣后） |
| `nTime` | :77 | `int64_t` | 入池时间戳 |
| `entry_sequence` | :78 | `uint64_t` | 入池序号（单调递增） |
| `entryHeight` | :79 | `unsigned int` | 入池时的区块高度 |
| `m_modified_fee` | :82 | `mutable CAmount` | 修改后的费用（prioritisetransaction） |
| `lockPoints` | :83 | `mutable LockPoints` | 时间锁缓存 |

## 3. LockPoints 结构

（将讲解 `src/kernel/mempool_entry.h:26-36`）

- LockPoints 的作用：避免重复计算 BIP68 时间锁
- height 和 time 字段的含义

## 4. 费率表示：FeePerWeight vs FeeFrac vs FeePerVSize

（将讲解 `src/util/feefrac.h` 中的费率比较逻辑）

- `FeeFrac` 结构（`src/util/feefrac.h:39-224`）：fee/size 对
- 比较语义：`FeeRateCompare`（:157）vs `operator<=>`（:178）
- `FeePerVSize`（:251-252）和 `FeePerWeight`（:255-256）的区别
- 为什么需要精确的费率比较（避免浮点数）

## 5. entry_sequence 的用途

（将讲解 entry_sequence 作为 fallback ordering 的角色）

- 在费率相同时如何打破平局
- 与 CTxMemPool 中的序号分配器的关系

---

## 小结

CTxMemPoolEntry 是 mempool 的原子单元。理解它的字段和继承关系后，
下一篇我们将进入 TxGraph::Ref 的"家"——TxGraph 接口层，
看看这个纯虚类如何定义了图引擎的完整 API。
