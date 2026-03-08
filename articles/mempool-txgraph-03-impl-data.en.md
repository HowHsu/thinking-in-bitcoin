# Part 3: TxGraphImpl Data Structures — Internal Representation

[中文](mempool-txgraph-03-impl-data.zh.md)

> This article is Part 3 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 2: TxGraph Interface](mempool-txgraph-02-txgraph-interface.en.md) | Next: [Part 4: Clustering and Linearization](mempool-txgraph-04-linearization.en.md)

---

## Focus

- Core file: `src/txgraph.cpp` (first half, data structure definitions)
- Key classes/structs: TxGraphImpl, Entry, Cluster, SingletonClusterImpl, GenericClusterImpl, Locator, ClusterSet
- Prerequisites: Part 2

---

## Overview

TxGraphImpl (`src/txgraph.cpp:390`) is the sole implementation of the TxGraph interface. It manages
a large internal state: a transaction array, cluster sets, chunk indexes, and an optional staging layer.

This article focuses on the "static" view of data structures—what each class and struct is and how
they're organized. Algorithm flows are covered in Parts 4-5.

## 1. Cluster Abstract Base Class

(Will explain `src/txgraph.cpp:102-248`)

- Cluster base class responsibilities: encapsulate a group of related transactions
- Key virtual methods: dependency management, linearization state
- QualityLevel enum (:38-58): states from OVERSIZED_SINGLETON to OPTIMAL

## 2. SingletonClusterImpl vs GenericClusterImpl

(Will explain `src/txgraph.cpp:311-365` and `:252-309`)

- `SingletonClusterImpl` (:311-365): specialized implementation for single-transaction clusters
  - Why specialize? Performance and memory advantages
  - No DepGraph needed, no linearization needed
- `GenericClusterImpl` (:252-309): general implementation for multi-transaction clusters
  - Internally holds a DepGraph
  - Linearization state management

## 3. Entry Struct

(Will explain `src/txgraph.cpp:601-625`)

- `Entry` struct: per-transaction internal representation within TxGraphImpl
- Key fields: `m_ref` (pointer to external Ref), `m_locator` (position within a cluster)
- Relationship with CTxMemPoolEntry

## 4. Locator Struct

(Will explain `src/txgraph.cpp:580-599`)

- `Locator`: (cluster_idx, pos_in_cluster) tuple
- How to locate a specific position within a specific cluster from a GraphIndex
- Locator state transitions (see Part 5 on staging)

## 5. ClusterSet Struct

(Will explain `src/txgraph.cpp:431-458`)

- `ClusterSet`: clusters managed in buckets by QualityLevel
- Meaning of each bucket: NEEDS_SPLIT, NEEDS_RELINEARIZE, ACCEPTABLE, OPTIMAL, etc.
- Why quality-level bucketing matters

## 6. TxGraphImpl Top-Level Structure

(Will explain `src/txgraph.cpp:390-850`)

- `m_entries` array (:628): GraphIndex → Entry mapping
- `m_unlinked` (:631): list of freed GraphIndex slots
- `m_main_clusterset` (:461): main cluster set
- `m_staging_clusterset` (:463): optional staging cluster set
- `m_main_chunkindex` (:544): chunk feerate index
- Compact mechanism (:1775): compacting holes in the m_entries array

## 7. BlockBuilderImpl

(Will explain `src/txgraph.cpp:852-879`)

- BlockBuilderImpl class: implementation of the BlockBuilder interface
- How it traverses ChunkIndex to generate block templates
- Interaction with TxGraphImpl

---

## Summary

This article shows the internal data organization of TxGraphImpl. After understanding these data
structures, the next article enters the algorithm core—clustering and linearization—to see how
clusters are split, merged, and ordered.
