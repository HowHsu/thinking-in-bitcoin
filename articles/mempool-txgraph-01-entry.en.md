# Part 1: CTxMemPoolEntry — Transaction Representation in the Mempool

[中文](mempool-txgraph-01-entry.zh.md)

> This article is Part 1 of the [Mempool & TxGraph Code Walkthrough](../README.en.md) series.
> Previous: [Part 0: Architecture Overview](mempool-txgraph-00-architecture.en.md) | Next: [Part 2: TxGraph Interface — Abstraction Layer Design](mempool-txgraph-02-txgraph-interface.en.md)

---

## Focus

- Core file: `src/kernel/mempool_entry.h`
- Supporting file: `src/util/feefrac.h`
- Key classes/functions: CTxMemPoolEntry, TxGraph::Ref, FeeFrac, FeePerWeight, FeePerVSize, LockPoints
- Prerequisites: Part 0

---

## Overview

CTxMemPoolEntry is the complete representation of a transaction once it enters the mempool. It contains
not only the transaction itself (CTransactionRef) but also metadata such as fee, weight, entry time,
and entry height.

A key design decision is that CTxMemPoolEntry **publicly inherits** from `TxGraph::Ref`
(`src/kernel/mempool_entry.h:65`), making each mempool entry simultaneously serve as a reference
handle in TxGraph. This means any `Ref*` pointer returned by TxGraph can be directly
`static_cast<CTxMemPoolEntry*>()` back, requiring no additional mapping table.

This article walks through every field of CTxMemPoolEntry, then dives deep into FeeFrac—the
infrastructure for exact fee rate comparison in Bitcoin Core.

## 1. Class Definition and Inheritance

```cpp
// src/kernel/mempool_entry.h:65
class CTxMemPoolEntry : public TxGraph::Ref
```

CTxMemPoolEntry inherits from `TxGraph::Ref` (defined at `src/txgraph.h:232-253`).
Ref is the external handle for a transaction in TxGraph, containing two private fields:

- `TxGraph* m_graph`: pointer to the owning TxGraph instance; `nullptr` means empty (not associated with any transaction)
- `GraphIndex m_index`: index into TxGraph's internal `m_entries` array

**Why inheritance over composition?**

If composition were used (CTxMemPoolEntry holding a `Ref` member), then `Ref*` pointers returned
by TxGraph couldn't be directly converted to `CTxMemPoolEntry*`—a `Ref* → CTxMemPoolEntry*`
mapping table would be needed. Through inheritance, each `CTxMemPoolEntry` object's memory layout
starts with `Ref`'s data, so `Ref*` and `CTxMemPoolEntry*` point to the same address, making
`static_cast` zero-cost.

The design comment in `txgraph.h` (:53-61) explicitly describes this pattern:
> "Users of the class can inherit from TxGraph::Ref. If all Refs are inherited this way,
> the Ref* pointers returned by TxGraph functions can be cast to, and used as, this inherited type."

**Move semantics restrictions:**

```cpp
// src/kernel/mempool_entry.h:71, 103-105
CTxMemPoolEntry(const CTxMemPoolEntry&) = delete;         // no copy construction
CTxMemPoolEntry& operator=(const CTxMemPoolEntry&) = delete; // no copy assignment
CTxMemPoolEntry(CTxMemPoolEntry&&) = default;             // move construction: OK
CTxMemPoolEntry& operator=(CTxMemPoolEntry&&) = delete;   // move assignment: deleted
```

These restrictions inherit from Ref's semantics: a TxGraph entry has exactly one corresponding Ref.
Copying would create two "owners," which is not allowed. Move construction is allowed (Ref's move
constructor notifies TxGraph to update its internal pointer), but move assignment is deleted
(it would need to handle the case where `*this` already holds a transaction).

## 2. Core Fields Walkthrough

CTxMemPoolEntry's fields are divided into **immutable fields** (`const`) and **mutable fields**.
Most fields are determined at construction time and never change after pool entry.

