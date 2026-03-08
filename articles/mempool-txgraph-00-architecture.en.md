# Part 0: Architecture Overview

[中文](mempool-txgraph-00-architecture.zh.md)

> This article is Part 0 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Next: [Part 1: CTxMemPoolEntry — Transaction Representation in the Mempool](mempool-txgraph-01-entry.en.md)

---

## Focus

- Core files: Global perspective, no single file focus
- Key classes/functions: CTxMemPool, TxGraph, Cluster, DepGraph, BlockAssembler, MemPoolAccept
- Prerequisites: None (series starting point)

---

## Overview

This article provides a bird's-eye view of Bitcoin Core's mempool subsystem architecture. We'll build
a "file map," trace the core data flow (transaction submission → validation → pool entry → block template
construction), and illustrate the relationships between key classes.

Understanding the overall architecture is a prerequisite for diving into source code. This article
does not cover implementation details—it serves as a navigation framework for the remaining 9 articles.

## 1. File Map

The mempool subsystem source code is spread across these key files:

| Layer | File | Responsibility |
|-------|------|----------------|
| Entry | `src/validation.cpp` | Transaction validation & acceptance (MemPoolAccept) |
| Mempool | `src/txmempool.h/cpp` | CTxMemPool: transaction storage, indexes, ChangeSet |
| Tx Repr | `src/kernel/mempool_entry.h` | CTxMemPoolEntry: per-transaction in-pool representation |
| Graph Engine | `src/txgraph.h` | TxGraph pure virtual interface |
| Graph Impl | `src/txgraph.cpp` | TxGraphImpl: cluster management, linearization, staging |
| Linearization | `src/cluster_linearize.h` | DepGraph, Linearize, PostLinearize algorithms |
| Fee Rates | `src/util/feefrac.h` | FeeFrac fee representation and comparison |
| Block Building | `src/node/miner.h/cpp` | BlockAssembler: block template generation |

## 2. Core Data Flow

(Will detail the complete path from RPC/P2P to block template)

- Submission: `AcceptToMemoryPool` → `MemPoolAccept::AcceptSingleTransactionInternal`
- Validation pipeline: `PreChecks` → `PolicyScriptChecks` → `ConsensusScriptChecks` → `FinalizeSubpackage`
- Pool entry: `ChangeSet::Apply` → `CTxMemPool::addNewTransaction` → `TxGraph::AddTransaction`
- Block building: `BlockAssembler::CreateNewBlock` → `addChunks` → `TxGraph::BlockBuilder`

## 3. Key Class Relationships

(Will include a class relationship diagram)

```
CTxMemPool
  ├── mapTx: boost::multi_index<CTxMemPoolEntry>
  ├── m_txgraph: unique_ptr<TxGraph>
  └── ChangeSet (staging management)

TxGraph (pure virtual interface)
  └── TxGraphImpl (implementation)
        ├── ClusterSet (m_main / m_staging)
        │     └── Cluster (SingletonClusterImpl / GenericClusterImpl)
        │           └── DepGraph<BitSet> (dependency graph + linearization)
        ├── Entry[] (transaction data)
        ├── ChunkIndex (chunk feerate index)
        └── BlockBuilderImpl

CTxMemPoolEntry : public TxGraph::Ref
  └── Each entry is simultaneously a Ref in TxGraph
```

## 4. Layered Architecture

(Will explain the layered design: validation → txmempool → txgraph → cluster_linearize)

- **validation layer**: Policy checks, script validation, RBF evaluation
- **txmempool layer**: Transaction storage and indexing (mapTx, mapNextTx, mapDeltas)
- **txgraph layer**: Dependency graph, cluster management, staging
- **cluster_linearize layer**: Pure algorithm layer, no side effects

## 5. Design Philosophy

(Will discuss the motivations behind these design decisions)

- Why separate TxGraph from CTxMemPool?
- Why does TxGraph use a pure virtual interface?
- Why adopt lazy evaluation (the DoWork mechanism)?
- Why is the staging dual-layer architecture needed?

---

## Summary

This article establishes a global cognitive framework for the mempool subsystem. In the next article,
we'll start with the most basic building block—CTxMemPoolEntry—and understand how a transaction
is represented inside the mempool.
