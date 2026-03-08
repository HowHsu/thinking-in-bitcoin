# Part 9: Testing and Debugging — Quality Assurance

[中文](mempool-txgraph-09-testing.zh.md)

> This article is Part 9 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 8: Block Building — From Mempool to Block Template](mempool-txgraph-08-block-building.en.md)

---

## Focus

- Core files: `src/test/txgraph_tests.cpp`, `src/test/fuzz/txgraph.cpp`
- Supporting file: `src/txgraph.cpp` (SanityCheck method)
- Key classes/functions: SanityCheck, SimTxGraph, FUZZ_TARGET(txgraph)
- Prerequisites: Part 4 (recommended)

---

## Overview

TxGraph is a complex state machine where correctness is paramount. Bitcoin Core ensures quality
through a multi-layered testing strategy: from internal consistency checks (SanityCheck) to
deterministic unit tests to high-coverage fuzz testing.

This article demonstrates the design and usage of these testing approaches.

## 1. SanityCheck — Internal Consistency Checks

(Will explain `src/txgraph.cpp:2932`)

- SanityCheck verification items:
  - Cluster internal consistency (DepGraph matching linearization)
  - Locator validity (position information correctness for each transaction)
  - ClusterSet bucketing correctness (quality levels matching actual state)
  - ChunkIndex ordering
  - m_entries and Ref bidirectional reference integrity
- Calling patterns in tests
- Invocation within CTxMemPool::check() (`src/txmempool.cpp:433`)

## 2. Unit Tests: txgraph_tests

(Will explain `src/test/txgraph_tests.cpp`, 434 lines)

- Test suite structure (:14-434):
  - `txgraph_trim_zigzag` (:29-90): zigzag dependency oversized graph trimming
  - `txgraph_trim_flower` (:92-149): flower-topology oversized graph trimming
  - `txgraph_trim_huge` (:151-263): large-scale (64,000 transactions) trimming
  - `txgraph_trim_big_singletons` (:265-302): oversized singleton transaction trimming
  - `txgraph_chunk_chain` (:304-379): chain-topology chunk feerate verification
  - `txgraph_staging` (:381-432): staging create/commit/abort
- Frequent SanityCheck calls (:67, :75, :81, :125, :131, :217, :247, :259, :286, :294, :368, :431)

## 3. Fuzz Testing: txgraph Fuzzer

(Will explain `src/test/fuzz/txgraph.cpp`, 1396 lines)

- Design approach: differential testing
  - `SimTxGraph` (:37-301): naive TxGraph simulation implementation
  - Real `TxGraphImpl` and `SimTxGraph` execute in parallel
  - Compare outputs for consistency
- `SimTxObject` (:26-32): test object inheriting TxGraph::Ref
- `FUZZ_TARGET(txgraph)` (:305-1396): fuzz target entry point
- Operations covered: AddTransaction, RemoveTransaction, AddDependency, SetTransactionFee, StartStaging, CommitStaging, AbortStaging, GetCluster, GetAncestors, GetDescendants, etc.
- SanityCheck calls (:1061, :1387)

## 4. TracingTxGraph — Decorator Pattern

(Will explain TracingTxGraph design)

- Decorator pattern: wrapping TxGraph interface, recording all operations
- Trace file format
- Used for performance comparison and regression testing
- Reference: [TxGraph Trace & Replay](txgraph-trace-replay.en.md)

## 5. txgraph-replay Tool

(Will explain replay tool usage)

- Replaying TxGraph operations from trace files
- A/B performance comparison: old vs new implementation
- Reference: [TxGraph Trace & Replay](txgraph-trace-replay.en.md)

## 6. Common Debugging Tips

(Will collect TxGraph-related debugging experience)

- How to diagnose SanityCheck failures
- Using `-debug=mempool` logging
- Suggested breakpoint locations: ApplyDependencies, MakeAcceptable, Merge
- Fuzz corpus minimization and reproduction

---

## Summary

Testing is the cornerstone of TxGraph reliability. The differential fuzz testing design is
particularly elegant—verifying the complex implementation's correctness by maintaining a simple
reference implementation.

This is the final article in the Mempool & TxGraph Code Walkthrough series. After reading the
complete series, you should have a comprehensive understanding of Bitcoin Core's mempool subsystem,
from architecture to implementation details.
