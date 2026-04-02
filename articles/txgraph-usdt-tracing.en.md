# USDT-Based TxGraph Tracing Pipeline: Record, Analyze, and Replay

[中文版](txgraph-usdt-tracing.zh.md)

---

> This article describes a USDT-based tracing pipeline for TxGraph: 27 USDT tracepoints embedded in every public TxGraph API, a BCC Python script that records them into a binary trace file, an analysis script for cluster topology, and a replay tool for performance comparison. Along the way we discovered and worked around a BCC 0.31.0 bug that silently zeroes 4-byte USDT stack arguments.
>
> Prerequisites: [Bitcoin Core USDT Tracing: Principles and Implementation](usdt-tracing.en.md), [TxGraph Trace & Replay (Decorator Approach)](txgraph-trace-replay.en.md)

## Comparison with the Decorator Approach

[TxGraph Trace & Replay](txgraph-trace-replay.en.md) uses a compile-time decorator (`TracingTxGraph`) that wraps `TxGraphImpl` and writes every API call to a trace file. Its advantage is that it requires no root privileges and no BCC/eBPF infrastructure.

The **USDT approach** described here takes a different path:

| | Decorator Approach | USDT Approach |
|---|---|---|
| **Intrusiveness** | Requires `TracingTxGraph` wrapper + `g_txgraph_on_unlink_ref` global callback | Only `TRACEPOINT` macros in function bodies (one branch check when unattached, negligible overhead) |
| **Build requirement** | `-DWITH_TXGRAPH_TRACING=ON`, dedicated cmake option | Only `-DWITH_USDT=ON` (enabled by default in Bitcoin Core) |
| **Recording method** | bitcoind writes trace file internally | External BCC Python script via eBPF |
| **Runtime requirement** | None (activated via environment variable) | Requires root + BCC + kernel headers |
| **Capturing ~Ref()** | Requires global callback hack | TRACEPOINT placed directly in `~Ref()` |

Both approaches produce compatible TXGTRACE binary files and share the same `txgraph-replay` tool.

---

## Component Overview

```
bitcoind (27 USDT tracepoints)
    |
    | eBPF attach
    v
txgraph_trace_recorder.py (BCC)  -->  trace.bin (TXGTRACE format)
                                          |
                          +---------------+---------------+
                          v                               v
                  analyze_trace.py              txgraph-replay
                  (cluster topology)            (performance replay)
```

Four components, four commits:

1. **txgraph.cpp / txmempool.cpp** — 27 USDT tracepoints
2. **txgraph_trace_recorder.py** — BCC recording script
3. **analyze_trace.py** — trace analysis script
4. **txgraph-replay** — C++ replay tool

---

## Commit 1: USDT Tracepoints

### Probe Inventory

27 tracepoints added to `src/txgraph.cpp` and `src/txmempool.cpp`, covering every public TxGraph API:

| Category | Tracepoint | Arguments |
|----------|------------|-----------|
| **Init** | `txgraph:init` | max_cluster_count, max_cluster_size, acceptable_cost |
| **Mutation** | `txgraph:add_transaction` | graph_idx, fee, size |
| | `txgraph:remove_transaction` | graph_idx |
| | `txgraph:add_dependency` | parent_idx, child_idx |
| | `txgraph:set_transaction_fee` | graph_idx, fee |
| | `txgraph:unlink_ref` | graph_idx |
| **Staging** | `txgraph:start_staging` | (none) |
| | `txgraph:abort_staging` | (none) |
| | `txgraph:commit_staging` | (none) |
| **Query** | `txgraph:get_ancestors` | graph_idx, level |
| | `txgraph:get_descendants` | graph_idx, level |
| | `txgraph:get_cluster` | graph_idx, level |
| | `txgraph:exists` | graph_idx, level |
| | `txgraph:get_main_chunk_feerate` | graph_idx |
| | `txgraph:get_individual_feerate` | graph_idx |
| | `txgraph:compare_main_order` | idx_a, idx_b |
| | `txgraph:get_transaction_count` | level |
| | `txgraph:is_oversized` | level |
| **Variable-length** | `txgraph:get_ancestors_union` | count, level, indices_ptr |
| | `txgraph:get_descendants_union` | count, level, indices_ptr |
| | `txgraph:count_distinct_clusters` | count, level, indices_ptr |
| **Maintenance** | `txgraph:do_work` | max_cost |
| | `txgraph:get_block_builder` | (none) |
| | `txgraph:get_main_memory_usage` | (none) |
| | `txgraph:get_worst_main_chunk` | (none) |
| | `txgraph:get_main_staging_diagrams` | (none) |
| | `txgraph:trim` | (none) |

