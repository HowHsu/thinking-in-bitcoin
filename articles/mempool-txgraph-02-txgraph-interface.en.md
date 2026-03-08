# Part 2: TxGraph Interface — Abstraction Layer Design

[中文](mempool-txgraph-02-txgraph-interface.zh.md)

> This article is Part 2 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 1: CTxMemPoolEntry](mempool-txgraph-01-entry.en.md) | Next: [Part 3: TxGraphImpl Data Structures](mempool-txgraph-03-impl-data.en.md)

---

## Focus

- Core file: `src/txgraph.h` (273 lines)
- Key classes/functions: TxGraph, TxGraph::Ref, TxGraph::BlockBuilder, Level, GraphIndex, MakeTxGraph
- Prerequisites: Part 1

---

## Overview

TxGraph (`src/txgraph.h:47`) is a pure virtual class that defines the complete public API for the
graph engine. It hides complex logic—cluster management, linearization, staging, block building—behind
a clean set of virtual function interfaces.

This file is only 273 lines, but extremely information-dense—each method's comments precisely describe
preconditions, behavioral semantics, and oversized restrictions. Understanding this interface means
understanding TxGraph's complete "capability boundary."

The design comments at the top of the file (:20-46) define several core concepts:

- **Main vs Staging**: TxGraph holds one or two graphs. Main is always present; staging is an optional temporary working copy.
- **Cluster**: All transactions reachable from each other through any sequence of parent/child relationships form a cluster (connected component).
- **Linearization**: Each cluster internally maintains a topology-compatible ordering (parents before children), aiming to approximate optimal mining order.
- **Chunk**: A contiguous segment in the linearization with monotonically decreasing feerate. Transactions in the same chunk are mined together.
- **Transitive closure of dependencies**: The interface is designed assuming the implementation only stores the transitive closure—if B depends on C, then "A depends on B" and "A depends on both B and C" are equivalent.

## 1. GraphIndex and Ref

### GraphIndex

```cpp
// src/txgraph.h:51
using GraphIndex = uint32_t;
```

GraphIndex is a transaction's internal identifier within TxGraph—a 32-bit unsigned integer used as
an index into `TxGraphImpl::m_entries`. External code never uses GraphIndex directly, instead
referencing transactions through Ref objects.

### Ref Class

```cpp
// src/txgraph.h:232-253
class Ref
{
    friend class TxGraph;
    TxGraph* m_graph = nullptr;
    GraphIndex m_index = GraphIndex(-1);
public:
    Ref() noexcept = default;
    virtual ~Ref();
    Ref& operator=(Ref&& other) noexcept = delete;
    Ref(Ref&& other) noexcept;
    Ref& operator=(const Ref&) = delete;
    Ref(const Ref&) = delete;
};
```

Ref is the externally-held transaction handle with RAII semantics. Key design points:

**Lifecycle management:**
- **Creation**: Ref is default-constructed in an empty state (`m_graph == nullptr`), then associated
  with a graph transaction via `TxGraph::AddTransaction(ref, feerate)`. TxGraph sets
  `m_graph = this` and `m_index = allocated_index`.
- **Move**: `Ref(Ref&&)` is non-trivial—after the move it calls `m_graph->UpdateRef(m_index, *this)`
  to notify TxGraph to update its internal pointer. Move assignment is deleted (would need to handle
  the case where `*this` already holds a transaction, adding complexity with no use case).
- **Destruction**: `virtual ~Ref()` is non-trivial—if `m_graph != nullptr`, it calls
  `m_graph->UnlinkRef(m_index)` to remove the transaction from the graph (from both main and staging).
  This is the RAII mechanism: owning a CTxMemPoolEntry (which inherits Ref) means owning the
  transaction's slot in the graph.

The **`virtual` destructor** makes it safe to delete CTxMemPoolEntry objects through `Ref*` pointers,
and also makes it safe for CTxMemPoolEntry to inherit from Ref (see Part 1).

**Protected accessors (:225-229):**

```cpp
static TxGraph*& GetRefGraph(Ref& arg) noexcept { return arg.m_graph; }
static GraphIndex& GetRefIndex(Ref& arg) noexcept { return arg.m_index; }
```

