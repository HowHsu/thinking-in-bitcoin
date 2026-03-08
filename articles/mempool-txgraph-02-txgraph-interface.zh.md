# 第 2 篇：TxGraph 接口 — 抽象层设计

[English](mempool-txgraph-02-txgraph-interface.en.md)

> 本文是 [Mempool & TxGraph 代码导读](../README.md) 系列第 2 篇。
> 上一篇：[第 1 篇：CTxMemPoolEntry](mempool-txgraph-01-entry.zh.md) | 下一篇：[第 3 篇：TxGraphImpl 数据结构](mempool-txgraph-03-impl-data.zh.md)

---

## 本篇聚焦

- 核心文件：`src/txgraph.h`（273 行）
- 关键类/函数：TxGraph, TxGraph::Ref, TxGraph::BlockBuilder, Level, GraphIndex, MakeTxGraph
- 前置阅读：第 1 篇

---

## 概述

TxGraph（`src/txgraph.h:47`）是一个纯虚类，定义了图引擎的完整公共 API。
它将 cluster 管理、线性化、staging、区块构建等复杂逻辑隐藏在一组清晰的虚函数接口之后。

这个文件只有 273 行，但信息密度极高——每个方法的注释都精确描述了前置条件、
行为语义和 oversized 限制。理解这个接口等于理解了 TxGraph 的全部"能力边界"。

文件顶部的设计注释（:20-46）定义了几个核心概念：

- **Main vs Staging**：TxGraph 持有一个或两个图。main 始终存在，staging 是可选的临时工作副本。
- **Cluster**：通过任意父子关系链可达的所有交易构成一个 cluster（连通分量）。
- **Linearization**：每个 cluster 内部维护一个拓扑兼容的排列（父在子前），并尽量接近最优挖矿顺序。
- **Chunk**：线性化中费率单调递减的连续段。同一 chunk 中的交易作为一个整体被挖掘。
- **依赖传递闭包**：接口设计假定实现只存储依赖的传递闭包——如果 B 依赖 C，
  那么 "A 依赖 B" 和 "A 同时依赖 B 和 C" 是等价的。

## 1. GraphIndex 与 Ref

### GraphIndex

```cpp
// src/txgraph.h:51
using GraphIndex = uint32_t;
```

GraphIndex 是交易在 TxGraph 内部的标识符——一个 32 位无符号整数，
作为 `TxGraphImpl::m_entries` 数组的索引。外部代码不直接使用 GraphIndex，
而是通过 Ref 对象间接引用。

### Ref 类

```cpp
// src/txgraph.h:232-253
class Ref
{
    friend class TxGraph;
    TxGraph* m_graph = nullptr;
    GraphIndex m_index = GraphIndex(-1);
public:
    Ref() noexcept = default;
    virtual ~Ref();
    Ref& operator=(Ref&& other) noexcept = delete;
    Ref(Ref&& other) noexcept;
    Ref& operator=(const Ref&) = delete;
    Ref(const Ref&) = delete;
};
```

Ref 是外部持有的交易句柄，RAII 语义。关键设计点：

**生命周期管理：**
- **创建**：Ref 以空状态默认构造（`m_graph == nullptr`），
  然后通过 `TxGraph::AddTransaction(ref, feerate)` 将其与一笔图中的交易关联。
  此时 TxGraph 设置 `m_graph = this` 和 `m_index = 分配的索引`。
- **移动**：`Ref(Ref&&)` 是非平凡的——移动后会调用 `m_graph->UpdateRef(m_index, *this)`
  通知 TxGraph 更新内部指针。移动赋值被禁止（需要先处理 `*this` 已有的交易，增加复杂性且无使用场景）。
- **销毁**：`virtual ~Ref()` 是非平凡的——如果 `m_graph != nullptr`，
  会调用 `m_graph->UnlinkRef(m_index)` 从图中移除交易（main 和 staging 都移除）。
  这就是 RAII：拥有 CTxMemPoolEntry（继承自 Ref）的所有权等于拥有图中交易的所有权。

**`virtual` 析构函数**使得通过 `Ref*` 指针删除 CTxMemPoolEntry 对象是安全的。
同时也使得 CTxMemPoolEntry 可以安全地继承 Ref（参见第 1 篇）。

**Protected 访问器（:225-229）：**

```cpp
static TxGraph*& GetRefGraph(Ref& arg) noexcept { return arg.m_graph; }
static GraphIndex& GetRefIndex(Ref& arg) noexcept { return arg.m_index; }
```

