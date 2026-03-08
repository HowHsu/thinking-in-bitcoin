# Part 6: CTxMemPool — Core Mempool Operations

[中文](mempool-txgraph-06-ctxmempool.zh.md)

> This article is Part 6 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 5: Staging — The Dual-Graph System](mempool-txgraph-05-staging.en.md) | Next: [Part 7: Transaction Validation and Acceptance — The ATMP Flow](mempool-txgraph-07-atmp.en.md)

---

## Focus

- Core files: `src/txmempool.h`, `src/txmempool.cpp`
- Key classes/functions: CTxMemPool, indexed_transaction_set, mapTx, mapNextTx, mapDeltas, addNewTransaction, removeUnchecked, TrimToSize, check
- Prerequisites: Part 5

---

## Overview

CTxMemPool (`src/txmempool.h:186`) is the core class of Bitcoin Core's mempool. It maintains
transaction storage and indexing, cooperates with TxGraph to manage transaction dependencies,
and provides external query and modification interfaces.

This article starts from CTxMemPool's data members and explains the implementation of major
operations one by one.

## 1. mapTx — Multi-Index Container

(Will explain `src/txmempool.h:231-234` and `:263`)

- `indexed_transaction_set`: a boost::multi_index-based container
- Index dimensions: by txid, by wtxid, by time (entry time ordering)
- Why multi_index over multiple separate maps

## 2. mapNextTx — Input Reverse Index

(Will explain `src/txmempool.h:298`)

- `indirectmap<COutPoint, txiter> mapNextTx`
- Purpose: given a UTXO outpoint, quickly find the mempool transaction spending it
- Used for conflict detection and double-spend prevention

## 3. mapDeltas — Manual Fee Rate Adjustments

(Will explain `src/txmempool.h:299`)

- `std::map<Txid, CAmount> mapDeltas`
- Backend storage for `prioritisetransaction` RPC
- How it affects a transaction's effective fee rate

## 4. m_txgraph — Graph Engine Integration

(Will explain `src/txmempool.h:261`)

- `std::unique_ptr<TxGraph> m_txgraph`
- How CTxMemPool manages transaction dependencies through the TxGraph interface
- Lifecycle: created and destroyed with CTxMemPool

## 5. addNewTransaction — Transaction Entry

(Will explain `src/txmempool.cpp:229`)

- Internal method (called via ChangeSet::Apply)
- Adds transaction to mapTx, mapNextTx
- Updates transactions and dependencies in TxGraph
- Not directly exposed as a public API (called indirectly through ChangeSet)

## 6. removeUnchecked — Transaction Removal

(Will explain `src/txmempool.cpp:263`)

- Removes from mapTx, mapNextTx
- Cleans up references in TxGraph
- Meaning of "unchecked": no dependency integrity checks

## 7. TrimToSize — Mempool Size Limiting

(Will explain `src/txmempool.cpp:861`)

- Triggered when mempool exceeds `-maxmempool` setting
- Uses `m_txgraph->GetWorstMainChunk()` to find the lowest-feerate chunk
- Evicts lowest-feerate transactions until size limit is satisfied
- Lowest feerate recorded for `minrelayfee` calculation

## 8. check() — Consistency Check

(Will explain `src/txmempool.cpp:433`)

- Verifies consistency between mapTx, mapNextTx, and TxGraph
- Calls `m_txgraph->SanityCheck()` (:450)
- Only enabled in debug/test modes

---

## Summary

CTxMemPool is the hub connecting upper-layer validation logic to the lower-layer graph engine.
After understanding its data structures and core operations, the next article dives into the
validation layer to see how a transaction passes through the complete validation pipeline to
finally enter the mempool.
