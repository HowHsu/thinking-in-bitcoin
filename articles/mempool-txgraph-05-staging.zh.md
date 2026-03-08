# 第 5 篇：Staging — 双图系统

[English](mempool-txgraph-05-staging.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 5 篇。
> 上一篇：[第 4 篇：聚类与线性化](mempool-txgraph-04-linearization.zh.md) | 下一篇：[第 6 篇：CTxMemPool — 内存池核心操作](mempool-txgraph-06-ctxmempool.zh.md)

---

## 本篇聚焦

- 核心文件：`src/txgraph.cpp`（staging 相关）, `src/txmempool.h`（ChangeSet 类）
- 关键类/函数：StartStaging, CommitStaging, AbortStaging, ChangeSet, GetMainStagingDiagrams
- 前置阅读：第 4 篇

---

## 概述

Staging 是 TxGraph 的双层架构，允许在不修改 main graph 的前提下，
预演一组变更（添加/移除交易），评估其效果（如 RBF 的费率图对比），
然后决定提交或回滚。

这种设计对于 RBF（Replace-By-Fee）评估至关重要——需要先在 staging 中模拟替换，
比较新旧费率图，再决定是否接受。

## 1. 两层架构的动机

（将解释为什么需要 staging）

- 问题：评估 RBF 时需要"假装"交易已入池
- 解决方案：staging 层作为 main 层的临时覆盖
- 类比：数据库事务的 BEGIN / COMMIT / ROLLBACK

## 2. Locator 状态机

（将讲解 Locator 在 staging 中的五种状态）

| 状态 | main | staging | 含义 |
|------|------|---------|------|
| (M,M) | 有位置 | 同位置 | 交易仅在 main 中，staging 未修改 |
| (P,M) | pending | 同 | main 层有 pending 依赖 |
| (P,P) | pending | pending | 两层都有 pending 依赖 |
| (M,P) | 有位置 | pending | staging 层修改了依赖 |
| (P,R) | pending | removed | staging 中被移除 |

## 3. StartStaging / CommitStaging / AbortStaging

（将讲解 `src/txgraph.cpp:2626`, `:2681`, `:2650`）

- `StartStaging`（:2626）：创建 staging ClusterSet，复制必要状态
- `CommitStaging`（:2681）：将 staging 变更合并到 main
- `AbortStaging`（:2650）：丢弃 staging 层，恢复到 main 状态

## 4. ChangeSet 类

（将讲解 `src/txmempool.h:620-693`）

- `ChangeSet` 是 CTxMemPool 中管理 staging 变更的高层接口
- `StageAddition`（:636）：暂存一笔待添加的交易
- `StageRemoval`（:638）：暂存一笔待移除的交易
- `CheckMemPoolPolicyLimits`（:643）：检查变更后是否满足策略限制
- `CalculateChunksForRBF`（:674）：计算新旧费率图用于 RBF 评估
- `Apply`（:679）：将暂存的变更实际应用到 mempool

## 5. Staging 与 RBF 评估

（将讲解 staging 如何服务于 RBF 流程）

- RBF 评估流程：
  1. StartStaging
  2. 在 staging 中移除被替换的交易
  3. 在 staging 中添加新交易
  4. 比较 main vs staging 费率图
  5. 决定 Commit 或 Abort

## 6. GetMainStagingDiagrams

（将讲解 `src/txgraph.cpp:2810`）

- 返回 main 和 staging 的费率图（feerate diagram）
- 费率图的含义：累积 size vs 费率的阶梯函数
- `CompareChunks`（`src/util/feefrac.h:234`）：比较两个费率图

---

## 小结

Staging 双层架构是 TxGraph 支持原子性变更评估的关键机制。
理解了 staging 后，下一篇我们将上升到 CTxMemPool 层，
看看它如何使用 TxGraph 和 ChangeSet 来管理整个内存池。
