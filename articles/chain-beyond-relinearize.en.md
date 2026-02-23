# O(N²) Bottlenecks Beyond Relinearize: The Full Cost of Chain Clusters in TxGraph

[中文版](chain-beyond-relinearize.zh.md)

---

## Motivation

`TryLinearizeChain` reduces the `Linearize()` step from O(N²) to O(N) for chain-shaped
clusters. However, `Relinearize()` is only the final step in a longer pipeline. Every code path
that reaches `Relinearize()` first passes through `ApplyDependencies()`, which internally
performs cluster splitting, merging, and dependency application — each of which is O(N²) for
chain clusters due to the DepGraph transitive closure.

This article traces all entry points that lead to `Relinearize()`, identifies the O(N²)
operations on each path, and evaluates how much of the total cost `TryLinearizeChain` actually
addresses.

---

## Entry Points to Relinearize

Every path to `Relinearize()` follows the same two-phase pattern:

```
Entry point
  → ApplyDependencies(level)     ← Phase 1: structural mutations (Split / Merge / dependency application)
    → Relinearize()              ← Phase 2: linearization
```

`TryLinearizeChain` optimizes Phase 2. Phase 1 is untouched.

### Phase 1: ApplyDependencies Pipeline

`ApplyDependencies(level)` is the gateway that processes all pending structural changes before
any linearization can occur:

```
ApplyDependencies(level)
  → GroupClusters(level)
    → SplitAll(level)
      → ApplyRemovals(level)          O(N) per cluster
      → Split(cluster)                O(N²) per cluster  ← bottleneck
    → [union-find to group clusters]  O(M α(M))
  → Merge(cluster_span)              O(N²) per group    ← bottleneck
  → cluster->ApplyDependencies(deps)  O(N²) per cluster  ← bottleneck
```

### Phase 2: Relinearize

```
Relinearize()
  → Linearize()
    → TryLinearizeChain             O(N) for chains  ✓ optimized
    → or SPF fallback               O(N²) for non-chains
  → PostLinearize()                 skipped for chains  ✓ optimized
  → Updated()                      O(N)
```

---

## Entry Point Catalogue

### A. `MakeAllAcceptable(level)` — synchronous, on-demand

Called when a consumer needs valid linearizations for all clusters:

| Caller | Trigger |
|--------|---------|
| `BlockBuilderImpl()` (txgraph.cpp:3208) | Block template construction |
| `GetWorstMainChunk()` (txgraph.cpp:3260) | Mempool eviction (finding the lowest-feerate chunk) |
| `GetMainStagingDiagrams()` (txgraph.cpp:2811,2813) | RBF evaluation (processes both main and staging) |

Call chain:
```
MakeAllAcceptable(level)
  → ApplyDependencies(level)              ← full Phase 1 pipeline, O(N²)
  → [drain NEEDS_FIX queue]
    → MakeAcceptable(cluster)
      → Relinearize(acceptable_iters)     ← Phase 2, O(N) for chains
  → [drain NEEDS_RELINEARIZE queue]
    → MakeAcceptable(cluster)
      → Relinearize(acceptable_iters)     ← Phase 2, O(N) for chains
```

### B. `DoWork(iters)` — budget-limited background work

Called after every mempool mutation to perform deferred optimization:

| Caller | Trigger |
|--------|---------|
| `CTxMemPool::Apply()` (txmempool.cpp:224) | New transaction accepted or RBF replacement committed |
| `CTxMemPool::removeForReorg()` (txmempool.cpp:381) | Chain reorganization |
| `CTxMemPool::removeForBlock()` (txmempool.cpp:424) | New block connected (confirmed transactions removed) |

Budget: `POST_CHANGE_WORK = 5 × ACCEPTABLE_ITERS = 8500` iterations per call.

Call chain:
```
DoWork(iters)
  → [for each quality: NEEDS_FIX, NEEDS_RELINEARIZE, ACCEPTABLE]
    → [for each level: staging, main]
      → ApplyDependencies(level)          ← full Phase 1 pipeline, O(N²)
      → [for each cluster in queue]
        → Relinearize(iters_now)          ← Phase 2, O(N) for chains
```

### C. `ApplyDependencies` + single `MakeAcceptable` — per-cluster queries

