# 基于 USDT 的 TxGraph 追踪管线：录制、分析与回放

[English](txgraph-usdt-tracing.en.md)

---

> 本文介绍一套基于 USDT tracepoint 的 TxGraph 追踪管线：在 TxGraph 的每个公开 API 中埋入 27 个 USDT 探针，用 BCC Python 脚本录制到二进制 trace 文件，再用分析脚本和 replay 工具进行离线研究。过程中我们发现并解决了 BCC 0.31.0 的一个 4 字节 USDT 栈参数读取 bug。
>
> 前置阅读：[Bitcoin Core USDT Tracing：原理与实现](usdt-tracing.zh.md)、[TxGraph Trace & Replay（装饰器方案）](txgraph-trace-replay.zh.md)

## 与装饰器方案的对比

[TxGraph Trace & Replay](txgraph-trace-replay.zh.md) 使用编译时装饰器（`TracingTxGraph`）包装 `TxGraphImpl`，将所有 API 调用写入 trace 文件。它的优势是不需要 root 权限，不依赖 BCC/eBPF 基础设施。

本文介绍的 **USDT 方案**走了另一条路：

| | 装饰器方案 | USDT 方案 |
|---|---|---|
| **侵入性** | 需要 `TracingTxGraph` 包装类 + `g_txgraph_on_unlink_ref` 全局回调 | 仅在函数体中插入 `TRACEPOINT` 宏（未挂钩时一次分支判断，开销可忽略） |
| **编译要求** | `-DWITH_TXGRAPH_TRACING=ON`，独立编译开关 | 只需 `-DWITH_USDT=ON`（Bitcoin Core 默认已启用） |
| **录制方式** | bitcoind 内部直接写文件 | 外部 BCC Python 脚本通过 eBPF 挂钩 |
| **运行时要求** | 无（环境变量激活） | 需要 root + BCC + 内核头文件 |
| **是否捕获 ~Ref()** | 需要全局回调 hack | TRACEPOINT 直接放在 `~Ref()` 中 |

两种方案产生兼容的 TXGTRACE 二进制格式，共享同一个 `txgraph-replay` 回放工具。

---

## 组件概览

```
bitcoind (27 USDT tracepoints)
    │
    │ eBPF attach
    ▼
txgraph_trace_recorder.py (BCC)  ──→  trace.bin (TXGTRACE format)
                                          │
                          ┌───────────────┼───────────────┐
                          ▼                               ▼
                  analyze_trace.py              txgraph-replay
                  (集群拓扑分析)                (性能回放对比)
```

四个组件对应四个 commit：

1. **txgraph.cpp / txmempool.cpp** — 27 个 USDT tracepoint
2. **txgraph_trace_recorder.py** — BCC 录制脚本
3. **analyze_trace.py** — trace 分析脚本
4. **txgraph-replay** — C++ 回放工具

---

## Commit 1：USDT Tracepoints

### 探针列表

在 `src/txgraph.cpp` 和 `src/txmempool.cpp` 中添加 27 个 tracepoint，覆盖 TxGraph 的全部公开 API：

| 类别 | Tracepoint | 参数 |
|------|------------|------|
| **初始化** | `txgraph:init` | max_cluster_count, max_cluster_size, acceptable_cost |
| **Mutation** | `txgraph:add_transaction` | graph_idx, fee, size |
| | `txgraph:remove_transaction` | graph_idx |
| | `txgraph:add_dependency` | parent_idx, child_idx |
| | `txgraph:set_transaction_fee` | graph_idx, fee |
| | `txgraph:unlink_ref` | graph_idx |
| **Staging** | `txgraph:start_staging` | (无) |
| | `txgraph:abort_staging` | (无) |
| | `txgraph:commit_staging` | (无) |
| **查询** | `txgraph:get_ancestors` | graph_idx, level |
| | `txgraph:get_descendants` | graph_idx, level |
| | `txgraph:get_cluster` | graph_idx, level |
| | `txgraph:exists` | graph_idx, level |
| | `txgraph:get_main_chunk_feerate` | graph_idx |
| | `txgraph:get_individual_feerate` | graph_idx |
| | `txgraph:compare_main_order` | idx_a, idx_b |
| | `txgraph:get_transaction_count` | level |
| | `txgraph:is_oversized` | level |
| **变长查询** | `txgraph:get_ancestors_union` | count, level, indices_ptr |
| | `txgraph:get_descendants_union` | count, level, indices_ptr |
| | `txgraph:count_distinct_clusters` | count, level, indices_ptr |
| **维护** | `txgraph:do_work` | max_cost |
| | `txgraph:get_block_builder` | (无) |
| | `txgraph:get_main_memory_usage` | (无) |
| | `txgraph:get_worst_main_chunk` | (无) |
| | `txgraph:get_main_staging_diagrams` | (无) |
| | `txgraph:trim` | (无) |

