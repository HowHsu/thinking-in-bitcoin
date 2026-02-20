# Complexity Analysis of the SPF Algorithm on Chain Clusters

[中文版](spf-chain-complexity.zh.md)

## Background

Bitcoin Core's `Linearize()` function uses the Spanning Forest (SPF) algorithm to linearize
transaction clusters. The algorithm proceeds through the following phases:

```
SpanningForestState forest(depgraph, rng_seed);
forest.MakeTopological();
forest.StartOptimizing();
while (forest.OptimizeStep()) {}
forest.StartMinimizing();
while (forest.MinimizeStep()) {}
```

This article analyses the per-phase complexity of SPF on a **chain cluster**
(tx_0 → tx_1 → ... → tx_{N-1}) under two extreme feerate distributions:

- **Increasing feerates**: fee_i = i+1 (fee_0=1, fee_1=2, ..., fee_{N-1}=N)
- **Decreasing feerates**: fee_i = N-i (fee_0=N, fee_1=N-1, ..., fee_{N-1}=1)

---

## Phase-by-Phase Analysis

### 1. `SpanningForestState` (constructor)

`GetReducedParents()` is called for each transaction and N-1 dependency entries are created.
In a chain each transaction has at most one direct parent.

**Both distributions: O(N).**

---

### 2. `MakeTopological()`

Initial state: N single-transaction chunks, all dependencies inactive.

The algorithm pops a chunk from the queue and attempts merges in two directions:

- **DownWard merge**: find the highest-feerate child chunk; if its feerate ≥ the current
  chunk's feerate, merge (a child with a higher feerate would need to precede its parent in
  the optimal order, violating the dependency constraint).
- **UpWard merge**: find the lowest-feerate parent chunk; if its feerate ≤ the current
  chunk's feerate, merge (a parent with a lower feerate would be ordered after its child,
  again violating the dependency constraint).

#### Increasing feerates (fee_i = i+1)

tx_{i+1} has a strictly higher feerate than tx_i, triggering a DownWard merge; tx_{i-1} has
a strictly lower feerate, triggering an UpWard merge. Either direction causes a
**full-chain cascade**:

```
{tx_0:1} + {tx_1:2} → {0,1 : 1.5}
{0,1:1.5} + {tx_2:3} → {0,1,2 : 2}
...
→ entire chain merged into one chunk
```

At merge step k, `Activate()` calls `UpdateChunk()`, which iterates over all k transactions
in the current chunk (each has exactly one dependency). Total iterations across all merges:

$$\sum_{k=1}^{N-1} k = \frac{N(N-1)}{2} = O(N^2)$$

**Complexity: O(N²)** (with a very small constant — each iteration is a single bitset
operation on a 64-bit integer).

#### Decreasing feerates (fee_i = N-i)

tx_i's parent has a strictly higher feerate (already in correct order) and its child has a
strictly lower feerate (also in correct order). Neither merge condition is triggered —
**no merges occur at all**.

**Complexity: O(N)** (N dequeues, each immediately discarded).

---

### 3. `StartOptimizing()` + `OptimizeStep()`

`OptimizeStep` searches every active dependency of each chunk for one satisfying
`top_setinfo.feerate > chunk.feerate`. If found, `Improve()` is called (Deactivate +
two MergeSequence passes) to split and reassemble the chunk.

#### Increasing feerates

After `MakeTopological` the state is **one big chunk** with N-1 active dependencies.

For dep_i (parent=tx_i, child=tx_{i+1}), `top_setinfo` = {tx_0,...,tx_i} with
feerate = (i+2)/2.

Comparing (i+2)/2 with the chunk feerate (N+1)/2: this reduces to i+2 vs N+1.
For all i ≤ N-2 we have i+2 ≤ N < N+1 — a strict inequality — so **no dependency
qualifies**. `OptimizeStep` scans all N-1 active deps and returns immediately.

**Complexity: O(N)** (single scan, no improvements).

#### Decreasing feerates

After `MakeTopological` the state is **N independent chunks**, all dependencies inactive.
The inner loop skips every dep with `if (!dep_data.active) continue`.

**Complexity: O(N)** (N dequeues, inner loop is a no-op).

---

### 4. `StartMinimizing()` + `MinimizeStep()`

`MinimizeStep` searches every active dependency of each chunk for one satisfying
`top_setinfo.feerate == chunk.feerate`, attempting to split the chunk into smaller
equal-feerate pieces.

#### Increasing feerates

One big chunk, N-1 active deps. For dep_i: top feerate = (i+2)/2, chunk feerate = (N+1)/2.
These are never equal (strict inequality). `have_any = false`, so the chunk is immediately
declared minimal.

**Complexity: O(N)** (single scan, minimal confirmed at once).

#### Decreasing feerates

N independent chunks, all deps inactive. The inner loop is a no-op for every chunk.
After N `MinimizeStep` calls the queue is empty.

**Complexity: O(N)**.

---

## Summary

| Phase | Increasing feerates (worst case) | Decreasing feerates (best case) |
|-------|----------------------------------|--------------------------------|
| Constructor | O(N) | O(N) |
| MakeTopological | **O(N²)** (cascade merges entire chain) | O(N) (no merges) |
| OptimizeStep | O(N) (single scan, no improvements) | O(N) (no active deps) |
| MinimizeStep | O(N) (strict inequality, single scan) | O(N) (no active deps) |
| **Total** | **O(N²)** | **O(N)** |

Both distributions yield an **optimal linearization**:
- Increasing feerates: the entire chain forms a single chunk (feerate = (N+1)/2),
  which cannot be split further.
- Decreasing feerates: each transaction is its own chunk, already ordered by
  decreasing feerate.

---

## Why Is the O(N²) Behaviour Invisible for N≤63?

### Measured data (before optimization, increasing-feerate chain)

| N | Total instructions | Instructions/tx |
|---|-------------------|----------------|
| 9 | 13,855 | 1,539 |
| 16 | 27,053 | 1,691 |
| 32 | 58,665 | 1,833 |
| 48 | 95,629 | 1,992 |
| 63 | 133,221 | 2,115 |

The instruction count grows slowly — roughly O(N^1.1) rather than O(N²).

### Model

Split the total instruction count into an O(N²) term (MakeTopological cascade) and an O(N)
term (constructor, `GetLinearization`, function-call overhead, etc.):

$$\text{total}(N) = C \cdot \frac{N^2}{2} + D \cdot N$$

Solving with the measured values at N=9 and N=63:

$$C \approx 21 \text{ ins/merge-iter}, \quad D \approx 1443 \text{ ins/tx}$$

Breakdown by N:

| N | O(N²) contribution | O(N) contribution | O(N²) share |
|---|-------------------|------------------|------------|
| 9 | 862 | 12,987 | **6%** |
| 63 | 41,622 | 90,909 | **31%** |
| 200 (projected) | 420,000 | 288,600 | **59%** |

### Conclusion

The O(N²) cascade in `MakeTopological` is real, but each iteration costs only ~**21
instructions** — pure bitset arithmetic, where every `BitSet<64>` operation compiles to a
single 64-bit hardware instruction. In contrast, the per-transaction O(N) overhead
(constructor, `GetReducedParents`, topological sort inside `GetLinearization`, etc.) costs
~**1443 instructions per tx**, dwarfing the O(N²) coefficient.

Over the tested range N=9..63, the O(N) overhead dominates (6–31% O(N²) share), making
the total appear nearly linear. The O(N²) term becomes the dominant factor around
**N ≈ 150**, where both contributions are roughly equal.

---

## Effect of the `TryLinearizeChain` Optimisation

See [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md) for the
algorithm description and benchmark results.