Called when a specific cluster's linearization is needed:

| Caller | Trigger |
|--------|---------|
| `GetBestChunkData()` (txgraph.cpp:2542+2549) | Query a transaction's chunk feerate |
| `CompareMainOrder()` (txgraph.cpp:2766+2775) | Compare mining priority of two transactions |
| `TrimToLimit()` (txgraph.cpp:3395) | Cluster exceeds size/count limits |
| `PullIn()` (txgraph.cpp:1703) | Pull a main cluster into staging |

Call chain:
```
ApplyDependencies(level)                  ← full Phase 1 pipeline, O(N²)
→ MakeAcceptable(cluster)
  → Relinearize(acceptable_iters)         ← Phase 2, O(N) for chains
```

---

## The Three O(N²) Bottlenecks in Phase 1

### 1. `Split()` — rebuild DepGraph for each connected component

**When**: After `RemoveTransaction` (e.g., block confirmation removes transactions from a chain
cluster), `ApplyRemovals` marks the cluster `NEEDS_SPLIT`, and `SplitAll` calls `Split()`.

**Code** (txgraph.cpp:1493–1499):
```cpp
for (auto i : m_linearization) {
    SetType new_parents;
    for (auto par : m_depgraph.GetReducedParents(i))
        new_parents.Set(remap[par].second);
    new_cluster->AddDependencies(new_parents, remap[i].second);
}
```

**Complexity breakdown**:

- `GetReducedParents(i)` iterates over `Ancestors(i)`. For a chain, the k-th transaction has
  k ancestors. Total iterations: 1 + 2 + ··· + N = **O(N²)**.
- Each `AddDependencies()` call updates the transitive closure in the new DepGraph: O(N) per
  call, N calls → **O(N²)**.

**Frequency**: Every block (~10 minutes) removes confirmed transactions, triggering Split on
affected chain clusters.

### 2. `Merge()` — construct DepGraph from scratch

**When**: `AddDependency` connects transactions in different clusters. `GroupClusters` identifies
which clusters must merge, then `Merge()` combines them into one.

**Code** (txgraph.cpp:1536–1542):
```cpp
other.ExtractTransactions(
    [&](DepGraphIndex pos, GraphIndex idx, FeePerWeight feerate) {
        auto new_pos = m_depgraph.AddTransaction(feerate);
        // ...
    },
    [&](DepGraphIndex pos, GraphIndex idx, SetType other_parents) {
        SetType parents;
        for (auto par : other_parents) parents.Set(remap[par]);
        m_depgraph.AddDependencies(parents, remap[pos]);
    });
```

**Complexity**: N transactions × O(N) per `AddDependencies` = **O(N²)**.

**Frequency**: Every new transaction that creates a dependency to an existing cluster triggers
potential merging.

### 3. `cluster->ApplyDependencies()` — update transitive closure for new edges

**When**: After `Merge()` places all transactions in a single cluster, the actual dependency
edges are applied.

**Code** (txgraph.cpp:1580–1583):
```cpp
// "this is O(N) in the size of the cluster, regardless of the
// number of parents being added"
m_depgraph.AddDependencies(parents, child_idx);
```

**Complexity**: Each call is O(N). With up to N unique children, worst case is **O(N²)**.

Inside `DepGraph::AddDependencies` (cluster_linearize.h:179–200):
```cpp
// For each new ancestor, add descendants of child
for (auto anc_of_par : par_anc) {
    entries[anc_of_par].descendants |= chl_des;   // O(1) per BitSet<64> OR
}
// For each descendant of child, add those ancestors
for (auto dec_of_chl : Descendants(child)) {
    entries[dec_of_chl].ancestors |= par_anc;      // O(1) per BitSet<64> OR
}
```

The individual BitSet operations are O(1) (single 64-bit OR), but the loop counts are O(N),
and the total across all edges is **O(N²)**.

---

## Visualizing the Coverage Gap

```
Entry point
  │
  ▼
  ApplyDependencies
  ├── SplitAll → Split()              O(N²) ← NOT optimized by TryLinearizeChain
  ├── Merge()                          O(N²) ← NOT optimized by TryLinearizeChain
  └── cluster->ApplyDependencies()     O(N²) ← NOT optimized by TryLinearizeChain
  │
  ▼
  Relinearize
  ├── Linearize → TryLinearizeChain    O(N)  ✓ optimized
  ├── PostLinearize                    skip  ✓ optimized (skipped for chains)
  └── Updated                          O(N)    always O(N)
```

