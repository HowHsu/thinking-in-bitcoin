# 第 7 篇：交易验证与入池 — ATMP 流程

[English](mempool-txgraph-07-atmp.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 7 篇。
> 上一篇：[第 6 篇：CTxMemPool — 内存池核心操作](mempool-txgraph-06-ctxmempool.zh.md) | 下一篇：[第 8 篇：区块构建 — 从 Mempool 到区块模板](mempool-txgraph-08-block-building.zh.md)

---

## 本篇聚焦

- 核心文件：`src/validation.cpp`
- 关键类/函数：MemPoolAccept, ATMPArgs, Workspace, PreChecks, PolicyScriptChecks, ConsensusScriptChecks, FinalizeSubpackage, AcceptSingleTransactionInternal, AcceptPackage, AcceptSubPackage
- 前置阅读：第 6 篇

---

## 概述

ATMP（AcceptToMemoryPool）是交易进入内存池的入口。MemPoolAccept 类（`src/validation.cpp:435`）
封装了完整的验证流水线：从基本策略检查到脚本验证，再到最终的入池操作。

本篇将沿着一笔交易的入池路径，逐步讲解每个验证阶段的逻辑。

## 1. MemPoolAccept 类结构

（将讲解 `src/validation.cpp:435-737`）

- 类的职责：协调交易验证的各个阶段
- `Workspace` 结构（:626-662）：单笔交易验证过程中的中间状态
- 与 CTxMemPool 和 CChainState 的关系

## 2. ATMPArgs — 验证参数

（将讲解 `src/validation.cpp:448-577`）

- ATMPArgs 的角色：控制验证行为的参数集
- 工厂方法：
  - `SingleAccept`（:482）：单笔交易提交
  - `PackageTestAccept`（:499）：Package 测试（dry-run）
  - `PackageChildWithParents`（:515）：带父交易的子交易
  - `SingleInPackageAccept`（:531）：Package 内的单笔交易
- 关键参数：`m_test_accept`（是否仅测试）、`m_allow_replacement`（是否允许 RBF）

## 3. PreChecks — 策略预检查

（将讲解 `src/validation.cpp:782`）

- 交易基本有效性检查
- 费用检查：是否满足最低中继费率
- 输入检查：UTXO 是否存在、是否已被花费
- 冲突检测：是否与已有交易冲突（RBF 候选识别）
- 大小和 sigops 限制
- 时间锁检查

## 4. PolicyScriptChecks — 策略脚本验证

（将讲解 `src/validation.cpp:1132`）

- 使用策略标志（policy flags）执行脚本验证
- 比共识规则更严格的策略检查
- 脚本缓存的使用

## 5. ConsensusScriptChecks — 共识脚本验证

（将讲解 `src/validation.cpp:1155`）

- 使用共识标志（consensus flags）执行脚本验证
- 成功后缓存脚本验证结果
- 与 PolicyScriptChecks 的区别

## 6. FinalizeSubpackage — 最终入池

（将讲解 `src/validation.cpp:1188`）

- 通过 ChangeSet 将交易应用到内存池
- 更新 TxGraph 中的依赖关系
- 触发通知（信号）

## 7. AcceptSingleTransactionInternal — 完整单交易流程

（将讲解 `src/validation.cpp:1314`）

- 完整的单交易验证流水线
- PreChecks → PolicyScriptChecks → ConsensusScriptChecks → FinalizeSubpackage
- RBF 评估在此过程中的位置

## 8. Package 验证

（将讲解 `src/validation.cpp:1593-1619`）

- `AcceptSubPackage`（:1593）：子 package 的验证
- `AcceptPackage`（:1619）：完整 package 验证流程
- Package 验证与单交易验证的差异
- CPFP（Child-Pays-For-Parent）的支持

## 9. 错误处理与 TxValidationResult

（将讲解验证过程中的错误分类和传播）

- TxValidationResult 枚举
- 不同错误类型对 P2P 行为的影响
- 罚分（misbehavior score）机制

---

## 小结

ATMP 流程是理解交易如何进入内存池的关键。掌握了验证流水线后，
下一篇我们将从相反方向看——交易如何从内存池中被选出，构建成区块模板。
