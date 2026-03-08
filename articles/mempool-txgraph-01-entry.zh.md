# 第 1 篇：CTxMemPoolEntry — 交易在内存池中的表示

[English](mempool-txgraph-01-entry.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 1 篇。
> 上一篇：[第 0 篇：架构概览](mempool-txgraph-00-architecture.zh.md) | 下一篇：[第 2 篇：TxGraph 接口 — 抽象层设计](mempool-txgraph-02-txgraph-interface.zh.md)

---

## 本篇聚焦

- 核心文件：`src/kernel/mempool_entry.h`
- 辅助文件：`src/util/feefrac.h`
- 关键类/函数：CTxMemPoolEntry, TxGraph::Ref, FeeFrac, FeePerWeight, FeePerVSize, LockPoints
- 前置阅读：第 0 篇

---

## 概述

CTxMemPoolEntry 是一笔交易进入内存池后的完整表示。它不仅包含交易本身（CTransactionRef），
还记录了费用、权重、入池时间、入池高度等元数据。

一个关键设计决策是 CTxMemPoolEntry **公开继承**自 `TxGraph::Ref`（`src/kernel/mempool_entry.h:65`），
使得每个 mempool entry 同时作为 TxGraph 中的引用句柄。这意味着 TxGraph 返回的任何
`Ref*` 指针都可以直接 `static_cast<CTxMemPoolEntry*>()` 回来，无需维护额外的映射表。

本篇将逐一讲解 CTxMemPoolEntry 的每个字段，然后深入 FeeFrac——Bitcoin Core 中精确费率比较的基础设施。

## 1. 类定义与继承关系

```cpp
// src/kernel/mempool_entry.h:65
class CTxMemPoolEntry : public TxGraph::Ref
```

CTxMemPoolEntry 继承自 `TxGraph::Ref`（定义于 `src/txgraph.h:232-253`）。
Ref 是 TxGraph 中交易的外部句柄，内含两个 private 字段：

- `TxGraph* m_graph`：指向所属的 TxGraph 实例，`nullptr` 表示空（未关联任何交易）
- `GraphIndex m_index`：在 TxGraph 内部 `m_entries` 数组中的索引

**为什么选择继承而非组合？**

如果用组合（CTxMemPoolEntry 持有一个 `Ref` 成员），那么 TxGraph 返回的 `Ref*`
指针就无法直接转换为 `CTxMemPoolEntry*`，需要维护一张 `Ref* → CTxMemPoolEntry*` 的映射表。
通过继承，每个 `CTxMemPoolEntry` 对象的内存布局开头就是 `Ref` 的数据，
`Ref*` 和 `CTxMemPoolEntry*` 指向同一地址，`static_cast` 是零开销的。

`txgraph.h` 中的设计注释（:53-61）明确描述了这一模式：
> "Users of the class can inherit from TxGraph::Ref. If all Refs are inherited this way,
> the Ref* pointers returned by TxGraph functions can be cast to, and used as, this inherited type."

**移动语义限制：**

```cpp
// src/kernel/mempool_entry.h:71, 103-105
CTxMemPoolEntry(const CTxMemPoolEntry&) = delete;         // 禁止拷贝构造
CTxMemPoolEntry& operator=(const CTxMemPoolEntry&) = delete; // 禁止拷贝赋值
CTxMemPoolEntry(CTxMemPoolEntry&&) = default;             // 允许移动构造
CTxMemPoolEntry& operator=(CTxMemPoolEntry&&) = delete;   // 禁止移动赋值
```

这些限制继承自 Ref 的语义：一个 TxGraph entry 有且仅有一个对应的 Ref。
拷贝会创建两个"所有者"，这是不允许的。移动构造是允许的（Ref 的移动构造会通知
TxGraph 更新内部指针），但移动赋值被禁止（因为需要先处理 `*this` 已有的交易）。

## 2. 核心字段逐一解读

CTxMemPoolEntry 的字段分为**不可变字段**（`const`）和**可变字段**（`mutable`）两类。
大部分字段在构造时确定，入池后不再改变。

### 2.1 不可变字段

| 字段 | 行号 | 类型 | 用途 |
|------|------|------|------|
| `tx` | :73 | `const CTransactionRef` | 交易本体（shared_ptr，与其他持有者共享） |
| `nFee` | :74 | `const CAmount` | 原始交易费（缓存值，避免每次从父交易推导） |
| `nTxWeight` | :75 | `const int32_t` | 原始交易权重（含 witness 折扣） |
| `nUsageSize` | :76 | `const size_t` | 此 entry 的总内存使用量（包括交易数据的动态内存） |
| `nTime` | :77 | `const int64_t` | 入池时的 Unix 时间戳（秒） |
| `entry_sequence` | :78 | `const uint64_t` | 入池序号（单调递增，全局唯一） |
| `entryHeight` | :79 | `const unsigned int` | 入池时的区块链高度 |
| `spendsCoinbase` | :80 | `const bool` | 是否花费了 coinbase 输出（影响成熟度检查） |
| `sigOpCost` | :81 | `const int64_t` | 总 sigop 开销（用于计算虚拟大小） |

所有 `const` 字段反映了一个设计原则：**交易入池后，其共识属性不可变**。
费用是交易结构决定的，权重是序列化格式决定的，它们不会因为内存池状态变化而改变。

### 2.2 可变字段

| 字段 | 行号 | 类型 | 用途 |
|------|------|------|------|
| `m_modified_fee` | :82 | `mutable CAmount` | 修改后的费用（通过 `prioritisetransaction` 调整） |
| `lockPoints` | :83 | `mutable LockPoints` | BIP68 时间锁缓存（链重组后可能需要更新） |
| `idx_randomized` | :138 | `mutable size_t` | 在 mempool 随机排列向量中的索引（用于 P2P 广播） |

这三个字段被声明为 `mutable`，因为它们代表与交易身份无关的**运行时状态**：

- `m_modified_fee`：矿工可以通过 `prioritisetransaction` RPC 手动调整交易优先级，
  初始值等于 `nFee`，通过 `UpdateModifiedFee(fee_diff)` 进行饱和加法修改。
- `lockPoints`：BIP68 相对时间锁的计算结果缓存。
  当链发生重组时，缓存可能失效（见下节），需要通过 `UpdateLockPoints()` 刷新。
- `idx_randomized`：mempool 维护一个随机排列的交易向量
  （`CTxMemPool::txns_randomized`），用于向 peer 随机广播交易。
  这个索引会随着交易的增删而变化。

### 2.3 构造函数

```cpp
// src/kernel/mempool_entry.h:87-101
CTxMemPoolEntry(const CTransactionRef& tx, CAmount fee,
                int64_t time, unsigned int entry_height,
                uint64_t entry_sequence, bool spends_coinbase,
                int64_t sigops_cost, LockPoints lp)
```

构造函数在初始化时执行两个计算：
- `nTxWeight = GetTransactionWeight(*tx)`：从交易序列化数据计算权重
- `nUsageSize = RecursiveDynamicUsage(tx)`：递归计算动态内存使用量（包括 shared_ptr 开销）

`m_modified_fee` 初始化为 `nFee`（未修改前等于原始费用）。

## 3. LockPoints 结构

```cpp
// src/kernel/mempool_entry.h:26-36
struct LockPoints {
    int height{0};
    int64_t time{0};
    CBlockIndex* maxInputBlock{nullptr};
};
```

LockPoints 缓存了 BIP68 相对时间锁的计算结果，避免每次需要时重新遍历输入：

- `height`：满足 BIP68 相对锁定高度要求所需的最低区块高度
- `time`：满足 BIP68 相对锁定时间要求所需的最低 MTP（Median Time Past）
- `maxInputBlock`：用于计算的所有输入中，包含该输入的**最高高度区块**的指针

`maxInputBlock` 的关键作用在于**缓存失效判定**：只要当前链仍然是 `maxInputBlock`
的后代（即没有发生涉及该区块的重组），缓存的 `height` 和 `time` 值就仍然有效。
如果重组深度超过了 `maxInputBlock` 的高度，就需要通过 `UpdateLockPoints()` 重新计算。

## 4. 费率表示：FeeFrac 深入解析

Bitcoin Core 使用 `FeeFrac`（`src/util/feefrac.h:39`）作为费率的核心表示。
这是一个 `{fee, size}` 对，通过精确的整数运算进行费率比较，完全避免浮点数。

### 4.1 基本结构

```cpp
// src/util/feefrac.h:107-108
int64_t fee;   // 聪（satoshis），64 位有符号
int32_t size;  // vbytes 或 weight units，32 位有符号
```

费率的数学定义是 `fee / size`，但直接除法会丢失精度。FeeFrac 的解决方案是
**交叉乘法**（cross-multiplication）：比较 `a.fee/a.size` 与 `b.fee/b.size`
等价于比较 `a.fee * b.size` 与 `b.fee * a.size`。

由于 `fee` 是 64 位、`size` 是 32 位，乘积需要 96 位。FeeFrac 提供了两种实现：
- 编译器支持 `__int128` 时（:83-86）：直接使用 128 位整数运算
- 回退实现（:44-78）：将 64 位数拆分为高低 32 位分别乘，手动管理进位

### 4.2 两种比较语义

FeeFrac 提供了两套不同的比较操作，理解它们的区别至关重要：

**`FeeRateCompare`（`src/util/feefrac.h:157`）— 仅比较费率**

```cpp
friend std::weak_ordering FeeRateCompare(const FeeFrac& a, const FeeFrac& b) noexcept;
```

返回 `std::weak_ordering`：两个 FeeFrac 可以有不同的 `(fee, size)` 值但被视为"等价"——
只要它们的费率（fee/size 比值）相同。配套的 `operator<<` 和 `operator>>` 分别表示
"费率严格低于"和"费率严格高于"。

**`operator<=>`（`src/util/feefrac.h:178`）— 全序比较**

```cpp
friend std::strong_ordering operator<=>(const FeeFrac& a, const FeeFrac& b) noexcept
{
    auto cross_a = Mul(a.fee, b.size), cross_b = Mul(b.fee, a.size);
    if (cross_a == cross_b) return b.size <=> a.size;  // 注意：b.size 在前
    return cross_a <=> cross_b;
}
```

返回 `std::strong_ordering`：这是一个**全序**。当费率相同时，通过 `b.size <=> a.size`
打破平局——**更大的 size 排在前面**（排序值更小）。这意味着相同费率下，占空间更大的
FeeFrac "更好"。

**空 FeeFrac**（fee=0, size=0）在全序中排在**最后**：
`cross_a = 0 * b.size = 0`，`cross_b = b.fee * 0 = 0`，相等，
然后 `b.size <=> 0` 将空值排到最后面。

### 4.3 EvaluateFee：按比例计算费用

```cpp
// src/util/feefrac.h:201-223
template<bool RoundDown>
int64_t EvaluateFee(int32_t at_size) const noexcept;
```

计算 `(this->fee * at_size) / this->size`，即"如果交易大小是 `at_size`，
按当前费率应付多少费用"。提供向下取整（`EvaluateFeeDown`）和向上取整
（`EvaluateFeeUp`）两个公共接口。

快速路径（:206）：当 `fee >= 0 && fee < 0x200000000`（33 位以内）时，
乘积可以用 `uint64_t` 表示，无需 96 位运算。编译器提示 `[[likely]]` 标记这是常见情况。

### 4.4 FeePerWeight vs FeePerVSize

为了防止不同度量单位的费率被混用，FeeFrac 通过 phantom type tag 提供类型安全：

```cpp
// src/util/feefrac.h:237-256
template<typename Tag>
struct FeePerUnit : public FeeFrac { ... };  // 零开销的 tagged 包装

struct VSizeTag {};
using FeePerVSize = FeePerUnit<VSizeTag>;    // 聪/虚拟字节

struct WeightTag {};
using FeePerWeight = FeePerUnit<WeightTag>;  // 聪/权重单位
```

`FeePerWeight` 是 TxGraph 内部使用的类型（`AddTransaction` 接受 `const FeePerWeight&`）。
`FeePerVSize` 用于面向用户的接口（如 RPC 返回的费率）。
两者之间不能隐式转换，编译期即可捕获误用。

### 4.5 CompareChunks：费率图比较

```cpp
// src/util/feefrac.h:226-234
std::partial_ordering CompareChunks(std::span<const FeeFrac> chunks0,
                                     std::span<const FeeFrac> chunks1);
```

比较两个费率图（feerate diagram）。每个图由一系列 chunk 的累积 `(fee, size)` 表示。
返回 `std::partial_ordering` 是因为两个图可能**不可比**——一个在某些费率范围更好，
另一个在其他范围更好。这个函数在 RBF 评估中被用来比较 main 和 staging 的费率图
（参见第 5 篇 staging）。

## 5. entry_sequence 的用途

`entry_sequence`（:78）是一个全局单调递增的序号，每当一笔交易入池时分配。
它在系统中有两个关键作用：

**1. Fallback ordering（兜底排序）**

当 TxGraph 需要在两个费率完全相同的 chunk 之间选择顺序时，需要一个稳定的打破平局规则。
`MakeTxGraph` 的 `fallback_order` 参数（`src/txgraph.h:266-271`）正是为此设计的。
在实际使用中，这个比较函数通常基于 `entry_sequence`：较早入池的交易排在前面。

**2. 防止过新交易被中继**

节点使用 `entry_sequence` 来判断一笔交易是否"太新"而不应该向 peer 广播，
这有助于防止交易广播风暴。

## 6. 公共方法一览

| 方法 | 返回类型 | 说明 |
|------|---------|------|
| `GetTx()` | `const CTransaction&` | 交易引用（解引用 shared_ptr） |
| `GetSharedTx()` | `CTransactionRef` | 交易 shared_ptr（增加引用计数） |
| `GetFee()` | `const CAmount&` | 原始费用（不含 prioritisetransaction 调整） |
| `GetTxSize()` | `int32_t` | 虚拟大小：`GetVirtualTransactionSize(nTxWeight, sigOpCost)` |
| `GetAdjustedWeight()` | `int32_t` | sigop 调整后的权重 |
| `GetTxWeight()` | `int32_t` | 原始权重（sigop 调整前） |
| `GetTime()` | `std::chrono::seconds` | 入池时间（类型安全的时间表示） |
| `GetHeight()` | `unsigned int` | 入池区块高度 |
| `GetSequence()` | `uint64_t` | 入池序号 |
| `GetModifiedFee()` | `CAmount` | 修改后费用（含 prioritisetransaction 调整） |
| `GetSigOpCost()` | `int64_t` | sigop 开销 |
| `DynamicMemoryUsage()` | `size_t` | 动态内存使用量 |
| `GetLockPoints()` | `const LockPoints&` | 时间锁缓存 |
| `GetSpendsCoinbase()` | `bool` | 是否花费 coinbase |
| `UpdateModifiedFee(diff)` | `void` | 饱和加法修改费用 |
| `UpdateLockPoints(lp)` | `void` | 更新时间锁缓存 |

注意 `GetTxSize()` 和 `GetAdjustedWeight()` 的区别：两者都考虑了 sigop 开销，
但前者返回虚拟字节（vbytes），后者返回权重单位（WU）。TxGraph 内部使用权重单位（FeePerWeight）。

---

## 小结

CTxMemPoolEntry 是 mempool 的原子单元，通过继承 TxGraph::Ref 将交易存储与图引擎无缝连接。
它的字段设计体现了"入池后不可变"的原则（const 字段）和"运行时可调整"的灵活性（mutable 字段）。
FeeFrac 提供了精确的整数费率比较基础设施，通过 phantom type 保证类型安全。

下一篇我们将进入 TxGraph::Ref 的"家"——TxGraph 接口层，
看看这个纯虚类如何定义了图引擎的完整 API。