### 2.1 Immutable Fields

| Field | Line | Type | Purpose |
|-------|------|------|---------|
| `tx` | :73 | `const CTransactionRef` | The transaction itself (shared_ptr, shared with other holders) |
| `nFee` | :74 | `const CAmount` | Original transaction fee (cached, avoids deriving from parent txs) |
| `nTxWeight` | :75 | `const int32_t` | Raw transaction weight (with witness discount) |
| `nUsageSize` | :76 | `const size_t` | Total memory usage of this entry (including dynamic memory of tx data) |
| `nTime` | :77 | `const int64_t` | Unix timestamp (seconds) when entering the mempool |
| `entry_sequence` | :78 | `const uint64_t` | Entry sequence number (monotonically increasing, globally unique) |
| `entryHeight` | :79 | `const unsigned int` | Blockchain height at time of entry |
| `spendsCoinbase` | :80 | `const bool` | Whether the tx spends a coinbase output (affects maturity checks) |
| `sigOpCost` | :81 | `const int64_t` | Total sigop cost (used to compute virtual size) |

All `const` fields reflect a design principle: **once a transaction enters the pool, its consensus
properties are immutable**. The fee is determined by the transaction structure, the weight by the
serialization format—they don't change due to mempool state changes.

### 2.2 Mutable Fields

| Field | Line | Type | Purpose |
|-------|------|------|---------|
| `m_modified_fee` | :82 | `mutable CAmount` | Modified fee (adjusted via `prioritisetransaction`) |
| `lockPoints` | :83 | `mutable LockPoints` | BIP68 timelock cache (may need updating after reorgs) |
| `idx_randomized` | :138 | `mutable size_t` | Index in mempool's randomly-ordered vector (for P2P broadcast) |

These three fields are declared `mutable` because they represent **runtime state** unrelated to
the transaction's identity:

- `m_modified_fee`: Miners can manually adjust transaction priority via the `prioritisetransaction`
  RPC. Initialized to `nFee`, modified through `UpdateModifiedFee(fee_diff)` using saturating addition.
- `lockPoints`: Cached BIP68 relative timelock computation results. When a chain reorg occurs,
  the cache may be invalidated (see next section) and needs refreshing via `UpdateLockPoints()`.
- `idx_randomized`: The mempool maintains a randomly-ordered transaction vector
  (`CTxMemPool::txns_randomized`) for broadcasting transactions to peers randomly.
  This index changes as transactions are added and removed.

### 2.3 Constructor

```cpp
// src/kernel/mempool_entry.h:87-101
CTxMemPoolEntry(const CTransactionRef& tx, CAmount fee,
                int64_t time, unsigned int entry_height,
                uint64_t entry_sequence, bool spends_coinbase,
                int64_t sigops_cost, LockPoints lp)
```

The constructor performs two computations during initialization:
- `nTxWeight = GetTransactionWeight(*tx)`: compute weight from serialized transaction data
- `nUsageSize = RecursiveDynamicUsage(tx)`: recursively compute dynamic memory usage (including shared_ptr overhead)

`m_modified_fee` is initialized to `nFee` (equal to the original fee before any modifications).

## 3. LockPoints Structure

```cpp
// src/kernel/mempool_entry.h:26-36
struct LockPoints {
    int height{0};
    int64_t time{0};
    CBlockIndex* maxInputBlock{nullptr};
};
```

LockPoints caches BIP68 relative timelock computation results, avoiding re-traversal of inputs
each time they're needed:

- `height`: minimum blockchain height needed to satisfy BIP68 relative lock-by-height requirements
- `time`: minimum MTP (Median Time Past) needed to satisfy BIP68 relative lock-by-time requirements
- `maxInputBlock`: pointer to the **highest-height block** containing an input used in the calculation

