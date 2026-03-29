# Bitcoin Core USDT Tracing：原理与实现

[English](usdt-tracing.en.md)

---

> 本文深入分析 Bitcoin Core 中 USDT（User-space Statically Defined Tracing）的实现原理：从 C++ 宏如何变成 ELF 二进制中的探针，到 BPF 程序如何在运行时挂钩读取数据。同时列出所有现有 tracepoint 及其参数。
> 相关资源：[官方文档 doc/tracing.md](https://github.com/bitcoin/bitcoin/blob/master/doc/tracing.md)、[contrib/tracing/ 示例脚本](https://github.com/bitcoin/bitcoin/tree/master/contrib/tracing)

## 什么是 USDT

USDT（User-space Statically Defined Tracing）是一种在用户态程序中预埋探针的技术。与动态追踪（如对任意函数设置 uprobe）不同，USDT 探针在**编译时**就写入了二进制文件——位置确定、参数明确、接口半稳定——可被 bpftrace、BCC 等 eBPF 前端在运行时挂钩。

Bitcoin Core 使用 USDT 来观测内部行为：mempool 交易进出、UTXO 缓存变化、P2P 消息收发、区块连接、币选择等。这些探针在**无人挂钩时几乎零开销**，适合生产环境使用。

## 整体架构

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

1. 追踪脚本编译 eBPF 代码并加载到内核虚拟机
2. eBPF 程序挂钩到一个或多个 tracepoint
3. tracepoint 被触发时，参数传给 eBPF 程序
4. eBPF 程序处理参数并将结果传回用户态脚本

## 实现原理：从宏到二进制

### 第一步：声明 Semaphore

每个 tracepoint 需要一个全局 semaphore（计数信号量），用于判断是否有追踪程序挂钩。

以 `mempool:added` 为例，在 `src/txmempool.cpp` 文件顶部：

```cpp
TRACEPOINT_SEMAPHORE(mempool, added);
```

该宏定义在 `src/util/trace.h`：

```cpp
#define TRACEPOINT_SEMAPHORE(context, event) \
    unsigned short context##_##event##_semaphore __attribute__((section(".probes")))
```

展开后：

```cpp
unsigned short mempool_added_semaphore __attribute__((section(".probes")));
```

这是一个放在 ELF `.probes` section 的 2 字节全局变量，初值为 0。当 bpftrace/BCC 等工具挂钩到该 tracepoint 时自动递增，脱离时自动递减。

### 第二步：埋入 Tracepoint

在函数内部的关键位置插入 `TRACEPOINT` 宏：

```cpp
// src/txmempool.cpp  CTxMemPool::addUnchecked()
TRACEPOINT(mempool, added,
    entry.GetTx().GetHash().data(),   // arg0: txid (32 bytes)
    entry.GetTxSize(),                // arg1: vsize
    entry.GetFee()                    // arg2: fee
);
```

宏定义：

```cpp
#define TRACEPOINT(context, event, ...)                                         \
    do {                                                                        \
        if (TRACEPOINT_ACTIVE(context, event)) {                                \
            STAP_PROBEV(context, event __VA_OPT__(, ) __VA_ARGS__);             \
        }                                                                       \
    } while(0)
```

其中 `TRACEPOINT_ACTIVE` 检查 semaphore：

```cpp
#define TRACEPOINT_ACTIVE(context, event) (context##_##event##_semaphore > 0)
```

完全展开后：

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

### 第三步：STAP_PROBEV → NOP 指令 + ELF note

`STAP_PROBEV` 来自 systemtap 的 `<sys/sdt.h>` 头文件。它的核心做法是用**内联汇编**在调用点插入一条 **NOP 指令**，同时在 ELF 的 `.note.stapsdt` section 记录一条元数据。

概念上等价于：

```c
// 伪代码，实际是编译器内联汇编
__asm__ __volatile__ (
    "nop"                          // ← 一条 NOP，运行时零开销
    :                              // 无输出
    : "r"(arg1), "r"(arg2), ...   // 参数作为 input operand
                                   //   编译器把它们放进寄存器
);

// 同时在 .note.stapsdt section 写入：
//   provider  = "mempool"
//   name      = "added"
//   location  = 这条 NOP 的地址
//   semaphore = &mempool_added_semaphore 的地址
//   arguments = "-8@%rdi -8@%rsi -8@%rdx"  // 参数的寄存器位置和大小
```

### 第四步：ELF 二进制中的最终形态

编译后的 `bitcoind` 二进制包含三部分与 USDT 相关的内容：

```
bitcoind ELF binary
│
├── .text section
│     CTxMemPool::addUnchecked():
│       0x4a3f20:  nop              ← tracepoint 位置（正常执行时零开销）
│
├── .probes section
│     mempool_added_semaphore    = 0x0000   ← 2 字节计数器
│     mempool_removed_semaphore  = 0x0000
│     mempool_replaced_semaphore = 0x0000
│     mempool_rejected_semaphore = 0x0000
│     ...（所有 tracepoint 的 semaphore）
│
└── .note.stapsdt section                   ← 只读元数据表
      ┌────────────────────────────────────────────────────────┐
      │ provider:  "mempool"                                   │
      │ name:      "added"                                     │
      │ location:  0x4a3f20           (NOP 的地址)             │
      │ semaphore: 0x6b8100           (semaphore 变量的地址)   │
      │ arguments: "-8@%rdi -8@%rsi -8@%rdx"                  │
      │            (txid 在 rdi, vsize 在 rsi, fee 在 rdx)    │
      ├────────────────────────────────────────────────────────┤
      │ provider:  "mempool"                                   │
      │ name:      "removed"                                   │
      │ ...                                                    │
      └────────────────────────────────────────────────────────┘
```

可以用 `readelf` 查看：

```bash
$ readelf -n ./build/bin/bitcoind | grep NT_STAPSDT -A 4 -B 2
  stapsdt              0x0000005d  NT_STAPSDT (SystemTap probe descriptors)
    Provider: mempool
    Name: added
    Location: 0x..., Base: 0x..., Semaphore: 0x...
    Arguments: -8@%rdi -8@%rsi -8@%rdx
```

## 运行时行为：挂钩与脱离

### 无人挂钩（正常运行）

```
semaphore == 0
    → if (0 > 0) → false → 跳过参数准备
    → NOP 正常执行
    → 总开销：一次条件判断（分支预测几乎 100% 命中 not-taken）
```

### BPF 程序挂钩

```
1. bpftrace/BCC 读取 .note.stapsdt，找到 "mempool:added" 的元数据

2. semaphore++  →  mempool_added_semaphore 变为 1
   → if (1 > 0) 开始为 true
   → 参数开始被准备并放入寄存器

3. 在 NOP 地址设置 uprobe
   → 内核把 NOP 替换为 INT3（断点指令）

4. 每次执行到该地址
   → 触发 uprobe → 内核运行 eBPF 程序
   → eBPF 程序按 arguments 描述的寄存器位置读取参数

5. 脱离时：semaphore-- → 回到 0，INT3 恢复为 NOP
```

### 完整流程图

```
编译时                          运行时（无人挂钩）             运行时（BPF 挂钩后）
──────                         ────────────────             ────────────────

TRACEPOINT(mempool,added,...)  semaphore == 0               bpftrace attach:
       │                       if (0 > 0) → skip              semaphore++ → 1
       ▼                       NOP 正常执行                    NOP → INT3
  内联汇编 + ELF note          开销: 一次条件判断                │
       │                       (分支预测: not-taken)            ▼
       ▼                                                    if (1 > 0) → 准备参数
  .text:  NOP                                               INT3 触发 uprobe
  .probes: semaphore = 0                                    内核调用 eBPF 程序
  .note.stapsdt: 元数据                                      eBPF 读取寄存器中的
    (地址、参数位置)                                            txid, vsize, fee
```

## 零开销设计的关键

1. **Semaphore 门控**：`TRACEPOINT` 宏先检查 semaphore，再调用 `STAP_PROBEV`。这避免了在无人监听时准备可能昂贵的参数（比如哈希计算、数据序列化）。

2. **NOP 占位**：`STAP_PROBEV` 在代码中只插入一条 NOP 指令。CPU 执行 NOP 的开销可忽略不计。

3. **分支预测友好**：`if (semaphore > 0)` 在正常运行时几乎总是 false，CPU 分支预测器会将其学习为 not-taken，不会造成流水线停顿。

4. **对于更昂贵的参数准备**，可以使用额外的 `TRACEPOINT_ACTIVE` 检查：

```cpp
if (TRACEPOINT_ACTIVE(example, expensive_event)) {
    auto result = expensive_calculation();     // 只在有人挂钩时才执行
    TRACEPOINT(example, expensive_event, result);
}
```

5. **编译时可完全禁用**：如果未定义 `ENABLE_TRACING`，所有宏展开为空：

```cpp
#define TRACEPOINT_SEMAPHORE(context, event)          // 空
#define TRACEPOINT_ACTIVE(context, event) false        // 始终 false
#define TRACEPOINT(context, ...)                       // 空
```

## 现有 Tracepoint 一览

### Context `net`（P2P 网络）

| Tracepoint | 触发时机 | 参数 |
|---|---|---|
| `net:inbound_message` | 收到 P2P 消息 | peer_id, addr, conn_type, msg_type, msg_size, msg_bytes |
| `net:outbound_message` | 发送 P2P 消息 | peer_id, addr, conn_type, msg_type, msg_size, msg_bytes |
| `net:inbound_connection` | 接受入站连接 | peer_id, addr, conn_type, network, inbound_count |
| `net:outbound_connection` | 发起出站连接 | peer_id, addr, conn_type, network, outbound_count |
| `net:evicted_inbound_connection` | 驱逐入站连接 | peer_id, addr, conn_type, network, connected_time |
| `net:closed_connection` | 关闭连接 | peer_id, addr, conn_type, network, connected_time |
| `net:misbehaving_connection` | 对端行为异常 | peer_id, reason |

### Context `validation`（区块验证）

| Tracepoint | 触发时机 | 参数 |
|---|---|---|
| `validation:block_connected` | 区块连接到链上后 | block_hash, height, tx_count, inputs, sigops, time_ns |

### Context `utxocache`（UTXO 缓存）

| Tracepoint | 触发时机 | 参数 |
|---|---|---|
| `utxocache:add` | UTXO 加入缓存 | txid, vout, height, value, is_coinbase |
| `utxocache:spent` | UTXO 从缓存花费 | txid, vout, height, value, is_coinbase |
| `utxocache:uncache` | UTXO 被主动移出缓存 | txid, vout, height, value, is_coinbase |
| `utxocache:flush` | UTXO 缓存 flush 后 | flush_time_us, mode, coins_count, mem_usage, is_prune |

### Context `mempool`（内存池）

| Tracepoint | 触发时机 | 参数 |
|---|---|---|
| `mempool:added` | 交易加入 mempool | txid, vsize, fee |
| `mempool:removed` | 交易从 mempool 移除 | txid, reason, vsize, fee, entry_time |
| `mempool:replaced` | 交易被 RBF 替换 | old_txid, old_vsize, old_fee, old_time, new_hash, new_vsize, new_fee, is_tx |
| `mempool:rejected` | 交易被拒绝入池 | txid, reject_reason |

### Context `coin_selection`（币选择，需启用钱包）

| Tracepoint | 触发时机 | 参数 |
|---|---|---|
| `coin_selection:selected_coins` | SelectCoins 完成 | wallet, algorithm, target, waste, selected_value |
| `coin_selection:normal_create_tx_internal` | 第一次 CreateTransactionInternal 完成 | wallet, success, fee, change_pos |
| `coin_selection:attempting_aps_create_tx` | 开始尝试 Avoid Partial Spends | wallet |
| `coin_selection:aps_create_tx_internal` | 第二次（APS）CreateTransactionInternal 完成 | wallet, use_aps, success, fee, change_pos |

## 源码位置速查

| 文件 | 内容 |
|---|---|
| `src/util/trace.h` | `TRACEPOINT`、`TRACEPOINT_SEMAPHORE`、`TRACEPOINT_ACTIVE` 宏定义 |
| `src/txmempool.cpp` | `mempool:added`、`mempool:removed` 的 semaphore 声明和调用 |
| `src/validation.cpp` | `mempool:replaced`、`mempool:rejected`、`validation:block_connected`、`utxocache:flush` |
| `src/coins.cpp` | `utxocache:add`、`utxocache:spent`、`utxocache:uncache` |
| `src/net.cpp` | `net:outbound_message`、`net:*_connection` |
| `src/net_processing.cpp` | `net:inbound_message`、`net:misbehaving_connection` |
| `src/wallet/spend.cpp` | `coin_selection:*` |
| `doc/tracing.md` | 官方 tracepoint 文档 |
| `contrib/tracing/` | 示例追踪脚本（bpftrace、BCC Python） |

## 示例：用 bpftrace 监控 mempool

```bash
# 监控交易入池
sudo bpftrace -e '
    usdt:./build/bin/bitcoind:mempool:added {
        printf("tx added: vsize=%d fee=%d\n", arg1, arg2);
    }
'

# 监控交易被拒绝
sudo bpftrace -e '
    usdt:./build/bin/bitcoind:mempool:rejected {
        printf("tx rejected: reason=%s\n", str(arg1));
    }
'
```

也可以使用 Bitcoin Core 自带的 `contrib/tracing/mempool_monitor.py`（基于 BCC），它会同时挂钩所有 4 个 mempool tracepoint 并实时输出事件。

## 查看可用 Tracepoint

```bash
# 使用 GDB
$ gdb ./build/bin/bitcoind -ex 'info probes' -ex quit

# 使用 readelf
$ readelf -n ./build/bin/bitcoind | grep NT_STAPSDT -A 4

# 使用 BCC 的 tplist
$ tplist -l ./build/bin/bitcoind -v
```

注意：必须在编译时启用 `ENABLE_TRACING`（CMake 中设置 `-DWITH_USDT=ON`），二进制中才会包含 USDT 探针。如果未启用，所有 `TRACEPOINT` 宏展开为空，二进制中不会有任何 tracing 相关的代码。
