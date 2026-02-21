# 为什么 SPF 之后需要 PostLinearize

[English](why-postlinearize.en.md)

---

## 问题背景

Bitcoin Core 的 cluster linearization 流程中，`Linearize()`（SPF 算法）产出线性化序列后，
还会调用 `PostLinearize()` 做后处理。为什么不直接使用 SPF 的输出？
如果省略 PostLinearize，直接对 SPF 输出用 `ChunkLinearization` 计算 chunk，
会出什么问题？

---

## ChunkLinearization 与 PostLinearize 的区别

两者都使用"合并相邻违规者"（merge adjacent violators）的前向扫描思路，
但在遇到高费率元素需要越过低费率元素时，处理方式截然不同。

### ChunkLinearization：无条件合并

```cpp
// cluster_linearize.h — ChunkLinearizationInfo
while (!ret.empty() && new_chunk.feerate >> ret.back().feerate) {
    new_chunk |= ret.back();   // 无条件 merge
    ret.pop_back();
}
```

当新交易的费率高于前一个 chunk 时，**直接合并**，不检查两者之间是否有依赖关系。
`ChunkLinearization` 只是一个纯粹的费率计算工具——给定一个线性化序列，
计算其对应的 chunk 划分。它不修改序列，也不关心依赖图。

### PostLinearize：merge 或 swap

```
当新 group C 的费率高于前面的 group P 时：
  - C 和 P 有依赖关系 → merge：P 并入 C，形成更大的 chunk
  - C 和 P 无依赖关系 → swap：将 C 移到 P 前面
```

PostLinearize 检查依赖关系。对于无依赖的高费率交易，它选择 **swap**（交换位置）
而不是 merge（合并成同一个 chunk）。swap 让高费率交易移到前面，同时保持两者
各自独立为不同的 chunk。

---

## 为什么 ChunkLinearization 会产生不连通的 chunk

考虑以下场景：

```
依赖关系：A → C, B → C
（A 和 B 各自是 C 的父交易，但 A 和 B 之间无依赖）

费率：A = 1, B = 3, C = 10
```

SPF 的 `GetLinearization` 输出线性化序列时，A 和 B 都必须在 C 前面（拓扑约束），
按费率排序后可能输出：`[B, A, C]`。

对这个序列执行 `ChunkLinearization`：

```
处理 B: stack = [{B, fee=3}]
处理 A: fee(A)=1 < fee(B)=3，不合并
         stack = [{B, fee=3}, {A, fee=1}]
处理 C: fee(C)=10 > fee(A)=1
         → 无条件合并 A: {A,C}, fee=11/2=5.5
         fee({A,C})=5.5 > fee(B)=3
         → 无条件合并 B: {B,A,C}, fee=14/3≈4.67
         stack = [{B,A,C, fee=4.67}]
```

结果：一个 chunk `{B, A, C}`。但 **B 和 A 之间没有依赖关系**——
它们只是因为各自依赖 C，被 ChunkLinearization 的无条件合并"拉"到了同一个 chunk 里。
这个 chunk 在依赖图中是连通的（A 和 B 通过 C 相连），所以这个例子碰巧没问题。

但在更复杂的场景下：

```
依赖关系：A → D, B → C
（两条独立的链，但在同一个 cluster 中通过其他边相连）

SPF 非最优输出：[A, B, C, D]

费率：A = 1, B = 2, C = 3, D = 10
```

```
处理 A: stack = [{A, fee=1}]
处理 B: fee(B)=2 > fee(A)=1
         → 无条件合并: {A,B}, fee=3/2=1.5
         stack = [{A,B, fee=1.5}]
处理 C: fee(C)=3 > fee({A,B})=1.5
         → 无条件合并: {A,B,C}, fee=6/3=2
         stack = [{A,B,C, fee=2}]
处理 D: fee(D)=10 > fee({A,B,C})=2
         → 无条件合并: {A,B,C,D}, fee=16/4=4
         stack = [{A,B,C,D, fee=4}]
```

chunk `{A, B, C, D}` 中，A→D 和 B→C 是两条独立的依赖链。
如果 A 和 B 之间、C 和 D 之间都没有依赖边，这个 chunk 可能不连通。

### PostLinearize 如何避免这个问题

同样的输入 `[A, B, C, D]`，PostLinearize 的 forward pass：

```
处理 A: L = [{A, fee=1}]
处理 B: fee(B)=2 > fee(A)=1，B 和 A 无依赖 → swap
         L = [{B, fee=2}, {A, fee=1}]
处理 C: fee(C)=3 > fee(A)=1，C 和 A 无依赖 → swap
         L = [{B, fee=2}, {C, fee=3}, {A, fee=1}]
         fee(C)=3 > fee(B)=2，C 依赖 B → merge
         L = [{B,C, fee=5/2=2.5}, {A, fee=1}]
处理 D: fee(D)=10 > fee(A)=1，D 和 A 有依赖 → merge
         L = [{B,C, fee=2.5}, {A,D, fee=11/2=5.5}]
         fee({A,D})=5.5 > fee({B,C})=2.5，检查依赖...
```

