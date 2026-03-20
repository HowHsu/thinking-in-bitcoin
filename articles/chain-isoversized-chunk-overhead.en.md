# ChainClusterImpl's IsOversized Overhead: The Chunk Computation Timing Problem

[中文版](chain-isoversized-chunk-overhead.zh.md)

---

## The Problem

In replay tests using a 4.5-day mainnet trace, ChainClusterImpl makes `DoWork` 2.8× faster,
but `IsOversized` becomes 2.1× slower. The overall result is still 2.5× faster, but the
IsOversized regression warrants investigation.

---

## Methodology

A 4.5-day mainnet trace (71M operations) was replayed using `txgraph-replay` with
instrumentation inside `IsOversized` to measure time spent in `ApplyRemovals`,
`GroupClusters`, `SplitAll`, and individual `cluster->Split()` calls.

Two branches were compared:
- **before\_chaincluster**: all clusters use `GenericClusterImpl`
- **chaincluster**: chain-shaped clusters use `ChainClusterImpl`

---

## Data

### Entry Point Timing

| Entry point | before\_chaincluster | chaincluster | Ratio |
|-------------|----:|----:|----:|
| DoWork | 3,607,902 us | 1,295,222 us | **2.8× faster** |
| IsOversized | 65,717 us | 140,463 us | **2.1× slower** |
| Diagrams | 35,752 us | 16,146 us | 2.2× faster |
| **TOTAL** | **3,729,140 us** | **1,474,694 us** | **2.5× faster** |

### IsOversized Breakdown

| | before\_chaincluster | chaincluster |
|---|----:|----:|
| cached returns | 1,099,671 calls | 1,099,671 calls |
| ApplyRemovals | 226 us | 417 us |
| GroupClusters | 34,806 us | 111,926 us |
| 　└─ SplitAll | 9,408 us | 91,798 us |

### SplitAll (first step of GroupClusters): cluster→Split by Type

**before\_chaincluster** (no ChainClusterImpl):

| Type | Calls | Total | Per call |
|------|------:|------:|------:|
| generic | 155,174 | 4,188 us | **27 ns** |
| singleton | 89,721 | 5 us | ~0 ns |

**chaincluster** (with ChainClusterImpl):

| Type | Calls | Total | Per call |
|------|------:|------:|------:|
| generic | 4,298 | 502 us | 117 ns |
| **chain** | **146,765** | **85,246 us** | **581 ns** |
| singleton | 93,832 | 0 us | ~0 ns |

Within the chain total of 85,246 us, the **early\_exit path accounts for 77,753 us**
(123,392 calls, 630 ns/call).

---

## Root Cause

The difference stems from the `QualityLevel` that `Split` assigns, which determines whether
`Updated()` performs chunk computation.

### QualityLevel and Updated()

`Updated()` has two phases:

1. **Locator rebuild** — iterate all transactions, call `SetPresent` and `ClearChunkData`.
   O(n).
2. **Chunk computation** — compute chunk feerates and write them into entries. O(n).
   **Only executed when `IsAcceptable()` returns true** (quality is `OPTIMAL` or
   `ACCEPTABLE`).

```
enum class QualityLevel {
    OVERSIZED_SINGLETON,
    NEEDS_SPLIT_FIX,
    NEEDS_SPLIT,
    NEEDS_FIX,
    NEEDS_RELINEARIZE,   ← not Acceptable
    ACCEPTABLE,          ← Acceptable → triggers chunk computation
    OPTIMAL,             ← Acceptable → triggers chunk computation
    NONE,
};
```

### GenericClusterImpl Path

```
ApplyRemovals:
  set NEEDS_SPLIT
  Updated(): locator O(n) ✓, chunks skipped (NEEDS_SPLIT is not Acceptable)

Split (single connected component, no holes):
  set NEEDS_RELINEARIZE
  Updated(): locator O(n) ✓, chunks skipped (NEEDS_RELINEARIZE is not Acceptable)

→ Split cost: 2 × O(n) locator only = 27 ns/call
→ Chunks computed later in DoWork (re-linearize → set ACCEPTABLE → Updated() does chunks)
```

### ChainClusterImpl Path

```
ApplyRemovals:
  set NEEDS_SPLIT
  Updated(): locator O(n) ✓, chunks skipped

Split early_exit (single chain segment, size ≥ 2):
  set OPTIMAL                         ← jumps straight to highest quality
  Updated(): locator O(n) ✓, chunks O(n) ✓  ← chunk computation triggered!

→ Split cost: 2 × O(n) locator + 1 × O(n) chunks = 630 ns/call
→ DoWork has nothing to do (already OPTIMAL)
```

### Why Does ChainCluster Set OPTIMAL?

A chain's linearization is inherently optimal — following the chain order yields the optimal
transaction ordering. No re-linearization is needed, so Split can jump directly to `OPTIMAL`.

GenericClusterImpl cannot do this — after removing transactions, the linearization may no
longer be optimal, so it must set `NEEDS_RELINEARIZE` and defer re-linearization to DoWork.

### Same Total Work, Different Timing

