# Part 8: Block Building — From Mempool to Block Template

[中文](mempool-txgraph-08-block-building.zh.md)

> This article is Part 8 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 7: Transaction Validation and Acceptance — The ATMP Flow](mempool-txgraph-07-atmp.en.md) | Next: [Part 9: Testing and Debugging — Quality Assurance](mempool-txgraph-09-testing.en.md)

---

## Focus

- Core files: `src/node/miner.h`, `src/node/miner.cpp`
- Key classes/functions: BlockAssembler, CreateNewBlock, addChunks, BlockBuilder
- Prerequisites: Part 7

---

## Overview

Block building is the ultimate output of the mempool subsystem—packaging mempool transactions
into a block template for miners. BlockAssembler (`src/node/miner.h:60`) handles this process,
using TxGraph's BlockBuilder interface to select chunks in descending feerate order to fill blocks.

## 1. BlockAssembler Class

(Will explain `src/node/miner.h:60-123`)

- Class responsibilities: coordinate block template generation
- `Options` struct (:81-88):
  - `nBlockMaxWeight`: maximum block weight
  - `blockMinFeeRate`: minimum block feerate
- Key members: `m_mempool`, `m_chainstate`, `pblocktemplate`

## 2. CreateNewBlock — Block Template Generation

(Will explain `src/node/miner.cpp:122`)

- Complete flow:
  1. Create block header and coinbase transaction
  2. Lock the mempool (`m_mempool->cs`)
  3. Call `m_mempool->StartBlockBuilding()` (:152)
  4. Call `addChunks()` to select transactions (:153)
  5. Call `m_mempool->StopBlockBuilding()` (:154)
  6. Finalize coinbase and block header

## 3. addChunks — Chunk-Based Transaction Selection

(Will explain `src/node/miner.cpp:279-334`)

- Core loop:
  1. `GetBlockBuilderChunk()` (:293, :331) gets the current best chunk
  2. Check if chunk meets block limits (weight, sigops)
  3. Meets limits: `IncludeBuilderChunk()` (:320) includes in block
  4. Doesn't meet: `SkipBuilderChunk()` (:311) skips
- Loop termination: no more chunks or block is full

## 4. BlockBuilder Interface Usage

(Will combine `src/txgraph.h:181-196` and `src/txgraph.cpp:852-879`)

- `GetCurrentChunk()`: returns the highest-feerate chunk
- `Include()`: marks chunk transactions as selected
- `Skip()`: skips the current chunk, moves to the next
- CTxMemPool wrapper methods: StartBlockBuilding, GetBlockBuilderChunk, IncludeBuilderChunk, SkipBuilderChunk, StopBlockBuilding

## 5. Comparison with Traditional Ancestor-Feerate Method

(Will discuss differences between old and new block building approaches)

- Old method: ancestor feerate sorting + greedy selection
- New method: cluster linearization + chunk iteration
- Advantages: more accurate feerate ordering, no need to maintain ancestor counts
- Reference: [Cluster Mempool design motivation](https://delvingbitcoin.org/t/introduction-to-cluster-linearization/1032)

## 6. getblocktemplate RPC Call Chain

(Will explain the complete call path from RPC to BlockAssembler)

- `getblocktemplate` RPC → `ProcessNewBlock` → `BlockAssembler::CreateNewBlock`
- Block template JSON format

---

## Summary

Block building is the ultimate expression of the mempool subsystem's value. The chunk iteration
approach is clean and efficient, fully leveraging TxGraph's linearization results. The next
(and final) article covers how to test and debug this complex subsystem.