通过 swap 操作，PostLinearize 将无依赖的交易各自分开，只在存在依赖时才合并。
最终每个 chunk 内的交易都通过依赖边相连——即 **chunk 连通**。

PostLinearize 的注释明确指出：

> One pass in either direction guarantees that the resulting chunks are connected.

---

## 为什么 SPF 不直接保证连通性

SPF 内部的 chunk 是通过沿依赖边的 merge 操作构建的，**SPF 内部的 chunk 本身是连通的**。

问题出在两个环节：

1. **`GetLinearization()` 输出的是交易序列**，不是 chunk 结构。
   SPF 内部的 chunk 信息在输出时被丢弃了。

2. **后续需要 `PostLinearize` 修改序列**，SPF 内部的 chunk 划分不再适用。
   特别是 SPF 因迭代预算耗尽而提前终止时，输出的线性化可能不是最优的，
   `GetLinearization` 按拓扑序 + 费率排列 chunk，但由于 chunk 划分本身不够好，
   输出序列经 `ChunkLinearization` 重新计算后可能产生与 SPF 内部不同的 chunk 边界。

因此 SPF 选择只输出线性化序列，把连通性保证交给 PostLinearize——
这是一个分层设计：SPF 专注费率图优化，PostLinearize 负责连通性修复。

---

## PostLinearize 的完整作用

综合来看，PostLinearize 在 SPF 之后承担三个职责：

| 职责 | 说明 |
|------|------|
| **保证 chunk 连通** | 通过 swap（而非无条件 merge）确保每个 chunk 是连通子图 |
| **改善非最优结果** | SPF 提前终止时，PostLinearize 可进一步改善线性化质量 |
| **优化 chunk 内排序** | 即使 SPF 报告最优，PostLinearize 仍可改善 chunk 内部的交易顺序 |

当 SPF 报告 optimal 时，PostLinearize 不会改变 chunk 划分（费率图已最优），
只改善 sub-chunk order（chunk 内部排序）。

### 即使 SPF 输出最优，sub-chunk 排序仍然重要

SPF 报告 optimal 意味着费率图已经最优——chunk 的划分和顺序不可能更好了。
但**同一个 chunk 内的交易排列顺序**并不影响费率图。例如一个 chunk `{A, B, C}`
无论内部排列为 `[A, B, C]` 还是 `[B, A, C]`，chunk 的总费率不变，费率图也不变。

然而 sub-chunk 排序在实际运行中有意义：

1. **确定性**：SPF 内部使用随机数（`rng_seed`），同一个 cluster 不同的种子
   可能产生不同的 chunk 内排序。PostLinearize 是完全确定性的，通过 merge/swap
   把随机化输出收敛到规范形式，减少不同节点之间的分歧。

2. **moved-tree 性质**：PostLinearize 以 backward pass 开始，保证 "moved-tree property"，
   这是一个有利于 RBF 替换场景的结构性质——当一个 leaf 交易被替换为同 size 更高 fee
   的交易后，对结果做 PostLinearize 不会比替换前更差。

3. **更好的局部排序**：在费率图相同的前提下，PostLinearize 倾向于把高费率交易
   排在 chunk 内部的前面，这对增量更新（如 `Relinearize` 传入 `old_linearization`）
   提供了更好的起点。

`GetLinearization` 本身也会在 chunk 内部按拓扑序 + 费率排序，但它使用的排序规则
是 SPF 内部状态的产物。PostLinearize 提供了一层独立于 SPF 内部状态的、
确定性的 sub-chunk 优化。

---

## 链式 Cluster 为什么可以跳过 PostLinearize

`TryLinearizeChain` 的结果可以安全跳过 PostLinearize，因为：

1. **唯一的拓扑序**：链式 cluster 中每个交易的祖先集大小唯一（{1,...,N}），
   拓扑序完全确定，没有排序歧义
2. **连通性天然满足**：链中每对相邻交易都有依赖，任何连续子序列都是连通的
3. **ChunkLinearization 等价于 PostLinearize**：链中不存在无依赖的相邻交易对，
   所以 PostLinearize 的 swap 分支永远不会触发，merge 行为与 ChunkLinearization 完全一致

---

## 相关文章

- [PostLinearize 算法分析](post_linearize.md)
- [链式 Cluster 的 O(N) 快速路径优化](chain-cluster-optimization.zh.md)