These `protected static` methods allow TxGraph implementation classes (TxGraphImpl) to access Ref's
private fields, without Ref needing to friend every possible implementation class.

## 2. Level Enum

```cpp
// src/txgraph.h:64-67
enum class Level {
    TOP,  //!< Refers to staging if it exists, main otherwise.
    MAIN  //!< Always refers to the main graph.
};
```

TxGraph supports a two-layer graph structure. The `Level` enum specifies which layer to query:

- **`Level::TOP`**: Dynamically resolved—points to staging when it exists, main when it doesn't.
  This is an abstraction for "the current working state."
- **`Level::MAIN`**: Always points to the main layer. Even when staging exists, you can query
  the "committed" stable state.

In the implementation (`src/txgraph.cpp:679-681`), Level maps to integers: 0 = main, 1 = staging.
`Level::TOP` maps to 1 when staging exists, 0 otherwise.

## 3. Mutation Method Group

These four methods modify graph state. They remain available in oversized state and are **lazy**—
they don't execute immediately but queue operations for later processing.

### AddTransaction (:71-78)

```cpp
virtual void AddTransaction(Ref& arg, const FeePerWeight& feerate) noexcept = 0;
```

- `arg` must be an empty Ref. After this call, `arg` will be associated with the newly created transaction.
- `feerate.size` must be strictly positive.
- If staging exists, the new transaction is only created in staging (not in main).
- Refs can outlive the TxGraph (safely becoming empty Refs).

### RemoveTransaction (:79-93)

```cpp
virtual void RemoveTransaction(const Ref& arg) noexcept = 0;
```

- If staging exists, removal only affects staging.
- No-op if the transaction was already removed.
- **Reordering caveat**: TxGraph may internally reorder transaction removals with dependency
  additions for performance. The comments give a specific example: if A→B dependency exists,
  adding C→B dependency then removing B may still leave C depending on A. But as long as
  removing B also removes all its descendants or ancestors (as is typical in practice),
  the reordering doesn't affect behavior.

### AddDependency (:94-98)

```cpp
virtual void AddDependency(const Ref& parent, const Ref& child) noexcept = 0;
```

- If staging exists, the dependency is only added to staging.
- No-op if either transaction has been removed.
- No-op if parent is already an ancestor of child (redundant dependency).
- **Precondition**: parent must NOT be a descendant of child (would create a cycle). Violating this is undefined behavior.

### SetTransactionFee (:99-102)

```cpp
virtual void SetTransactionFee(const Ref& arg, int64_t fee) noexcept = 0;
```

- **Unlike other mutation methods**: modifies the fee in **both** main and staging simultaneously.
  This is because fee modifications (from `prioritisetransaction`) are considered universally
  applicable regardless of staging state.
- If the transaction doesn't exist in a given layer, has no effect in that layer.

## 4. Work and Staging Method Group

### DoWork (:104-108)

```cpp
virtual bool DoWork(uint64_t max_cost) noexcept = 0;
```

TxGraph is **lazy**—mutation methods only queue; they don't execute immediately. `DoWork`
proactively advances pending work: applying removals, applying dependencies, splitting disconnected
clusters, merging clusters that need merging, and optimizing linearizations.

- `max_cost`: maximum computation budget (in abstract cost units from cluster_linearize).
- Returns `true` if all currently-available work is done; `false` if work remains.
- Can be called in oversized state, but oversized clusters are skipped.
- Design intent: call during node idle time to pre-compute, making subsequent queries and block building faster.

### Staging Control (:110-120)

```cpp
virtual void StartStaging() noexcept = 0;   // Precondition: no staging currently exists
virtual void AbortStaging() noexcept = 0;   // Precondition: staging exists
virtual void CommitStaging() noexcept = 0;  // Precondition: staging exists
virtual bool HaveStaging() const noexcept = 0;
```

- `StartStaging()`: Creates a staging graph—conceptually a full copy of main. Subsequent mutation operations only affect staging.
- `AbortStaging()`: Discards all staging changes, restoring to main state.
- `CommitStaging()`: Replaces main with staging. All staging changes become permanent.
- `HaveStaging()`: Queries whether staging exists. This is a `const` method.

## 5. Query Method Group

Query methods retrieve graph state. The following table lists all query methods, noting their
Level parameter and oversized availability:

| Method | Signature | Level | Oversized OK | Notes |
|--------|-----------|-------|-------------|-------|
| `IsOversized` | `bool(Level)` | Yes | Yes | Whether cluster exceeds size limits |
| `Exists` | `bool(const Ref&, Level)` | Yes | Yes | Whether transaction hasn't been removed |
| `GetIndividualFeerate` | `FeePerWeight(const Ref&)` | No | Yes | Single-tx feerate (either layer) |
| `GetMainChunkFeerate` | `FeePerWeight(const Ref&)` | No (main only) | No | Chunk feerate in main |
| `GetCluster` | `vector<Ref*>(const Ref&, Level)` | Yes | No | All cluster members, **linearization order** |
| `GetAncestors` | `vector<Ref*>(const Ref&, Level)` | Yes | No | All ancestors (incl self), unordered |
| `GetDescendants` | `vector<Ref*>(const Ref&, Level)` | Yes | No | All descendants (incl self), unordered |
| `GetAncestorsUnion` | `vector<Ref*>(span, Level)` | Yes | No | Union of multiple txs' ancestors |
| `GetDescendantsUnion` | `vector<Ref*>(span, Level)` | Yes | No | Union of multiple txs' descendants |
| `GetTransactionCount` | `GraphIndex(Level)` | Yes | Yes | Total transaction count |
| `CompareMainOrder` | `strong_ordering(Ref&, Ref&)` | No (main only) | No | Linearization order comparison |
| `CountDistinctClusters` | `GraphIndex(span, Level)` | Yes | No | Count distinct clusters |
| `GetMainStagingDiagrams` | `pair<vec, vec>()` | No (both required) | No | Main and staging feerate diagrams |
| `Trim` | `vector<Ref*>()` | No (implicit TOP) | Required | Remove low-fee txs to restore non-oversized |

Several design details worth noting:

**`GetCluster` (:139-142)** returns a `vector<Ref*>` in **linearization order**—unlike
`GetAncestors`/`GetDescendants` which return unordered results. Linearization order is the
mining priority ordering within a cluster.

**`GetMainChunkFeerate` (:135-138)** only queries the main layer (no Level parameter), returning the
aggregate feerate of the chunk a transaction belongs to—reflecting the transaction's actual
priority in block building.

**`GetMainStagingDiagrams` (:169-173)** returns feerate diagram pairs for main and staging.
Key optimization: only includes chunks from clusters that **differ** between the two layers;
identical clusters are excluded. The return type is `FeeFrac` (not `FeePerWeight`), making it
directly usable with `CompareChunks()` (`src/util/feefrac.h:234`) for feerate diagram comparison.

**`Trim` (:174-178)** removes low-feerate transactions and their descendants when the graph
is oversized. This is a "best-effort" operation, not guaranteeing preservation of specific
transactions. Only effective when oversized; no-op otherwise.

## 6. BlockBuilder Interface

```cpp
// src/txgraph.h:181-196
class BlockBuilder
{
protected:
    BlockBuilder() noexcept = default;
public:
    virtual ~BlockBuilder() = default;
    virtual std::optional<std::pair<std::vector<Ref*>, FeePerWeight>>
        GetCurrentChunk() noexcept = 0;
    virtual void Include() noexcept = 0;
    virtual void Skip() noexcept = 0;
};
```

BlockBuilder is an **iterator pattern** interface for traversing transactions in optimal mining order.
It's an inner class of `TxGraph` with a `protected` constructor—can only be created through
`TxGraph::GetBlockBuilder()`.

### GetCurrentChunk (:190)

```cpp
virtual std::optional<std::pair<std::vector<Ref*>, FeePerWeight>>
    GetCurrentChunk() noexcept = 0;
```

Returns the current highest-feerate chunk, containing:
- `std::vector<Ref*>`: references to all transactions in the chunk
- `FeePerWeight`: the chunk's aggregate feerate

Returns `std::nullopt` when all chunks have been traversed (block building complete).

### Include (:192)

```cpp
virtual void Include() noexcept = 0;
```

Marks the current chunk as "included in the block" and advances the iterator to the next chunk.
The caller is responsible for actually adding these transactions to the block.

### Skip (:193-195)

```cpp
virtual void Skip() noexcept = 0;
```

