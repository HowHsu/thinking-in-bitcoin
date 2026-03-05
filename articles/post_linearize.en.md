# PostLinearize Algorithm Analysis

[中文](post_linearize.md)

## Role of the Algorithm

`PostLinearize` is a post-processing step for cluster linearization, running after `Linearize` (SpanningForestState). The two have different responsibilities:

- **Linearize**: Randomized iterative optimization that seeks the best possible ordering within a limited budget, but the output may have disconnected chunks and is affected by random seeds
- **PostLinearize**: Deterministic post-processing that fixes the above issues, guaranteeing the result never gets worse

Code location: `src/cluster_linearize.h:1585`, called from `GenericClusterImpl::Relinearize` (`src/txgraph.cpp:2069`).

## Why PostLinearize Is Needed

### 1. Guaranteeing Connected Chunks

The output of Linearize (SpanningForestState) does not guarantee that each chunk is a connected subgraph, especially when it exits early after exhausting its budget. PostLinearize ensures connectivity within each chunk through merge/swap operations. This is a key invariant for TxGraph — `BlockBuilder` needs each chunk to be connected when building blocks by chunk.

### 2. Eliminating Randomness

Linearize heavily uses random numbers internally (for processing order, merge direction, tiebreaking, etc.), and different `rng_seed` values produce different linearizations for the same cluster. PostLinearize is entirely deterministic (no random numbers), converging the randomized output to a more canonical form and reducing divergence between different nodes.

### 3. Safety Guarantees for RBF Replacement

PostLinearize guarantees that if a leaf transaction is replaced by one with the same size but higher fee, running PostLinearize on the result will not be worse than before the replacement (comments at lines 1578-1582). This allows RBF replacement to take the lightweight "drop + append + PostLinearize" path without running a full Linearize.

## Algorithm Principles

### Overall Structure

Two passes are performed:

- **Pass 0 (backward)**: Scans back to front, guarantees optimality for trees where each tx has at most one parent
- **Pass 1 (forward)**: Scans front to back, guarantees optimality for trees where each tx has at most one child

Neither pass ever makes the result worse.

### Forward Pass Detailed Steps

Maintains a group linked list L, where each group is a set of consecutive transactions with a combined feerate.

For each transaction i in the input linearization, from front to back:

1. Create a new group `C = {i}`, append to end of L
2. Look at the group P before C:
   - If `feerate(C) > feerate(P)`:
     - **C and P have a dependency** → **merge**: absorb P into C, C grows larger, continue looking forward
     - **C and P have no dependency** → **swap**: swap C and P's positions, C moves forward, continue looking forward
   - If `feerate(C) <= feerate(P)` → stop

### Backward Pass

Completely symmetric: scans back to front, `deps` uses descendants instead of ancestors, feerate is negated, and the output is reversed.

### Complete Example

4 transactions, dependency: `A→B` (A is B's parent), C and D are independent.

feerates: `A=2, B=10, C=1, D=8`

Input linearization: `[A, B, C, D]`

**Forward pass:**

```
Process A: L = [{A, fee=2}]

Process B: L = [{A, fee=2}, {B, fee=10}]
  B(10) > A(2)? Yes
  B and A have dependency? Yes (B's ancestors include A) → merge
  L = [{A,B, fee=12/2=6}]
  No more groups ahead → stop

Process C: L = [{A,B, fee=6}, {C, fee=1}]
  C(1) > AB(6)? No → stop

Process D: L = [{A,B, fee=6}, {C, fee=1}, {D, fee=8}]
  D(8) > C(1)? Yes, D and C have no dependency → swap
  L = [{A,B, fee=6}, {D, fee=8}, {C, fee=1}]
  D(8) > AB(6)? Yes, D and AB have no dependency → swap
  L = [{D, fee=8}, {A,B, fee=6}, {C, fee=1}]
  Front is sentinel → stop
```

Output: `[D, A, B, C]`, chunks are `{D:8}, {A,B:6}, {C:1}`, feerates in decreasing order.

### Meaning of Merge and Swap

| Operation | Condition | Effect |
|-----------|-----------|--------|
| **Merge** | C has higher feerate than P **and** there is a dependency | P must be before C (topological constraint), so combine them into one chunk |
| **Swap** | C has higher feerate than P **and** no dependency | C can safely move before P (no topological violation), placing the higher feerate group earlier |

The essence of merge: when P and C have a dependency, they cannot be swapped (would violate topological order), but since C has higher feerate than P, it's more profitable to mine P together with C, so they are merged into one chunk.

The essence of swap: no dependency, C has higher feerate, C should be placed before P to be mined earlier.

## Why It Is Optimal for Tree-Shaped Graphs

PostLinearize's comments state: for tree-shaped graphs (each tx has at most one child, or each tx has at most one parent), the result is optimal.

### Potential Issues on General DAGs

PostLinearize cannot split groups. When C is forced to merge P (due to a dependency), all txs in P are absorbed. If some txs in P are not necessary dependencies of C but were "dragged in" by earlier merges, this merge may not be optimal.

### Why Tree-Shaped Graphs Avoid This Problem

In a tree where each tx has at most one child: when C is forced to merge P, all txs in P "serve only the single path to C." Since each tx has only one child, there is no situation where "some tx in P is simultaneously needed by two different descendant paths." So merging all of P into C incurs no loss.

From a scheduling theory perspective, the forward pass on a tree where each tx has at most one child is equivalent to the classic **Sidney decomposition algorithm** (1975), which has been proven optimal for single-machine scheduling with tree-shaped precedence constraints. Correctness is based on an exchange argument: under tree constraints, local greedy decisions (merge/swap) do not prevent global optimality.

The two passes each cover one tree direction, together guaranteeing optimality for all tree-shaped graphs.

### On General DAGs

The comments only claim optimality for tree-shaped cases (sufficient condition). Brute-force search over 5-6 node DAGs found no counterexamples where PostLinearize is suboptimal, but theoretically suboptimality cannot be ruled out for larger, more complex DAGs.

## Time Complexity

**O(N²)**, where N is the number of transactions in the cluster.

Analysis:
- **Merge operations**: Each tx can be merged at most once (the group disappears after merging), so total merges ≤ N
- **Swap operations**: Swap does not eliminate a group, only swaps positions. A tx's group can repeatedly swap with preceding groups, advancing one step each time. A single tx can swap at most O(N) times (from last to first), so total swaps across N txs are at worst O(N²)

Total complexity across 2 passes remains O(N²).

## Data Structures

The implementation uses a circular linked list composed of singly-linked lists:

- Within each group: txs are linked by `prev_tx` (tail→head)
- Between groups: linked by `prev_group` into a circular linked list with a sentinel node at the head
- Each group's tail tx stores: `first_tx`, `prev_group`, `group` (BitSet), `deps` (ancestors/descendants BitSet), `feerate`

The backward pass reuses the same code by negating the fee and reversing parent/child semantics.
