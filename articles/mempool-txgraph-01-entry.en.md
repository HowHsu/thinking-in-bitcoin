# Part 1: CTxMemPoolEntry — Transaction Representation in the Mempool

[中文](mempool-txgraph-01-entry.zh.md)

> This article is Part 1 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 0: Architecture Overview](mempool-txgraph-00-architecture.en.md) | Next: [Part 2: TxGraph Interface — Abstraction Layer Design](mempool-txgraph-02-txgraph-interface.en.md)

---

## Focus

- Core file: `src/kernel/mempool_entry.h`
- Supporting file: `src/util/feefrac.h`
- Key classes/functions: CTxMemPoolEntry, TxGraph::Ref, FeeFrac, FeePerWeight, FeePerVSize
- Prerequisites: Part 0

---

## Overview

CTxMemPoolEntry is the complete representation of a transaction once it enters the mempool. It contains
not only the transaction itself (CTransactionRef) but also metadata such as fee, weight, entry time,
and entry height.

A key design decision is that CTxMemPoolEntry inherits from TxGraph::Ref (`src/kernel/mempool_entry.h:65`),
making each mempool entry simultaneously serve as a reference handle in TxGraph, eliminating the need
for a separate mapping table.

## 1. Class Definition and Inheritance

(Will explain the class definition at `src/kernel/mempool_entry.h:65`)

- `class CTxMemPoolEntry : public TxGraph::Ref`
- Why inheritance over composition?
- TxGraph::Ref semantics: move-only, non-copyable

## 2. Core Fields Walkthrough

(Will explain each field at `src/kernel/mempool_entry.h:73-83`)

| Field | Line | Type | Purpose |
|-------|------|------|---------|
| `tx` | :73 | `CTransactionRef` | The transaction itself (shared pointer) |
| `nFee` | :74 | `CAmount` | Original transaction fee |
| `nTxWeight` | :75 | `int32_t` | Transaction weight (post-witness discount) |
| `nTime` | :77 | `int64_t` | Entry timestamp |
| `entry_sequence` | :78 | `uint64_t` | Entry sequence number (monotonically increasing) |
| `entryHeight` | :79 | `unsigned int` | Block height at time of entry |
| `m_modified_fee` | :82 | `mutable CAmount` | Modified fee (via prioritisetransaction) |
| `lockPoints` | :83 | `mutable LockPoints` | Timelock cache |

## 3. LockPoints Structure

(Will explain `src/kernel/mempool_entry.h:26-36`)

- Purpose of LockPoints: avoiding redundant BIP68 timelock calculations
- Meaning of height and time fields

## 4. Fee Representation: FeePerWeight vs FeeFrac vs FeePerVSize

(Will explain fee comparison logic in `src/util/feefrac.h`)

- `FeeFrac` struct (`src/util/feefrac.h:39-224`): fee/size pairs
- Comparison semantics: `FeeRateCompare` (:157) vs `operator<=>` (:178)
- `FeePerVSize` (:251-252) and `FeePerWeight` (:255-256) distinction
- Why precise fee comparison matters (avoiding floating point)

## 5. entry_sequence Purpose

(Will explain entry_sequence's role as fallback ordering)

- How ties are broken when feerates are equal
- Relationship with the sequence allocator in CTxMemPool

---

## Summary

CTxMemPoolEntry is the atomic unit of the mempool. After understanding its fields and inheritance,
the next article takes us to TxGraph::Ref's "home"—the TxGraph interface layer—to see how this
pure virtual class defines the complete graph engine API.