### Variable-Length Operations

`get_ancestors_union`, `get_descendants_union`, and `count_distinct_clusters` accept a set of Ref references. USDT tracepoints support at most 12 scalar arguments and cannot pass variable-length arrays directly.

Solution: use `TRACEPOINT_ACTIVE` as a gate — only when a tracer is attached, build a fixed-size stack buffer (`uint32_t[64]`), fill in each Ref's `GraphIndex`, and pass the buffer pointer as a tracepoint argument. The eBPF program reads the entire buffer via `bpf_probe_read_user`.

```cpp
if (TRACEPOINT_ACTIVE(txgraph, get_ancestors_union)) {
    uint32_t buf[MAX_TRACE]{};
    for (size_t i = 0; i < std::min(args.size(), MAX_TRACE); ++i) {
        buf[i] = GetRefIndex(*args[i]);
    }
    TRACEPOINT(txgraph, get_ancestors_union,
        (uint64_t)args.size(),
        (uint64_t)level_select,
        (uint64_t)(uintptr_t)buf);
}
```

### The Critical 64-Bit Cast

Every TRACEPOINT argument is explicitly cast to `uint64_t` or `int64_t`:

```cpp
TRACEPOINT(txgraph, add_transaction,
    (uint64_t)GetRefIndex(arg),     // originally returns uint32_t
    (int64_t)feerate.fee,           // originally int64_t
    (int64_t)feerate.size           // originally int32_t
);
```

