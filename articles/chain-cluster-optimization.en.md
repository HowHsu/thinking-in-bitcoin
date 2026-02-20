# O(N) Fast Path for Chain-Shaped Clusters

[中文版](chain-cluster-optimization.zh.md)

---

## Background

The SPF algorithm used by `Linearize()` exhibits **O(N²)** behaviour on chain-shaped clusters
with strictly increasing feerates (see
[Complexity Analysis of the SPF Algorithm on Chain Clusters](spf-chain-complexity.en.md)).
This article describes `TryLinearizeChain`, the O(N) fast path that bypasses SPF entirely for
chain-shaped clusters, and presents measured benchmark results.

Instrumentation added to a running node revealed that **virtually every cluster seen in the
mempool is chain-shaped** — each transaction spends an output of the previous one. This is
consistent with widespread use of CPFP (Child-Pays-For-Parent) fee bumping, where a
low-feerate parent is paired with a high-feerate child spending one of its outputs, forming a
two-transaction chain; longer chains arise from repeated CPFP stacking or batch-payment
patterns. Because chain clusters dominate in practice, a topology-specific O(N) fast path has
disproportionately large impact.

---

## Algorithm

### Detection

A cluster with N transactions is a chain if and only if its ancestor-set sizes form exactly
{1, 2, ..., N}. This is checked in O(N) using a boolean presence array;
`Ancestors(i).Count()` is O(1) for the fixed-size `BitSet<64>` used here.

### Topological order

The node with k ancestors occupies position k−1 in the unique topological order. Recovered
in O(N) by a single mapping pass.

### Optimal chunking (stack-based forward merge pass)

For a chain every consecutive pair has a dependency, so the standard "merge adjacent
violators" algorithm never needs to swap — only merges occur. Each transaction is pushed onto
and popped from the stack at most once, giving O(N) total work. This is equivalent to one
forward pass of `PostLinearize`, which is already guaranteed optimal for graphs where every
transaction has at most one parent (and a chain satisfies both tree-shape conditions).

### Integration into `Linearize()`

`TryLinearizeChain` runs as an early exit inside `Linearize()`, before the SPF machinery is
initialised:

```cpp
// In Linearize():
if (auto chain_lin = TryLinearizeChain(depgraph); !chain_lin.empty()) {
    return {std::move(chain_lin), /*optimal=*/true, /*cost=*/0, /*is_chain=*/true};
}
// General path: SPF algorithm (LinearizeSPF).
```

The fourth return value (`is_chain`) signals to the caller (`Relinearize()`) that the result
is already optimal and connected — `PostLinearize()` can be skipped entirely.

---

## Benchmark: `LinearizeOptimallyMonotoneChainTotal`

`MakeMonotoneChainGraph` builds a chain with fee_i = i+1, size_i = 1 — the worst-case
feerate distribution for SPF. Without `TryLinearizeChain`, `MakeTopological` triggers an
O(N²) cascade of merges; with it the result is produced in O(N).

Each benchmark op is one complete call to `Linearize()` for a chain cluster of the given
size.

| Size | Before (ns/op) | After (ns/op) | Speedup |
|------|---------------|--------------|---------|
| 9 tx | 973 | 85 | **12×** |
| 16 tx | 1,772 | 126 | **14×** |
| 32 tx | 3,880 | 237 | **16×** |
| 48 tx | 6,205 | 341 | **18×** |
| 63 tx | 8,551 | 443 | **19×** |

Instructions per call after optimisation:

| N | Total instructions | Instructions/tx |
|---|-------------------|----------------|
| 9 | 1,725 | 192 |
| 16 | 2,502 | 156 |
| 32 | 4,285 | 134 |
| 48 | 6,222 | 130 |
| 63 | 8,049 | 128 |

Instructions per tx decrease slightly as N grows (the constant converges), confirming O(N)
behaviour. The speedup grows with N because the baseline trends towards O(N²) while the
optimised path remains O(N).
