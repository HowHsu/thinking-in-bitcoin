# PostLinearize 算法分析

[English](post_linearize.en.md)

## 算法定位

`PostLinearize` 是 cluster linearization 的后处理步骤，在 `Linearize`（SpanningForestState）之后运行。两者职责不同：

- **Linearize**：基于随机化迭代优化，在有限 budget 内寻找尽量好的排序，但输出可能 chunk 不连通、受随机种子影响
- **PostLinearize**：确定性的后处理，修复上述问题，保证结果不变差

代码位置：`src/cluster_linearize.h:1585`，调用点在 `GenericClusterImpl::Relinearize`（`src/txgraph.cpp:2069`）。

## 为什么需要 PostLinearize

### 1. 保证 chunk 连通

Linearize（SpanningForestState）的输出不保证每个 chunk 是连通子图，在 budget 耗尽提前退出时尤其如此。PostLinearize 通过 merge/swap 保证每个 chunk 内的交易都是连通的。这是 TxGraph 对外的关键不变量——`BlockBuilder` 按 chunk 出块时需要每个 chunk 是连通的。

### 2. 消除随机性

Linearize 内部大量使用随机数（选择处理顺序、merge 方向、tiebreak 等），同一个 cluster 不同的 `rng_seed` 会产生不同的 linearization。PostLinearize 是完全确定性的（没有随机数），把随机化输出收敛到更规范的形式，减少不同节点之间的分歧。

### 3. RBF 替换的安全性保证

PostLinearize 保证：如果一个 leaf 交易被替换为同 size 更高 fee 的交易，对结果做 PostLinearize 后不会比替换前更差（注释 1578-1582 行）。这使得 RBF 替换后可以走"drop + append + PostLinearize"的轻量级路径，不需要跑完整的 Linearize。

## 算法原理

### 总体结构

做 2 个 pass：

- **Pass 0（backward）**：从后往前扫描，对"每个 tx 最多一个 parent"的树保证 optimal
- **Pass 1（forward）**：从前往后扫描，对"每个 tx 最多一个 child"的树保证 optimal

每个 pass 都不会让结果变差。

### Forward Pass 详细步骤

维护一个 group 链表 L，每个 group 是一组连续的交易，带有合并后的 feerate。

对输入 linearization 中的每个交易 i，从前到后：

1. 创建新 group `C = {i}`，追加到 L 末尾
2. 看 C 前面的 group P：
   - 如果 `feerate(C) > feerate(P)`：
     - **C 和 P 有依赖关系** → **merge**：把 P 并入 C，C 变大，继续往前看
     - **C 和 P 无依赖关系** → **swap**：把 C 和 P 交换位置，C 往前移，继续往前看
   - 如果 `feerate(C) <= feerate(P)` → 停止

### Backward Pass

完全对称：从后往前扫描，`deps` 用 descendants 而不是 ancestors，feerate 取反，输出时反转。

### 完整例子

4 个交易，依赖关系 `A→B`（A 是 B 的 parent），C 和 D 独立。

feerates: `A=2, B=10, C=1, D=8`

输入 linearization：`[A, B, C, D]`

**Forward pass：**

```
处理 A: L = [{A, fee=2}]

处理 B: L = [{A, fee=2}, {B, fee=10}]
  B(10) > A(2)？是
  B 和 A 有依赖？是（B 的 ancestors 包含 A）→ merge
  L = [{A,B, fee=12/2=6}]
  前面没有更多 group → 停止

处理 C: L = [{A,B, fee=6}, {C, fee=1}]
  C(1) > AB(6)？否 → 停止

处理 D: L = [{A,B, fee=6}, {C, fee=1}, {D, fee=8}]
  D(8) > C(1)？是，D 和 C 无依赖 → swap
  L = [{A,B, fee=6}, {D, fee=8}, {C, fee=1}]
  D(8) > AB(6)？是，D 和 AB 无依赖 → swap
  L = [{D, fee=8}, {A,B, fee=6}, {C, fee=1}]
  前面是 sentinel → 停止
```

输出：`[D, A, B, C]`，chunks 为 `{D:8}, {A,B:6}, {C:1}`，feerate 递减。

### Merge 和 Swap 的含义

| 操作 | 条件 | 效果 |
|------|------|------|
| **Merge** | C 比 P feerate 高 **且** 有依赖 | P 必须在 C 前面（拓扑约束），把它们合为一个 chunk |
| **Swap** | C 比 P feerate 高 **且** 无依赖 | C 可以安全移到 P 前面（不违反拓扑），让高 feerate 的 group 更靠前 |

Merge 的本质：P 和 C 有依赖时不能 swap（会违反拓扑序），但 C 的 feerate 比 P 高，意味着 P 被 C "拖着"一起挖更划算，所以合并成一个 chunk。

Swap 的本质：无依赖，C 的 feerate 更高，C 应该排在 P 前面被更早挖。

## 为什么对树形图是 Optimal

PostLinearize 的注释声明：对树形图（每个 tx 最多一个 child，或每个 tx 最多一个 parent），结果是 optimal。

### 一般 DAG 上的潜在问题

PostLinearize 不能拆分 group。当 C 被迫 merge P 时（因为有依赖），P 中的所有 tx 都被合并进来。如果 P 中某些 tx 并非 C 的必要依赖，只是因为之前的 merge 被"拖进来"的，那这个合并可能不是最优选择。

### 树形图为什么避免了这个问题

在"每个 tx 最多一个 child"的树中：当 C 被迫 merge P 时，P 中的所有 tx 都"只服务于 C 这一条路径"。因为每个 tx 只有一个 child，不存在"P 中的某个 tx 同时被两个不同方向的后代需要"的情况。所以把 P 整体 merge 进 C 不会有损失。

从调度理论角度，forward pass 对"每个 tx 最多一个 child"的树等价于经典的 **Sidney 分解算法**（1975），已被证明对树形优先级约束下的单机调度问题是最优的。正确性基于交换论证：在树形约束下，局部贪心决策（merge/swap）不会阻碍全局最优。

两个 pass 各覆盖一种树形方向，合在一起对所有树形图都 optimal。

### 对一般 DAG

注释只声明了树形情况的最优性保证（充分条件）。在 5-6 节点的 DAG 暴力搜索中未找到 PostLinearize 次优的反例，但理论上对更大更复杂的 DAG 不排除次优可能。

## 时间复杂度

**O(N²)**，其中 N 是 cluster 中的交易数。

分析：
- **Merge 操作**：每个 tx 最多被 merge 一次（merge 后 group 消失），总 merge 次数 ≤ N
- **Swap 操作**：swap 不消除 group，只交换位置。一个 tx 的 group 可以反复和前面的 group swap，每次前进一步。单个 tx 最多 swap O(N) 次（从最后 swap 到最前面），N 个 tx 总 swap 次数最坏 O(N²)

2 个 pass 总复杂度仍为 O(N²)。

## 数据结构

实现使用单链表组成的循环链表：

- 每个 group 内部：tx 用 `prev_tx` 链接（尾→头）
- group 之间：用 `prev_group` 链接成循环链表，头部有 sentinel 节点
- 每个 group 的 tail tx 存储：`first_tx`、`prev_group`、`group`（BitSet）、`deps`（ancestors/descendants BitSet）、`feerate`

Backward pass 通过取反 fee 和反转 parent/child 语义复用同一套代码。
