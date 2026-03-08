# Part 5: Staging — The Dual-Graph System

[中文](mempool-txgraph-05-staging.zh.md)

> This article is Part 5 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 4: Clustering and Linearization](mempool-txgraph-04-linearization.en.md) | Next: [Part 6: CTxMemPool — Core Mempool Operations](mempool-txgraph-06-ctxmempool.en.md)

---

## Focus

- Core files: `src/txgraph.cpp` (staging-related), `src/txmempool.h` (ChangeSet class)
- Key classes/functions: StartStaging, CommitStaging, AbortStaging, ChangeSet, GetMainStagingDiagrams
- Prerequisites: Part 4

---

## Overview

Staging is TxGraph's dual-layer architecture that allows previewing a set of changes (adding/removing
transactions) without modifying the main graph, evaluating their effects (e.g., feerate diagram
comparison for RBF), then deciding to commit or rollback.

This design is critical for RBF (Replace-By-Fee) evaluation—the replacement must be simulated in
staging first, old and new feerate diagrams compared, before deciding whether to accept.

## 1. Motivation for the Dual-Layer Architecture

(Will explain why staging is needed)

- Problem: RBF evaluation requires "pretending" transactions are already in the pool
- Solution: staging layer as a temporary overlay on the main layer
- Analogy: database transactions with BEGIN / COMMIT / ROLLBACK

## 2. Locator State Machine

(Will explain the five Locator states in staging)

| State | main | staging | Meaning |
|-------|------|---------|---------|
| (M,M) | positioned | same | Transaction only in main, staging unmodified |
| (P,M) | pending | same | main layer has pending dependencies |
| (P,P) | pending | pending | Both layers have pending dependencies |
| (M,P) | positioned | pending | Staging layer modified dependencies |
| (P,R) | pending | removed | Removed in staging |

## 3. StartStaging / CommitStaging / AbortStaging

(Will explain `src/txgraph.cpp:2626`, `:2681`, `:2650`)

- `StartStaging` (:2626): create staging ClusterSet, copy necessary state
- `CommitStaging` (:2681): merge staging changes into main
- `AbortStaging` (:2650): discard staging layer, restore to main state

## 4. ChangeSet Class

(Will explain `src/txmempool.h:620-693`)

- `ChangeSet` is the high-level interface for managing staging changes in CTxMemPool
- `StageAddition` (:636): stage a transaction for addition
- `StageRemoval` (:638): stage a transaction for removal
- `CheckMemPoolPolicyLimits` (:643): check if post-change state satisfies policy limits
- `CalculateChunksForRBF` (:674): compute old/new feerate diagrams for RBF evaluation
- `Apply` (:679): actually apply staged changes to the mempool

## 5. Staging and RBF Evaluation

(Will explain how staging serves the RBF flow)

- RBF evaluation flow:
  1. StartStaging
  2. Remove replaced transactions in staging
  3. Add new transaction in staging
  4. Compare main vs staging feerate diagrams
  5. Decide to Commit or Abort

## 6. GetMainStagingDiagrams

(Will explain `src/txgraph.cpp:2810`)

- Returns feerate diagrams for both main and staging
- Feerate diagram meaning: cumulative size vs feerate step function
- `CompareChunks` (`src/util/feefrac.h:234`): comparing two feerate diagrams

---

## Summary

The staging dual-layer architecture is the key mechanism for TxGraph's atomic change evaluation.
After understanding staging, the next article moves up to the CTxMemPool layer to see how it
uses TxGraph and ChangeSet to manage the entire mempool.
