# Part 2: TxGraph Interface — Abstraction Layer Design

[中文](mempool-txgraph-02-txgraph-interface.zh.md)

> This article is Part 2 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 1: CTxMemPoolEntry](mempool-txgraph-01-entry.en.md) | Next: [Part 3: TxGraphImpl Data Structures](mempool-txgraph-03-impl-data.en.md)

---

## Focus

- Core file: `src/txgraph.h`
- Key classes/functions: TxGraph, TxGraph::Ref, TxGraph::BlockBuilder, Level, GraphIndex
- Prerequisites: Part 1

---

## Overview

TxGraph (`src/txgraph.h:47`) is a pure virtual class that defines the complete public API for the
graph engine. It hides complex logic—cluster management, linearization, staging, block building—behind
a clean set of virtual function interfaces.

This design allows CTxMemPool to interact with the graph engine only through the interface, facilitating
testing (mock injection) and future implementation replacement.

## 1. GraphIndex and Ref

(Will explain `src/txgraph.h:51` and `:232-253`)

- `using GraphIndex = uint32_t;` (:51): internal index of a transaction in the graph
- `Ref` class (:232-253): externally-held transaction handle
  - `m_graph` and `m_index` members
  - Move semantics, non-copyable
  - Automatically removes transaction from graph on destruction
- Ref lifecycle management

## 2. Level Enum

(Will explain `src/txgraph.h:64-67`)

- `Level::TOP`: points to staging layer when it exists, otherwise to main
- `Level::MAIN`: always points to the main layer
- Why two levels are needed

## 3. Mutation Method Group

(Will explain `src/txgraph.h:78-102`)

- `AddTransaction` (:78): add a transaction to the graph
- `RemoveTransaction` (:93): remove a transaction from the graph
- `AddDependency` (:98): add a parent-child dependency
- `SetTransactionFee` (:102): update a transaction's fee

## 4. Work and Staging Method Group

(Will explain `src/txgraph.h:108-120`)

- `DoWork` (:108): execute deferred computation (cost budget mechanism)
- `StartStaging` (:114) / `AbortStaging` (:116) / `CommitStaging` (:118)
- `HaveStaging` (:120)
- Design motivation for lazy evaluation

## 5. Query Method Group

(Will explain `src/txgraph.h:127-178`)

- Single-tx queries: `Exists` (:130), `GetIndividualFeerate` (:134), `GetMainChunkFeerate` (:138)
- Cluster queries: `GetCluster` (:142), `GetAncestors` (:146), `GetDescendants` (:150)
- Batch queries: `GetAncestorsUnion` (:154), `GetDescendantsUnion` (:158)
- Global queries: `GetTransactionCount` (:161), `CountDistinctClusters` (:168)
- Ordering: `CompareMainOrder` (:164)
- Diagnostics: `GetMainStagingDiagrams` (:173)
- Trimming: `Trim` (:178)

## 6. BlockBuilder Interface

(Will explain `src/txgraph.h:181-196` and `:201`)

- `BlockBuilder` inner class (:181-196)
- `GetCurrentChunk()` (:190): get the current best chunk
- `Include()` (:192): include the current chunk in the block
- `Skip()` (:195): skip the current chunk
- `GetBlockBuilder()` (:201): factory method
- Iterator pattern design

## 7. Factory Function and Others

(Will explain `src/txgraph.h:206-271`)

- `GetWorstMainChunk` (:206): get the lowest-feerate chunk (for eviction)
- `GetMainMemoryUsage` (:213): memory usage statistics
- `SanityCheck` (:216): consistency check
- `MakeTxGraph` (:266-271): factory function to create TxGraphImpl instances

---

## Summary

The TxGraph interface defines the complete capability boundary of the graph engine. After understanding
these API categories, the next article dives into TxGraphImpl to see how the data structures behind
these interfaces are organized.
