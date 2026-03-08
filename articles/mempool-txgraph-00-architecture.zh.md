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

| 层级 | 文件 | 职责 | 详见 |
|------|------|------|------|
| 入口层 | `src/validation.cpp` | 交易验证与入池（MemPoolAccept 类，:435） | 第 7 篇 |
| 内存池 | `src/txmempool.h/cpp` | CTxMemPool：交易存储、索引、ChangeSet（:186） | 第 6 篇 |
| 交易表示 | `src/kernel/mempool_entry.h` | CTxMemPoolEntry：单笔交易的池内表示（:65） | 第 1 篇 |
| 图接口 | `src/txgraph.h` | TxGraph 纯虚接口（:47） | 第 2 篇 |
| 图实现 | `src/txgraph.cpp` | TxGraphImpl：cluster 管理、线性化、staging（:390） | 第 3-5 篇 |
| 线性化 | `src/cluster_linearize.h` | DepGraph、Linearize、PostLinearize 算法（:29） | 第 4 篇 |
| 费率 | `src/util/feefrac.h` | FeeFrac 费率表示与精确比较（:39） | 第 1 篇 |
| 区块构建 | `src/node/miner.h/cpp` | BlockAssembler：区块模板生成（:60） | 第 8 篇 |

## 2. 核心数据流

### 2.1 交易入池路径

一笔交易从外部到达（P2P 网络或 RPC）到最终进入内存池，经过以下路径：

```
P2P / sendrawtransaction RPC
  │
  ▼
AcceptToMemoryPool()                     [src/validation.cpp]
  │  创建 MemPoolAccept 对象
  ▼
MemPoolAccept::AcceptSingleTransactionInternal()   [:1314]
  │  获取锁：cs_main + m_pool.cs
  │  创建 ChangeSet → 调用 m_txgraph->StartStaging()
  │
  ├─ ChangeSet::StageAddition()          [src/txmempool.h:636]
  │    将交易插入 staging 图
  │
  ├─ PreChecks()                         [:782]
  │    去重、标准性、费率、输入可用性、冲突检测
  │
  ├─ ReplacementChecks()
  │    RBF 激励兼容性评估
  │
  ├─ CheckMemPoolPolicyLimits()          [src/txmempool.h:643]
  │    cluster 数量/大小限制检查
  │
  ├─ PolicyScriptChecks()                [:1132]
  │    策略级脚本验证
  │
  ├─ ConsensusScriptChecks()             [:1155]
  │    共识级脚本验证 + 缓存结果
  │
  └─ FinalizeSubpackage()                [:1188]
       │  调用 ChangeSet::Apply()
       ▼
     CTxMemPool::Apply()
       ├─ removeUnchecked()  移除被替换的交易
       ├─ mapTx.insert()     插入新交易
       ├─ addNewTransaction()  更新 mapNextTx 等索引
       └─ m_txgraph->CommitStaging()
            staging 合并到 main
```

### 2.2 区块构建路径

矿工请求区块模板时，交易从内存池被选出打包：

```
getblocktemplate RPC / Stratum
  │
  ▼
BlockAssembler::CreateNewBlock()         [src/node/miner.cpp:122]
  │  LOCK(cs_main) + LOCK(m_mempool->cs)
  │
  ├─ m_mempool->StartBlockBuilding()     [:152]
  │    创建 BlockBuilderImpl，获取 ChunkIndex 迭代器
  │
  ├─ addChunks()                         [:279]
  │    循环：
  │    ┌─ GetBlockBuilderChunk()         [:293]
  │    │    从 ChunkIndex 取最高费率 chunk
  │    │
  │    ├─ 检查权重/sigops 限制
  │    │
  │    ├─ 满足 → IncludeBuilderChunk()   [:320]
  │    │           将 chunk 中的交易加入区块
  │    │
  │    └─ 不满足 → SkipBuilderChunk()    [:311]
  │                 跳过该 chunk（同 cluster 后续 chunk 也跳过）
  │
  └─ m_mempool->StopBlockBuilding()      [:154]
       释放 BlockBuilderImpl
```

