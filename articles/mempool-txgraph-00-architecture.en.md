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

This article provides a bird's-eye view of Bitcoin Core's mempool subsystem architecture.
We'll build a "file map," trace the core data flows (transaction submission → validation →
pool entry → block template construction), and illustrate the relationships between key classes.

Understanding the overall architecture is a prerequisite for diving into source code. This article
does not cover implementation details—it serves as a navigation framework for the remaining 9 articles.

## 1. File Map

The mempool subsystem source code is spread across these key files:

| Layer | File | Responsibility | See also |
|-------|------|----------------|----------|
| Entry | `src/validation.cpp` | Transaction validation & acceptance (MemPoolAccept, :435) | Part 7 |
| Mempool | `src/txmempool.h/cpp` | CTxMemPool: transaction storage, indexes, ChangeSet (:186) | Part 6 |
| Tx Repr | `src/kernel/mempool_entry.h` | CTxMemPoolEntry: per-transaction in-pool representation (:65) | Part 1 |
| Graph Interface | `src/txgraph.h` | TxGraph pure virtual interface (:47) | Part 2 |
| Graph Impl | `src/txgraph.cpp` | TxGraphImpl: cluster management, linearization, staging (:390) | Parts 3-5 |
| Linearization | `src/cluster_linearize.h` | DepGraph, Linearize, PostLinearize algorithms (:29) | Part 4 |
| Fee Rates | `src/util/feefrac.h` | FeeFrac fee representation & exact comparison (:39) | Part 1 |
| Block Building | `src/node/miner.h/cpp` | BlockAssembler: block template generation (:60) | Part 8 |

## 2. Core Data Flows

### 2.1 Transaction Acceptance Path

A transaction arriving from the outside (P2P network or RPC) follows this path to enter the mempool:

```
P2P / sendrawtransaction RPC
  │
  ▼
AcceptToMemoryPool()                     [src/validation.cpp]
  │  Creates MemPoolAccept object
  ▼
MemPoolAccept::AcceptSingleTransactionInternal()   [:1314]
  │  Acquires locks: cs_main + m_pool.cs
  │  Creates ChangeSet → calls m_txgraph->StartStaging()
  │
  ├─ ChangeSet::StageAddition()          [src/txmempool.h:636]
  │    Inserts transaction into the staging graph
  │
  ├─ PreChecks()                         [:782]
  │    Dedup, standardness, feerate, input availability, conflict detection
  │
  ├─ ReplacementChecks()
  │    RBF incentive compatibility evaluation
  │
  ├─ CheckMemPoolPolicyLimits()          [src/txmempool.h:643]
  │    Cluster count/size limit checks
  │
  ├─ PolicyScriptChecks()                [:1132]
  │    Policy-level script validation
  │
  ├─ ConsensusScriptChecks()             [:1155]
  │    Consensus-level script validation + cache results
  │
  └─ FinalizeSubpackage()                [:1188]
       │  Calls ChangeSet::Apply()
       ▼
     CTxMemPool::Apply()
       ├─ removeUnchecked()  Remove replaced transactions
       ├─ mapTx.insert()     Insert new transaction
       ├─ addNewTransaction()  Update mapNextTx and other indexes
       └─ m_txgraph->CommitStaging()
            Merge staging into main
```

### 2.2 Block Building Path

When a miner requests a block template, transactions are selected from the mempool:

```
getblocktemplate RPC / Stratum
  │
  ▼
BlockAssembler::CreateNewBlock()         [src/node/miner.cpp:122]
  │  LOCK(cs_main) + LOCK(m_mempool->cs)
  │
  ├─ m_mempool->StartBlockBuilding()     [:152]
  │    Creates BlockBuilderImpl, acquires ChunkIndex iterator
  │
  ├─ addChunks()                         [:279]
  │    Loop:
  │    ┌─ GetBlockBuilderChunk()         [:293]
  │    │    Fetch highest-feerate chunk from ChunkIndex
  │    │
  │    ├─ Check weight/sigops limits
  │    │
  │    ├─ Fits → IncludeBuilderChunk()   [:320]
  │    │          Add chunk transactions to block
  │    │
  │    └─ Doesn't fit → SkipBuilderChunk()  [:311]
  │                      Skip chunk (all later chunks from same cluster also skipped)
  │
  └─ m_mempool->StopBlockBuilding()      [:154]
       Release BlockBuilderImpl
```

