# Part 4: Clustering and Linearization — Core Algorithms

[中文](mempool-txgraph-04-linearization.zh.md)

> This article is Part 4 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 3: TxGraphImpl Data Structures](mempool-txgraph-03-impl-data.en.md) | Next: [Part 5: Staging — The Dual-Graph System](mempool-txgraph-05-staging.en.md)

---

## Focus

- Core files: `src/cluster_linearize.h`, `src/txgraph.cpp` (algorithm sections)
- Key classes/functions: DepGraph, Linearize, PostLinearize, QualityLevel, ApplyDependencies, MakeAcceptable, DoWork
- Prerequisites: Part 3

---

## Overview

Clustering and linearization are the algorithmic core of TxGraph. When transactions have dependency
relationships, they are grouped into the same cluster. Each cluster must be internally linearized—
determining a transaction ordering that optimizes the feerate diagram as much as possible.

This article dives into the DepGraph template class design and the algorithm flows within TxGraphImpl.

## 1. DepGraph Template Class

(Will explain `src/cluster_linearize.h:29-357`)

- `DepGraph<SetType>` template parameter: BitSet type
- Entry internal structure (:33-49): `feerate`, `ancestors`, `descendants`
- BitSet representation of ancestors/descendants
- Interfaces for adding transactions and dependencies

## 2. SetInfo Helper Structure

(Will explain `src/cluster_linearize.h:360-427`)

- `SetInfo<SetType>`: transaction set + aggregate feerate
- Used for chunk computation and linearization decisions

## 3. Linearize Function

(Will explain `src/cluster_linearize.h:1798-1805`)

- Input: DepGraph + existing linearization (optional)
- Output: new linearization + remaining work
- Algorithm strategy: branch-and-bound
- Iteration limit (cost budget)

## 4. PostLinearize Function

(Will explain `src/cluster_linearize.h:1854-1855`)

- Purpose of PostLinearize: refine an existing linearization
- Relationship with Linearize: coarse sort first, then fine-tune
- Reference: [PostLinearize Algorithm Explained](post_linearize.en.md)

## 5. QualityLevel State Machine

(Will explain `src/txgraph.cpp:38-58`)

- State definitions:
  - `OVERSIZED_SINGLETON`: oversized single transaction
  - `NEEDS_SPLIT_FIX` / `NEEDS_SPLIT`: needs splitting
  - `NEEDS_FIX` / `NEEDS_RELINEARIZE`: needs re-linearization
  - `ACCEPTABLE`: acceptable but possibly non-optimal
  - `OPTIMAL`: optimal achieved
  - `NONE`: invalid state
- State transition triggers

## 6. ApplyDependencies

(Will explain `src/txgraph.cpp:2114`)

- Applying dependencies from the pending queue to clusters
- Cluster merging caused by dependency propagation
- Collaboration with GroupClusters (:1856) and Merge (:2068)

## 7. MakeAcceptable and Linearization Triggers

(Will explain `src/txgraph.cpp:2207-2215`)

- `MakeAcceptable` (:2207): bring a single cluster to ACCEPTABLE quality
- `MakeAllAcceptable` (:2215): bring all clusters to ACCEPTABLE
- Split (:1829) / SplitAll (:1841): split disconnected clusters

## 8. Chunk Definition and ChunkIndex

(Will explain the role of chunks in linearization results)

- Chunk: a contiguous segment with monotonically decreasing feerate in the linearization
- ChunkIndex (`src/txgraph.cpp:544`): feerate-sorted chunk index
- Relationship between chunks and block building

## 9. DoWork Cost Budget Mechanism

(Will explain `src/txgraph.cpp:3113`)

- `DoWork(max_cost)` semantics
- How computation budget is allocated across different operations
- Actual work distribution in lazy evaluation

---

## Summary

Clustering and linearization are the key to understanding TxGraph. After mastering DepGraph,
the QualityLevel state machine, and the lazy evaluation mechanism, the next article shows how
the staging dual-layer architecture supports atomic RBF evaluation.