`TryLinearizeChain` eliminates the O(N²) in `Relinearize()`, but the three O(N²) operations
in `ApplyDependencies` remain unaffected.

---

## Quantitative Perspective

For a chain cluster of size N, using BitSet<64> (where each BitSet operation is a single
64-bit OR instruction):

| Operation | Loop iterations | Per-iteration cost | Estimated time (N=11) |
|-----------|:-:|:-:|:-:|
| Split (GetReducedParents + AddDeps) | ~N² | ~1 cycle (BitSet OR) | ~50 ns |
| Merge (AddDependencies per tx) | ~N² | ~1 cycle (BitSet OR) | ~50 ns |
| SPF MakeTopological (without TryLinearizeChain) | ~N² | ~30 cycles (FeeFrac compare + swap) | ~2100 ns |
| TryLinearizeChain | ~N | ~3 cycles (popcount) | ~10 ns |

At N=11 (the average chain cluster size observed in real mempool data), the SPF O(N²) in
`Relinearize()` dominates — roughly 35–100× more expensive per iteration than the BitSet-based
O(N²) in Split/Merge. This is why `TryLinearizeChain` delivers a measurable 16.9× aggregate
speedup in replay benchmarks despite not addressing the other O(N²) operations.

However, this balance shifts as N grows. At N=25 (the largest common chain size):

| Operation | Estimated time (N=25) |
|-----------|:--------------------:|
| Split (GetReducedParents + AddDeps) | ~250 ns |
| Merge (AddDependencies per tx) | ~250 ns |
| SPF MakeTopological | ~18,750 ns |
| TryLinearizeChain | ~25 ns |

The non-Relinearize O(N²) grows quadratically with N. If `MAX_CLUSTER_COUNT` were increased
beyond 64 (requiring multi-word BitSets), the constant factor would also increase
significantly, as BitSet operations would no longer be single machine instructions.

---

## Root Cause: DepGraph's Transitive Closure

All three O(N²) bottlenecks share the same root cause: **DepGraph stores and maintains the
full transitive closure** of ancestor/descendant relationships.

For a chain of N transactions:

```
ancestors[0] = {0}
ancestors[1] = {0, 1}
ancestors[2] = {0, 1, 2}
...
ancestors[N-1] = {0, 1, ..., N-1}
```

Total information stored: N × (N+1) / 2 bits = O(N²), when the actual structural information
of a chain is just N−1 edges (O(N)).

Every operation that creates, destroys, or modifies a DepGraph must rebuild or update this
transitive closure, which is inherently O(N²).

---

## Toward a Complete Solution

`TryLinearizeChain` is an effective optimization for the linearization step, but it operates
within the constraint that chain clusters are still represented as `GenericClusterImpl` backed
by a full `DepGraph`.

A more comprehensive approach would be a dedicated `ChainClusterImpl` that stores a chain as a
simple ordered array of transactions — avoiding the DepGraph (and its O(N²) transitive closure)
entirely:

| Operation | GenericClusterImpl (DepGraph) | ChainClusterImpl (array) |
|-----------|:---:|:---:|
| Split | O(N²) | O(N) — array slice |
| Merge (two chains) | O(N²) | O(N) — array concatenation |
| ApplyDependencies | O(N²) | O(N) — update linear order |
| Relinearize | O(N) via TryLinearizeChain | O(N) — sort by ancestor feerate |
| Memory | O(N²) bits | O(N) |

This would follow the precedent of `SingletonClusterImpl`, which avoids `DepGraph` for
single-transaction clusters. When a chain cluster receives a dependency that breaks the chain
topology (creating a fork or diamond), it would be promoted to `GenericClusterImpl` — the same
pattern singletons use when they first receive a dependency.

---

## Related Articles

- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
- [Complexity Analysis of the SPF Algorithm on Chain Clusters](spf-chain-complexity.en.md)
- [Replay Benchmark: TryLinearizeChain on Real Mempool Data](chain-fast-path-replay-bench.en.md)