This is not a style choice — it works around a serious BCC 0.31.0 bug. See [BCC Bug Deep Dive](#bcc-bug-deep-dive) below.

---

## Commit 2: BCC Recording Script

`contrib/tracing/txgraph/txgraph_trace_recorder.py` is a BCC-based Python script that attaches to all 27 tracepoints and writes events to a TXGTRACE binary file.

### Runtime eBPF Code Generation

BCC does not allow its builtins (`bpf_usdt_readarg`, `perf_submit`) inside C preprocessor macro expansions. This means we cannot use a single C macro template for all 27 probes.

Solution: generate a standalone C handler function for each probe from the Python `PROBES` list:

```python
PROBES = [
    ("init",             0x00, 3, False),
    ("add_transaction",  0x01, 3, False),
    ...
    ("get_ancestors_union", 0x15, 2, True),   # varlen=True
    ...
]

def generate_bpf_program():
    for name, opcode, nargs, varlen in PROBES:
        # emit trace_{name}() as a standalone C function
        # each function calls bpf_usdt_readarg + perf_submit independently
```

### Prerequisites

For complete traces, bitcoind should be started with the `TXGRAPH_WAIT_FOR_TRACER=1` environment variable. This causes bitcoind to wait for the tracer to attach before mempool initialization, ensuring the trace captures every event from the INIT onwards. No special CMake flags or rebuild is required. Cluster limit parameters are automatically captured from bitcoind's runtime configuration via the `txgraph:init` tracepoint.

### Usage

```bash
sudo python3 contrib/tracing/txgraph/txgraph_trace_recorder.py \
    -p $(pidof bitcoind) -o /tmp/trace.bin
```

---

## Commit 3: Trace Analysis Script

`contrib/tracing/txgraph/analyze_trace.py` parses TXGTRACE files and reports mempool cluster topology statistics:

```bash
python3 contrib/tracing/txgraph/analyze_trace.py /tmp/trace.bin
```

### Features

- **Peak and final state**: tracks the peak transaction count and snapshots the graph at that moment
- **Cluster size distribution**: discovers connected components via BFS and counts clusters of each size
- **Chain-shaped cluster identification**: a cluster is "chain-shaped" if every transaction has at most one parent and one child (linear A->B->C->... topology)
- **Staging correctness**: buffers mutations inside staging; applies them only on CommitStaging, discards on AbortStaging
- **Edge integrity checks**: detects stale edges referencing removed transactions

### Sample Output

```
Trace format version: 1
Parameters: max_cluster_count=64 max_cluster_size=404000 acceptable_cost=75000

Processed 86924 operations, 5000 CommitStagings
Peak mempool size: 3744 transactions (at op #61440)

============================================================
  Peak state (3744 transactions)
  3744 transactions, 1129 clusters
============================================================

    Size    Clusters      Chains   Non-chain    Chain%
    ----    --------      ------   ---------    ------
       1         652         652           0    100.0%
       2         265         265           0    100.0%
       3         100         100           0    100.0%
       ...
      25           1           0           1      0.0%
    ----    --------      ------   ---------
   TOTAL        1129        1121           8     99.3%

  Transactions in chain clusters:       3624 (96.8%)
  Transactions in non-chain clusters:    120 ( 3.2%)
```

---

## Commit 4: txgraph-replay Tool

`txgraph-replay` is a standalone C++ executable that reads a TXGTRACE file, replays all operations, and reports per-entry-point timing statistics.

Built separately from Bitcoin Core via `contrib/tracing/txgraph/build_replay.sh`, which links against pre-built Bitcoin Core libraries. No modifications to Bitcoin Core's CMake build system are required.

When bitcoind is started with `TXGRAPH_WAIT_FOR_TRACER=1`, it waits for a tracer to attach before mempool initialization, followed by a 2-second grace period. The grace period is necessary because BCC attaches probes (incrementing semaphores) during the `BPF()` constructor, but the perf ring buffer is not ready until `open_perf_buffer()` is called after the constructor returns — without the delay, early events would be silently dropped.

### Build

```bash
# Build Bitcoin Core first (standard build, no special flags needed)
cmake -B build
cmake --build build -j$(nproc)

# Build txgraph-replay separately
contrib/tracing/txgraph/build_replay.sh
```

### Usage

```bash
./build/bin/txgraph-replay /tmp/trace.bin
```

---

## BCC Bug Deep Dive

### Symptoms

During initial testing, every `ADD_TX` event recorded by the BCC script had `graph_idx` and `size` equal to zero:

```
op#4  ADD_TX idx=0 fee=99324 size=0
op#11 ADD_TX idx=0 fee=30000 size=0
op#18 ADD_TX idx=0 fee=8296  size=0
```

The `fee` field (int64_t, natively 8 bytes) was correct, but `graph_idx` (uint32_t, 4 bytes) and `size` (int32_t, 4 bytes) were always zero.

### Investigation

**Step 1: Confirm the problem is not in trace writing.**

Hex-dumping the trace file confirmed that the binary data actually contained zeros — not a parsing error.

**Step 2: Examine the ELF USDT notes.**

```bash
readelf -n bitcoind | grep -A4 'add_transaction'
```

The argument descriptors showed:

```
Arguments: -4@20(%rsp) -8@24(%rsp) -4@32(%rsp)
```

The first argument (`graph_idx`) has descriptor `4@20(%rsp)` — 4 bytes at stack offset rsp+20. The second (`fee`) is `8@24(%rsp)` — 8 bytes.

**Step 3: Write a debug BCC script.**

We created a diagnostic script that reads the same tracepoint argument two ways simultaneously:

```c
// Method 1: BCC builtin bpf_usdt_readarg
bpf_usdt_readarg(1, ctx, &e.usdt_arg0);  // read graph_idx

// Method 2: raw stack read
void *sp = (void *)PT_REGS_SP(ctx);
u32 raw_val = 0;
bpf_probe_read_user(&raw_val, sizeof(raw_val), sp + 20);
e.raw_arg0 = raw_val;                     // read the same location
```

Result: `bpf_usdt_readarg` returned 0, while `bpf_probe_read_user` returned the correct value.

**Step 4: Identify the root cause.**

`bpf_usdt_readarg` is implemented by BCC generating eBPF bytecode at runtime. It reads the argument descriptor from the ELF note (e.g., `4@20(%rsp)`) and emits the corresponding eBPF load instructions.

The bug: **BCC 0.31.0 fails to correctly handle 4-byte stack argument descriptors.** When the argument type is `4@offset(%rsp)` (4-byte signed or unsigned), the generated eBPF code returns zero. Only `8@offset(%rsp)` (8-byte) descriptors work correctly.

This explains why:
- `fee` (int64_t -> `8@24(%rsp)`) was correct
- `graph_idx` (uint32_t -> `4@20(%rsp)`) was always zero
- `size` (int32_t -> `-4@32(%rsp)`) was always zero

### The Fix

Cast all TRACEPOINT arguments to 64-bit types:

```cpp
// Before (broken with BCC 0.31.0)
TRACEPOINT(txgraph, add_transaction,
    GetRefIndex(arg),    // uint32_t -> 4@(%rsp) -> BCC returns 0
    feerate.fee,         // int64_t  -> 8@(%rsp) -> works
    feerate.size         // int32_t  -> 4@(%rsp) -> BCC returns 0
);

// After (fixed)
TRACEPOINT(txgraph, add_transaction,
    (uint64_t)GetRefIndex(arg),    // -> 8@(%rsp) -> works
    (int64_t)feerate.fee,          // -> 8@(%rsp) -> works
    (int64_t)feerate.size          // -> 8@(%rsp) -> works
);
```

After casting, the compiler generates `STAP_PROBEV` inline assembly with 8-byte operand constraints, producing `8@offset(%rsp)` descriptors in the ELF note. BCC can then read these correctly.

### Implications for Other Tracepoints

This bug does not only affect TxGraph tracepoints — it may affect any USDT probe in Bitcoin Core that uses arguments smaller than 8 bytes. Until BCC fixes this bug, the safe approach is to **cast all TRACEPOINT arguments to 64-bit types**.

---

## Full Workflow

### 1. Build bitcoind and txgraph-replay

```bash
cmake -B build
cmake --build build -j$(nproc)
contrib/tracing/txgraph/build_replay.sh
```

### 2. Start bitcoind

```bash
TXGRAPH_WAIT_FOR_TRACER=1 ./build/bin/bitcoind -datadir=/path/to/.bitcoin
```

### 3. Record a trace

```bash
sudo python3 contrib/tracing/txgraph/txgraph_trace_recorder.py \
    -p $(pidof bitcoind) -o /tmp/trace.bin
# Ctrl+C to stop recording
```

### 4. Analyze cluster topology

```bash
python3 contrib/tracing/txgraph/analyze_trace.py /tmp/trace.bin
```

### 5. Performance comparison via replay

```bash
# On branch A
./build-A/bin/txgraph-replay /tmp/trace.bin > result-A.txt

# On branch B
./build-B/bin/txgraph-replay /tmp/trace.bin > result-B.txt

diff result-A.txt result-B.txt
```

---

## Docker Requirements

Running BCC inside a Docker container requires:

```bash
docker run --privileged \
    -v /lib/modules:/lib/modules:ro \
    -v /usr/src:/usr/src:ro \
    ...
```

- `--privileged`: eBPF requires CAP_SYS_ADMIN
- `/lib/modules` and `/usr/src`: BCC needs kernel headers to compile eBPF programs
- The container must have `bcc` (`python3-bpfcc`) and `kmod` packages installed

---

## File Inventory

| File | Purpose |
|------|---------|
| `src/txgraph.cpp` | 27 USDT tracepoints (semaphore declarations + TRACEPOINT calls) |
| `src/txmempool.cpp` | `txgraph:init` tracepoint + `TXGRAPH_WAIT_FOR_TRACER` wait logic |
| `contrib/tracing/txgraph/txgraph_trace_recorder.py` | BCC recording script |
| `contrib/tracing/txgraph/analyze_trace.py` | Trace analysis script (cluster topology) |
| `contrib/tracing/txgraph/txgraph_replay.cpp` | C++ replay tool |
| `contrib/tracing/txgraph/CMakeLists.txt` | Standalone CMake build for txgraph-replay |
| `contrib/tracing/txgraph/build_replay.sh` | Convenience build script |