### 变长操作的特殊处理

`get_ancestors_union`、`get_descendants_union`、`count_distinct_clusters` 接收一组 Ref 引用。USDT tracepoint 最多传递 12 个标量参数，无法直接传递变长数组。

解决方案：用 `TRACEPOINT_ACTIVE` 门控，在有追踪器挂钩时才构建一个固定大小的栈上缓冲区（`uint32_t[64]`），将每个 Ref 的 `GraphIndex` 填入，然后将缓冲区指针作为参数传递给 TRACEPOINT。eBPF 程序通过 `bpf_probe_read_user` 读取整个缓冲区。

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

### 关键：64 位强制转型

所有 TRACEPOINT 参数都显式转型为 `uint64_t` 或 `int64_t`：

```cpp
TRACEPOINT(txgraph, add_transaction,
    (uint64_t)GetRefIndex(arg),     // 原本返回 uint32_t
    (int64_t)feerate.fee,           // 原本是 int64_t
    (int64_t)feerate.size           // 原本是 int32_t
);
```

这不是代码风格偏好，而是为了规避 BCC 0.31.0 的一个严重 bug。详见下文 [BCC Bug 深度分析](#bcc-bug-深度分析) 一节。

---

## Commit 2：BCC 录制脚本

`contrib/tracing/txgraph/txgraph_trace_recorder.py` 是一个基于 BCC 的 Python 脚本，挂钩所有 27 个 tracepoint，将事件写入 TXGTRACE 二进制文件。

### 运行时 eBPF 代码生成

BCC 不允许在 C 宏展开中使用 `bpf_usdt_readarg`、`perf_submit` 等内建函数。因此不能用 C 宏为 27 个探针生成统一的处理函数。

解决方案：从 Python 的 `PROBES` 数据定义列表出发，为每个探针生成一个独立的 C 处理函数：

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
        # 生成 trace_{name}() 函数
        # 每个函数独立调用 bpf_usdt_readarg + perf_submit
```

### 使用前提

要录制完整的 trace，需以 `TXGRAPH_WAIT_FOR_TRACER=1` 环境变量启动 bitcoind。此环境变量使 bitcoind 在 mempool 初始化前等待追踪器 attach，确保 trace 从 INIT 事件开始完整录制。不需要特殊的 CMake 编译选项。集群限制等参数由 `txgraph:init` tracepoint 从 bitcoind 运行时配置中自动捕获。

### 使用方法

```bash
sudo python3 contrib/tracing/txgraph/txgraph_trace_recorder.py \
    -p $(pidof bitcoind) -o /tmp/trace.bin
```

---

## Commit 3：Trace 分析脚本

`contrib/tracing/txgraph/analyze_trace.py` 解析 TXGTRACE 文件，输出 mempool 的集群拓扑统计：

```bash
python3 contrib/tracing/txgraph/analyze_trace.py /tmp/trace.bin
```

### 功能

- **峰值与最终状态**：追踪 mempool 中交易数量的峰值，保存峰值时刻的 graph 快照
- **集群大小分布**：通过 BFS 发现连通分量，统计每种大小的集群数量
- **链形集群识别**：如果集群中每笔交易最多有 1 个父交易和 1 个子交易（线性 A→B→C→… 拓扑），则标记为"链形"
- **Staging 正确性**：缓冲 staging 内的 mutation，只在 CommitStaging 时应用，AbortStaging 时丢弃
- **边完整性检查**：检测引用已删除交易的悬空边

### 输出示例

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

## Commit 4：txgraph-replay 回放工具

`txgraph-replay` 是一个 C++ 独立可执行文件，读取 TXGTRACE 文件并回放所有操作，按入口点统计耗时。

通过 `contrib/tracing/txgraph/build_replay.sh` 从 Bitcoin Core 独立编译，链接预编译的 Bitcoin Core 静态库。不需要修改 Bitcoin Core 的 CMake 构建系统。

当 bitcoind 以 `TXGRAPH_WAIT_FOR_TRACER=1` 环境变量启动时，它在 mempool 初始化前等待追踪器 attach，并在检测到 attach 后等待 2 秒 grace period。这个 grace period 是必要的，因为 BCC 在 `BPF()` 构造函数中附加探针（semaphore 递增），但 perf ring buffer 要在 `open_perf_buffer()` 调用后才就绪——如果不等待，早期事件会被丢弃。

### 编译

```bash
# 先编译 Bitcoin Core（标准编译，无需特殊选项）
cmake -B build
cmake --build build -j$(nproc)

# 独立编译 txgraph-replay
contrib/tracing/txgraph/build_replay.sh
```

### 使用

```bash
./build/bin/txgraph-replay /tmp/trace.bin
```

---

## BCC Bug 深度分析

### 现象

最初测试时，BCC 录制脚本录到的所有 `ADD_TX` 操作中 `graph_idx` 和 `size` 都是 0：

```
op#4  ADD_TX idx=0 fee=99324 size=0
op#11 ADD_TX idx=0 fee=30000 size=0
op#18 ADD_TX idx=0 fee=8296  size=0
```

`fee` 字段（int64_t，原本就是 8 字节）正确，但 `graph_idx`（uint32_t，4 字节）和 `size`（int32_t，4 字节）始终为 0。

### 调查过程

**第一步：确认问题不在 trace 写入端。**

用十六进制查看 trace 文件，确认二进制数据中确实写的是 0，不是解析错误。

**第二步：检查 ELF USDT note。**

```bash
readelf -n bitcoind | grep -A4 'add_transaction'
```

输出的参数描述符：

```
Arguments: -4@20(%rsp) -8@24(%rsp) -4@32(%rsp)
```

第一个参数 (`graph_idx`) 的描述符是 `4@20(%rsp)` —— 4 字节，存储在栈上 rsp+20 的位置。第二个参数 (`fee`) 是 `8@24(%rsp)` —— 8 字节。

**第三步：编写调试 BCC 脚本。**

创建了一个调试脚本，对同一个 tracepoint 同时使用两种方式读取参数：

```c
// 方式 1: BCC 内建 bpf_usdt_readarg
bpf_usdt_readarg(1, ctx, &e.usdt_arg0);  // 读 graph_idx

// 方式 2: 直接从栈上读取
void *sp = (void *)PT_REGS_SP(ctx);
u32 raw_val = 0;
bpf_probe_read_user(&raw_val, sizeof(raw_val), sp + 20);
e.raw_arg0 = raw_val;                     // 读同一个位置
```

结果：`bpf_usdt_readarg` 返回 0，而 `bpf_probe_read_user` 返回正确值！

**第四步：定位根因。**

`bpf_usdt_readarg` 由 BCC 在运行时生成 eBPF 字节码实现。它读取 ELF note 中的参数描述符（如 `4@20(%rsp)`），然后生成对应的 eBPF load 指令。

问题出在：**BCC 0.31.0 无法正确处理 4 字节的栈参数描述符**。当参数类型是 `4@offset(%rsp)`（4 字节有符号或无符号）时，BCC 生成的 eBPF 代码读取结果为 0；而 `8@offset(%rsp)`（8 字节）则正常工作。

这解释了为什么：
- `fee`（int64_t → `8@24(%rsp)`）正确
- `graph_idx`（uint32_t → `4@20(%rsp)`）始终为 0
- `size`（int32_t → `-4@32(%rsp)`）始终为 0

### 解决方案

将所有 TRACEPOINT 参数强制转型为 64 位类型：

```cpp
// 修复前
TRACEPOINT(txgraph, add_transaction,
    GetRefIndex(arg),    // uint32_t → 4@(%rsp) → BCC 返回 0
    feerate.fee,         // int64_t  → 8@(%rsp) → 正常
    feerate.size         // int32_t  → 4@(%rsp) → BCC 返回 0
);

// 修复后
TRACEPOINT(txgraph, add_transaction,
    (uint64_t)GetRefIndex(arg),    // → 8@(%rsp) → 正常
    (int64_t)feerate.fee,          // → 8@(%rsp) → 正常
    (int64_t)feerate.size          // → 8@(%rsp) → 正常
);
```

转型后，编译器生成 `STAP_PROBEV` 内联汇编时会使用 8 字节的操作数约束，ELF note 中的描述符变为 `8@offset(%rsp)`，BCC 即可正确读取。

### 对其他 tracepoint 的启示

这个 bug 不仅影响 TxGraph tracepoint，也可能影响 Bitcoin Core 中其他使用了小于 8 字节参数的 USDT 探针。在 BCC 修复此 bug 之前，安全做法是**将所有 TRACEPOINT 参数转型为 64 位**。

---

## 完整使用流程

### 1. 编译 bitcoind 和 txgraph-replay

```bash
cmake -B build
cmake --build build -j$(nproc)
contrib/tracing/txgraph/build_replay.sh
```

### 2. 启动 bitcoind

```bash
TXGRAPH_WAIT_FOR_TRACER=1 ./build/bin/bitcoind -datadir=/path/to/.bitcoin
```

### 3. 录制 trace

```bash
sudo python3 contrib/tracing/txgraph/txgraph_trace_recorder.py \
    -p $(pidof bitcoind) -o /tmp/trace.bin
# Ctrl+C 停止录制
```

### 4. 分析集群拓扑

```bash
python3 contrib/tracing/txgraph/analyze_trace.py /tmp/trace.bin
```

### 5. 性能回放对比

```bash
# 在分支 A
./build-A/bin/txgraph-replay /tmp/trace.bin > result-A.txt

# 在分支 B
./build-B/bin/txgraph-replay /tmp/trace.bin > result-B.txt

diff result-A.txt result-B.txt
```

---

## Docker 环境要求

在 Docker 容器中运行 BCC 需要：

```bash
docker run --privileged \
    -v /lib/modules:/lib/modules:ro \
    -v /usr/src:/usr/src:ro \
    ...
```

- `--privileged`：eBPF 需要 CAP_SYS_ADMIN
- `/lib/modules` 和 `/usr/src`：BCC 需要内核头文件来编译 eBPF 程序
- 容器内需安装 `bcc`（`python3-bpfcc`）和 `kmod` 包

---

## 文件清单

| 文件 | 作用 |
|------|------|
| `src/txgraph.cpp` | 27 个 USDT tracepoint（semaphore 声明 + TRACEPOINT 调用） |
| `src/txmempool.cpp` | `txgraph:init` tracepoint + `TXGRAPH_WAIT_FOR_TRACER` 等待逻辑 |
| `contrib/tracing/txgraph/txgraph_trace_recorder.py` | BCC 录制脚本 |
| `contrib/tracing/txgraph/analyze_trace.py` | trace 分析脚本（集群拓扑） |
| `contrib/tracing/txgraph/txgraph_replay.cpp` | C++ 回放工具 |
| `contrib/tracing/txgraph/CMakeLists.txt` | txgraph-replay 独立 CMake 构建 |
| `contrib/tracing/txgraph/build_replay.sh` | 便捷编译脚本 |