## 3. Key Class Relationships

```
CTxMemPool                           [src/txmempool.h:186]
  ├── mapTx: boost::multi_index     Transaction store (by txid / wtxid / time)
  │     └── CTxMemPoolEntry          Per-transaction in-pool representation
  │           └── : public TxGraph::Ref   Inherits, making entry a graph reference handle
  ├── mapNextTx                      UTXO → spending transaction reverse index
  ├── mapDeltas                      prioritisetransaction fee adjustments
  ├── m_txgraph: unique_ptr<TxGraph> Graph engine (dependencies, ordering, staging)
  └── ChangeSet                      Transactional change interface (StageAddition/Apply)

TxGraph                              [src/txgraph.h:47] (pure virtual interface)
  └── TxGraphImpl                    [src/txgraph.cpp:390] (sole implementation)
        ├── m_main_clusterset        Main ClusterSet
        ├── m_staging_clusterset     Optional staging ClusterSet
        │     └── ClusterSet
        │           └── Cluster[]    Bucketed by QualityLevel
        │                 ├── SingletonClusterImpl  Single-tx specialization (no DepGraph)
        │                 └── GenericClusterImpl    Multi-tx (holds DepGraph)
        │                       └── DepGraph<BitSet<64>>  Dependency graph + linearization
        ├── m_entries: Entry[]       Dense array: GraphIndex → Entry
        ├── m_main_chunkindex        Chunk feerate sorted index (std::set)
        └── BlockBuilderImpl         Block building iterator
```

The core design: **transaction storage** (mapTx) and the **ordering engine** (TxGraph) are separated.
mapTx handles storage and lookup, TxGraph handles dependency relationships, cluster linearization,
and mining priority ordering. The two are connected through `TxGraph::Ref` inheritance—each
`CTxMemPoolEntry` is simultaneously a Ref handle in the graph, requiring no additional mapping table.

## 4. Layered Architecture

```
┌─────────────────────────────────────────────────────────┐
│  validation layer   [src/validation.cpp]                │
│  MemPoolAccept: policy checks, script validation, RBF   │
│  Responsibility: decide whether a tx CAN enter the pool │
├─────────────────────────────────────────────────────────┤
│  txmempool layer    [src/txmempool.h/cpp]               │
│  CTxMemPool: tx storage, indexes, ChangeSet interface    │
│  Responsibility: manage tx storage and lifecycle         │
├─────────────────────────────────────────────────────────┤
│  txgraph layer      [src/txgraph.h, src/txgraph.cpp]    │
│  TxGraph/TxGraphImpl: dep graph, clusters, staging       │
│  Responsibility: manage tx dependencies & mining order   │
├─────────────────────────────────────────────────────────┤
│  cluster_linearize layer  [src/cluster_linearize.h]     │
│  DepGraph, Linearize, PostLinearize: pure algorithms     │
│  Responsibility: compute optimal linearization order     │
└─────────────────────────────────────────────────────────┘
```

Each layer depends only on layers below, never upward:

- The **validation layer** calls the txmempool layer's `ChangeSet` interface to stage and commit
  changes, but txmempool is unaware of validation's existence.
- The **txmempool layer** uses TxGraph's pure virtual interface via `m_txgraph`, but TxGraph
  is unaware of CTxMemPool's existence.
- The **txgraph layer** uses `DepGraph` and `Linearize` algorithms from cluster_linearize,
  but cluster_linearize is a stateless pure-function library unaware of TxGraph.

