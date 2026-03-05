# TxGraph Trace & Replay: Reproducible Performance Comparison Tool

[中文版](txgraph-trace-replay.zh.md)

---

## Motivation

When optimising TxGraph internals (e.g. the ChainCluster fast path), we need a way to
**precisely compare different implementations under identical workloads**.

Profiling a live node with perf or callgrind is possible but problematic:
- Two runs see different mempool states, making results incomparable
- Network and disk I/O noise obscures TxGraph's own cost

**Trace & Replay** solves both problems: record every TxGraph API call on a real node,
then replay the trace with a standalone tool on different branches — eliminating external
noise and ensuring a fair comparison.

---

## Design

### Core Idea: Decorator Pattern

```
CTxMemPool  →  TracingTxGraph (wrapper)  →  TxGraphImpl (real)
                    │
                    ↓
              trace file (binary)
```

`TracingTxGraph` inherits from `TxGraph` and wraps the real `TxGraphImpl`.
For each API call it:
1. Writes the opcode and arguments to a binary trace file
2. Forwards the call to the inner implementation

**Zero intrusion**: no modifications to `txgraph.h` or `txgraph.cpp` — all tracing
logic lives in separate files.

### Compile-Time Gating

Controlled by the cmake option `WITH_TXGRAPH_TRACING`, which defaults to OFF:

```cmake
option(WITH_TXGRAPH_TRACING "Enable TxGraph binary trace recording and replay tool." OFF)
```

When enabled:
- Compiles `txgraph_tracing.cpp` into `bitcoin_node`
- Defines the `ENABLE_TXGRAPH_TRACING` preprocessor macro
- Builds the `txgraph-replay` standalone tool

When disabled, there is zero impact on the main codebase — no extra includes, no
runtime checks.

### Runtime Activation

When compiled with tracing support, recording is activated by setting the
`TXGRAPH_TRACE_FILE` environment variable:

```bash
TXGRAPH_TRACE_FILE=/tmp/txgraph.trace ./build/bin/bitcoind -signet
```

If the variable is unset or empty, no trace is recorded even if the tracing code
is compiled in.

### Binary Trace Format

```
Header:  "TXGTRACE" (8 bytes) + uint32 version=1
INIT:    0x00 [uint32 max_cluster_count][uint64 max_cluster_size][uint64 acceptable_cost]
ADD_TX:  0x01 [uint32 graph_idx][int64 fee][int32 size]
...
```

All multi-byte integers are little-endian. Opcodes fall into three categories:

| Category | Opcodes | Description |
|----------|---------|-------------|
| **Mutation** | ADD_TX, REMOVE_TX, ADD_DEP, SET_FEE | Modify graph state |
| **Trigger** | GET_BLOCK_BUILDER, DO_WORK, CompareMainOrder, GetAncestors, ... | Entry points that trigger ApplyDependencies |
| **Staging** | START_STAGING, ABORT_STAGING, COMMIT_STAGING | Staging operations |

Pure queries (HaveStaging, IsOversized, Exists, etc.) do not trigger
ApplyDependencies and are not recorded.

### Ref Identification

The wrapper uses `GetRefIndex(ref)` to obtain the stable `GraphIndex` assigned by the
inner implementation — no address-to-ID mapping table needed.
This is a protected static method on `TxGraph`, accessible to the decorator subclass.

---

## Replay Tool

`txgraph-replay` is a standalone executable that reads a trace file, reconstructs a
TxGraph, and replays every operation:

```bash
./build/bin/txgraph-replay /tmp/txgraph.trace
```

**Mutations** are executed but not timed (they are inherently fast).
**Trigger and Staging operations** are timed with `steady_clock`, with statistics
accumulated per entry point.

Sample output:

```
TxGraph parameters: max_cluster_count=64, max_cluster_size=400000, acceptable_cost=75000

=== TxGraph Replay Summary ===
Total ops replayed: 123456

Mutations (not timed):
  ADD_TX:                       45000
  REMOVE_TX:                    12000
  ADD_DEP:                      38000
  SET_FEE:                       2000

Timed entry points:
  Entry point                       Calls      Total (us)       Avg (us)
  ---                                 ---             ---            ---
  StartStaging                       5000         120000          24.00
  CommitStaging                      5000        1850000         370.00
  AbortStaging                       1200          28000          23.33
  GetAncestors                      30000         450000          15.00
  GetDescendants                    28000         420000          15.00
  CompareMainOrder                  15000         180000          12.00
  GetBlockBuilder                      50       12000000      240000.00
  ...
                                      ---             ---
  TOTAL                             84250       15048000
```

Replay the same trace on different branches and directly compare TOTAL or per-entry-point
timings.

---

## Usage

### 1. Build with Tracing Support

```bash
cmake -B build -DWITH_TXGRAPH_TRACING=ON
cmake --build build
```

### 2. Record a Trace

```bash
TXGRAPH_TRACE_FILE=/tmp/txgraph.trace ./build/bin/bitcoind -signet
# Wait for the mempool to accumulate enough transactions, then stop the node
```

### 3. Replay on Different Branches

```bash
# Branch A (baseline)
git checkout before_chaincluster
cmake -B build-A -DWITH_TXGRAPH_TRACING=ON
cmake --build build-A --target txgraph-replay
./build-A/bin/txgraph-replay /tmp/txgraph.trace > result-A.txt

# Branch B (optimised)
git checkout chaincluster
cmake -B build-B -DWITH_TXGRAPH_TRACING=ON
cmake --build build-B --target txgraph-replay
./build-B/bin/txgraph-replay /tmp/txgraph.trace > result-B.txt

# Compare
diff result-A.txt result-B.txt
```

---

## File Inventory

| File | Purpose |
|------|---------|
| `src/txgraph_tracing.h` | TxGraphTraceOp enum + MakeTracingTxGraph declaration |
| `src/txgraph_tracing.cpp` | TracingTxGraph decorator (~27 virtual methods) |
| `src/txgraph_replay.cpp` | Standalone replay tool with per-entry-point timing |
| `src/txmempool.cpp` | 6-line `#ifdef` integration |
| `CMakeLists.txt` | WITH_TXGRAPH_TRACING option |
| `src/CMakeLists.txt` | Conditional compilation and linking |

---

## Design Trade-offs

**Why not USDT/eBPF?**
USDT tracepoints are well suited for live monitoring but cannot record a complete
sequence of operations for offline replay. We need the ability to "record once, replay
on different implementations many times".

**Why not add timing inside txgraph.cpp?**
Too invasive — it would touch every public method in the core implementation.
The decorator pattern keeps tracing logic fully isolated and does not affect the
readability or maintainability of the core code.

**Why are Mutations not timed?**
AddTransaction, RemoveTransaction, etc. are O(1) queue appends. The real work happens
in the subsequent Trigger operations that invoke ApplyDependencies internally. Timing
mutations would only introduce noise.
