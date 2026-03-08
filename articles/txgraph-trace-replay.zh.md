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

### 编译时开关

通过 cmake 选项 `WITH_TXGRAPH_TRACING` 控制，默认关闭：

```cmake
option(WITH_TXGRAPH_TRACING "Enable TxGraph binary trace recording and replay tool." OFF)
```

开启后：
- 编译 `txgraph_tracing.cpp` 到 `bitcoin_node`
- 定义 `ENABLE_TXGRAPH_TRACING` 预处理宏
- 构建 `txgraph-replay` 独立工具
- 在 `txgraph.h` 和 `txgraph.cpp` 中启用 Ref 的 `m_wrapper` 支持（见下文）

未开启时，对主代码完全无影响——宏控代码被编译器忽略，无运行时开销。

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
| **Mutation** | ADD_TX, REMOVE_TX, ADD_DEP, SET_FEE, UNLINK_REF | 修改 graph 状态 |
| **Trigger** | GET_BLOCK_BUILDER, DO_WORK, CompareMainOrder, GetAncestors, ... | 触发 ApplyDependencies 的入口点 |
| **Staging** | START_STAGING, ABORT_STAGING, COMMIT_STAGING | Staging 操作 |

纯查询（HaveStaging, IsOversized, Exists 等）不触发 ApplyDependencies，不录制。

### Ref 标识

使用 `GetRefIndex(ref)` 获取稳定的 `GraphIndex`（由内部实现分配），无需维护地址映射表。
这是 `TxGraph` 的 protected static 方法，装饰器子类可以直接访问。

---

## 核心设计挑战：拦截 Ref 析构

### 问题

装饰器模式有一个根本困难：**Ref 的析构绕过了 wrapper**。

`TracingTxGraph::AddTransaction()` 调用 `m_impl->AddTransaction(ref, ...)`，
这会设置 `ref.m_graph = m_impl`（指向内部实现，不是 wrapper）。
因此当 Ref 被销毁时：

```
~Ref()  →  m_graph->UnlinkRef()  →  直接进入 TxGraphImpl
                                      ↑ 绕过了 TracingTxGraph！
```

TracingTxGraph 完全不知道 Ref 被销毁了，无法在 trace 中记录这个事件。

### 解决方案：m_wrapper 指针

在 Ref 类中加一个条件编译的 `m_wrapper` 指针：

```cpp
// txgraph.h — Ref 类内部
#ifdef ENABLE_TXGRAPH_TRACING
    TxGraph* m_wrapper = nullptr;
#endif
```

当 `TracingTxGraph::AddTransaction()` 被调用时，设置 `ref.m_wrapper = this`。
然后 `~Ref()` 优先检查 m_wrapper：

```cpp
// txgraph.cpp — ~Ref()
TxGraph::Ref::~Ref() {
    if (m_graph) {
#ifdef ENABLE_TXGRAPH_TRACING
        if (m_wrapper) {
            m_wrapper->UnlinkRef(m_index);  // → TracingTxGraph 写 UNLINK_REF + 转发
            m_graph = nullptr;
            return;
        }
#endif
        m_graph->UnlinkRef(m_index);  // 普通路径
        m_graph = nullptr;
    }
}
```

这样 TracingTxGraph 能拦截所有 Ref 析构，发出 UNLINK_REF 记录，
然后通过 `ForwardUnlinkRef` 转发给真正的实现。

### 为什么需要 ForwardUnlinkRef？

`UnlinkRef` 和 `UpdateRef` 是 TxGraph 的 **protected** 方法。
虽然 TracingTxGraph 继承了 TxGraph，可以调用自己的 protected 方法，
但 C++ 不允许通过基类指针（`m_impl`）访问另一个对象的 protected 方法。

解决方法是在 TxGraph 中加两个 static 辅助函数（同样宏控）：

```cpp
#ifdef ENABLE_TXGRAPH_TRACING
    static void ForwardUpdateRef(TxGraph& target, GraphIndex index, Ref& new_location) noexcept {
        target.UpdateRef(index, new_location);
    }
    static void ForwardUnlinkRef(TxGraph& target, GraphIndex index) noexcept {
        target.UnlinkRef(index);
    }
#endif
```

