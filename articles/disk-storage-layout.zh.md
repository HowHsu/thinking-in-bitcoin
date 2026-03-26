# Bitcoin Core 磁盘存储布局：数据分类与关联

[English](disk-storage-layout.en.md)

---

> 本文由对 [PR #34897](https://github.com/bitcoin/bitcoin/pull/34897)（indexes: Don't commit ahead of the flushed chainstate）的 review 和分析延伸而来。理解索引损坏问题的根因，需要先弄清磁盘上各类数据的存储方式及其相互关联，因此整理成文。PR 解析见 [PR #34897 解析](index-flush-pr34897.zh.md)。

## 总览

Bitcoin Core 在 `~/.bitcoin/`（数据目录）下存储所有持久化数据。存储介质分为两类：**LevelDB**（键值数据库）和 **flat file**（平坦文件，顺序追加写入）。

```
~/.bitcoin/
├── blocks/
│   ├── blk00000.dat    ┐
│   ├── blk00001.dat    ├─ flat file: 原始区块数据
│   ├── ...             ┘
│   ├── rev00000.dat    ┐
│   ├── rev00001.dat    ├─ flat file: 区块撤销数据
│   ├── ...             ┘
│   └── index/          ←── LevelDB: 区块索引
│
├── chainstate/         ←── LevelDB: UTXO 集合
│
└── indexes/
    ├── txindex/            ←── LevelDB: 交易索引
    ├── blockfilter/basic/
    │   ├── db/             ←── LevelDB: 过滤器元数据
    │   └── fltr00000.dat   ←── flat file: BIP 158 过滤器数据
    └── coinstatsindex/     ←── LevelDB: UTXO 统计索引
```

## 各数据库详解

### 1. 区块 flat file（`blocks/blk*.dat`、`blocks/rev*.dat`）

区块数据按接收顺序连续追加到 `blk*.dat` 文件中，每个文件最大约 128 MB（`MAX_BLOCKFILE_SIZE`）。

```
blk00003.dat
┌───────────────────────────────────────────────────┐
│ offset=0      │ Block A 的完整序列化数据           │
│               │ (magic + size + header + txs)      │
├───────────────┼───────────────────────────────────┤
│ offset=18291  │ Block B 的完整序列化数据           │
├───────────────┼───────────────────────────────────┤
│ offset=95420  │ Block C 的完整序列化数据           │
├───────────────┼───────────────────────────────────┤
│ ...           │ ...                               │
└───────────────────────────────────────────────────┘
```

对应的 `rev*.dat` 存储每个区块的撤销数据（undo data）：每笔非 coinbase 交易所花费的旧 UTXO 完整信息，用于 reorg 时恢复。

```
rev00003.dat
┌───────────────────────────────────────────────────┐
│ offset=0      │ Block A 的 CBlockUndo              │
│               │ { vtxundo: [                       │
│               │     tx1 的 spent coins,             │
│               │     tx2 的 spent coins, ...         │
│               │ ] }                                │
├───────────────┼───────────────────────────────────┤
│ ...           │ ...                               │
└───────────────────────────────────────────────────┘
```

### 2. 区块索引（`blocks/index/` LevelDB）

存储每个已知区块的元数据（`CDiskBlockIndex`），启动时**全部加载到内存**构建区块树。

```
key: 'b' + block_hash
val: CDiskBlockIndex {
    nHeight          区块高度
    nStatus          状态标志位（BLOCK_HAVE_DATA, BLOCK_HAVE_UNDO, BLOCK_VALID_*, ...）
    nTx              区块中的交易数
    nFile            ───→ 指向 blk?????.dat 和 rev?????.dat 的文件编号
    nDataPos         ───→ 区块数据在 blk 文件中的字节偏移
    nUndoPos         ───→ 撤销数据在 rev 文件中的字节偏移
    nVersion         区块版本号
    hashPrev         前一个区块的哈希
    hashMerkleRoot   Merkle 根
    nTime            时间戳
    nBits            难度目标
    nNonce           nonce
}
```

还存储 block file 的元信息（每个 blk/rev 文件对的统计数据）。

### 3. Chainstate / UTXO 集（`chainstate/` LevelDB）

存储当前所有**未花费的交易输出（UTXO）**。这是节点验证新交易和新区块的核心数据。

```
key: 'C' + txid + varint(vout_index)
val: Coin {
    nValue           金额（satoshis）
    scriptPubKey     锁定脚本
    nHeight          该 UTXO 来自哪个高度的区块
    fCoinBase        是否来自 coinbase 交易
}

key: 'B'
val: best_block_hash    上次 flush 时的链 tip

key: 'H'
val: [new_tip, old_tip]  仅在 flush 进行中存在，用于原子性保证
```

主网当前约有 1.8 亿条 UTXO 记录。在运行时，节点使用 `dbcache`（默认 450 MB，可配置）在内存中缓存热数据，定期 flush 到 LevelDB。

### 4. 交易索引（`indexes/txindex/` LevelDB）

可选索引。启用后，可以通过 txid 快速定位任意交易在磁盘上的物理位置。

```
key: 't' + txid
val: CDiskTxPos {
    nFile        ───→ blk?????.dat 的文件编号
    nPos         ───→ 该交易所在区块在文件中的偏移
    nTxOffset    ───→ 交易在区块内的偏移（跳过区块头和 tx count）
}

key: 'B'
val: CBlockLocator    索引已处理到哪个区块
```

### 5. 区块过滤器索引（`indexes/blockfilter/basic/`）

可选索引。存储 BIP 158 compact block filter，支持轻钱包高效扫描区块。

```
LevelDB (db/):
    key: height → (block_hash, DBVal {
                       filter_hash      过滤器的哈希
                       header           过滤器链的 header（链式哈希）
                       pos: FlatFilePos ───→ 指向 fltr?????.dat 中的位置
                   })
    key: 'B' → CBlockLocator
    key: 'P' → FlatFilePos    下一个过滤器的写入位置

flat file (fltr*.dat):
    ┌──────────────────────────────┐
    │ block_hash + encoded_filter  │  每个区块的 GCS 过滤器
    ├──────────────────────────────┤
    │ block_hash + encoded_filter  │
    ├──────────────────────────────┤
    │ ...                          │
    └──────────────────────────────┘
```

### 6. UTXO 统计索引（`indexes/coinstatsindex/` LevelDB）

可选索引。维护 UTXO 集合的增量统计摘要，支持 `gettxoutsetinfo` RPC 快速查询。

```
key: height → (block_hash, DBVal {
                   muhash                    MuHash 最终摘要（uint256）
                   transaction_output_count  当前 UTXO 总数
                   bogo_size                 UTXO 集的估算大小
                   total_amount              所有 UTXO 的总金额
                   total_subsidy             累计出块奖励
                   total_prevout_spent_amount 累计已花费金额
                   total_coinbase_amount      累计 coinbase 金额
                   ...各类 unspendable 统计
               })

key: 'M' → MuHash3072 {
               m_numerator      3072-bit 大数
               m_denominator    3072-bit 大数
           }
           运行中的 MuHash 分数状态，与 DB_BEST_BLOCK 原子提交

key: 'B' → CBlockLocator
```

## 数据关联全景图

```
                    ┌─────────────────────────────────────────┐
                    │            blocks/index/ LevelDB        │
                    │                                         │
                    │  block_hash → CDiskBlockIndex {         │
                    │    nHeight, nStatus, nTx,               │
                    │    nFile, nDataPos, nUndoPos,           │
                    │    + 区块头字段                          │
                    │  }                                      │
                    └──────┬──────────────┬───────────────────┘
                           │              │
                   nFile + nDataPos    nFile + nUndoPos
                           │              │
                           ▼              ▼
               ┌──────────────────┐  ┌──────────────────┐
               │  blk?????.dat    │  │  rev?????.dat    │
               │                  │  │                  │
               │  区块 A 数据     │  │  区块 A undo     │
               │  ┌────────────┐  │  │  ┌────────────┐  │
               │  │ header     │  │  │  │ spent coin │  │
               │  │ tx_count   │  │  │  │ spent coin │  │
               │  │ tx[0] ─────┼──┼──┼──┤            │  │
               │  │ tx[1] ─────┼──┼──┼──┤            │  │
               │  │ ...        │  │  │  │ ...        │  │
               │  └────────────┘  │  │  └────────────┘  │
               │  区块 B 数据     │  │  区块 B undo     │
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
              │    nPos       →  区块的偏移       │
              │    nTxOffset  →  交易在区块内偏移  │
              │  }                               │
              └──────────────────────────────────┘


              ┌──────────────────────────────────┐
              │    chainstate/ LevelDB           │
              │                                  │
              │  'C' + txid + n → Coin {         │
              │    amount, scriptPubKey,          │     独立存储，不引用
              │    height, is_coinbase            │     flat file 或 block index
              │  }                               │
              │                                  │
              │  'B' → best_block_hash           │
              └──────────────────────────────────┘


              ┌──────────────────────────────────┐
              │  indexes/coinstatsindex/ LevelDB │
              │                                  │
              │  height → (hash, 统计摘要)       │     独立计算，不引用
              │  'M'    → MuHash3072 运行状态    │     其他数据库
              │  'B'    → CBlockLocator          │
              └──────────────────────────────────┘


              ┌──────────────────────────┐     ┌────────────────────┐
              │  blockfilter/basic/db/   │     │  fltr?????.dat     │
              │  LevelDB                 │     │                    │
              │                          │     │  filter A 数据     │
              │  height → (hash, DBVal { │     │  filter B 数据     │
              │    filter_hash,          │     │  ...               │
              │    header,               │     │                    │
              │    pos ──────────────────────→ │  偏移位置           │
              │  })                      │     │                    │
              │  'B' → CBlockLocator     │     │                    │
              │  'P' → next FlatFilePos  │     │                    │
              └──────────────────────────┘     └────────────────────┘
```

## 查询路径示例

### getrawtransaction(txid)：通过 txindex 查询交易

```
txid
 │
 ▼
txindex LevelDB ──→ CDiskTxPos { nFile=3, nPos=18291, nTxOffset=81 }
 │
 ▼
打开 blocks/blk00003.dat
seek 到 18291 + 81
 │
 ▼
反序列化 CTransaction → 返回完整交易
```

### 验证交易输入：通过 chainstate 查询 UTXO

```
交易输入: vin[0] = { txid=abc..., vout=2 }
 │
 ▼
chainstate LevelDB ──→ key = 'C' + abc... + 2
                   ──→ Coin { 0.5 BTC, OP_DUP OP_HASH160..., height=500000 }
 │
 ▼
验证签名、检查金额 → 通过则从缓存中删除该 UTXO
```

### gettxoutsetinfo：通过 coinstatsindex 查询 UTXO 集摘要

```
gettxoutsetinfo(hash_type="muhash")
 │
 ▼
coinstatsindex LevelDB ──→ key = height
                       ──→ DBVal { muhash=a1b2..., total_amount=21M..., count=180M... }
 │
 ▼
直接返回预计算的统计数据（无需遍历 chainstate）
```

### getblockfilter(block_hash)：通过 blockfilterindex 查询过滤器

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
打开 indexes/blockfilter/basic/fltr00005.dat
seek 到 42000
 │
 ▼
反序列化 BlockFilter → 返回 GCS 过滤器
```

## 数据依赖关系

```
blk*.dat / rev*.dat         ← 最底层，其他数据的原始来源
       │
       │ 位置信息 (nFile, nDataPos, nUndoPos)
       ▼
blocks/index/ LevelDB       ← 中间层，定位区块的"电话簿"
       │
       │ 启动时加载到内存，构建 CBlockIndex 树
       │ 所有上层组件通过内存中的 CBlockIndex 访问区块
       ▼
┌──────┴──────────┬───────────────────┐
│                 │                   │
▼                 ▼                   ▼
chainstate/     txindex/          blockfilterindex/
(UTXO 集)      (交易定位)          (区块过滤器)
                  │
                  │ 值中包含 FlatFilePos
                  │ 指回 blk*.dat
                  ▼
              coinstatsindex/
              (UTXO 统计)
              完全独立计算
              不引用任何其他存储
```

**关键点**：

- `blocks/index/` 是连接 flat file 和上层应用的桥梁
- `chainstate/` 和 `coinstatsindex/` 是自包含的，不存储对 flat file 的引用
- `txindex` 直接存储 flat file 位置，绕过 block index 进行查询
- `blockfilterindex` 有自己的 flat file（`fltr*.dat`），其 LevelDB 存储指向这些文件的位置
