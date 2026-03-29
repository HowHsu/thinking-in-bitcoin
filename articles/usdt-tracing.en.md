# Bitcoin Core USDT Tracing: Internals and Implementation

[中文](usdt-tracing.zh.md)

---

> This article explains how USDT (User-space Statically Defined Tracing) is implemented in Bitcoin Core: from C++ macros to ELF binary probes, and how BPF programs hook into them at runtime. It also catalogs all existing tracepoints and their arguments.
> Related resources: [Official doc/tracing.md](https://github.com/bitcoin/bitcoin/blob/master/doc/tracing.md), [contrib/tracing/ example scripts](https://github.com/bitcoin/bitcoin/tree/master/contrib/tracing)

## What Is USDT

USDT (User-space Statically Defined Tracing) is a technique for embedding probes into user-space programs. Unlike dynamic tracing (e.g., setting uprobes on arbitrary functions), USDT probes are baked into the binary at **compile time** — their locations are fixed, arguments are well-defined, and the interface is semi-stable. They can be hooked at runtime by eBPF front-ends such as bpftrace and BCC.

Bitcoin Core uses USDT to observe internal behavior: mempool transaction flow, UTXO cache changes, P2P message I/O, block connection, coin selection, and more. These probes have **near-zero overhead when unattached**, making them safe for production use.

## Architecture Overview

```
                ┌──────────────────┐            ┌──────────────┐
                │ tracing script   │            │ bitcoind     │
                │==================│      2.    │==============│
                │  eBPF  │ tracing │      hooks │              │
                │  code  │ logic   │      into┌─┤►tracepoint 1─┼───┐ 3.
                └────┬───┴──▲──────┘          ├─┤►tracepoint 2 │   │ pass args
            1.       │      │ 4.              │ │ ...          │   │ to eBPF
    User    compiles │      │ pass data to    │ └──────────────┘   │ program
    Space    & loads │      │ tracing script  │                    │
    ─────────────────┼──────┼─────────────────┼────────────────────┼───
    Kernel           │      │                 │                    │
    Space       ┌──┬─▼──────┴─────────────────┴────────────┐       │
                │  │  eBPF program                         │◄──────┘
                │  └───────────────────────────────────────┤
                │ eBPF kernel Virtual Machine (sandboxed)  │
                └──────────────────────────────────────────┘
```

1. The tracing script compiles eBPF code and loads the eBPF program into the kernel VM
2. The eBPF program hooks into one or more tracepoints
3. When a tracepoint fires, arguments are passed to the eBPF program
4. The eBPF program processes the arguments and returns data to the user-space script

## Implementation: From Macro to Binary

### Step 1: Declare the Semaphore

Each tracepoint requires a global semaphore (a counting variable) to determine whether any tracing program is attached.

For example, `mempool:added` in `src/txmempool.cpp`:

```cpp
TRACEPOINT_SEMAPHORE(mempool, added);
```

The macro is defined in `src/util/trace.h`:

```cpp
#define TRACEPOINT_SEMAPHORE(context, event) \
    unsigned short context##_##event##_semaphore __attribute__((section(".probes")))
```

This expands to:

```cpp
unsigned short mempool_added_semaphore __attribute__((section(".probes")));
```

A 2-byte global variable placed in the ELF `.probes` section, initialized to 0. When bpftrace/BCC attaches to the tracepoint, the value is automatically incremented; when detaching, it is decremented.

### Step 2: Embed the Tracepoint

The `TRACEPOINT` macro is inserted at the key location within the function:

```cpp
// src/txmempool.cpp  CTxMemPool::addUnchecked()
TRACEPOINT(mempool, added,
    entry.GetTx().GetHash().data(),   // arg0: txid (32 bytes)
    entry.GetTxSize(),                // arg1: vsize
    entry.GetFee()                    // arg2: fee
);
```

The macro definition:

```cpp
#define TRACEPOINT(context, event, ...)                                         \
    do {                                                                        \
        if (TRACEPOINT_ACTIVE(context, event)) {                                \
            STAP_PROBEV(context, event __VA_OPT__(, ) __VA_ARGS__);             \
        }                                                                       \
    } while(0)
```

Where `TRACEPOINT_ACTIVE` checks the semaphore:

```cpp
#define TRACEPOINT_ACTIVE(context, event) (context##_##event##_semaphore > 0)
```

Fully expanded:

```cpp
do {
    if (mempool_added_semaphore > 0) {
        STAP_PROBEV(mempool, added,
            entry.GetTx().GetHash().data(),
            entry.GetTxSize(),
            entry.GetFee());
    }
} while(0)
```

### Step 3: STAP_PROBEV → NOP Instruction + ELF Note

`STAP_PROBEV` comes from systemtap's `<sys/sdt.h>` header. Its core mechanism is to use **inline assembly** to insert a **NOP instruction** at the call site, while recording metadata in the ELF `.note.stapsdt` section.

Conceptually equivalent to:

```c
// Pseudocode — the real implementation is compiler inline assembly
__asm__ __volatile__ (
    "nop"                          // ← A single NOP, zero runtime cost
    :                              // no outputs
    : "r"(arg1), "r"(arg2), ...   // arguments as input operands
                                   //   compiler places them in registers
);

// Simultaneously writes to .note.stapsdt section:
//   provider  = "mempool"
//   name      = "added"
//   location  = address of this NOP
//   semaphore = address of &mempool_added_semaphore
//   arguments = "-8@%rdi -8@%rsi -8@%rdx"  // register locations and sizes
```

### Step 4: Final Form in the ELF Binary

The compiled `bitcoind` binary contains three USDT-related parts:

```
bitcoind ELF binary
│
├── .text section
│     CTxMemPool::addUnchecked():
│       0x4a3f20:  nop              ← tracepoint location (zero cost at runtime)
│
├── .probes section
│     mempool_added_semaphore    = 0x0000   ← 2-byte counter
│     mempool_removed_semaphore  = 0x0000
│     mempool_replaced_semaphore = 0x0000
│     mempool_rejected_semaphore = 0x0000
│     ... (semaphores for all tracepoints)
│
└── .note.stapsdt section                   ← read-only metadata table
      ┌────────────────────────────────────────────────────────┐
      │ provider:  "mempool"                                   │
      │ name:      "added"                                     │
      │ location:  0x4a3f20           (address of the NOP)     │
      │ semaphore: 0x6b8100           (address of semaphore)   │
      │ arguments: "-8@%rdi -8@%rsi -8@%rdx"                  │
      │            (txid in rdi, vsize in rsi, fee in rdx)     │
      ├────────────────────────────────────────────────────────┤
      │ provider:  "mempool"                                   │
      │ name:      "removed"                                   │
      │ ...                                                    │
      └────────────────────────────────────────────────────────┘
```

You can inspect these with `readelf`:

```bash
$ readelf -n ./build/bin/bitcoind | grep NT_STAPSDT -A 4 -B 2
  stapsdt              0x0000005d  NT_STAPSDT (SystemTap probe descriptors)
    Provider: mempool
    Name: added
    Location: 0x..., Base: 0x..., Semaphore: 0x...
    Arguments: -8@%rdi -8@%rsi -8@%rdx
```

## Runtime Behavior: Attaching and Detaching

### No Tracer Attached (Normal Operation)

```
semaphore == 0
    → if (0 > 0) → false → skip argument preparation
    → NOP executes normally
    → Total cost: one conditional check (branch predictor learns not-taken ~100%)
```

### BPF Program Attaches

```
1. bpftrace/BCC reads .note.stapsdt, finds "mempool:added" metadata

2. semaphore++  →  mempool_added_semaphore becomes 1
   → if (1 > 0) now evaluates to true
   → arguments are prepared and placed in registers

3. Sets a uprobe at the NOP address
   → Kernel replaces NOP with INT3 (breakpoint instruction)

4. Each time execution reaches that address
   → Triggers uprobe → kernel runs the eBPF program
   → eBPF program reads arguments from register locations per the arguments spec

5. On detach: semaphore-- → back to 0, INT3 restored to NOP
```

### End-to-End Flow

```
Compile time                  Runtime (no tracer)           Runtime (BPF attached)
────────────                  ───────────────────           ──────────────────────

TRACEPOINT(mempool,added,...) semaphore == 0                bpftrace attach:
       │                      if (0 > 0) → skip              semaphore++ → 1
       ▼                      NOP executes normally           NOP → INT3
  inline asm + ELF note       Cost: one branch check               │
       │                      (predictor: not-taken)               ▼
       ▼                                                   if (1 > 0) → prepare args
  .text:  NOP                                              INT3 triggers uprobe
  .probes: semaphore = 0                                   kernel invokes eBPF program
  .note.stapsdt: metadata                                  eBPF reads registers:
    (address, arg locations)                                 txid, vsize, fee
```

## Key Design Decisions for Zero Overhead

1. **Semaphore gating**: The `TRACEPOINT` macro checks the semaphore before calling `STAP_PROBEV`. This avoids preparing potentially expensive arguments (e.g., hashing, serialization) when no tracer is listening.

2. **NOP placeholder**: `STAP_PROBEV` inserts only a single NOP instruction in the code. The CPU cost of executing a NOP is negligible.

3. **Branch-prediction friendly**: `if (semaphore > 0)` is almost always false during normal operation. The CPU branch predictor learns this as not-taken, avoiding pipeline stalls.

4. **Extra gating for expensive arguments**: For costly argument preparation, an explicit `TRACEPOINT_ACTIVE` check can be used:

```cpp
if (TRACEPOINT_ACTIVE(example, expensive_event)) {
    auto result = expensive_calculation();     // only runs when someone is attached
    TRACEPOINT(example, expensive_event, result);
}
```

5. **Compile-time disable**: If `ENABLE_TRACING` is not defined, all macros expand to nothing:

```cpp
#define TRACEPOINT_SEMAPHORE(context, event)          // empty
#define TRACEPOINT_ACTIVE(context, event) false        // always false
#define TRACEPOINT(context, ...)                       // empty
```

## Complete Tracepoint Catalog

### Context `net` (P2P Network)

| Tracepoint | Fires When | Arguments |
|---|---|---|
| `net:inbound_message` | P2P message received | peer_id, addr, conn_type, msg_type, msg_size, msg_bytes |
| `net:outbound_message` | P2P message sent | peer_id, addr, conn_type, msg_type, msg_size, msg_bytes |
| `net:inbound_connection` | Inbound connection accepted | peer_id, addr, conn_type, network, inbound_count |
| `net:outbound_connection` | Outbound connection opened | peer_id, addr, conn_type, network, outbound_count |
| `net:evicted_inbound_connection` | Inbound connection evicted | peer_id, addr, conn_type, network, connected_time |
| `net:closed_connection` | Connection closed | peer_id, addr, conn_type, network, connected_time |
| `net:misbehaving_connection` | Peer misbehaving | peer_id, reason |

### Context `validation` (Block Validation)

| Tracepoint | Fires When | Arguments |
|---|---|---|
| `validation:block_connected` | After block connected to chain | block_hash, height, tx_count, inputs, sigops, time_ns |

### Context `utxocache` (UTXO Cache)

| Tracepoint | Fires When | Arguments |
|---|---|---|
| `utxocache:add` | UTXO added to cache | txid, vout, height, value, is_coinbase |
| `utxocache:spent` | UTXO spent from cache | txid, vout, height, value, is_coinbase |
| `utxocache:uncache` | UTXO purposefully unloaded | txid, vout, height, value, is_coinbase |
| `utxocache:flush` | After UTXO cache flush | flush_time_us, mode, coins_count, mem_usage, is_prune |

### Context `mempool` (Transaction Mempool)

| Tracepoint | Fires When | Arguments |
|---|---|---|
| `mempool:added` | Transaction enters mempool | txid, vsize, fee |
| `mempool:removed` | Transaction removed from mempool | txid, reason, vsize, fee, entry_time |
| `mempool:replaced` | Transaction replaced via RBF | old_txid, old_vsize, old_fee, old_time, new_hash, new_vsize, new_fee, is_tx |
| `mempool:rejected` | Transaction rejected from mempool | txid, reject_reason |

### Context `coin_selection` (Wallet, requires wallet enabled)

| Tracepoint | Fires When | Arguments |
|---|---|---|
| `coin_selection:selected_coins` | SelectCoins completes | wallet, algorithm, target, waste, selected_value |
| `coin_selection:normal_create_tx_internal` | First CreateTransactionInternal completes | wallet, success, fee, change_pos |
| `coin_selection:attempting_aps_create_tx` | Starting Avoid Partial Spends attempt | wallet |
| `coin_selection:aps_create_tx_internal` | Second (APS) CreateTransactionInternal completes | wallet, use_aps, success, fee, change_pos |

## Source File Quick Reference

| File | Contents |
|---|---|
| `src/util/trace.h` | `TRACEPOINT`, `TRACEPOINT_SEMAPHORE`, `TRACEPOINT_ACTIVE` macro definitions |
| `src/txmempool.cpp` | `mempool:added`, `mempool:removed` semaphore declarations and call sites |
| `src/validation.cpp` | `mempool:replaced`, `mempool:rejected`, `validation:block_connected`, `utxocache:flush` |
| `src/coins.cpp` | `utxocache:add`, `utxocache:spent`, `utxocache:uncache` |
| `src/net.cpp` | `net:outbound_message`, `net:*_connection` |
| `src/net_processing.cpp` | `net:inbound_message`, `net:misbehaving_connection` |
| `src/wallet/spend.cpp` | `coin_selection:*` |
| `doc/tracing.md` | Official tracepoint documentation |
| `contrib/tracing/` | Example tracing scripts (bpftrace, BCC Python) |

## Example: Monitor Mempool with bpftrace

```bash
# Monitor transactions entering the mempool
sudo bpftrace -e '
    usdt:./build/bin/bitcoind:mempool:added {
        printf("tx added: vsize=%d fee=%d\n", arg1, arg2);
    }
'

# Monitor rejected transactions
sudo bpftrace -e '
    usdt:./build/bin/bitcoind:mempool:rejected {
        printf("tx rejected: reason=%s\n", str(arg1));
    }
'
```

Bitcoin Core also ships `contrib/tracing/mempool_monitor.py` (BCC-based), which hooks all 4 mempool tracepoints simultaneously and prints events in real time.

## Listing Available Tracepoints

```bash
# Using GDB
$ gdb ./build/bin/bitcoind -ex 'info probes' -ex quit

# Using readelf
$ readelf -n ./build/bin/bitcoind | grep NT_STAPSDT -A 4

# Using BCC's tplist
$ tplist -l ./build/bin/bitcoind -v
```

Note: The binary must be compiled with `ENABLE_TRACING` (set `-DWITH_USDT=ON` in CMake) for USDT probes to be present. Without this flag, all `TRACEPOINT` macros expand to nothing and no tracing-related code exists in the binary.