这些是 TxGraph 自身的成员函数，所以可以访问任意 TxGraph 对象的 protected 方法。

### REMOVE_TX vs UNLINK_REF

两者代表不同的语义：

| 操作 | 时机 | 含义 |
|------|------|------|
| REMOVE_TX | `RemoveTransaction()` 调用时 | 从 graph 中逻辑删除交易（但 Ref 对象仍存活） |
| UNLINK_REF | `~Ref()` 析构时 | Ref 对象被销毁，释放 GraphIndex 供复用 |

在实际运行中，一笔交易的生命周期是：
```
AddTransaction → ... → RemoveTransaction → ... → mapTx.erase → ~Ref → UNLINK_REF
```

RemoveTransaction 和 ~Ref 之间可能有显著时间差。
replay 工具需要知道这两个时间点才能正确模拟 Ref 的生命周期。

---

## Replay 工具

`txgraph-replay` 是独立可执行文件，读取 trace 文件，重建 TxGraph 并回放所有操作：

```bash
./build/bin/txgraph-replay /tmp/txgraph.trace
```

**Mutation 操作**只执行不计时（它们本身很快），
**Trigger 和 Staging 操作**使用 `steady_clock` 计时，按入口点分别统计。

### Ref 生命周期管理

replay 工具维护一个 `refs` map（`GraphIndex → unique_ptr<Ref>`）。

- **ADD_TX**: 创建新 Ref 并加入 map
- **REMOVE_TX**: 调用 `graph->RemoveTransaction()`，但 **Ref 保持存活**在 map 中
- **UNLINK_REF**: 从 map 中 erase（销毁 Ref → `~Ref()` → `graph->UnlinkRef()`）

注意：trace 中 UNLINK_REF 记录的 `graph_idx` 是析构时刻的 `m_index`。
如果中间发生过 Compact（内部索引压缩），这个值可能与 ADD_TX 时的值不同，
导致 `refs.erase()` 找不到对应的 key。这种情况下 erase 是 no-op，
Ref 在程序结束时自然销毁，不影响性能测量的正确性。

### 输出示例

```
=== TxGraph Replay Summary ===
Total ops replayed: 123456

Mutations (not timed):
  ADD_TX:                       45000
  REMOVE_TX:                    12000
  ADD_DEP:                      38000
  SET_FEE:                       2000
  UNLINK_REF:                   45000

Timed entry points:
  Entry point                       Calls      Total (us)       Avg (us)
  ---                                 ---             ---            ---
  StartStaging                       5000         120000          24.00
  CommitStaging                      5000        1850000         370.00
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
| `src/txgraph_tracing.h` | TxGraphTraceOp 枚举（含 UNLINK_REF）+ MakeTracingTxGraph 声明 |
| `src/txgraph_tracing.cpp` | TracingTxGraph 装饰器实现（~27 个虚方法） |
| `src/txgraph_replay.cpp` | 独立回放工具，按入口点计时统计 |
| `src/txgraph.h` | Ref 加 `m_wrapper`，TxGraph 加 `GetRefWrapper`/`ForwardUnlinkRef`/`ForwardUpdateRef`（宏控） |
| `src/txgraph.cpp` | `~Ref()` 和 `Ref(Ref&&)` 中处理 `m_wrapper`（宏控） |
| `src/txmempool.cpp` | `#ifdef` 集成代码（MakeTracingTxGraph 调用） |
| `CMakeLists.txt` | WITH_TXGRAPH_TRACING 选项 |
| `src/CMakeLists.txt` | 条件编译和链接 |
| `contrib/txgraph_tracing/analyze_trace.py` | Python trace 分析脚本（集群分布、链形拓扑） |
| `contrib/txgraph_tracing/periodic_gbt.sh` | 周期性调用 getblocktemplate 的辅助脚本 |

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

**为什么需要修改 txgraph.h/txgraph.cpp？**
由于 Ref.m_graph 指向内部实现而非 wrapper，~Ref() 会绕过 TracingTxGraph。
在 Ref 中加入 m_wrapper 指针是拦截析构通知的最干净方案。
所有修改均在 `#ifdef ENABLE_TXGRAPH_TRACING` 宏控下，未开启时对编译产物零影响。