## 3. 关键类关系

```
CTxMemPool                           [src/txmempool.h:186]
  ├── mapTx: boost::multi_index     交易存储（by txid / wtxid / time）
  │     └── CTxMemPoolEntry          每笔交易的池内表示
  │           └── : public TxGraph::Ref   继承，使 entry 同时作为图中的引用句柄
  ├── mapNextTx                      UTXO → 花费交易的反向索引
  ├── mapDeltas                      prioritisetransaction 费率调整
  ├── m_txgraph: unique_ptr<TxGraph> 图引擎（依赖关系、排序、staging）
  └── ChangeSet                      暂存变更的事务接口（StageAddition/Apply）

TxGraph                              [src/txgraph.h:47]（纯虚接口）
  └── TxGraphImpl                    [src/txgraph.cpp:390]（唯一实现）
        ├── m_main_clusterset        主 ClusterSet
        ├── m_staging_clusterset     可选的 staging ClusterSet
        │     └── ClusterSet
        │           └── Cluster[]    按 QualityLevel 分桶管理
        │                 ├── SingletonClusterImpl  单交易特化（无 DepGraph）
        │                 └── GenericClusterImpl    多交易（持有 DepGraph）
        │                       └── DepGraph<BitSet<64>>  依赖图 + 线性化
        ├── m_entries: Entry[]       GraphIndex → Entry 的密集数组
        ├── m_main_chunkindex        chunk 费率排序索引（std::set）
        └── BlockBuilderImpl         区块构建迭代器
```

核心设计：**交易存储**（mapTx）和**排序引擎**（TxGraph）分离。mapTx 负责存储和查询，
TxGraph 负责依赖关系、cluster 线性化和挖矿优先级排序。两者通过 `TxGraph::Ref` 的继承关系连接——
每个 `CTxMemPoolEntry` 同时就是图中的一个 Ref 句柄，无需额外的映射表。