| | Generic | Chain |
|---|---|---|
| Work in Split | locator only (cheap) | **locator + chunks (expensive)** |
| Work in DoWork | **re-linearize + chunks (expensive)** | nothing |
| IsOversized time | 65,717 us | 140,463 us |
| DoWork time | 3,607,902 us | 1,295,222 us |
| **Combined** | **3,673,619 us** | **1,435,685 us** |

ChainClusterImpl **moves** chunk computation from DoWork into Split. From DoWork's
perspective this is a 2.8× speedup; from IsOversized's perspective it's a 2.1× regression.
The net effect is a 2.6× improvement overall.

---

## The Core Issue

IsOversized **does not need chunks at all**. It only needs to determine whether any merged
cluster group exceeds the size limit. But because Split sets `OPTIMAL`, `Updated()` computes
chunks as a side effect.

The 77,753 us (123,392 calls × 630 ns) is entirely spent on chunk computation that
IsOversized never uses — triggered only because the `OPTIMAL` quality level implies
`IsAcceptable() = true`.

### Not Just IsOversized

Any entry point that goes through `GroupClusters → SplitAll → Split` triggers the same
overhead. `CountDistinctClusters` is another example:

```
CountDistinctClusters
  → ApplyDependencies
    → GroupClusters
      → SplitAll
        → ChainClusterImpl::Split → OPTIMAL → Updated() does chunk computation
```

| Entry point | before\_chaincluster | chaincluster | Ratio |
|-------------|----:|----:|----:|
| IsOversized | 65,717 us | 140,463 us | 2.1× slower |
| CountDistinctClusters | 575 us | 2,474 us | 4.3× slower |

The 4.3× regression in `CountDistinctClusters` is worse than IsOversized's 2.1× because
IsOversized has a large number of cached returns (1,099,671 calls) that dilute the average
overhead, while every `CountDistinctClusters` call walks the full path.

### Potential Optimization Direction

If Split could avoid triggering chunk computation on these code paths — for example by
introducing an intermediate quality state meaning "linearization is correct, but chunks
are not yet computed" — the per-call cost of chain Split would drop from 630 ns to near
generic's 27 ns, eliminating these entry points as regression points for ChainClusterImpl.

---

## Appendix: Full Profiling Output

### before\_chaincluster

```
Timed entry points:
  Entry point                         Calls     Total (us)       Avg (us)
  ---                                   ---            ---            ---
  DoWork                            1764078        3607902           2.05
  CompareMainOrder                 54285313           1391           0.00
  GetAncestors                        38823              1           0.00
  GetDescendants                      16761           8509           0.51
  CountDistinctClusters               16636            603           0.04
  GetMainMemoryUsage                3553929            130           0.00
  GetMainStagingDiagrams              31129          35752           1.15
  IsOversized                       1857503          65717           0.04
  StartStaging                      1764603             86           0.00
  AbortStaging                         1087              0           0.00
  CommitStaging                     1763516           9049           0.01
                                        ---            ---
  TOTAL                            65093378        3729140

[IsOversized profile] cached returns: 1099671 calls
[IsOversized profile] ApplyRemovals: total=226 us, calls=757832
[IsOversized profile] GroupClusters: total=34806 us, calls=757832
[IsOversized profile] GroupClusters breakdown:
  SplitAll:    total=9408 us, calls=757832
    ApplyRemovals (top-level):       total=11 us
    ApplyRemovals (per-cluster):     total=3 us
    cluster->Split:    generic=4188 us (155174 calls), singleton=5 us (89721 calls)
  build_an:    total=68 us, calls=757832
  union_find:  total=32 us, calls=757832
  translate:   total=54 us, calls=757832
  compact:     total=16 us, calls=757832
```

### chaincluster

```
Timed entry points:
  Entry point                         Calls     Total (us)       Avg (us)
  ---                                   ---            ---            ---
  DoWork                            1764078        1295222           0.73
  CompareMainOrder                 54285313           1441           0.00
  GetAncestors                        38823              1           0.00
  GetDescendants                      16761           8333           0.50
  CountDistinctClusters               16636           2520           0.15
  GetMainMemoryUsage                3553929            181           0.00
  GetMainStagingDiagrams              31129          16146           0.52
  IsOversized                       1857503         140463           0.08
  StartStaging                      1764603             85           0.00
  AbortStaging                         1087              1           0.00
  CommitStaging                     1763516          10301           0.01
                                        ---            ---
  TOTAL                            65093378        1474694

[IsOversized profile] cached returns: 1099671 calls
[IsOversized profile] ApplyRemovals: total=417 us, calls=757832
[IsOversized profile] GroupClusters: total=111926 us, calls=757832
[IsOversized profile] GroupClusters breakdown:
  SplitAll:    total=91798 us, calls=757832
    ApplyRemovals (top-level):       total=7 us
    ApplyRemovals (per-cluster):     total=8 us
    cluster->Split:    generic=502 us (4298 calls), chain=85246 us (146765 calls),
                       singleton=0 us (93832 calls)
    ChainClusterImpl::Split breakdown:
      early_exit=77753 us (123392 calls), append=0 us, insert=0 us,
      updated=3910 us, compact=11 us, other=0 us, singleton_seg=1 us
  build_an:    total=182 us, calls=757832
  union_find:  total=36 us, calls=757832
  translate:   total=60 us, calls=757832
  compact:     total=11 us, calls=757832
```
