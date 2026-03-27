# Bitcoin Core 存储架构全景图

[English](storage-architecture-ascii.en.md)

---

> 本文以 ASCII 图的形式展示 Bitcoin Core 的完整存储架构，涵盖内存结构、LevelDB 数据库、flat file、可选索引，以及 flush 顺序。图中的 key/value schema 均来自源码。
> 相关文章：[磁盘存储布局](disk-storage-layout.zh.md)、[Flush 时机与顺序](index-flush-ordering.zh.md)、[PR #34897 解析](index-flush-pr34897.zh.md)

## 完整存储架构图

```
====================================================== IN-MEMORY =======================================================

  Entry points:
    ChainstateManager::AcceptBlock() --> m_blockman.WriteBlock(block, height)   // write to flat file on receipt
    Chainstate::FlushStateToDisk()   --> m_blockman.WriteBlockIndexDB()         // batch write to LevelDB periodically

  +-- BlockManager ------------------------------------------+   +-- CCoinsViewCache ----------------------------------+
  |  BlockMap m_block_index;            // all CBlockIndex*  |   |  CCoinsMap cacheCoins;  // dirty UTXO (def 450 MB)  |
  |  std::set<CBlockIndex*> m_dirty_blockindex;              |   |  uint256 hashBlock;     // current tip hash         |
  |  std::set<int> m_dirty_fileinfo;                         |   |  Flush() -> empty cache, write all to LevelDB       |
  |  std::vector<CBlockFileInfo> m_blockfile_info;           |   |  Sync()  -> write only dirty entries to LevelDB     |
  +----------------------------------------------------------+   +-----------------------------------------------------+

  +-- BaseIndex (base class for each optional index) ------------------------------------------------+
  |  std::atomic<const CBlockIndex*> m_best_block_index;   // block the index has processed up to    |
  |  bool m_synced;                                         // whether caught up with chain tip      |
  |  Commit() {                                                                                      |
  |    if (tip > m_last_flushed_block) SKIP;   // PR #34897 guard                                    |
  |    CustomCommit(batch);                    // subclass metadata (DB_MUHASH, DB_FILTER_POS, ...)  |
  |    WriteBestBlock(batch, locator);         // DB_BEST_BLOCK                                      |
  |    WriteBatch(batch);                      // atomic                                             |
  |  }                                                                                               |
  +--------------------------------------------------------------------------------------------------+

             |                    |                |                               |
             |  WriteBlockIndexDB |                |  CoinsTip().Flush()           |  BaseIndex::Commit()
             v                    v                v                               v

======================================================= LevelDB ========================================================

  +-- blocks/index/ ------------------------------------+   +-- chainstate/ ----------------------------------------+
  |  key = ('b', block_hash)         <- DB_BLOCK_INDEX  |   |  key = ('C', txid, varint(n))      <- DB_COIN         |
  |  val = CDiskBlockIndex {                            |   |  val = Coin {                                         |
  |    nHeight, nStatus, nTx                            |   |    code: nHeight * 2 + fCoinBase                      |
  |    nFile -----------------------------------+       |   |    Using<TxOutCompression>(out)                       |
  |    nDataPos (if BLOCK_HAVE_DATA) -----------+       |   |    -> nValue (satoshis), scriptPubKey                 |
  |    nUndoPos (if BLOCK_HAVE_UNDO) -----------+       |   |  }                                                    |
  |    nVersion, hashPrev, hashMerkleRoot       |       |   |                                                       |
  |    nTime, nBits, nNonce                     |       |   |  key = 'B'                         <- DB_BEST_BLOCK   |
  |  }                                          |       |   |  val = uint256 hashBlock (tip at last flush)          |
  |                                             |       |   |                                                       |
  |  key = ('f', nFile)    <- DB_BLOCK_FILES    |       |   |  key = 'H'                         <- DB_HEAD_BLOCKS  |
  |  val = CBlockFileInfo {                     |       |   |  val = [hashNewTip, hashOldTip]                       |
  |    nBlocks, nSize, nUndoSize                |       |   |    (only during flush, for 2-phase atomicity)         |
  |    nHeightFirst/Last, nTimeFirst/Last       |       |   +-------------------------------------------------------+
  |  }                                          |       |
  |                                             |       |
  |  key = ('F', name)     <- DB_FLAG           |       |
  |  val = '0'/'1' (e.g. 'prunedblockfiles')    |       |
  |                                             |       |
  |  key = 'R'            <- DB_REINDEX_FLAG    |       |
  |  val = '1' (key exists = reindexing)        |       |
  |                                             |       |
  |  key = 'l'            <- DB_LAST_BLOCK      |       |
  |  val = int nFile (last block file number)   |       |
  +-----------------------------------------------------+
                                                |
                                                |
                               nFile + nDataPos | nFile + nUndoPos
                                                |

====================================================== FLAT FILES ======================================================

  blocks/blk?????.dat (?????=%05u nFile) <--------+--------> blocks/rev?????.dat
  ~128 MB per file (MAX_BLOCKFILE_SIZE)           |          1:1 correspondence with blk files
  XOR-encrypted with key from blocks/xor.dat      |          same XOR key

  +-- blk00003.dat -------------------------------------+   +-- rev00003.dat ---------------------------------------+
  |  +- Block A ----------------------------------+     |   |  +- BlockUndo A ----------------------------------+   |
  |  |  pchMessageStart {0xf9, 0xbe, 0xb4, 0xd9}  |     |   |  |  pchMessageStart {0xf9, 0xbe, 0xb4, 0xd9}      |   |
  |  |  block_size (uint32_t)                     |     |   |  |  blockundo_size (uint32_t)                     |   |
  |  |  TX_WITH_WITNESS(block) {                  |     |   |  |  blockundo {                                   |   |
  |  |    block_header (80 bytes)                 |     |   |  |    vtxundo[]: spent Coins for non-coinbase tx  |   |
  |  |    tx_count (varint)                       |     |   |  |  }                                             |   |
  |  |    tx[0], tx[1], ..., tx[n]                |     |   |  |  hash(block.pprev->GetBlockHash(), blockundo)  |   |
  |  |  }                                         |     |   |  |    ^ integrity checksum                        |   |
  |  +--------------------------------------------+     |   |  +------------------------------------------------+   |
  |                                                     |   |                                                       |
  |  +- Block B ----------------------------------+     |   |  +- BlockUndo B ----------------------------------+   |
  |  |  ...                                       |     |   |  |  ...                                           |   |
  |  +--------------------------------------------+     |   |  +------------------------------------------------+   |
  +-----------------------------------------------------+   +-------------------------------------------------------+

=================================================== OPTIONAL INDEXES ===================================================

  +-- indexes/txindex/ LevelDB --------------+   +-- indexes/blockfilter/basic/ ------------------------+
  |  key = ('t', txid)       <- DB_TXINDEX   |   |  LevelDB (db/):                                      |
  |  val = CDiskTxPos {                      |   |    key = DBHeightKey(height)                         |
  |    nFile    ---> blk?????.dat            |   |    val = (block_hash, {                              |
  |    nPos     ---> block offset in file    |   |      filter_hash,                                    |
  |    nTxOffset --> tx offset within block  |   |      header (chained hash),                          |
  |  }                                       |   |      pos: FlatFilePos ---> fltr?????.dat             |
  |                                          |   |    })                                                |
  |  key = 'B'  <- DB_BEST_BLOCK             |   |    key = 'P'  <- DB_FILTER_POS  -> next FlatFilePos  |
  |  val = CBlockLocator                     |   |    key = 'B'  <- DB_BEST_BLOCK  -> CBlockLocator     |
  |                                          |   |                                                      |
  |  Stores flat file positions directly;    |   |  Flat file (fltr?????.dat):                          |
  |  bypasses block index for queries        |   |    +--------------------------------------------+    |
  |                                          |   |    | block_hash + encoded GCS filter (BIP 158)  |    |
  |                                          |   |    | block_hash + encoded GCS filter            |    |
  |                                          |   |    | ...                                        |    |
  |                                          |   |    +--------------------------------------------+    |
  +------------------------------------------+   +------------------------------------------------------+

  +-- indexes/coinstatsindex/ LevelDB ----------------------------------------------------------+
  |  key = DBHeightKey(height)                    key = 'M'  <- DB_MUHASH                       |
  |  val = (block_hash, {                         val = MuHash3072 {                            |
  |    muhash (uint256 finalized),                  m_numerator   (3072-bit)                    |
  |    transaction_output_count,                    m_denominator (3072-bit)                    |
  |    bogo_size, total_amount,                   }                                             |
  |    total_subsidy,                             Insert:  numerator   *= h(element)            |
  |    total_prevout_spent_amount,                Remove:  denominator *= h(element)            |
  |    total_coinbase_amount,                     Finalize: num * inv(den) mod p -> uint256     |
  |    total_unspendable_amount, ...                                                            |
  |  })                                           key = 'B'  <- DB_BEST_BLOCK -> CBlockLocator  |
  |                                                                                             |
  |  NOTE: DB_MUHASH and DB_BEST_BLOCK must be committed atomically in the same batch           |
  |  Fully self-contained -- no references to flat files or other databases                     |
  +---------------------------------------------------------------------------------------------+
```

## 数据依赖与指针方向

```
           blk*.dat / rev*.dat                          <-- Layer 0: raw source of truth
       ^          ^              ^
       |nFile+    |nFile+        |nFile+nPos+nTxOffset
       |nDataPos  |nUndoPos      |
       |          |              |
blocks/index/ LevelDB -----------+                      <-- Layer 1: block "phone book" (loaded into memory at startup)
       ^                         |
       |                         |
  +----+----------+--------------+
  |               |              |
  v               v              v
chainstate/    txindex/     blockfilterindex/
(UTXO set)   (tx locator)    +- db/ LevelDB
self-            |               |  pos --> fltr*.dat (own flat files)
contained        |               |
                 |          coinstatsindex/
                 |          self-contained (incremental stats)
                 |
                 +--> points directly to blk*.dat, bypasses block index for queries
```

## Flush 顺序 (FlushStateToDisk)

```
(1) FlushChainstateBlockFile()                      fsync blk*.dat / rev*.dat
|                                                    file contents guaranteed on disk
v
(2) WriteBlockIndexDB()                             batch write to blocks/index/ LevelDB
|                                                    nDataPos/nUndoPos point to positions fsynced in (1)
v
(3) CoinsTip().Flush() / Sync()                     write to chainstate/ LevelDB
|                                                    best_block consistent with block index in (2)
v
(4) m_last_flushed_block = m_chain.Tip()            record flush boundary (added by PR #34897)
|
v
(5) signal ChainStateFlushed                        notify all ValidationInterface subscribers
|
v
BaseIndex::Commit() {
  if (tip > m_last_flushed_block) SKIP;              <-- PR #34897 guard: index must not commit ahead
  CustomCommit(batch);                                // subclass metadata (DB_MUHASH, DB_FILTER_POS)
  WriteBestBlock(batch, locator);                     // DB_BEST_BLOCK
  WriteBatch(batch);                                  // atomic commit
}
```
