# TxGraph Trace & Replay：可复现的性能对比工具

[English](txgraph-trace-replay.en.md)

---

## 动机

在优化 TxGraph 实现（如 ChainCluster 快速路径）时，需要一种方式来**精确对比不同实现在相同工作负载下的性能**。

直接在运行中的节点上用 perf 或 callgrind 采样虽然可行，但存在问题：
- 两次测量的 mempool 状态不同，缺乏可比性
- 网络和磁盘 I/O 的噪声掩盖了 TxGraph 自身的耗时

**Trace & Replay** 方法解决了这两个问题：在真实节点上录制所有 TxGraph API 调用序列，
然后用独立工具在不同分支上回放，消除外部噪声，确保对比公平。

---

## 设计

### 核心思路：装饰器模式

```
CTxMemPool  →  TracingTxGraph (wrapper)  →  TxGraphImpl (real)
                    │
                    ↓
              trace file (binary)
```

`TracingTxGraph` 继承 `TxGraph`，包装真正的 `TxGraphImpl`。
每个 API 调用：
1. 将操作码和参数写入二进制 trace 文件
2. 转发给内部实现

**零侵入**：不修改 `txgraph.h` 和 `txgraph.cpp`，所有 trace 逻辑在独立文件中。

### 编译时开关

通过 cmake 选项 `WITH_TXGRAPH_TRACING` 控制，默认关闭：

```cmake
option(WITH_TXGRAPH_TRACING "Enable TxGraph binary trace recording and replay tool." OFF)
```

开启后：
- 编译 `txgraph_tracing.cpp` 到 `bitcoin_node`
- 定义 `ENABLE_TXGRAPH_TRACING` 预处理宏
- 构建 `txgraph-replay` 独立工具

未开启时，对主代码完全无影响——无额外 include、无运行时检查。

### 运行时激活

编译时开启后，通过环境变量 `TXGRAPH_TRACE_FILE` 指定 trace 文件路径来激活录制：

```bash
TXGRAPH_TRACE_FILE=/tmp/txgraph.trace ./build/bin/bitcoind -signet
```

如果环境变量未设置或为空，即使编译了 tracing 代码也不会录制。

### 二进制 Trace 格式

```
Header:  "TXGTRACE" (8 bytes) + uint32 version=1
INIT:    0x00 [uint32 max_cluster_count][uint64 max_cluster_size][uint64 acceptable_cost]
ADD_TX:  0x01 [uint32 graph_idx][int64 fee][int32 size]
...
```

所有多字节整数使用小端序。操作码分为三类：

| 类别 | 操作码 | 说明 |
|------|--------|------|
| **Mutation** | ADD_TX, REMOVE_TX, ADD_DEP, SET_FEE | 修改 graph 状态 |
| **Trigger** | GET_BLOCK_BUILDER, DO_WORK, CompareMainOrder, GetAncestors, ... | 触发 ApplyDependencies 的入口点 |
| **Staging** | START_STAGING, ABORT_STAGING, COMMIT_STAGING | Staging 操作 |

纯查询（HaveStaging, IsOversized, Exists 等）不触发 ApplyDependencies，不录制。

### Ref 标识

使用 `GetRefIndex(ref)` 获取稳定的 `GraphIndex`（由内部实现分配），无需维护地址映射表。
这是 `TxGraph` 的 protected static 方法，装饰器子类可以直接访问。

---

## Replay 工具

`txgraph-replay` 是独立可执行文件，读取 trace 文件，重建 TxGraph 并回放所有操作：

```bash
./build/bin/txgraph-replay /tmp/txgraph.trace
```

**Mutation 操作**只执行不计时（它们本身很快），
**Trigger 和 Staging 操作**使用 `steady_clock` 计时，按入口点分别统计。

输出示例：

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

在不同分支上对同一 trace 文件回放，直接对比 TOTAL 或单个入口点的耗时差异。

---

## 使用步骤

### 1. 编译（带 tracing 支持）

```bash
cmake -B build -DWITH_TXGRAPH_TRACING=ON
cmake --build build
```

### 2. 录制 trace

```bash
TXGRAPH_TRACE_FILE=/tmp/txgraph.trace ./build/bin/bitcoind -signet
# 等待 mempool 积累足够交易后停止节点
```

### 3. 在不同分支上回放对比

```bash
# 分支 A (baseline)
git checkout before_chaincluster
cmake -B build-A -DWITH_TXGRAPH_TRACING=ON
cmake --build build-A --target txgraph-replay
./build-A/bin/txgraph-replay /tmp/txgraph.trace > result-A.txt

# 分支 B (优化后)
git checkout chaincluster
cmake -B build-B -DWITH_TXGRAPH_TRACING=ON
cmake --build build-B --target txgraph-replay
./build-B/bin/txgraph-replay /tmp/txgraph.trace > result-B.txt

# 对比
diff result-A.txt result-B.txt
```

---

## 文件清单

| 文件 | 作用 |
|------|------|
| `src/txgraph_tracing.h` | TxGraphTraceOp 枚举 + MakeTracingTxGraph 声明 |
| `src/txgraph_tracing.cpp` | TracingTxGraph 装饰器实现（~27 个虚方法） |
| `src/txgraph_replay.cpp` | 独立回放工具，按入口点计时统计 |
| `src/txmempool.cpp` | 6 行 `#ifdef` 集成代码 |
| `CMakeLists.txt` | WITH_TXGRAPH_TRACING 选项 |
| `src/CMakeLists.txt` | 条件编译和链接 |

---

## 设计取舍

**为什么不用 USDT/eBPF？**
USDT tracepoint 适合实时监控，但无法录制完整的操作序列以供异地回放。
我们需要的是"录制一次，在不同实现上回放多次"的能力。

**为什么不在 txgraph.cpp 内部加计时？**
侵入性太强，修改了核心代码的每个公开方法。
装饰器模式将 trace 逻辑完全隔离，不影响核心代码的可读性和可维护性。

**为什么 Mutation 不计时？**
AddTransaction、RemoveTransaction 等操作本身是 O(1) 的队列追加，
真正的工作发生在后续 Trigger 操作中触发的 ApplyDependencies。
对 Mutation 计时只会引入噪声。
