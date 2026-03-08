# Part 7: Transaction Validation and Acceptance — The ATMP Flow

[中文](mempool-txgraph-07-atmp.zh.md)

> This article is Part 7 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 6: CTxMemPool — Core Mempool Operations](mempool-txgraph-06-ctxmempool.en.md) | Next: [Part 8: Block Building — From Mempool to Block Template](mempool-txgraph-08-block-building.en.md)

---

## Focus

- Core file: `src/validation.cpp`
- Key classes/functions: MemPoolAccept, ATMPArgs, Workspace, PreChecks, PolicyScriptChecks, ConsensusScriptChecks, FinalizeSubpackage, AcceptSingleTransactionInternal, AcceptPackage, AcceptSubPackage
- Prerequisites: Part 6

---

## Overview

ATMP (AcceptToMemoryPool) is the gateway for transactions entering the mempool. The MemPoolAccept
class (`src/validation.cpp:435`) encapsulates the complete validation pipeline: from basic policy
checks to script validation to final pool entry.

This article follows a transaction's acceptance path, explaining the logic at each validation stage.

## 1. MemPoolAccept Class Structure

(Will explain `src/validation.cpp:435-737`)

- Class responsibilities: coordinate the various stages of transaction validation
- `Workspace` struct (:626-662): intermediate state during single-transaction validation
- Relationship with CTxMemPool and CChainState

## 2. ATMPArgs — Validation Parameters

(Will explain `src/validation.cpp:448-577`)

- ATMPArgs role: parameter set controlling validation behavior
- Factory methods:
  - `SingleAccept` (:482): single transaction submission
  - `PackageTestAccept` (:499): package testing (dry-run)
  - `PackageChildWithParents` (:515): child transaction with parents
  - `SingleInPackageAccept` (:531): single transaction within a package
- Key parameters: `m_test_accept` (test-only), `m_allow_replacement` (allow RBF)

## 3. PreChecks — Policy Pre-Checks

(Will explain `src/validation.cpp:782`)

- Basic transaction validity checks
- Fee checks: meets minimum relay feerate
- Input checks: UTXO existence, already-spent detection
- Conflict detection: identifying RBF candidates
- Size and sigops limits
- Timelock checks

## 4. PolicyScriptChecks — Policy Script Validation

(Will explain `src/validation.cpp:1132`)

- Script validation using policy flags
- Stricter policy checks beyond consensus rules
- Script cache usage

## 5. ConsensusScriptChecks — Consensus Script Validation

(Will explain `src/validation.cpp:1155`)

- Script validation using consensus flags
- Caches script validation results on success
- Differences from PolicyScriptChecks

## 6. FinalizeSubpackage — Final Pool Entry

(Will explain `src/validation.cpp:1188`)

- Applies transactions to the mempool via ChangeSet
- Updates dependencies in TxGraph
- Triggers notifications (signals)

## 7. AcceptSingleTransactionInternal — Complete Single-Tx Flow

(Will explain `src/validation.cpp:1314`)

- Complete single-transaction validation pipeline
- PreChecks → PolicyScriptChecks → ConsensusScriptChecks → FinalizeSubpackage
- Where RBF evaluation fits in this process

## 8. Package Validation

(Will explain `src/validation.cpp:1593-1619`)

- `AcceptSubPackage` (:1593): sub-package validation
- `AcceptPackage` (:1619): complete package validation flow
- Differences between package and single-transaction validation
- CPFP (Child-Pays-For-Parent) support

## 9. Error Handling and TxValidationResult

(Will explain error classification and propagation during validation)

- TxValidationResult enum
- How different error types affect P2P behavior
- Misbehavior score mechanism

---

## Summary

The ATMP flow is key to understanding how transactions enter the mempool. After mastering the
validation pipeline, the next article looks at the reverse direction—how transactions are selected
from the mempool to construct block templates.
