# PR #34897: Indexes Must Not Commit Ahead of the Flushed Chainstate

[中文](index-flush-pr34897.zh.md)

---

## Background: What's the Problem

Bitcoin Core maintains several optional indexes (`txindex`, `blockfilterindex`, `coinstatsindex`) that track the main chain in the background, writing per-block data into their own LevelDB databases. Each index has a `DB_BEST_BLOCK` field that records "which block have I processed up to."

The problem: an index's `Commit()` (which persists `DB_BEST_BLOCK` to disk) and the chainstate's `FlushStateToDisk()` (which persists the UTXO set + block index) are **decoupled**. An index could commit its state to disk before the chainstate has been flushed.

### Crash Scenario

```
Timeline:
  ┌─ chainstate last flushed at height H
  │
  │  Connect block H+1 (UTXO set updated in memory, not flushed)
  │  Index processes block H+1
  │  Index Commit(): DB_BEST_BLOCK = locator for H+1 ← written to disk
  │
  │  ══ CRASH ══
  │
  └─ After recovery:
       chainstate → H (recovered from LevelDB)
       block index → H (flushed together with chainstate)
       index's DB_BEST_BLOCK → hash of H+1
```

### Where the Error Occurs

After restart, `BaseIndex::Init()` (`src/index/base.cpp:104`) runs:

```cpp
// Line 119: read DB_BEST_BLOCK from the index's LevelDB
const auto locator{GetDB().ReadBestBlock()};  // → contains hash of H+1

// Line 129: look up this hash in the block index
const CBlockIndex* locator_index{
    m_blockman.LookupBlockIndex(locator.vHave.at(0))  // look up H+1
};

// Lines 130-131: block index only flushed to H, H+1 doesn't exist → init fails
if (!locator_index) {
    return InitError("best block of <index> not found. Please rebuild the index.");
}
```

This is exactly [#33208](https://github.com/bitcoin/bitcoin/issues/33208) — "Indexes stuck on unknown best block after unclean shutdown."

For `coinstatsindex`, even if this check passes, `CustomInit` (`coinstatsindex.cpp:284-289`) would fail because `DB_MUHASH` (the MuHash running state) would be inconsistent with the per-height entry.

## The Fix

PR #34897 consists of three commits:

### Commit 1: validation: track last flushed block

Adds a new member `m_last_flushed_block` to `Chainstate`, recording the chain tip at the time of the last flush to disk.

```cpp
// src/validation.h
CBlockIndex* m_last_flushed_block GUARDED_BY(cs_main){nullptr};
const CBlockIndex* GetLastFlushedBlock() const { return m_last_flushed_block; }
```

It is set in two places:

| Location | When | Why |
|----------|------|-----|
| `FlushStateToDisk()` | After chainstate is written to disk | Records the flush boundary during normal operation |
| `LoadChainTip()` | On startup recovery | The tip loaded from disk is inherently the flushed state |

A query method is exposed through `interfaces::Chain`:

```cpp
// src/interfaces/chain.h
virtual bool isBlockInFlushedChain(const uint256& block_hash, int height) = 0;
```

Implementation: checks whether the given block is an ancestor of (or equal to) `m_last_flushed_block`.

### Commit 2: index: Don't commit ahead of the flushed chainstate

Adds a precondition check to `BaseIndex::Commit()` (`src/index/base.cpp:270`):

```cpp
bool BaseIndex::Commit()
{
    bool ok = m_best_block_index != nullptr;
    if (ok) {
        const CBlockIndex* index_tip = m_best_block_index.load();
        // ← NEW: skip commit if index tip is not in the flushed chain
        if (index_tip && !m_chain->isBlockInFlushedChain(
                index_tip->GetBlockHash(), index_tip->nHeight)) {
            LogInfo("Skipping commit, index is ahead of flushed chainstate");
            return false;
        }
        // ... existing commit logic
    }
}
```

This guarantees that `DB_BEST_BLOCK` never points to a block that hasn't been persisted to the `blocks/index` LevelDB.

### Commit 3: test: add test to ensure indexes dont commit too early

Adds a unit test `coinstatsindex_no_commit_ahead_of_flush`:

1. Creates a `CoinStatsIndex`, syncs to height 100
2. Manually connects block 101 but **does not flush chainstate** (`m_last_flushed_block` remains null)
3. Triggers the `ChainStateFlushed` signal
4. Verifies the index **did not commit** (after reload, `best_block_height == 0`)

## MuHash Note

`coinstatsindex` uses MuHash (Multiplicative Hash) to maintain an incremental digest of the entire UTXO set. Key properties of MuHash:

- Based on a multiplicative group modulo a large prime; maintains a `numerator / denominator` fraction internally
- **Adding** an element: `numerator *= h(element)`
- **Removing** an element: `denominator *= h(element)`
- Final hash: `Finalize() = numerator * inverse(denominator) mod p → uint256`

MuHash **is reversible** (elements can be both added and removed). The `RevertBlock` function reverses the muhash state during reorgs. However, this requires a consistent persisted starting point to revert to — which is exactly what this PR guarantees.

## Related Issues

- [#33208](https://github.com/bitcoin/bitcoin/issues/33208) — Indexes stuck on unknown best block after unclean shutdown
- [#34261](https://github.com/bitcoin/bitcoin/issues/34261) — Block filter index corruption post reorg and unclean shutdown
