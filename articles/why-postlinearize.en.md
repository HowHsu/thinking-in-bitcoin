# Why PostLinearize Is Needed After SPF

[中文版](why-postlinearize.zh.md)

---

## The Question

In Bitcoin Core's cluster linearization pipeline, `Linearize()` (the SPF algorithm) produces a
linearization, after which `PostLinearize()` is called as a post-processing step. Why not use
SPF's output directly? What goes wrong if we skip PostLinearize and compute chunks with
`ChunkLinearizationInfo` on SPF's raw output?

---

## ChunkLinearizationInfo vs. PostLinearize

Both use a forward-scan "merge adjacent violators" approach, but they handle the case where a
high-feerate element needs to pass a low-feerate element very differently.

### ChunkLinearizationInfo: unconditional merge

```cpp
// cluster_linearize.h — ChunkLinearizationInfoInfo
while (!ret.empty() && new_chunk.feerate >> ret.back().feerate) {
    new_chunk |= ret.back();   // unconditional merge
    ret.pop_back();
}
```

When a new transaction has higher feerate than the previous chunk, it **merges unconditionally**
without checking whether the two are related by a dependency. `ChunkLinearizationInfo` is a pure
feerate computation tool — given a linearization, it computes the corresponding chunk
decomposition. It does not modify the sequence and does not consult the dependency graph.

### PostLinearize: merge or swap

```
When new group C has higher feerate than the preceding group P:
  - C depends on P → merge: absorb P into C, forming a larger chunk
  - C does not depend on P → swap: move C ahead of P
```

PostLinearize inspects the dependency graph. For a high-feerate transaction that has no
dependency on the preceding group, it **swaps** (exchanges positions) rather than merging.
The swap moves the high-feerate transaction forward while keeping the two groups as separate
chunks.

---

## How ChunkLinearizationInfo Can Produce Disconnected Chunks

Consider this scenario:

```
Dependencies: A → C, B → C
(A and B are both parents of C, but A and B are unrelated)

Feerates: A = 1, B = 3, C = 10
```

SPF's `GetLinearization` must place both A and B before C (topological constraint). Sorting by
feerate, a possible output is: `[B, A, C]`.

Applying `ChunkLinearizationInfo` to this sequence:

```
Process B: stack = [{B, fee=3}]
Process A: fee(A)=1 < fee(B)=3, no merge
           stack = [{B, fee=3}, {A, fee=1}]
Process C: fee(C)=10 > fee(A)=1
           → unconditional merge with A: {A,C}, fee=11/2=5.5
           fee({A,C})=5.5 > fee(B)=3
           → unconditional merge with B: {B,A,C}, fee=14/3≈4.67
           stack = [{B,A,C, fee=4.67}]
```

Result: one chunk `{B, A, C}`. Here B and A have no direct dependency — they were pulled into
the same chunk by ChunkLinearizationInfo's unconditional merging. This particular example happens
to remain connected (A and B are both connected to C), but in more complex topologies:

```
Dependencies: A → D, B → C
(Two independent dependency chains within the same cluster)

SPF non-optimal output: [A, B, C, D]

Feerates: A = 1, B = 2, C = 3, D = 10
```

```
Process A: stack = [{A, fee=1}]
Process B: fee(B)=2 > fee(A)=1
           → unconditional merge: {A,B}, fee=3/2=1.5
           stack = [{A,B, fee=1.5}]
Process C: fee(C)=3 > fee({A,B})=1.5
           → unconditional merge: {A,B,C}, fee=6/3=2
           stack = [{A,B,C, fee=2}]
Process D: fee(D)=10 > fee({A,B,C})=2
           → unconditional merge: {A,B,C,D}, fee=16/4=4
           stack = [{A,B,C,D, fee=4}]
```

Chunk `{A, B, C, D}` contains two independent dependency chains (A→D and B→C). If there are
no dependency edges between A↔B or C↔D, this chunk is disconnected.

### How PostLinearize avoids this

Same input `[A, B, C, D]`, PostLinearize forward pass:

```
Process A: L = [{A, fee=1}]
Process B: fee(B)=2 > fee(A)=1, B and A unrelated → swap
           L = [{B, fee=2}, {A, fee=1}]
Process C: fee(C)=3 > fee(A)=1, C and A unrelated → swap
           L = [{B, fee=2}, {C, fee=3}, {A, fee=1}]
           fee(C)=3 > fee(B)=2, C depends on B → merge
           L = [{B,C, fee=5/2=2.5}, {A, fee=1}]
Process D: fee(D)=10 > fee(A)=1, D depends on A → merge
           L = [{B,C, fee=2.5}, {A,D, fee=11/2=5.5}]
           fee({A,D})=5.5 > fee({B,C})=2.5, check dependency...
```