The key role of `maxInputBlock` is **cache invalidation**: as long as the current chain is still
a descendant of `maxInputBlock` (i.e., no reorg has occurred involving that block), the cached
`height` and `time` values remain valid. If a reorg is deeper than `maxInputBlock`'s height,
recalculation via `UpdateLockPoints()` is needed.

## 4. Fee Representation: FeeFrac Deep Dive

Bitcoin Core uses `FeeFrac` (`src/util/feefrac.h:39`) as the core fee rate representation.
It's a `{fee, size}` pair that performs fee rate comparison through exact integer arithmetic,
completely avoiding floating point.

### 4.1 Basic Structure

```cpp
// src/util/feefrac.h:107-108
int64_t fee;   // satoshis, 64-bit signed
int32_t size;  // vbytes or weight units, 32-bit signed
```

The mathematical definition of fee rate is `fee / size`, but direct division loses precision.
FeeFrac's solution is **cross-multiplication**: comparing `a.fee/a.size` vs `b.fee/b.size`
is equivalent to comparing `a.fee * b.size` vs `b.fee * a.size`.

Since `fee` is 64-bit and `size` is 32-bit, the product requires 96 bits. FeeFrac provides
two implementations:
- When the compiler supports `__int128` (:83-86): uses 128-bit integer arithmetic directly
- Fallback (:44-78): splits the 64-bit number into high/low 32 bits, multiplies separately, manually manages carries

### 4.2 Two Comparison Semantics

FeeFrac provides two distinct comparison operations. Understanding their difference is crucial:

**`FeeRateCompare` (`src/util/feefrac.h:157`) — fee rate only**

```cpp
friend std::weak_ordering FeeRateCompare(const FeeFrac& a, const FeeFrac& b) noexcept;
```

Returns `std::weak_ordering`: two FeeFracs can have different `(fee, size)` values but be
considered "equivalent"—as long as their fee rates (fee/size ratios) are equal. The companion
`operator<<` and `operator>>` represent "strictly lower feerate" and "strictly higher feerate."

**`operator<=>` (`src/util/feefrac.h:178`) — total ordering**

```cpp
friend std::strong_ordering operator<=>(const FeeFrac& a, const FeeFrac& b) noexcept
{
    auto cross_a = Mul(a.fee, b.size), cross_b = Mul(b.fee, a.size);
    if (cross_a == cross_b) return b.size <=> a.size;  // note: b.size first
    return cross_a <=> cross_b;
}
```

Returns `std::strong_ordering`: this is a **total order**. When feerates are equal, the tie is
broken by `b.size <=> a.size`—**larger size sorts first** (has a smaller sort value). This means
at equal feerate, a FeeFrac occupying more space is "better."

**Empty FeeFrac** (fee=0, size=0) sorts **last** in the total order:
`cross_a = 0 * b.size = 0`, `cross_b = b.fee * 0 = 0`, equal,
then `b.size <=> 0` places the empty value after everything with size > 0.

### 4.3 EvaluateFee: Proportional Fee Calculation

```cpp
// src/util/feefrac.h:201-223
template<bool RoundDown>
int64_t EvaluateFee(int32_t at_size) const noexcept;
```

Computes `(this->fee * at_size) / this->size`—"if the transaction size were `at_size`,
how much fee would be owed at this rate." Provides two public interfaces: round-down
(`EvaluateFeeDown`) and round-up (`EvaluateFeeUp`).

Fast path (:206): when `fee >= 0 && fee < 0x200000000` (fits in 33 bits), the product fits in
`uint64_t`, no 96-bit arithmetic needed. The `[[likely]]` hint marks this as the common case.

### 4.4 FeePerWeight vs FeePerVSize

To prevent mixing fee rates with different units, FeeFrac provides type safety through phantom type tags:

```cpp
// src/util/feefrac.h:237-256
template<typename Tag>
struct FeePerUnit : public FeeFrac { ... };  // zero-overhead tagged wrapper

struct VSizeTag {};
using FeePerVSize = FeePerUnit<VSizeTag>;    // satoshis per virtual byte

struct WeightTag {};
using FeePerWeight = FeePerUnit<WeightTag>;  // satoshis per weight unit
```

