# 第 6 篇：CTxMemPool — 内存池核心操作

[English](mempool-txgraph-06-ctxmempool.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 6 篇。
> 上一篇：[第 5 篇：Staging — 双图系统](mempool-txgraph-05-staging.zh.md) | 下一篇：[第 7 篇：交易验证与入池 — ATMP 流程](mempool-txgraph-07-atmp.zh.md)

---

## 本篇聚焦

- 核心文件：`src/txmempool.h`, `src/txmempool.cpp`
- 关键类/函数：CTxMemPool, indexed_transaction_set, mapTx, mapNextTx, mapDeltas, addNewTransaction, removeUnchecked, TrimToSize, check
- 前置阅读：第 5 篇

---

## 概述

CTxMemPool（`src/txmempool.h:186`）是 Bitcoin Core 内存池的核心类。
它维护交易的存储和索引，与 TxGraph 配合管理交易依赖关系，
并提供对外的查询和修改接口。

本篇从 CTxMemPool 的数据成员出发，逐一讲解主要操作的实现。

## 1. mapTx — 多索引容器

（将讲解 `src/txmempool.h:231-234` 和 `:263`）

- `indexed_transaction_set`：基于 boost::multi_index 的容器
- 索引维度：by txid、by wtxid、by time（入池时间排序）
- 为什么选择 multi_index 而非多个独立 map

## 2. mapNextTx — 输入反向索引

（将讲解 `src/txmempool.h:298`）

- `indirectmap<COutPoint, txiter> mapNextTx`
- 作用：给定一个 UTXO 输出点，快速找到花费它的内存池交易
- 用于冲突检测和双花防护

## 3. mapDeltas — 手动费率调整

（将讲解 `src/txmempool.h:299`）

- `std::map<Txid, CAmount> mapDeltas`
- `prioritisetransaction` RPC 的后端存储
- 如何影响交易的有效费率

## 4. m_txgraph — 图引擎集成

（将讲解 `src/txmempool.h:261`）

- `std::unique_ptr<TxGraph> m_txgraph`
- CTxMemPool 如何通过 TxGraph 接口管理交易依赖
- 生命周期：随 CTxMemPool 创建和销毁

## 5. addNewTransaction — 交易入池

（将讲解 `src/txmempool.cpp:229`）

- 内部方法（通过 ChangeSet::Apply 调用）
- 将交易添加到 mapTx、mapNextTx
- 更新 TxGraph 中的交易和依赖关系
- 不直接暴露为公共 API（通过 ChangeSet 间接调用）

## 6. removeUnchecked — 交易移除

（将讲解 `src/txmempool.cpp:263`）

- 从 mapTx、mapNextTx 中移除
- 清理 TxGraph 中的引用
- "unchecked" 的含义：不检查依赖完整性

## 7. TrimToSize — 内存池大小限制

（将讲解 `src/txmempool.cpp:861`）

- 当内存池超过 `-maxmempool` 设定时触发
- 使用 `m_txgraph->GetWorstMainChunk()` 找到最低费率的 chunk
- 驱逐最低费率的交易直到满足大小限制
- 最低费率记录用于 `minrelayfee` 计算

## 8. check() — 一致性检查

（将讲解 `src/txmempool.cpp:433`）

- 验证 mapTx、mapNextTx、TxGraph 之间的一致性
- 调用 `m_txgraph->SanityCheck()`（:450）
- 仅在调试/测试模式下启用

---

## 小结

CTxMemPool 是连接上层验证逻辑和底层图引擎的枢纽。理解了它的数据结构和核心操作后，
下一篇我们将深入 validation 层，看看一笔交易是如何通过完整的验证流水线最终进入内存池的。
