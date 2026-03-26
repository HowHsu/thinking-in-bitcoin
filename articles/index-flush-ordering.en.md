# Bitcoin Core Data Persistence: Flush Timing and Ordering

[中文](index-flush-ordering.zh.md)

---

> This article grew out of reviewing and analyzing [PR #34897](https://github.com/bitcoin/bitcoin/pull/34897) (indexes: Don't commit ahead of the flushed chainstate). Understanding the index corruption bug it fixes requires understanding the flush timing and ordering of different data types, hence this write-up. See [PR #34897 Analysis](index-flush-pr34897.en.md) for the PR itself.

## Overview

Bitcoin Core maintains many data structures at runtime. They are updated in memory and periodically written (flushed) to disk. Different data types are flushed at different times, and the ordering has strict requirements. This article covers the flush mechanics for block data, the block index, chainstate (UTXO set), and the three optional indexes (txindex, blockfilterindex, coinstatsindex).

## Data Classification

| Data | Storage | Disk Location | Write Timing |
|------|---------|---------------|--------------|
| Block data | flat file | `blocks/blk*.dat` | Appended immediately when a block is received |
| Undo data | flat file | `blocks/rev*.dat` | Written during `ConnectBlock` |
| Block index | LevelDB | `blocks/index/` | Batch-written during `FlushStateToDisk` |
| UTXO set | LevelDB | `chainstate/` | Written during `FlushStateToDisk` |
| txindex | LevelDB | `indexes/txindex/` | Per-block data written immediately; metadata committed periodically |
| blockfilterindex | LevelDB + flat file | `indexes/blockfilter/` | Per-block data written immediately; committed periodically |
| coinstatsindex | LevelDB | `indexes/coinstatsindex/` | Per-height data written immediately; running state committed periodically |

## Block and Undo Data: Written on Receipt

Block data (`blk*.dat`) is appended to disk as soon as a block is received and passes initial validation. This is the earliest data to be persisted.

Undo data (`rev*.dat`) is written during `ConnectBlock`, recording the old state of every UTXO spent by the block's transactions, for potential reorg rollback.

Both data types are written independently of `FlushStateToDisk`, but their file buffers are fsynced in the first step of `FlushStateToDisk`.

## FlushStateToDisk: The Core Persistence Function

`FlushStateToDisk()` (`src/validation.cpp`) is the unified persistence entry point for the chainstate layer. It is called:

- Periodically (roughly once per hour, or when the UTXO cache approaches its limit)
- During normal node shutdown
- When pruning requires it

The internal **execution order is strict**:

```
FlushStateToDisk()
│
├─ ① FlushChainstateBlockFile()
│     fsync the blk*.dat and rev*.dat file buffers to disk
│     Ensures the physical file positions referenced later are durable
│
├─ ② WriteBlockIndexDB()
│     Batch-writes dirty CBlockIndex entries to
│     blocks/index/ LevelDB (WriteBatchSync, synchronous)
│     Each entry contains nFile, nDataPos, nUndoPos pointing into flat files
│
├─ ③ CoinsTip().Flush() or CoinsTip().Sync()
│     Writes the in-memory UTXO cache to chainstate/ LevelDB
│     Flush() empties the cache; Sync() writes only dirty entries
│     Uses the DB_HEAD_BLOCKS mechanism for atomicity (see below)
│
├─ ④ m_last_flushed_block = m_chain.Tip()   ← Added by PR #34897
│     Records the flush boundary
│
└─ ⑤ signals->ChainStateFlushed(locator)
      Notifies all ValidationInterface subscribers
      Indexes call Commit() upon receiving this signal
```

### Why This Order Is Required

```
① flat file fsync
      ↓ File contents guaranteed on disk
② block index written to LevelDB
      ↓ nDataPos/nUndoPos point to positions fsynced in ①
③ chainstate written to LevelDB
      ↓ chainstate's best_block is consistent with block index in ②
⑤ indexes notified to commit
      ↓ index DB_BEST_BLOCK points to blocks persisted in ②
```

If the order were reversed (e.g., writing chainstate before the block index), a crash could leave the chainstate referencing a non-existent block.

## Chainstate Write Atomicity

`CCoinsViewDB::BatchWrite()` (`src/txdb.cpp:100`) uses a two-phase commit for atomicity:

```
Phase 1:
  Delete DB_BEST_BLOCK
  Write DB_HEAD_BLOCKS = [new_tip, old_tip]   ← marks "transition in progress"

  Write dirty coins in a loop...

Phase 2:
  Delete DB_HEAD_BLOCKS
  Write DB_BEST_BLOCK = new_tip               ← marks "transition complete"
```

If a crash occurs between Phase 1 and Phase 2:
- On restart, the presence of `DB_HEAD_BLOCKS` signals an incomplete transition
- Recovery can resume or restart the operation

## Index Flush Mechanics

### Two-Layer Writes

Index data writes happen at two layers:

**Immediate writes** (per block processed):

```cpp
// txindex: write txid → file position mapping
m_db->WriteTxs(vPos);

// coinstatsindex: write per-height statistics
m_db->Write(DBHeightKey(block.height), value);

// blockfilterindex: write filter data to flat file, metadata to LevelDB
WriteFilterToDisk(m_next_filter_pos, filter);
m_db->Write(DBHeightKey(block_height), value);
```

These writes are made durable through LevelDB's WAL, but they **do not update `DB_BEST_BLOCK`**.

**Commit** (periodic, or upon receiving the `ChainStateFlushed` signal):

```cpp
// BaseIndex::Commit() atomically writes DB_BEST_BLOCK and subclass metadata
CDBBatch batch(GetDB());
CustomCommit(batch);                    // Subclass metadata (e.g., DB_MUHASH)
GetDB().WriteBestBlock(batch, locator); // Write DB_BEST_BLOCK
GetDB().WriteBatch(batch);              // Atomic commit
```

### Commit Triggers

| Trigger | Code Location | Description |
|---------|--------------|-------------|
| `ChainStateFlushed` signal | `base.cpp:429` | After chainstate flush completes |
| `Sync()` loop reaches tip | `base.cpp:225` | When catch-up sync finishes |
| `Sync()` periodic write | `base.cpp:258` | Every 30 seconds (`SYNC_LOCATOR_WRITE_INTERVAL`) |
| `Sync()` interrupted | `base.cpp:215` | Saves progress |

The PR #34897 fix adds a check inside `Commit()`: if the index tip exceeds `m_last_flushed_block`, the commit is skipped. This way, regardless of which trigger fires, the index never persists prematurely.

## Complete Timing Diagram

During normal operation, the full flow from connecting a block to all data being on disk:

```
ConnectBlock(H+1)
│
├─ Write rev*.dat (undo data)
├─ Update in-memory UTXO cache
├─ Update in-memory CBlockIndex
│
├─ ValidationInterface::BlockConnected signal
│   ├─ Each index processes the block (immediate per-block writes)
│   ├─ Wallet updates transaction status
│   └─ ...
│
│  ... possibly connect multiple blocks ...
│
FlushStateToDisk() (triggered periodically or on shutdown)
│
├─ ① fsync blk/rev flat files
├─ ② Write block index LevelDB
├─ ③ Write chainstate LevelDB
├─ ④ m_last_flushed_block = tip
│
└─ ValidationInterface::ChainStateFlushed signal
    ├─ Each index calls Commit()
    │   ├─ Check index tip ≤ m_last_flushed_block  ← PR #34897
    │   ├─ CustomCommit() (e.g., DB_MUHASH)
    │   └─ Write DB_BEST_BLOCK
    └─ Wallet writes best block locator
```