Through swap operations, PostLinearize keeps unrelated transactions in separate groups,
merging only when a dependency exists. The result: every chunk is a connected subgraph in the
dependency graph.

PostLinearize's source comments state explicitly:

> One pass in either direction guarantees that the resulting chunks are connected.

---

## Why SPF Doesn't Guarantee Connectivity Directly

SPF's internal chunks **are** connected — they are built by merging along dependency edges.
The problem arises in two steps:

1. **`GetLinearization()` outputs a transaction sequence**, not the chunk structure.
   SPF's internal chunk information is discarded at output time.

2. **PostLinearize needs to modify the sequence afterwards**, making SPF's chunk boundaries
   stale. In particular, when SPF terminates early (iteration budget exhausted), its internal
   chunk decomposition is suboptimal. `GetLinearization` arranges chunks by topology then
   feerate, but because the chunk decomposition itself is imperfect, `ChunkLinearizationInfo`
   applied to the output may produce different chunk boundaries than SPF had internally.

SPF therefore outputs only the linearization sequence, delegating the connectivity guarantee
to PostLinearize — a layered design where SPF focuses on feerate-diagram optimisation and
PostLinearize handles structural repair.

---

## The Full Role of PostLinearize

In summary, PostLinearize serves three purposes after SPF:

| Purpose | Description |
|---------|-------------|
| **Guarantee chunk connectivity** | Uses swap (not unconditional merge) to ensure every chunk is a connected subgraph |
| **Improve non-optimal results** | When SPF terminates early, PostLinearize can further improve linearization quality |
| **Optimise sub-chunk ordering** | Even when SPF reports optimal, PostLinearize improves transaction ordering within chunks |

When SPF reports optimal, PostLinearize does not change the chunk decomposition (the feerate
diagram is already optimal) — it only improves sub-chunk order (transaction ordering within
each chunk).

### Sub-chunk ordering matters even when SPF is optimal

SPF reporting optimal means the feerate diagram is already the best possible — the chunk
decomposition and chunk ordering cannot be improved. But the **arrangement of transactions
within a chunk** does not affect the feerate diagram. For example, a chunk `{A, B, C}` has
the same total feerate whether it is internally ordered `[A, B, C]` or `[B, A, C]`, so the
feerate diagram is identical.

Yet sub-chunk ordering matters in practice:

1. **Determinism**: SPF uses random numbers internally (`rng_seed`), so the same cluster with
   different seeds can produce different intra-chunk orderings. PostLinearize is fully
   deterministic — its merge/swap passes converge the randomised output to a canonical form,
   reducing divergence between different nodes.

2. **Moved-tree property**: PostLinearize starts with a backward pass, which guarantees the
   "moved-tree property". This is a structural property beneficial for RBF replacement
   scenarios — when a leaf transaction is replaced by one with the same size but higher fee,
   running PostLinearize on the result is guaranteed to be no worse than before the
   replacement.

3. **Better local ordering**: Given an identical feerate diagram, PostLinearize tends to place
   higher-feerate transactions earlier within each chunk, providing a better starting point for
   incremental updates (e.g., when `Relinearize` passes in an `old_linearization`).

`GetLinearization` does sort transactions within each chunk by topology and feerate, but the
ordering rules it applies are a product of SPF's internal state. PostLinearize provides an
additional layer of deterministic sub-chunk optimisation that is independent of SPF internals.

---

## Why Chain Clusters Can Skip PostLinearize

`TryLinearizeChain` results can safely skip PostLinearize because:

1. **Unique topological order**: in a chain cluster, every transaction has a unique
   ancestor-set size ({1,...,N}), so the topological order is fully determined — no ordering
   ambiguity
2. **Connectivity is inherent**: every adjacent pair in a chain has a dependency, so any
   contiguous subsequence is connected
3. **ChunkLinearizationInfo equals PostLinearize**: there are no dependency-free adjacent pairs in
   a chain, so PostLinearize's swap branch never triggers — its merge behaviour is identical
   to ChunkLinearizationInfo

---

## Related Articles

- [PostLinearize Algorithm Analysis](post_linearize.md)
- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
