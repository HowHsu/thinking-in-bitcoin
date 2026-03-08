# 第 8 篇：区块构建 — 从 Mempool 到区块模板

[English](mempool-txgraph-08-block-building.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 8 篇。
> 上一篇：[第 7 篇：交易验证与入池 — ATMP 流程](mempool-txgraph-07-atmp.zh.md) | 下一篇：[第 9 篇：测试与调试 — 质量保障](mempool-txgraph-09-testing.zh.md)

---

## 本篇聚焦

- 核心文件：`src/node/miner.h`, `src/node/miner.cpp`
- 关键类/函数：BlockAssembler, CreateNewBlock, addChunks, BlockBuilder
- 前置阅读：第 7 篇

---

## 概述

区块构建是 mempool 子系统的最终输出——将内存池中的交易打包成一个区块模板，
供矿工使用。BlockAssembler（`src/node/miner.h:60`）负责这一流程，
它利用 TxGraph 的 BlockBuilder 接口，按费率从高到低选择 chunk 填充区块。

## 1. BlockAssembler 类

（将讲解 `src/node/miner.h:60-123`）

- 类的职责：协调区块模板的生成
- `Options` 结构（:81-88）：
  - `nBlockMaxWeight`：区块最大权重
  - `blockMinFeeRate`：区块最低费率
- 关键成员：`m_mempool`、`m_chainstate`、`pblocktemplate`

## 2. CreateNewBlock — 区块模板生成

（将讲解 `src/node/miner.cpp:122`）

- 完整流程：
  1. 创建区块头和 coinbase 交易
  2. 锁定内存池（`m_mempool->cs`）
  3. 调用 `m_mempool->StartBlockBuilding()`（:152）
  4. 调用 `addChunks()` 选择交易（:153）
  5. 调用 `m_mempool->StopBlockBuilding()`（:154）
  6. 完成 coinbase 和区块头

## 3. addChunks — 基于 chunk 的交易选择

（将讲解 `src/node/miner.cpp:279-334`）

- 核心循环：
  1. `GetBlockBuilderChunk()`（:293, :331）获取当前最优 chunk
  2. 检查 chunk 是否满足区块限制（权重、sigops）
  3. 满足：`IncludeBuilderChunk()`（:320）纳入区块
  4. 不满足：`SkipBuilderChunk()`（:311）跳过
- 循环终止条件：无更多 chunk 或区块已满

## 4. BlockBuilder 接口的使用

（将结合 `src/txgraph.h:181-196` 和 `src/txgraph.cpp:852-879` 讲解）

- `GetCurrentChunk()`：返回当前最高费率的 chunk
- `Include()`：将 chunk 中的交易标记为已选中
- `Skip()`：跳过当前 chunk，移动到下一个
- CTxMemPool 中的封装方法：StartBlockBuilding、GetBlockBuilderChunk、IncludeBuilderChunk、SkipBuilderChunk、StopBlockBuilding

## 5. 与传统 ancestor-feerate 方法的对比

（将讨论新旧区块构建方法的差异）

- 旧方法：ancestor feerate 排序 + 贪心选择
- 新方法：cluster 线性化 + chunk 迭代
- 优势：更准确的费率排序、无需维护 ancestor 计数
- 参考：[Cluster Mempool 的设计动机](https://delvingbitcoin.org/t/introduction-to-cluster-linearization/1032)

## 6. getblocktemplate RPC 调用链

（将讲解从 RPC 到 BlockAssembler 的完整调用路径）

- `getblocktemplate` RPC → `ProcessNewBlock` → `BlockAssembler::CreateNewBlock`
- 区块模板的 JSON 格式

---

## 小结

区块构建是 mempool 子系统价值的最终体现。chunk 迭代的方式简洁高效，
充分利用了 TxGraph 的线性化结果。下一篇（也是最后一篇）将介绍
如何测试和调试这个复杂的子系统。