This layering makes each layer independently testable. The cluster_linearize layer in particular,
as a pure algorithm library, has extremely high fuzz test coverage (see Part 9).

## 5. Design Philosophy

### 5.1 Why Separate TxGraph from CTxMemPool?

Historically, CTxMemPool directly managed transaction dependencies (via ancestor/descendant counts).
The Cluster Mempool reform extracted dependency management into a standalone TxGraph module for
three reasons:

1. **Separation of concerns**: CTxMemPool focuses on transaction storage and indexing (mapTx CRUD),
   while TxGraph focuses on graph algorithms (clustering, linearization, feerate ordering).
2. **Algorithmic complexity**: Cluster linearization involves NP-hard optimization problems
   (branch-and-bound search). Encapsulating this in a separate module makes it easier to
   reason about and optimize.
3. **Staging requirements**: RBF evaluation requires "pretending" transactions are in the pool
   to compare feerate diagrams. Having graph operations as a separate module naturally supports
   the main/staging dual-layer architecture.

### 5.2 Why Does TxGraph Use a Pure Virtual Interface?

`TxGraph` (`src/txgraph.h:47`) is defined as a pure virtual class, with the sole implementation
`TxGraphImpl` in the `.cpp` file. This isn't for "polymorphism"—there will only ever be one
implementation. The real purposes are:

1. **Testability**: The fuzz test (`src/test/fuzz/txgraph.cpp`) runs `TxGraphImpl` in parallel with
   a naive implementation `SimTxGraph` for differential testing.
2. **Compilation firewall**: The 3500+ lines of `TxGraphImpl` implementation details are completely
   hidden in the `.cpp` file. Modifying internal data structures doesn't require recompiling
   files that depend on `txgraph.h`.
3. **Interface enforcement**: Forces all interactions through a well-defined API, preventing
   internal state leakage.

### 5.3 Why Lazy Evaluation?

TxGraph is internally **lazy**—calling `AddTransaction`, `RemoveTransaction`, `AddDependency`
doesn't execute immediately. Instead, operations are queued. Actual computation only happens
when a query method needs results or `DoWork(max_cost)` is explicitly called.

Benefits of this design:

1. **Batch optimization**: Multiple consecutive add/remove operations can be processed together,
   avoiding wasteful intermediate-state computation.
2. **Budget control**: `DoWork(max_cost)` lets callers control computation per invocation,
   preventing excessive time spent on critical paths.
3. **Idle utilization**: `DoWork` can be called during node idle time to pre-compute
   linearizations, making subsequent queries and block building faster.

### 5.4 Why the Staging Dual-Layer Architecture?

RBF (Replace-By-Fee) evaluation needs to answer: "If we replace old transactions with a new one,
will miners earn more?" This requires comparing pre- and post-replacement feerate diagrams.

The staging architecture enables this evaluation atomically:

1. `StartStaging()`: Create a logical copy of main
2. Remove replaced transactions and add new ones in staging
3. `GetMainStagingDiagrams()`: Compare main and staging feerate diagrams
4. Decide to accept (`CommitStaging()`) or reject (`AbortStaging()`)

This is directly analogous to database transactions with BEGIN / COMMIT / ROLLBACK.
CTxMemPool wraps this mechanism through the `ChangeSet` class (`src/txmempool.h:620`)
into a higher-level interface.

---

## Summary

This article established a global cognitive framework for the mempool subsystem:

- **File map**: 8 key files, from validation to cluster_linearize
- **Data flows**: Transaction acceptance (ATMP pipeline) and block building (chunk iteration)
- **Class relationships**: CTxMemPool (storage) + TxGraph (ordering) separation, connected via Ref inheritance
- **Layered architecture**: 4 layers with one-way dependencies, each independently testable
- **Design philosophy**: Separation of concerns, pure virtual interface, lazy evaluation, staging dual-layer

The next article starts with the most basic building block—CTxMemPoolEntry—to understand how
a transaction is represented inside the mempool.