`FeePerWeight` is the type used internally by TxGraph (`AddTransaction` accepts `const FeePerWeight&`).
`FeePerVSize` is used for user-facing interfaces (e.g., fee rates returned by RPCs).
The two cannot be implicitly converted, catching misuse at compile time.

### 4.5 CompareChunks: Feerate Diagram Comparison

```cpp
// src/util/feefrac.h:226-234
std::partial_ordering CompareChunks(std::span<const FeeFrac> chunks0,
                                     std::span<const FeeFrac> chunks1);
```

Compares two feerate diagrams. Each diagram is represented by a series of cumulative `(fee, size)`
values for each chunk. Returns `std::partial_ordering` because two diagrams may be
**incomparable**—one may be better in some fee ranges while the other is better in others.
This function is used in RBF evaluation to compare main and staging feerate diagrams
(see Part 5 on staging).

## 5. entry_sequence Purpose

`entry_sequence` (:78) is a globally monotonically increasing sequence number assigned when
a transaction enters the pool. It serves two key roles in the system:

**1. Fallback ordering**

When TxGraph needs to choose an order between two chunks with identical feerates, it needs a
stable tie-breaking rule. The `fallback_order` parameter of `MakeTxGraph` (`src/txgraph.h:266-271`)
is designed for exactly this. In practice, this comparison function is typically based on
`entry_sequence`: earlier-entered transactions sort first.

**2. Preventing relay of too-new transactions**

Nodes use `entry_sequence` to determine whether a transaction is "too new" to broadcast to peers,
helping prevent transaction broadcast storms.

## 6. Public Methods Overview

| Method | Return Type | Description |
|--------|------------|-------------|
| `GetTx()` | `const CTransaction&` | Transaction reference (dereferences shared_ptr) |
| `GetSharedTx()` | `CTransactionRef` | Transaction shared_ptr (increments refcount) |
| `GetFee()` | `const CAmount&` | Original fee (without prioritisetransaction adjustments) |
| `GetTxSize()` | `int32_t` | Virtual size: `GetVirtualTransactionSize(nTxWeight, sigOpCost)` |
| `GetAdjustedWeight()` | `int32_t` | Sigop-adjusted weight |
| `GetTxWeight()` | `int32_t` | Raw weight (before sigop adjustment) |
| `GetTime()` | `std::chrono::seconds` | Entry time (type-safe time representation) |
| `GetHeight()` | `unsigned int` | Entry block height |
| `GetSequence()` | `uint64_t` | Entry sequence number |
| `GetModifiedFee()` | `CAmount` | Modified fee (with prioritisetransaction adjustments) |
| `GetSigOpCost()` | `int64_t` | Sigop cost |
| `DynamicMemoryUsage()` | `size_t` | Dynamic memory usage |
| `GetLockPoints()` | `const LockPoints&` | Timelock cache |
| `GetSpendsCoinbase()` | `bool` | Whether it spends a coinbase |
| `UpdateModifiedFee(diff)` | `void` | Modify fee with saturating addition |
| `UpdateLockPoints(lp)` | `void` | Update timelock cache |

Note the difference between `GetTxSize()` and `GetAdjustedWeight()`: both account for sigop cost,
but the former returns virtual bytes (vbytes), the latter weight units (WU). TxGraph internally
uses weight units (FeePerWeight).

---

## Summary

CTxMemPoolEntry is the atomic unit of the mempool, seamlessly connecting transaction storage to the
graph engine through TxGraph::Ref inheritance. Its field design reflects the "immutable after entry"
principle (const fields) and "runtime adjustable" flexibility (mutable fields). FeeFrac provides
exact integer-based fee rate comparison infrastructure, with phantom types ensuring type safety.

The next article takes us to TxGraph::Ref's "home"—the TxGraph interface layer—to see how this
pure virtual class defines the complete graph engine API.
