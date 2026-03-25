# Bitcoin 软分叉激活机制：从 BIP9 投票到 BuriedDeployment

[English](soft-fork-activation.en.md)

---

## 引言

本文源于作者在 review PR [#26201](https://github.com/bitcoin/bitcoin/pull/26201)（Remove Taproot activation height）时产生的进一步研究。该 PR 将 Taproot 从 BIP9 部署追踪中完全移除，一个看似简单的清理动作，却牵涉出 Bitcoin 软分叉激活的整套机制。为了真正理解这个 PR 在做什么以及为什么这样做，作者深入梳理了从 BIP9 投票到 BuriedDeployment 的完整脉络，整理成文。

Bitcoin 的共识规则升级通过「软分叉」实现——新规则是旧规则的子集，未升级的节点仍能验证区块，但可能接受不符合新规则的交易。软分叉的核心难题不是技术实现，而是**协调激活**：如何在去中心化网络中安全地切换到新规则？

---

## 1. BIP9：基于矿工信号的激活

### 1.1 为什么需要 BIP9

早期软分叉（BIP34、BIP65、BIP66）使用递增的区块版本号来协调激活：版本号从 v1→v2→v3→v4，当 950/1000 个区块使用新版本号时激活新规则。

这种方式的问题是**无法并行**——版本号是单调递增的整数，同一时间只能有一个软分叉在投票。

BIP9（2015 年由 Pieter Wuille、Peter Todd 和 Greg Maxwell 提出）解决了这个问题：利用区块头 `nVersion` 字段的 bit 位作为独立信号，最多支持 29 个软分叉同时并行投票。

### 1.2 nVersion 的 bit 布局

`nVersion` 是 32 位整数：

```
bit 31 30 29 | 28 27 26 ... 2 1 0
     0  0  1 |  ← 29 个信号 bit →
     ↑↑↑
  固定前缀 = 0x20000000
```

代码定义（`src/versionbits.h`）：

```cpp
static const int32_t VERSIONBITS_TOP_BITS = 0x20000000UL;  // 前缀 001
static const int32_t VERSIONBITS_TOP_MASK = 0xE0000000UL;  // 前 3 bit 遮罩
```

节点首先检查前 3 bit 是否为 `001`，是的话才视为 BIP9 版本号。29 个 bit 并不意味着只能做 29 次升级——一个软分叉完成（ACTIVE 或 FAILED）后，bit 会被释放供未来复用。约束仅在于**同一时间**最多 29 个并行投票。

### 1.3 矿工如何投票

每个 BIP9 部署分配一个 bit。矿工在出块时设置对应 bit 来表达支持。例如 Taproot 使用 bit 2：

```
nVersion = 0x20000000 | (1 << 2) = 0x20000004
```

信号检查逻辑（`src/versionbits_impl.h`）：

```cpp
bool Condition(int32_t nVersion) const {
    return (((nVersion & VERSIONBITS_TOP_MASK) == VERSIONBITS_TOP_BITS)  // 前缀正确
            && (nVersion & Mask()) != 0);                                // 对应 bit 已设置
}
```

**投票权与算力成正比**：一个矿工在一个统计周期内出了多少块，就有多少票。这是有意的设计——BIP9 本质上是在测量「有多少算力准备好了新规则」，而不是「有多少人支持」。

### 1.4 状态机

BIP9 定义了五个状态，以 2016 个区块为一个统计周期（与难度调整周期相同），在周期边界上进行状态转换：

```
DEFINED → STARTED → LOCKED_IN → ACTIVE
                 ↘ FAILED
```

每个部署在 `chainparams.cpp` 中配置完整参数：

```cpp
struct BIP9Deployment {
    int bit;                     // nVersion 中使用的 bit 位
    int64_t nStartTime;          // 开始接受信号的时间
    int64_t nTimeout;            // 超时时间
    int min_activation_height;   // 锁定后最早激活高度
    uint32_t period;             // 统计周期（通常 2016）
    uint32_t threshold;          // 激活所需信号数
};
```

状态转换的核心实现在 `src/versionbits.cpp` 的 `GetStateFor()` 中：

| 当前状态 | 转换条件 | 下一状态 |
|---------|---------|---------|
| DEFINED | 周期的 MTP >= nStartTime | STARTED |
| STARTED | 周期内信号数 >= threshold | LOCKED_IN |
| STARTED | MTP >= nTimeout 且未达标 | FAILED |
| LOCKED_IN | 下一周期高度 >= min_activation_height | ACTIVE |
| ACTIVE | — | ACTIVE（终态）|
| FAILED | — | FAILED（终态）|

### 1.5 Taproot 的实际激活时间线

Taproot 主网参数：bit=2，threshold=1815/2016（≈90%），nStartTime=2021-04-24，nTimeout=2021-08-11，min_activation_height=709632。

```
2021-04-24   MTP 到达 nStartTime → 进入 STARTED
             矿工开始在 bit 2 发出信号

2021-06-12   某个 2016 块周期内 1815+ 个区块信号支持
             → 进入 LOCKED_IN

2021-06 ~ 11 min_activation_height = 709632 尚未到达
             → 保持 LOCKED_IN

2021-11-14   高度 709632 到达
             → 进入 ACTIVE，Taproot 规则强制执行
```

### 1.6 阈值由谁决定

阈值由软分叉的提案者在代码中设定，随版本发布。不同网络可以设不同值：

| 软分叉 | 主网阈值 |
|-------|---------|
| CSV、SegWit | 1916/2016 ≈ 95% |
| Taproot（Speedy Trial）| 1815/2016 ≈ 90% |

Taproot 从 95% 降到 90% 是社区讨论后的选择——95% 意味着少数算力可以长期阻止激活。

但最终的决定权在于**多少节点选择运行包含这个阈值的代码**。如果社区不认可，可以拒绝运行，或 fork 修改版。最著名的案例是 2016-2017 年的 **SegWit 激活之争**：部分大矿工长期拒绝信号支持，社区出现 BIP148（UASF）——节点运营者绕过矿工投票直接强制激活。最终矿工在 UASF 压力下妥协。这证明了**最终权力在运行全节点的用户手中**——全节点决定什么链是有效的，矿工出的不被接受的区块就是废纸。

---

## 2. BuriedDeployment：埋藏已成历史的激活

### 2.1 动机

BIP9 部署激活后，状态机的计算仍然每次都要跑：从当前区块往回按周期遍历，统计信号数。对于 2012 年就激活的 BIP34，每次验证都回溯十多年的历史区块来确认「是的，它确实激活了」——完全是浪费。

BIP90（2016 年由 Suhas Daftuar 提出）的核心思想极其简单：

> 对于激活已久、不可能被重组回去的软分叉，直接把激活高度硬编码进代码。

### 2.2 代码结构

BuriedDeployment 使用一个简单的枚举加上硬编码高度：

```cpp
enum BuriedDeployment : int16_t {
    DEPLOYMENT_HEIGHTINCB,  // BIP34
    DEPLOYMENT_CLTV,        // BIP65
    DEPLOYMENT_DERSIG,      // BIP66
    DEPLOYMENT_CSV,         // BIP68/112/113
    DEPLOYMENT_SEGWIT,      // BIP141/143/147
};

struct Params {
    int BIP34Height;    // 227931
    int BIP65Height;    // 388381
    int BIP66Height;    // 363725
    int CSVHeight;      // 419328
    int SegwitHeight;   // 481824
};
```

激活检查从状态机遍历简化为 **O(1) 的高度比较**：

```cpp
// BuriedDeployment：直接比高度
inline bool DeploymentActiveAfter(..., BuriedDeployment dep, ...) {
    return (pindexPrev->nHeight + 1) >= params.DeploymentHeight(dep);
}

// BIP9 DeploymentPos：走状态机 + 缓存
inline bool DeploymentActiveAfter(..., DeploymentPos dep, VersionBitsCache& cache) {
    return cache.IsActiveAfter(pindexPrev, params, dep);
}
```

### 2.3 统一的调用接口

`src/deploymentstatus.h` 通过函数重载对外提供相同的接口，调用方不需要关心底层是哪种机制：

```cpp
DeploymentActiveAt(block, params, Consensus::DEPLOYMENT_SEGWIT, cache);   // → 比高度
DeploymentActiveAt(block, params, Consensus::DEPLOYMENT_TESTDUMMY, cache); // → 状态机
```

---

## 3. 软分叉的完整生命周期

```
阶段 1：提案
  代码中加入新的 DeploymentPos 枚举值
  chainparams.cpp 配置 bit、startTime、timeout、threshold
  验证逻辑加入条件检查

阶段 2：BIP9 投票
  矿工通过 nVersion bit 信号
  节点用状态机追踪：DEFINED → STARTED → LOCKED_IN → ACTIVE

阶段 3：已激活，仍在 BIP9 追踪
  状态机每次返回 ACTIVE
  计算冗余但无害

阶段 4：埋藏（BuriedDeployment）
  把 DeploymentPos 改为 BuriedDeployment
  加入硬编码高度，删除 BIP9 配置
  验证简化为高度比较

阶段 5：从 genesis 强制执行
  规则视为「始终有效」
  从部署追踪中完全删除
```

---

## 4. PR #26201：Taproot 从阶段 3 直接到阶段 5

PR #26201 将 Taproot 从 BIP9 部署追踪中完全移除，但**跳过了 BuriedDeployment 阶段**。

### 为什么 SegWit 需要 BuriedDeployment 而 Taproot 不需要

SegWit 仍留在 `BuriedDeployment` 中，因为代码需要知道激活高度：
- 激活前的区块不带 witness 数据，节点需要知道从哪个高度开始要求下载 witness
- BIP147 依赖硬编码的激活高度

Taproot 没有这种结构性分界——它的验证规则（Schnorr 签名、tapscript）在脚本执行层面，`SCRIPT_VERIFY_TAPROOT` 可以直接始终启用，不需要根据高度做区块结构上的区分。

### 具体改动

| 文件 | 改动 |
|------|------|
| `consensus/params.h` | 删除 `DEPLOYMENT_TAPROOT`，提升 `MinBIP9WarningHeight` |
| `kernel/chainparams.cpp` | 删除所有网络的 Taproot BIP9 配置 |
| `deploymentinfo.cpp` | 删除 taproot 的 `VBDeploymentInfo` |
| `rpc/blockchain.cpp` | `getdeploymentinfo` 不再返回 taproot |
| `rpc/mining.cpp` | `getblocktemplate` 的 rules 中明确加入 `"taproot"` |
| `mining_basic.py` | 断言 rules = `['csv', '!segwit', 'taproot']` |

`MinBIP9WarningHeight` 从 segwit 激活后（483840）提升到 taproot 激活后（711648），防止节点对 taproot 激活期间的历史 BIP9 信号发出「未知部署」警告。

---

## 总结

Bitcoin 的软分叉激活机制体现了去中心化系统的核心张力：需要协调升级，但没有中央权威可以下令。BIP9 用算力信号来测量准备度，BuriedDeployment 用硬编码来清理历史包袱，最终的决定权分散在每一个选择运行哪个版本代码的节点运营者手中。
