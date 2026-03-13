# ChainClusterImpl: Optimised Cluster for Chain-Shaped Topologies

[中文版](chain-cluster.zh.md)

---

## Motivation

TxGraph groups transactions into clusters based on dependencies. The generic implementation (`GenericClusterImpl`) uses a `DepGraph` plus `m_linearization` to represent arbitrary topologies. For **chain-shaped** clusters (A→B→C→D…), this is overkill: the topology is fixed, and the unique topological order is trivially the chain order.

Trace analysis on real mempool data shows that **~99% of clusters are chain-shaped** (see [Trace analysis](#trace-analysis) below). A dedicated `ChainClusterImpl` replaces the generic representation with a compact linear `m_txdata` vector and a backward-absorbing chunk algorithm, reducing memory and improving performance for the dominant case.

---

## Design Overview

### Cluster type hierarchy

| Type | Topology | Representation | Use case |
|------|----------|----------------|----------|
| `SingletonClusterImpl` | 1 tx | `m_graph_index`, `m_feerate` | Single transactions |
| `ChainClusterImpl` | A→B→C→… | `m_txdata` (chain order) | Linear chains |
| `GenericClusterImpl` | Arbitrary | `DepGraph` + `m_linearization` | Diamonds, forks, etc. |

`ChainClusterImpl` is intended for clusters of size ≥ 2. Chains of size 1 are `SingletonClusterImpl`.

### Key idea

For a chain, the linearization is always `[0, 1, …, n−1]` — no `m_linearization` vector needed. Chunk boundaries are computed by a backward-absorbing pass over feerates. Ancestors of position `i` are `[0..i]`; descendants are `[i..n−1]`. All of this is O(1) or O(n) without the DepGraph machinery.

---

## Implementation Details

### 1. m_maybe_chain flag

When grouping clusters for merge, `GroupData` records which clusters are to be merged. A `m_maybe_chain` flag on `GroupEntry` hints that the merged result might be chain-shaped (e.g. two chains connected by a single dependency). This allows `ApplyDependencies` to try the fast path before falling back to the generic merge.

### 2. ChainClusterImpl data structures

```cpp
struct TxData {
    GraphIndex graph_index;
    FeeFrac feerate;  // fee + size for chunk computation
};

std::vector<TxData> m_txdata;           // Transactions in chain order [root..tail]
std::vector<DepGraphIndex> m_split_segments;  // Sizes of contiguous segments after removals
```

- **m_txdata**: Stores `(graph_index, feerate)` in chain order. Index `i` is the i-th transaction from root to tail.
- **m_split_segments**: Populated by `ApplyRemovals` when middle transactions are removed. Each element is the size of a contiguous remaining segment. Used by `Split()` to partition correctly.

### 3. TryChainMerge

In `ApplyDependencies`, when merging clusters that might form a chain:

1. Check that the dependency graph of the merged set is a single chain (each tx has at most one parent and one child, except root and tail).
2. If so, create a `ChainClusterImpl` and append transactions in chain order.
3. If not (diamond, fork, etc.), fall back to `GenericClusterImpl` merge.

This is the **optimistic path**: we try the chain representation first; only on failure do we use the generic path.

### 4. ApplyRemovals and Split

**Problem**: Removing a middle transaction (e.g. B from A→B→C→D) breaks the chain. If we simply rebuild `m_txdata` as [A,C,D], we falsely imply C depends on A.

**Solution**: `ApplyRemovals` computes `m_split_segments` before rebuilding:

1. Mark removed positions in a `removed[]` array.
2. Scan remaining positions to get contiguous segment sizes, e.g. [1, 2] for segments {A} and {C,D}.
3. Rebuild `m_txdata` without removed entries.
4. Set quality to `NEEDS_SPLIT`.

`Split()` then uses `m_split_segments`:

- If one segment and size ≥ 2: keep cluster, clear segments, set OPTIMAL.
- Otherwise: for each segment, create a new cluster — `ChainClusterImpl` if size ≥ 2, `SingletonClusterImpl` if size 1.

### 5. ComputeChunks

The backward-absorbing chunk algorithm is centralised in `ComputeChunks()`:

```cpp
for (DepGraphIndex i = 0; i < m_txdata.size(); ++i) {
    ChunkBound c{i, i+1, m_txdata[i].feerate};
    while (!chunks.empty() && c.feerate >> chunks.back().feerate) {
        c.feerate += chunks.back().feerate;
        c.start = chunks.back().start;
        chunks.pop_back();
    }
    chunks.push_back(c);
}
```

Used by `Updated()`, `AppendTrimData()`, and `SanityCheck()`.

### 6. SetFee and Relinearize (bug fix)

**Bug**: When `SetTransactionFee` is called on a transaction whose sibling's Ref has been destroyed (pending `ApplyRemovals`), `ChainClusterImpl::Updated()` dereferences `m_ref` in chunk computation and crashes.

**Fix**: Mirror `GenericClusterImpl::SetFee` — on fee change, downgrade quality to `NEEDS_RELINEARIZE` so chunk computation is skipped. `Relinearize()` then promotes back to OPTIMAL and recomputes chunks after removals are processed. For chains, `Relinearize()` is a no-op on the topology; it only updates quality and chunk data.

---

## Trace Analysis

Trace analysis on real signet data (`analyze_trace.py`) shows:

- **Peak state**: 27,512 transactions, 8,596 clusters. **99.1%** of clusters are chain-shaped.
- **Final state**: 6,079 transactions, 4,665 clusters. **98.9%** chain-shaped.
- **Excluding size=1**: At peak, 23.8% of multi-tx transactions are in chains; at final, 42.2%.

Clusters with size > 64 appear because the trace captures state at discrete snapshots; Split is triggered lazily by DoWork. Peak state is the snapshot with the most transactions, not necessarily the most chain clusters.

---

## Performance

### Trace replay comparison

Same trace replayed on baseline vs ChainCluster branch. Parameters: `max_cluster_count=64`, `max_cluster_size=404000`, `acceptable_cost=75000`.

| Entry point | Baseline (μs) | ChainCluster (μs) |
|-------------|---------------|-------------------|
| DoWork | 3,595,581 | 1,297,506 |
| **TOTAL** | **3,682,890** | **1,444,911** |

ChainCluster achieves ~2.5× speedup overall, driven primarily by DoWork.

### Reproducibility

- Trace and replay code: [`github.com/HowHsu/bitcoin`](https://github.com/HowHsu/bitcoin) branch `before_chaincluster`
- Usage guide: [TxGraph Trace & Replay](https://howhsu.github.io/thinking-in-bitcoin/articles/txgraph-trace-replay.en.html)

---

## Fuzz Testing

| Check | Result |
|-------|--------|
| Crash files | None |
| Workers | All 8 finished |
| Total runs | ~39.6M |
| Duration | 10 h |

Environment: Debian 12, Clang 20.1.8, libFuzzer, AddressSanitizer, UndefinedBehaviorSanitizer.

---

## Related

- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md) — `TryLinearizeChain` within GenericCluster (different optimisation layer)
- [TxGraph Trace & Replay](txgraph-trace-replay.en.md) — Tooling for reproducible performance comparison
