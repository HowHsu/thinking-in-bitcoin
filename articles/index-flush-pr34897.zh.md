# PR #34897 解析：索引不应提交到超过 chainstate flush 位置的区块

[English](index-flush-pr34897.en.md)

---

## 背景：问题出在哪里

Bitcoin Core 维护着若干索引（`txindex`、`blockfilterindex`、`coinstatsindex`），它们在后台跟踪主链，将每个新区块的相关数据写入各自的 LevelDB 数据库。这些索引有一个 `DB_BEST_BLOCK` 字段，记录"我已经处理到了哪个区块"。

问题在于：索引的 `Commit()`（将 `DB_BEST_BLOCK` 持久化到磁盘）和 chainstate（UTXO 集合 + 区块索引）的 `FlushStateToDisk()` 是**解耦的**。索引有可能在 chainstate 尚未 flush 的情况下，就先把自己的状态 commit 到了磁盘。

### 崩溃场景

```
时间线：
  ┌─ chainstate 上次 flush 在高度 H
  │
  │  连接区块 H+1（内存中更新 UTXO，但未 flush）
  │  索引处理区块 H+1
  │  索引 Commit()：DB_BEST_BLOCK = H+1 的 locator ← 写入磁盘
  │
  │  ══ CRASH ══
  │
  └─ 恢复后：
       chainstate → H（从 LevelDB 恢复）
       block index → H（与 chainstate 一起 flush 的）
       索引的 DB_BEST_BLOCK → H+1 的 hash
```

### 具体报错位置

重启后 `BaseIndex::Init()`（`src/index/base.cpp:104`）执行：

```cpp
// 第 119 行：从索引的 LevelDB 读取 DB_BEST_BLOCK
const auto locator{GetDB().ReadBestBlock()};  // → 包含 H+1 的 hash

// 第 129 行：在区块索引中查找这个 hash
const CBlockIndex* locator_index{
    m_blockman.LookupBlockIndex(locator.vHave.at(0))  // 查找 H+1
};

// 第 130-131 行：区块索引只 flush 到了 H，H+1 不存在 → 初始化失败
if (!locator_index) {
    return InitError("best block of <index> not found. Please rebuild the index.");
}
```

这正是 [#33208](https://github.com/bitcoin/bitcoin/issues/33208) 报告的 "Indexes stuck on unknown best block after unclean shutdown"。

对于 `coinstatsindex`，即使通过了上述检查，`CustomInit`（`coinstatsindex.cpp:284-289`）也会因为 `DB_MUHASH`（MuHash 运行状态）与 per-height 条目不一致而失败。

## 解决方案

PR #34897 由三个 commit 组成：

### Commit 1：validation: track last flushed block

在 `Chainstate` 中新增成员 `m_last_flushed_block`，记录最后一次 flush 到磁盘时的链 tip。

```cpp
// src/validation.h
CBlockIndex* m_last_flushed_block GUARDED_BY(cs_main){nullptr};
const CBlockIndex* GetLastFlushedBlock() const { return m_last_flushed_block; }
```

在两个位置设置它：

| 位置 | 时机 | 原因 |
|------|------|------|
| `FlushStateToDisk()` | chainstate 写入磁盘后 | 正常运行时记录 flush 边界 |
| `LoadChainTip()` | 节点启动恢复时 | 从磁盘加载的 tip 即为已 flush 状态 |

通过 `interfaces::Chain` 暴露一个查询方法：

```cpp
// src/interfaces/chain.h
virtual bool isBlockInFlushedChain(const uint256& block_hash, int height) = 0;
```

实现逻辑：检查给定区块是否是 `m_last_flushed_block` 的祖先（或就是它自身）。

### Commit 2：index: Don't commit ahead of the flushed chainstate

在 `BaseIndex::Commit()`（`src/index/base.cpp:270`）中加入一个前置检查：

```cpp
bool BaseIndex::Commit()
{
    bool ok = m_best_block_index != nullptr;
    if (ok) {
        const CBlockIndex* index_tip = m_best_block_index.load();
        // ← 新增：如果索引 tip 不在已 flush 的链上，跳过 commit
        if (index_tip && !m_chain->isBlockInFlushedChain(
                index_tip->GetBlockHash(), index_tip->nHeight)) {
            LogInfo("Skipping commit, index is ahead of flushed chainstate");
            return false;
        }
        // ... 原有的 commit 逻辑
    }
}
```

这保证了 `DB_BEST_BLOCK` 永远不会指向一个尚未持久化到 `blocks/index` LevelDB 的区块。

### Commit 3：test: add test to ensure indexes dont commit too early

新增单元测试 `coinstatsindex_no_commit_ahead_of_flush`：

1. 创建 `CoinStatsIndex`，同步到高度 100
2. 手动连接区块 101，但**不 flush chainstate**（`m_last_flushed_block` 仍为 null）
3. 触发 `ChainStateFlushed` 信号
4. 验证索引**没有 commit**（重新加载后 `best_block_height == 0`）

## MuHash 补充

`coinstatsindex` 使用 MuHash（乘法哈希）维护整个 UTXO 集合的增量摘要。MuHash 的核心特性：

- 基于大素数模下的乘法群，内部维护 `numerator / denominator` 分数
- **添加**元素：`numerator *= h(element)`
- **删除**元素：`denominator *= h(element)`
- 最终哈希：`Finalize() = numerator * inverse(denominator) mod p → uint256`

MuHash **是可逆的**（可以添加和删除元素）。`RevertBlock` 函数在 reorg 时反转 muhash 状态。但前提是有一个一致的持久化起始点可以回滚到——这正是 PR 所保证的。

## 相关 Issue

- [#33208](https://github.com/bitcoin/bitcoin/issues/33208) — Indexes stuck on unknown best block after unclean shutdown
- [#34261](https://github.com/bitcoin/bitcoin/issues/34261) — Block filter index corruption post reorg and unclean shutdown