Skips the current chunk (e.g., insufficient block space or feerate too low) and advances the iterator.
**Critical semantic**: after skipping a chunk, **all subsequent chunks from the same cluster are no
longer returned**. This is because later chunks within a cluster topologically depend on earlier
ones—if earlier chunks aren't mined, later ones can't be either.

### GetBlockBuilder (:198-201)

```cpp
virtual std::unique_ptr<BlockBuilder> GetBlockBuilder() noexcept = 0;
```

Factory method creating a BlockBuilder instance. Preconditions:
- Main must NOT be oversized.
- While the BlockBuilder exists, no mutation operations on the main graph are allowed
  (internally enforced via reference count `m_main_chunkindex_observers`).
- The BlockBuilder must NOT outlive the TxGraph that created it.

### GetWorstMainChunk (:202-206)

```cpp
virtual std::pair<std::vector<Ref*>, FeePerWeight> GetWorstMainChunk() noexcept = 0;
```

Returns the **lowest-feerate** chunk in the main graph—the set of transactions with the lowest
mining priority that would be mined last. Used for mempool eviction (`TrimToSize` evicts
the lowest-feerate transactions to satisfy size limits).

**Special return order**: transactions are in **reverse topological order**—each transaction is
preceded by all its descendants. This is designed for the eviction use case: evict descendants
first, then ancestors.

## 7. MakeTxGraph Factory Function

```cpp
// src/txgraph.h:256-271
std::unique_ptr<TxGraph> MakeTxGraph(
    unsigned max_cluster_count,
    uint64_t max_cluster_size,
    uint64_t acceptable_cost,
    const std::function<std::strong_ordering(const TxGraph::Ref&, const TxGraph::Ref&)>&
        fallback_order
) noexcept;
```

This is the sole entry point for creating TxGraphImpl instances. Four parameters:

| Parameter | Type | Meaning |
|-----------|------|---------|
| `max_cluster_count` | `unsigned` | Max transactions per cluster. Cannot exceed `MAX_CLUSTER_COUNT_LIMIT` (64, :18) |
| `max_cluster_size` | `uint64_t` | Max total transaction size sum per cluster (weight units) |
| `acceptable_cost` | `uint64_t` | Linearization optimization computation budget. Higher = better quality, more CPU |
| `fallback_order` | `std::function<...>` | Tie-breaking rule for equal feerates. Must be a stable total order. Typically based on entry_sequence |

When a cluster's transaction count exceeds `max_cluster_count` or its total size exceeds
`max_cluster_size`, the graph enters **oversized** state. Most query methods become unavailable
(see table above), and `Trim()` must be called to restore normal state.

## 8. Memory and Diagnostics

### GetMainMemoryUsage (:208-213)

```cpp
virtual size_t GetMainMemoryUsage() noexcept = 0;
```

Returns approximate memory usage of the main graph in bytes. Excludes staging, BlockBuilder,
pending queues, and temporary caches. If staging exists, returns what memory usage would be
"after calling `AbortStaging()`."

### SanityCheck (:215-216)

```cpp
virtual void SanityCheck() const = 0;
```

Internal consistency check. Verifies all internal invariants: cluster state matches QualityLevel
bucketing, Locators are valid, ChunkIndex is ordered, m_entries and Refs have bidirectional
reference integrity. This is a `const` method, used only in debugging and testing (see Part 9).

---

## Summary

The TxGraph interface is only 273 lines yet defines the graph engine's complete capability boundary.
Key takeaways:

- **4 mutation methods**: AddTransaction, RemoveTransaction, AddDependency, SetTransactionFee.
  The first three only affect the TOP layer (staging only when it exists); SetTransactionFee affects both layers.
- **Lazy evaluation**: mutations only queue; DoWork advances actual computation.
- **Staging trio**: StartStaging → modify → Commit/Abort.
- **BlockBuilder iterator**: GetCurrentChunk → Include/Skip loop.
- **Ref RAII semantics**: destroying a Ref automatically removes the transaction from the graph.
- **Oversized protection**: most queries unavailable when limits exceeded; restored via Trim.
- **MakeTxGraph factory**: 4 parameters controlling cluster limits, optimization budget, and ordering rules.

The next article dives into TxGraphImpl to see how the data structures behind these interfaces
are organized.