这些 `protected static` 方法允许 TxGraph 的实现类（TxGraphImpl）访问 Ref 的私有字段，
而不需要 Ref 友元每一个可能的实现类。

## 2. Level 枚举

```cpp
// src/txgraph.h:64-67
enum class Level {
    TOP,  //!< Refers to staging if it exists, main otherwise.
    MAIN  //!< Always refers to the main graph.
};
```

TxGraph 支持两层图结构。`Level` 枚举用于查询方法中指定要查询哪一层：

- **`Level::TOP`**：动态解析——当 staging 存在时指向 staging，不存在时指向 main。
  这是"当前工作状态"的抽象。
- **`Level::MAIN`**：始终指向 main 层。即使 staging 存在，也可以查询"已提交"的稳定状态。

在实现中（`src/txgraph.cpp:679-681`），Level 映射为整数：0 = main，1 = staging。
`Level::TOP` 在 staging 存在时映射为 1，否则为 0。

## 3. Mutation 方法组

这四个方法修改图的状态。它们在 oversized 状态下仍然可用，并且是**惰性**的——
不会立即执行，而是将操作放入待处理队列。

### AddTransaction（:71-78）

```cpp
virtual void AddTransaction(Ref& arg, const FeePerWeight& feerate) noexcept = 0;
```

- `arg` 必须是空的 Ref。调用后 `arg` 将关联到新创建的交易。
- `feerate.size` 必须严格为正。
- 如果 staging 存在，新交易只创建在 staging 中（不在 main 中）。
- Ref 可以在 TxGraph 销毁后继续存在（安全地变为空 Ref）。

### RemoveTransaction（:79-93）

```cpp
virtual void RemoveTransaction(const Ref& arg) noexcept = 0;
```

- 如果 staging 存在，只在 staging 中移除。
- 如果交易已被移除，则为空操作。
- **重排序注意事项**：TxGraph 可能在内部将交易移除与依赖添加重排序以优化性能。
  注释给出了具体例子：如果 A→B 依赖已存在，添加 C→B 依赖后再移除 B，
  那么 C 仍可能保留对 A 的依赖。但只要移除 B 时同时移除了其所有后代或所有祖先
  （实际场景中通常如此），重排序不影响行为。

### AddDependency（:94-98）

```cpp
virtual void AddDependency(const Ref& parent, const Ref& child) noexcept = 0;
```

- 如果 staging 存在，依赖只添加到 staging。
- 如果任一交易已被移除，则为空操作。
- 如果 parent 已是 child 的祖先，则为空操作（冗余依赖）。
- **前置条件**：parent 不能是 child 的后代（会形成环）。违反此条件是未定义行为。

### SetTransactionFee（:99-102）

```cpp
virtual void SetTransactionFee(const Ref& arg, int64_t fee) noexcept = 0;
```

- **与其他 mutation 方法不同**：同时修改 main 和 staging 中的费用。
  这是因为费用修改（来自 `prioritisetransaction`）被认为是全局适用的，
  无论 staging 状态如何。
- 如果交易在某一层不存在，则在该层无效果。

## 4. Work 与 Staging 方法组

### DoWork（:104-108）

```cpp
virtual bool DoWork(uint64_t max_cost) noexcept = 0;
```

TxGraph 是**惰性**的——mutation 方法只是排队，不立即执行。`DoWork` 主动推进待处理的工作：
应用移除、应用依赖、拆分不连通的 cluster、合并需要合并的 cluster、优化线性化。

- `max_cost`：最大计算预算（以 cluster_linearize 的抽象代价单位计）。
- 返回 `true` 表示所有当前可用的工作已完成；`false` 表示还有未完成的工作。
- 可在 oversized 状态下调用，但 oversized cluster 会被跳过。
- 设计用途：在节点空闲时调用，预先完成计算以加速后续查询和区块构建。

### Staging 控制（:110-120）

```cpp
virtual void StartStaging() noexcept = 0;   // 前提：当前无 staging
virtual void AbortStaging() noexcept = 0;   // 前提：staging 存在
virtual void CommitStaging() noexcept = 0;  // 前提：staging 存在
virtual bool HaveStaging() const noexcept = 0;
```

- `StartStaging()`：创建 staging 图——概念上是 main 的完整副本。后续 mutation 操作只影响 staging。
- `AbortStaging()`：丢弃所有 staging 中的变更，恢复到 main 状态。
- `CommitStaging()`：将 staging 替换为 main。所有 staging 中的变更成为永久状态。
- `HaveStaging()`：查询 staging 是否存在。这是 `const` 方法。

## 5. Query 方法组

