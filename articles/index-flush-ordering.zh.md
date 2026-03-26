# Bitcoin Core 数据持久化：Flush 时机与顺序

[English](index-flush-ordering.en.md)

---

> 本文由对 [PR #34897](https://github.com/bitcoin/bitcoin/pull/34897)（indexes: Don't commit ahead of the flushed chainstate）的 review 和分析延伸而来。在分析该 PR 修复的索引损坏问题时，需要理解各类数据的 flush 时机和先后关系，因此整理成文。PR 解析见 [PR #34897 解析](index-flush-pr34897.zh.md)。

## 概述

Bitcoin Core 在运行时维护大量数据结构，它们在内存中更新，定期写入（flush）磁盘。不同数据的 flush 时机不同，顺序有严格要求。本文梳理 block 数据、block index、chainstate（UTXO 集）、以及三类可选索引（txindex、blockfilterindex、coinstatsindex）的 flush 机制。

## 数据分类

| 数据 | 存储方式 | 磁盘位置 | 写入时机 |
|------|---------|---------|---------|
| 区块数据 | flat file | `blocks/blk*.dat` | 收到区块时立即追加写入 |
| 撤销数据 | flat file | `blocks/rev*.dat` | `ConnectBlock` 时写入 |
| 区块索引 | LevelDB | `blocks/index/` | `FlushStateToDisk` 时批量写入 |
| UTXO 集 | LevelDB | `chainstate/` | `FlushStateToDisk` 时写入 |
| txindex | LevelDB | `indexes/txindex/` | 每个区块即时写入数据，定期 commit 元数据 |
| blockfilterindex | LevelDB + flat file | `indexes/blockfilter/` | 每个区块即时写入，定期 commit |
| coinstatsindex | LevelDB | `indexes/coinstatsindex/` | 每个区块即时写入 per-height 数据，定期 commit 运行状态 |

## 区块数据和撤销数据：收到即写

区块数据（`blk*.dat`）在节点收到并初步验证区块后就追加写入磁盘。这是最早持久化的数据。

撤销数据（`rev*.dat`）在 `ConnectBlock` 时写入，记录该区块花费的每个 UTXO 的旧状态，用于将来可能的 reorg 回滚。

这两类数据的写入独立于 `FlushStateToDisk`，但文件缓冲区的 flush（`fsync`）在 `FlushStateToDisk` 的第一步完成。

## FlushStateToDisk：核心持久化函数

`FlushStateToDisk()`（`src/validation.cpp`）是 chainstate 层面的统一持久化入口。它在以下情况被调用：

- 定期触发（默认约每小时，或 UTXO 缓存接近上限时）
- 节点正常关机时
- 手动 flush（如 pruning 需要时）

函数内部的**执行顺序是严格的**：

```
FlushStateToDisk()
│
├─ ① FlushChainstateBlockFile()
│     将 blk*.dat 和 rev*.dat 的文件缓冲区 fsync 到磁盘
│     确保后续步骤引用的物理文件位置都已持久化
│
├─ ② WriteBlockIndexDB()
│     将内存中标记为 dirty 的 CBlockIndex 条目批量写入
│     blocks/index/ LevelDB（WriteBatchSync，同步写入）
│     每个条目包含 nFile、nDataPos、nUndoPos 指向 flat file 中的位置
│
├─ ③ CoinsTip().Flush() 或 CoinsTip().Sync()
│     将内存中的 UTXO 缓存写入 chainstate/ LevelDB
│     Flush() 清空缓存，Sync() 只写入 dirty 条目
│     使用 DB_HEAD_BLOCKS 机制保证原子性（见下文）
│
├─ ④ m_last_flushed_block = m_chain.Tip()   ← PR #34897 新增
│     记录已 flush 的边界
│
└─ ⑤ signals->ChainStateFlushed(locator)
      通知所有注册了 ValidationInterface 的组件
      索引在收到此信号后调用 Commit()
```

### 为什么顺序必须如此

```
① flat file fsync
      ↓ 保证文件内容在磁盘上
② block index 写入 LevelDB
      ↓ 写入的 nDataPos/nUndoPos 指向 ① 已经 fsync 的文件位置
③ chainstate 写入 LevelDB
      ↓ chainstate 的 best_block 与 ② 中的 block index 一致
⑤ 通知索引 commit
      ↓ 索引的 DB_BEST_BLOCK 指向 ② 中已持久化的区块
```

如果顺序颠倒（例如先写 chainstate 再写 block index），crash 后可能出现 chainstate 引用不存在的区块。

## Chainstate 写入的原子性保证

`CCoinsViewDB::BatchWrite()`（`src/txdb.cpp:100`）使用两阶段提交保证原子性：

```
阶段 1：
  删除 DB_BEST_BLOCK
  写入 DB_HEAD_BLOCKS = [new_tip, old_tip]   ← 标记"正在切换"

  循环写入 dirty coins...

阶段 2：
  删除 DB_HEAD_BLOCKS
  写入 DB_BEST_BLOCK = new_tip               ← 标记"切换完成"
```

如果在阶段 1 和阶段 2 之间 crash：
- 重启时检测到 `DB_HEAD_BLOCKS` 存在 → 知道处于中间状态
- 可以继续完成或重新开始

## 索引的 Flush 机制

### 两层写入

索引的数据写入分为两层：

**即时写入**（每处理一个区块）：

```cpp
// txindex: 写入 txid → 文件位置的映射
m_db->WriteTxs(vPos);

// coinstatsindex: 写入 per-height 统计数据
m_db->Write(DBHeightKey(block.height), value);

// blockfilterindex: 写入 filter 数据到 flat file，元数据到 LevelDB
WriteFilterToDisk(m_next_filter_pos, filter);
m_db->Write(DBHeightKey(block_height), value);
```

这些写入通过 LevelDB 的 WAL 保证了基本的持久性，但它们**不更新 `DB_BEST_BLOCK`**。

**Commit**（定期，或收到 `ChainStateFlushed` 信号时）：

```cpp
// BaseIndex::Commit() 将 DB_BEST_BLOCK 和特定于子类的元数据原子写入
CDBBatch batch(GetDB());
CustomCommit(batch);                    // 子类写入元数据（如 DB_MUHASH）
GetDB().WriteBestBlock(batch, locator); // 写入 DB_BEST_BLOCK
GetDB().WriteBatch(batch);              // 原子提交
```

### Commit 触发时机

| 触发源 | 代码位置 | 说明 |
|--------|---------|------|
| `ChainStateFlushed` 信号 | `base.cpp:429` | chainstate flush 完成后触发 |
| `Sync()` 循环同步到 tip 时 | `base.cpp:225` | 同步追赶完成时 |
| `Sync()` 定期写入 | `base.cpp:258` | 每 30 秒一次（`SYNC_LOCATOR_WRITE_INTERVAL`） |
| `Sync()` 被中断时 | `base.cpp:215` | 保存进度 |

PR #34897 的修复正是在 `Commit()` 内部加了检查：如果索引 tip 超过了 `m_last_flushed_block`，跳过本次 commit。这样无论哪个时机触发 commit，都不会提前持久化。

## 完整时序图

正常运行时，一个区块从连接到所有数据落盘的完整流程：

```
ConnectBlock(H+1)
│
├─ 写入 rev*.dat（撤销数据）
├─ 更新内存中的 UTXO 缓存
├─ 更新内存中的 CBlockIndex
│
├─ ValidationInterface::BlockConnected 信号
│   ├─ 各 index 处理区块（即时写入 per-block 数据）
│   ├─ 钱包更新交易状态
│   └─ ...
│
│  ... 可能连接多个区块 ...
│
FlushStateToDisk()（定期或关机时触发）
│
├─ ① fsync blk/rev flat files
├─ ② 写入 block index LevelDB
├─ ③ 写入 chainstate LevelDB
├─ ④ m_last_flushed_block = tip
│
└─ ValidationInterface::ChainStateFlushed 信号
    ├─ 各 index 调用 Commit()
    │   ├─ 检查 index tip ≤ m_last_flushed_block  ← PR #34897
    │   ├─ CustomCommit()（如 DB_MUHASH）
    │   └─ 写入 DB_BEST_BLOCK
    └─ 钱包写入 best block locator
```
