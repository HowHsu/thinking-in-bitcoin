# Bitcoin Core Disk Storage Layout: Data Classification and Relationships

[中文](disk-storage-layout.zh.md)

---

> This article grew out of reviewing and analyzing [PR #34897](https://github.com/bitcoin/bitcoin/pull/34897) (indexes: Don't commit ahead of the flushed chainstate). Understanding the root cause of the index corruption bug requires knowing how different data types are stored on disk and how they relate to each other, hence this write-up. See [PR #34897 Analysis](index-flush-pr34897.en.md) for the PR itself.

## Overview

Bitcoin Core stores all persistent data under `~/.bitcoin/` (the data directory). Storage uses two media: **LevelDB** (key-value database) and **flat files** (sequentially appended files).

```
~/.bitcoin/
├── blocks/
│   ├── blk00000.dat    ┐
│   ├── blk00001.dat    ├─ flat file: raw block data
│   ├── ...             ┘
│   ├── rev00000.dat    ┐
│   ├── rev00001.dat    ├─ flat file: block undo data
│   ├── ...             ┘
│   └── index/          ←── LevelDB: block index
│
├── chainstate/         ←── LevelDB: UTXO set
│
└── indexes/
    ├── txindex/            ←── LevelDB: transaction index
    ├── blockfilter/basic/
    │   ├── db/             ←── LevelDB: filter metadata
    │   └── fltr00000.dat   ←── flat file: BIP 158 filter data
    └── coinstatsindex/     ←── LevelDB: UTXO statistics index
```

## Detailed Database Descriptions

### 1. Block Flat Files (`blocks/blk*.dat`, `blocks/rev*.dat`)

Block data is appended sequentially to `blk*.dat` files, each capped at approximately 128 MB (`MAX_BLOCKFILE_SIZE`).

```
blk00003.dat
┌───────────────────────────────────────────────────┐
│ offset=0      │ Block A serialized data            │
│               │ (magic + size + header + txs)       │
├───────────────┼────────────────────────────────────┤
│ offset=18291  │ Block B serialized data             │
├───────────────┼────────────────────────────────────┤
│ offset=95420  │ Block C serialized data             │
├───────────────┼────────────────────────────────────┤
│ ...           │ ...                                │
└───────────────────────────────────────────────────┘
```

The corresponding `rev*.dat` files store undo data for each block: the complete previous state of every UTXO spent by non-coinbase transactions, used for reorg rollback.

```
rev00003.dat
┌───────────────────────────────────────────────────┐
│ offset=0      │ Block A CBlockUndo                 │
│               │ { vtxundo: [                       │
│               │     tx1 spent coins,               │
│               │     tx2 spent coins, ...           │
│               │ ] }                                │
├───────────────┼────────────────────────────────────┤
│ ...           │ ...                                │
└───────────────────────────────────────────────────┘
```

### 2. Block Index (`blocks/index/` LevelDB)

Stores metadata for every known block (`CDiskBlockIndex`). **Loaded entirely into memory** at startup to build the block tree.

```
key: 'b' + block_hash
val: CDiskBlockIndex {
    nHeight          Block height
    nStatus          Status flags (BLOCK_HAVE_DATA, BLOCK_HAVE_UNDO, BLOCK_VALID_*, ...)
    nTx              Number of transactions in the block
    nFile            ───→ Points to which blk?????.dat and rev?????.dat
    nDataPos         ───→ Byte offset of block data in the blk file
    nUndoPos         ───→ Byte offset of undo data in the rev file
    nVersion         Block version
    hashPrev         Previous block hash
    hashMerkleRoot   Merkle root
    nTime            Timestamp
    nBits            Difficulty target
    nNonce           Nonce
}
```

Also stores block file metadata (statistics for each blk/rev file pair).

### 3. Chainstate / UTXO Set (`chainstate/` LevelDB)

Stores all current **unspent transaction outputs (UTXOs)**. This is the core data for validating new transactions and blocks.

```
key: 'C' + txid + varint(vout_index)
val: Coin {
    nValue           Amount (satoshis)
    scriptPubKey     Locking script
    nHeight          Height of the block this UTXO came from
    fCoinBase        Whether from a coinbase transaction
}

key: 'B'
val: best_block_hash    Chain tip at last flush

key: 'H'
val: [new_tip, old_tip]  Only present during flush, for atomicity
```

Mainnet currently has approximately 180 million UTXO records. At runtime, the node uses `dbcache` (default 450 MB, configurable) to cache hot data in memory, flushing to LevelDB periodically.

### 4. Transaction Index (`indexes/txindex/` LevelDB)

Optional index. When enabled, allows fast lookup of any transaction's physical disk location by txid.

```
key: 't' + txid
val: CDiskTxPos {
    nFile        ───→ Which blk?????.dat
    nPos         ───→ Byte offset of the block in the file
    nTxOffset    ───→ Offset of the transaction within the block (past header and tx count)
}

key: 'B'
val: CBlockLocator    Which block the index has processed up to
```

### 5. Block Filter Index (`indexes/blockfilter/basic/`)

Optional index. Stores BIP 158 compact block filters, supporting efficient block scanning for light wallets.

```
LevelDB (db/):
    key: height → (block_hash, DBVal {
                       filter_hash      Hash of the filter
                       header           Filter chain header (chained hash)
                       pos: FlatFilePos ───→ Points to position in fltr?????.dat
                   })
    key: 'B' → CBlockLocator
    key: 'P' → FlatFilePos    Next filter write position

flat file (fltr*.dat):
    ┌──────────────────────────────┐
    │ block_hash + encoded_filter  │  GCS filter for each block
    ├──────────────────────────────┤
    │ block_hash + encoded_filter  │
    ├──────────────────────────────┤
    │ ...                          │
    └──────────────────────────────┘
```

### 6. UTXO Statistics Index (`indexes/coinstatsindex/` LevelDB)

Optional index. Maintains an incremental statistical digest of the UTXO set, supporting fast `gettxoutsetinfo` RPC queries.

```
key: height → (block_hash, DBVal {
                   muhash                    MuHash final digest (uint256)
                   transaction_output_count  Current UTXO count
                   bogo_size                 Estimated UTXO set size
                   total_amount              Total value of all UTXOs
                   total_subsidy             Cumulative block rewards
                   total_prevout_spent_amount Cumulative spent amount
                   total_coinbase_amount      Cumulative coinbase amount
                   ...various unspendable statistics
               })

key: 'M' → MuHash3072 {
               m_numerator      3072-bit large number
               m_denominator    3072-bit large number
           }
           Running MuHash fraction state, committed atomically with DB_BEST_BLOCK

key: 'B' → CBlockLocator
```

## Data Relationship Overview

```
                    ┌─────────────────────────────────────────┐
                    │            blocks/index/ LevelDB        │
                    │                                         │
                    │  block_hash → CDiskBlockIndex {         │
                    │    nHeight, nStatus, nTx,               │
                    │    nFile, nDataPos, nUndoPos,           │
                    │    + block header fields                │
                    │  }                                      │
                    └──────┬──────────────┬───────────────────┘
                           │              │
                   nFile + nDataPos    nFile + nUndoPos
                           │              │
                           ▼              ▼
               ┌──────────────────┐  ┌──────────────────┐
               │  blk?????.dat    │  │  rev?????.dat    │
               │                  │  │                  │
               │  Block A data    │  │  Block A undo    │
               │  ┌────────────┐  │  │  ┌────────────┐  │
               │  │ header     │  │  │  │ spent coin │  │
               │  │ tx_count   │  │  │  │ spent coin │  │
               │  │ tx[0] ─────┼──┼──┼──┤            │  │
               │  │ tx[1] ─────┼──┼──┼──┤            │  │
               │  │ ...        │  │  │  │ ...        │  │
               │  └────────────┘  │  │  └────────────┘  │
               │  Block B data    │  │  Block B undo    │
               │  ...             │  │  ...             │
               └────────▲─────────┘  └──────────────────┘
                        │
                        │ nFile + nPos + nTxOffset
                        │
              ┌─────────┴───────────────────────┐
              │      indexes/txindex/ LevelDB    │
              │                                  │
              │  txid → CDiskTxPos {             │
              │    nFile      →  blk?????.dat    │
              │    nPos       →  block offset    │
              │    nTxOffset  →  tx offset in    │
              │                  the block       │
              │  }                               │
              └──────────────────────────────────┘


              ┌──────────────────────────────────┐
              │    chainstate/ LevelDB           │
              │                                  │
              │  'C' + txid + n → Coin {         │
              │    amount, scriptPubKey,          │     Self-contained,
              │    height, is_coinbase            │     no references to
              │  }                               │     flat files or block index
              │                                  │
              │  'B' → best_block_hash           │
              └──────────────────────────────────┘


              ┌──────────────────────────────────┐
              │  indexes/coinstatsindex/ LevelDB │
              │                                  │
              │  height → (hash, statistics)     │     Self-contained,
              │  'M'    → MuHash3072 state       │     no references to
              │  'B'    → CBlockLocator          │     other databases
              └──────────────────────────────────┘


              ┌──────────────────────────┐     ┌────────────────────┐
              │  blockfilter/basic/db/   │     │  fltr?????.dat     │
              │  LevelDB                 │     │                    │
              │                          │     │  filter A data     │
              │  height → (hash, DBVal { │     │  filter B data     │
              │    filter_hash,          │     │  ...               │
              │    header,               │     │                    │
              │    pos ──────────────────────→ │  offset position   │
              │  })                      │     │                    │
              │  'B' → CBlockLocator     │     │                    │
              │  'P' → next FlatFilePos  │     │                    │
              └──────────────────────────┘     └────────────────────┘
```

## Query Path Examples

### getrawtransaction(txid): Look Up a Transaction via txindex

```
txid
 │
 ▼
txindex LevelDB ──→ CDiskTxPos { nFile=3, nPos=18291, nTxOffset=81 }
 │
 ▼
Open blocks/blk00003.dat
seek to 18291 + 81
 │
 ▼
Deserialize CTransaction → return full transaction
```

### Validating a Transaction Input: UTXO Lookup via chainstate

```
Transaction input: vin[0] = { txid=abc..., vout=2 }
 │
 ▼
chainstate LevelDB ──→ key = 'C' + abc... + 2
                   ──→ Coin { 0.5 BTC, OP_DUP OP_HASH160..., height=500000 }
 │
 ▼
Verify signature, check amount → if valid, remove UTXO from cache
```

### gettxoutsetinfo: UTXO Set Digest via coinstatsindex

```
gettxoutsetinfo(hash_type="muhash")
 │
 ▼
coinstatsindex LevelDB ──→ key = height
                       ──→ DBVal { muhash=a1b2..., total_amount=21M..., count=180M... }
 │
 ▼
Return precomputed statistics directly (no need to iterate chainstate)
```

### getblockfilter(block_hash): Filter Lookup via blockfilterindex

```
block_hash
 │
 ▼
block index ──→ CBlockIndex { nHeight=800000 }
 │
 ▼
blockfilter LevelDB ──→ key = 800000
                    ──→ DBVal { pos = { nFile=5, nPos=42000 } }
 │
 ▼
Open indexes/blockfilter/basic/fltr00005.dat
seek to 42000
 │
 ▼
Deserialize BlockFilter → return GCS filter
```

## Data Dependency Hierarchy

```
blk*.dat / rev*.dat         ← Bottom layer, raw source for all other data
       │
       │ Position info (nFile, nDataPos, nUndoPos)
       ▼
blocks/index/ LevelDB       ← Middle layer, the "phone book" for locating blocks
       │
       │ Loaded entirely into memory at startup, builds CBlockIndex tree
       │ All upper-layer components access blocks through in-memory CBlockIndex
       ▼
┌──────┴──────────┬───────────────────┐
│                 │                   │
▼                 ▼                   ▼
chainstate/     txindex/          blockfilterindex/
(UTXO set)     (tx locator)       (block filters)
                  │
                  │ Values contain FlatFilePos
                  │ pointing back to blk*.dat
                  ▼
              coinstatsindex/
              (UTXO statistics)
              Fully self-contained
              No references to any other storage
```

**Key Points**:

- `blocks/index/` is the bridge connecting flat files to upper-layer applications
- `chainstate/` and `coinstatsindex/` are self-contained — they store no references to flat files or the block index
- `txindex` stores flat file positions directly, bypassing the block index for queries
- `blockfilterindex` has its own flat files (`fltr*.dat`); its LevelDB stores positions pointing to these files