查询方法检索图的状态。下表列出所有查询方法，标注其 Level 参数和 oversized 可用性：

| 方法 | 签名 | Level | Oversized 可用 | 说明 |
|------|------|-------|---------------|------|
| `IsOversized` | `bool(Level)` | 有 | 是 | cluster 是否超过大小限制 |
| `Exists` | `bool(const Ref&, Level)` | 有 | 是 | 交易是否未被移除 |
| `GetIndividualFeerate` | `FeePerWeight(const Ref&)` | 无 | 是 | 单笔交易费率（任一层） |
| `GetMainChunkFeerate` | `FeePerWeight(const Ref&)` | 无(仅main) | 否 | main 中所属 chunk 的费率 |
| `GetCluster` | `vector<Ref*>(const Ref&, Level)` | 有 | 否 | cluster 全部成员，**线性化顺序** |
| `GetAncestors` | `vector<Ref*>(const Ref&, Level)` | 有 | 否 | 所有祖先（含自身），无序 |
| `GetDescendants` | `vector<Ref*>(const Ref&, Level)` | 有 | 否 | 所有后代（含自身），无序 |
| `GetAncestorsUnion` | `vector<Ref*>(span, Level)` | 有 | 否 | 多笔交易祖先的并集 |
| `GetDescendantsUnion` | `vector<Ref*>(span, Level)` | 有 | 否 | 多笔交易后代的并集 |
| `GetTransactionCount` | `GraphIndex(Level)` | 有 | 是 | 交易总数 |
| `CompareMainOrder` | `strong_ordering(Ref&, Ref&)` | 无(仅main) | 否 | main 线性化中的顺序比较 |
| `CountDistinctClusters` | `GraphIndex(span, Level)` | 有 | 否 | 计算不同 cluster 数量 |
| `GetMainStagingDiagrams` | `pair<vec, vec>()` | 无(两层都要) | 否 | main 和 staging 的费率图 |
| `Trim` | `vector<Ref*>()` | 无(隐式TOP) | 需要 | 移除低费率交易使图不再 oversized |

几个值得注意的设计细节：

**`GetCluster`（:139-142）**返回的 `vector<Ref*>` 是按**线性化顺序**排列的——
这不同于 `GetAncestors`/`GetDescendants` 返回的无序结果。线性化顺序就是
cluster 内部的挖矿优先级排序。

**`GetMainChunkFeerate`（:135-138）**只查询 main 层（没有 Level 参数），
返回交易所属 chunk 的聚合费率——这反映了该交易在区块构建中的实际优先级。

**`GetMainStagingDiagrams`（:169-173）**返回 main 和 staging 的费率图对。
关键优化：只包含两层之间**不同的** cluster 的 chunk，相同的 cluster 被排除。
返回类型是 `FeeFrac`（而非 `FeePerWeight`），使其可以直接传给
`CompareChunks()`（`src/util/feefrac.h:234`）进行费率图比较。

**`Trim`（:174-178）**在图 oversized 时移除低费率交易及其后代。
这是一个"尽力而为"（best-effort）的操作，不保证保留特定交易。
只在 oversized 时有效，非 oversized 时无操作。

## 6. BlockBuilder 接口

```cpp
// src/txgraph.h:181-196
class BlockBuilder
{
protected:
    BlockBuilder() noexcept = default;
public:
    virtual ~BlockBuilder() = default;
    virtual std::optional<std::pair<std::vector<Ref*>, FeePerWeight>>
        GetCurrentChunk() noexcept = 0;
    virtual void Include() noexcept = 0;
    virtual void Skip() noexcept = 0;
};
```

BlockBuilder 是一个**迭代器模式**的接口，用于按最优挖矿顺序遍历交易。
它是 `TxGraph` 的内部类，构造函数为 `protected`——只能通过
`TxGraph::GetBlockBuilder()` 创建。

### GetCurrentChunk（:190）

```cpp
virtual std::optional<std::pair<std::vector<Ref*>, FeePerWeight>>
    GetCurrentChunk() noexcept = 0;
```

返回当前最高费率的 chunk，包含：
- `std::vector<Ref*>`：chunk 中所有交易的引用
- `FeePerWeight`：chunk 的聚合费率

返回 `std::nullopt` 表示所有 chunk 已遍历完毕（区块构建结束）。

### Include（:192）

```cpp
virtual void Include() noexcept = 0;
```

将当前 chunk 标记为"已纳入区块"，推进迭代器到下一个 chunk。
调用者负责实际将这些交易添加到区块中。

### Skip（:193-195）

```cpp
virtual void Skip() noexcept = 0;
```