## 4. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  validation 层   [src/validation.cpp]                   │
│  MemPoolAccept：策略检查、脚本验证、RBF 评估            │
│  职责：决定一笔交易"能不能"进入内存池                    │
├─────────────────────────────────────────────────────────┤
│  txmempool 层    [src/txmempool.h/cpp]                  │
│  CTxMemPool：交易存储、索引、ChangeSet 事务接口          │
│  职责：管理交易的存储和生命周期                          │
├─────────────────────────────────────────────────────────┤
│  txgraph 层      [src/txgraph.h, src/txgraph.cpp]       │
│  TxGraph/TxGraphImpl：依赖图、cluster、staging、排序     │
│  职责：管理交易之间的依赖关系和挖矿优先级                │
├─────────────────────────────────────────────────────────┤
│  cluster_linearize 层  [src/cluster_linearize.h]        │
│  DepGraph、Linearize、PostLinearize：纯算法             │
│  职责：给定一个依赖图，计算最优（或接近最优）的线性化排列 │
└─────────────────────────────────────────────────────────┘
```

每一层只依赖下层，不反向依赖上层：

- **validation 层**调用 txmempool 层的 `ChangeSet` 接口来暂存和提交变更，
  但 txmempool 不知道 validation 的存在。
- **txmempool 层**通过 `m_txgraph` 指针使用 TxGraph 的纯虚接口，
  但 TxGraph 不知道 CTxMemPool 的存在。
- **txgraph 层**使用 cluster_linearize 中的 `DepGraph` 和 `Linearize` 算法，
  但 cluster_linearize 是无状态的纯函数库，不知道 TxGraph 的存在。

这种分层使得每一层都可以独立测试。特别是 cluster_linearize 层作为纯算法库，
拥有极高的 fuzz 测试覆盖率（参见第 9 篇）。

## 5. 设计哲学

### 5.1 为什么要将 TxGraph 从 CTxMemPool 中分离？

历史上，CTxMemPool 直接管理交易之间的依赖关系（通过 ancestor/descendant 计数）。
Cluster Mempool 改革将依赖关系管理抽离为独立的 TxGraph 模块，原因有三：

1. **关注点分离**：CTxMemPool 专注于交易存储和索引（mapTx 的增删查），
   TxGraph 专注于图算法（cluster 分组、线性化、费率排序）。
2. **算法复杂度**：cluster 线性化涉及 NP-hard 的优化问题（分支定界搜索），
   将其封装在独立模块中更容易推理和优化。
3. **staging 需求**：RBF 评估需要"假装"交易已入池来比较费率图。
   将图操作独立出来，可以自然地支持 main/staging 双层架构。

### 5.2 为什么 TxGraph 使用纯虚接口？

`TxGraph`（`src/txgraph.h:47`）被定义为纯虚类，唯一实现 `TxGraphImpl` 在 `.cpp` 文件中。
这不是为了"多态"——系统中永远只有一种实现。真正的目的是：

1. **可测试性**：fuzz 测试（`src/test/fuzz/txgraph.cpp`）可以将 `TxGraphImpl` 与
   朴素实现 `SimTxGraph` 并行执行，进行差分测试。
2. **编译隔离**：`TxGraphImpl` 的 3500+ 行实现细节完全隐藏在 `.cpp` 中，
   修改内部数据结构不需要重新编译依赖 `txgraph.h` 的文件。
3. **接口约束**：强制所有交互通过明确定义的 API 进行，防止内部状态泄漏。

### 5.3 为什么采用 lazy evaluation？

TxGraph 内部是**惰性求值**的——调用 `AddTransaction`、`RemoveTransaction`、`AddDependency`
时并不立即执行，而是将操作放入 pending 队列。只有当查询方法需要结果、
或者显式调用 `DoWork(max_cost)` 时，才真正执行计算。

这样设计的好处是：

1. **批量优化**：多个连续的添加/删除操作可以合并处理，避免中间状态的无谓计算。
2. **预算控制**：`DoWork(max_cost)` 允许调用者控制每次执行的计算量，
   避免在关键路径上花费过多时间。
3. **空闲利用**：可以在节点空闲时调用 `DoWork` 预先完成线性化，
   使后续的查询和区块构建更快。

### 5.4 为什么需要 staging 双层架构？

RBF（Replace-By-Fee）评估需要回答一个问题："如果用新交易替换旧交易，
矿工的收益会不会更好？" 这需要比较替换前后的费率图（feerate diagram）。

Staging 架构使得这一评估可以原子地进行：

1. `StartStaging()`：创建 main 的逻辑副本
2. 在 staging 中移除被替换的交易、添加新交易
3. `GetMainStagingDiagrams()`：比较 main 和 staging 的费率图
4. 决定接受（`CommitStaging()`）或拒绝（`AbortStaging()`）

这与数据库事务的 BEGIN / COMMIT / ROLLBACK 模式完全类似。
CTxMemPool 通过 `ChangeSet` 类（`src/txmempool.h:620`）将这一机制封装为更高层的接口。

---

## 小结

本篇建立了 mempool 子系统的全局认知框架：

- **文件地图**：8 个关键文件，从 validation 到 cluster_linearize
- **数据流**：交易入池（ATMP 流水线）和区块构建（chunk 迭代）两条主路径
- **类关系**：CTxMemPool（存储）+ TxGraph（排序）分离，通过 Ref 继承连接
- **分层架构**：4 层单向依赖，每层独立可测试
- **设计哲学**：分离关注点、纯虚接口、lazy evaluation、staging 双层

下一篇我们将从最基本的构建单元开始——CTxMemPoolEntry，了解一笔交易在内存池中是如何被表示的。