跳过当前 chunk（例如区块空间不足或费率太低），推进迭代器。
**关键语义**：跳过一个 chunk 后，**同一 cluster 中的后续 chunk 都不再返回**。
这是因为 cluster 内部后续的 chunk 在拓扑上依赖于前面的 chunk——
如果前面的 chunk 不被挖掘，后面的也不能被挖掘。

### GetBlockBuilder（:198-201）

```cpp
virtual std::unique_ptr<BlockBuilder> GetBlockBuilder() noexcept = 0;
```

工厂方法，创建 BlockBuilder 实例。前提条件：
- Main 不能是 oversized 状态。
- BlockBuilder 存在期间，main 图不允许进行 mutation 操作
  （内部通过引用计数 `m_main_chunkindex_observers` 保护）。
- BlockBuilder 不能比创建它的 TxGraph 活得更久。

### GetWorstMainChunk（:202-206）

```cpp
virtual std::pair<std::vector<Ref*>, FeePerWeight> GetWorstMainChunk() noexcept = 0;
```

返回 main 图中**最低费率**的 chunk——挖矿优先级最低、应该最后被挖掘的那组交易。
用于 mempool 驱逐（`TrimToSize` 驱逐最低费率的交易以满足大小限制）。

**特殊返回顺序**：交易按**反向拓扑序**排列——每个交易前面是它的所有后代。
这是为驱逐场景设计的：先驱逐后代，再驱逐祖先。

## 7. 工厂函数 MakeTxGraph

```cpp
// src/txgraph.h:256-271
std::unique_ptr<TxGraph> MakeTxGraph(
    unsigned max_cluster_count,
    uint64_t max_cluster_size,
    uint64_t acceptable_cost,
    const std::function<std::strong_ordering(const TxGraph::Ref&, const TxGraph::Ref&)>&
        fallback_order
) noexcept;
```

这是创建 TxGraphImpl 实例的唯一入口。四个参数：

| 参数 | 类型 | 含义 |
|------|------|------|
| `max_cluster_count` | `unsigned` | 单个 cluster 最大交易数量。不能超过 `MAX_CLUSTER_COUNT_LIMIT`（64，:18） |
| `max_cluster_size` | `uint64_t` | 单个 cluster 最大交易 size 总和（权重单位） |
| `acceptable_cost` | `uint64_t` | 线性化优化的计算预算。值越大，线性化质量越好但消耗更多 CPU |
| `fallback_order` | `std::function<...>` | 费率相同时的打破平局规则。必须是稳定的全序。实践中通常基于 entry_sequence |

当一个 cluster 的交易数超过 `max_cluster_count` 或 size 总和超过 `max_cluster_size` 时，
图进入 **oversized** 状态。此时大部分查询方法不可用（见上表），
需要调用 `Trim()` 恢复正常状态。

## 8. 内存与诊断

### GetMainMemoryUsage（:208-213）

```cpp
virtual size_t GetMainMemoryUsage() noexcept = 0;
```

返回 main 图的近似内存使用量（字节）。不包含 staging、BlockBuilder、
待处理队列和临时缓存的内存。如果 staging 存在，返回的是"如果调用
`AbortStaging()` 之后"的内存使用量。

### SanityCheck（:215-216）

```cpp
virtual void SanityCheck() const = 0;
```

内部一致性检查。验证所有内部不变量：Cluster 状态与 QualityLevel 分桶一致、
Locator 有效、ChunkIndex 有序、m_entries 与 Ref 双向引用完整。
这是 `const` 方法，只在调试和测试中使用（参见第 9 篇）。

---

## 小结

TxGraph 接口只有 273 行，却定义了图引擎的完整能力边界。核心要点：

- **4 个 mutation 方法**：AddTransaction、RemoveTransaction、AddDependency、SetTransactionFee。
  前三个只影响 TOP 层（staging 存在时仅修改 staging），SetTransactionFee 同时影响两层。
- **惰性求值**：mutation 只排队，DoWork 推进实际计算。
- **Staging 三步曲**：StartStaging → 修改 → Commit/Abort。
- **BlockBuilder 迭代器**：GetCurrentChunk → Include/Skip 循环。
- **Ref 的 RAII 语义**：销毁 Ref 自动从图中移除交易。
- **Oversized 保护**：超限时大部分查询不可用，通过 Trim 恢复。
- **MakeTxGraph 工厂**：4 个参数控制 cluster 限制、优化预算和排序规则。

下一篇我们将深入 TxGraphImpl，看看这些接口背后的数据结构是如何组织的。
