# Bitcoin Core 手续费估算机制详解

[English](fee-estimation-notes.en.md)

## 背景

比特币每个区块有大小限制（约 4MB 权重），矿工打包区块时优先选择费率（feerate，单位 sat/vB）高的交易。用户发交易时需要设置手续费，费率太低会迟迟不被确认，太高则多花钱。

**手续费估算要解决的问题**：用户该设多少费率才能在 N 个区块内被确认？

---

## 关键文件

| 文件 | 作用 |
|------|------|
| `src/policy/fees/block_policy_estimator.h` | 主类定义、常量、数据结构 |
| `src/policy/fees/block_policy_estimator.cpp` | 算法实现 |
| `src/rpc/fees.cpp` | RPC 接口 (`estimatesmartfee`, `estimaterawfee`) |
| `src/kernel/mempool_entry.h` | `NewMempoolTransactionInfo`, `RemovedMempoolTransactionInfo` |
| `src/validationinterface.h` | 回调接口定义 |
| `src/policy/feerate.h` | `CFeeRate` 类 |

---

## 算法概览

核心类 `CBlockPolicyEstimator` 继承 `CValidationInterface`，通过回调监听内存池和区块事件。

### 核心思路

1. **把交易按费率分桶（bucket）**：从 1000 sat/KB 到 1e7 sat/KB，以 1.05 倍指数间距分成约 200 个桶。
2. **追踪每个桶的确认率**：交易进入内存池时记录高度，被区块打包时算出"等了几个区块才确认"，更新统计。
3. **估算时，从最高费率桶往低扫**，找到最后一个"确认成功率 >= 阈值"的桶，取其平均费率作为估算结果。

### 三个时间窗口

为了平衡短期波动和长期趋势，算法维护三套统计数据（`TxConfirmStats`）：

| 窗口 | 最大追踪区块 | 每周期区块数 (scale) | 衰减系数 (decay) | 半衰期 |
|------|------------|---------------------|-----------------|-------|
| SHORT | 12 | 1 | 0.962 | ~3小时 |
| MED | 48 | 2 | 0.9952 | ~1天 |
| LONG | 1008 | 24 | 0.99931 | ~1周 |

每来一个新区块，所有统计量乘以衰减系数（指数移动平均），老数据影响越来越小。

### 成功率阈值

| 阈值 | 值 | 用于 |
|------|-----|------|
| HALF_SUCCESS_PCT | 60% | target/2 的子估算 |
| SUCCESS_PCT | 85% | target 的子估算 |
| DOUBLE_SUCCESS_PCT | 95% | 2*target 的子估算 |

---

## TxConfirmStats 数据结构

```cpp
txCtAvg[bucket]           — 每个桶中交易总数（衰减移动平均）
confAvg[period][bucket]   — 在 period 个周期内被确认的交易数
failAvg[period][bucket]   — 超过 period 个周期仍未确认、已离开内存池的交易数
unconfTxs[age][bucket]    — 当前内存池中各桶各年龄段的未确认交易数（环形缓冲区）
oldUnconfTxs[bucket]      — 超出环形缓冲区长度的老未确认交易
m_feerate_avg[bucket]     — 每个桶的费率总和（用于算平均费率）
```

---

## 以一笔交易为例的完整流程

假设交易 txA：手续费 5000 sat，虚拟大小 200 vB，费率 = 25 sat/vB = 25000 sat/KB。当前区块高度 800000。

### 第一步：txA 进入内存池

内存池通过 `ValidationInterface` 回调通知估算器：

```cpp
// block_policy_estimator.cpp:581-584
void CBlockPolicyEstimator::TransactionAddedToMempool(const NewMempoolTransactionInfo& tx, uint64_t)
{
    processTransaction(tx);
}
```

进入 `processTransaction()`（第596行）：

```cpp
void CBlockPolicyEstimator::processTransaction(const NewMempoolTransactionInfo& tx)
{
    LOCK(m_cs_fee_estimator);
    const unsigned int txHeight = tx.info.txHeight;       // = 800000
    const auto& hash = tx.info.m_tx->GetHash();           // = txA 的 txid

    // 检查高度是否匹配，确保链同步
    if (txHeight != nBestSeenHeight) {
        return;
    }

    // 四个条件全部满足才纳入估算
    const bool validForFeeEstimation = !tx.m_mempool_limit_bypassed  // 不是绕过限制的
                                    && !tx.m_submitted_in_package     // 不是包提交的
                                    && tx.m_chainstate_is_current     // 链已同步
                                    && tx.m_has_no_mempool_parents;   // 没有未确认的父交易

    if (!validForFeeEstimation) {
        untrackedTxs++;
        return;
    }
    trackedTxs++;

    // 计算费率：5000 sat / 200 vB = 25000 sat/KB
    const CFeeRate feeRate(tx.info.m_fee, tx.info.m_virtual_transaction_size);

    // 记录到 mapMemPoolTxs：保存进入时的高度和桶索引
    mapMemPoolTxs[hash].blockHeight = txHeight;  // 800000

    // 在三个统计器中注册这笔交易
    unsigned int bucketIndex = feeStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
    mapMemPoolTxs[hash].bucketIndex = bucketIndex;
    shortStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
    longStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
}
```

`NewTx()` 把 txA 放入"未确认交易"的环形缓冲区：

```cpp
// block_policy_estimator.cpp:477-483
unsigned int TxConfirmStats::NewTx(unsigned int nBlockHeight, double val)
{
    // val = 25000 (sat/KB)
    // bucketMap 中找到 >= 25000 的最小桶上界，大约是 buckets[167]
    unsigned int bucketindex = bucketMap.lower_bound(val)->second;

    // 在高度 800000 对应的环形槽位中，给这个桶计数 +1
    unsigned int blockIndex = nBlockHeight % unconfTxs.size();
    unconfTxs[blockIndex][bucketindex]++;
    return bucketindex;
}
```

**此时 txA 的状态**：在 `mapMemPoolTxs` 中记着 `{blockHeight=800000, bucketIndex=167}`，在三个 `TxConfirmStats` 的 `unconfTxs` 环形缓冲区中各有 +1 计数。

### 第二步：区块 800003 到来，txA 被打包

矿工在区块 800003 中包含了 txA。内存池移除 txA 并触发回调：

```cpp
// block_policy_estimator.cpp:591-594
void CBlockPolicyEstimator::MempoolTransactionsRemovedForBlock(
    const std::vector<RemovedMempoolTransactionInfo>& txs_removed_for_block,
    unsigned int nBlockHeight)
{
    processBlock(txs_removed_for_block, nBlockHeight);
}
```

进入 `processBlock()`（第669行）：

```cpp
void CBlockPolicyEstimator::processBlock(
    const std::vector<RemovedMempoolTransactionInfo>& txs_removed_for_block,
    unsigned int nBlockHeight)  // = 800003
{
    LOCK(m_cs_fee_estimator);
    if (nBlockHeight <= nBestSeenHeight) {
        return;  // 忽略侧链和重组
    }

    nBestSeenHeight = nBlockHeight;  // 更新为 800003

    // 操作1：滚动环形缓冲区
    feeStats->ClearCurrent(nBlockHeight);
    shortStats->ClearCurrent(nBlockHeight);
    longStats->ClearCurrent(nBlockHeight);

    // 操作2：衰减所有历史统计量（每个统计量 *= decay）
    feeStats->UpdateMovingAverages();
    shortStats->UpdateMovingAverages();
    longStats->UpdateMovingAverages();

    // 操作3：处理区块中的每笔交易
    unsigned int countedTxs = 0;
    for (const auto& tx : txs_removed_for_block) {
        if (processBlockTx(nBlockHeight, tx))
            countedTxs++;
    }
}
```

`UpdateMovingAverages()` 执行指数衰减：

```cpp
// block_policy_estimator.cpp:231-242
void TxConfirmStats::UpdateMovingAverages()
{
    for (unsigned int j = 0; j < buckets.size(); j++) {
        for (unsigned int i = 0; i < confAvg.size(); i++) {
            confAvg[i][j] *= decay;   // 如 MED: *= 0.9952
            failAvg[i][j] *= decay;
        }
        m_feerate_avg[j] *= decay;
        txCtAvg[j] *= decay;
    }
}
```

然后对 txA 调用 `processBlockTx()`（第641行）：

```cpp
bool CBlockPolicyEstimator::processBlockTx(unsigned int nBlockHeight,
                                           const RemovedMempoolTransactionInfo& tx)
{
    // 先从 mapMemPoolTxs 中移除，并更新 unconfTxs 计数
    if (!_removeTx(tx.info.m_tx->GetHash(), true)) {
        return false;
    }

    // txA: 800003 - 800000 = 3 个区块才确认
    int blocksToConfirm = nBlockHeight - tx.info.txHeight;  // = 3

    CFeeRate feeRate(tx.info.m_fee, tx.info.m_virtual_transaction_size);
    // = 25000 sat/KB

    // 在三个统计器中记录确认数据
    feeStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    shortStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    longStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    return true;
}
```

`_removeTx()` 把 txA 从未确认计数中扣除（`inBlock=true` 表示被区块打包，不算失败）：

```cpp
// block_policy_estimator.cpp:485-520
void TxConfirmStats::removeTx(unsigned int entryHeight, unsigned int nBestSeenHeight,
                               unsigned int bucketindex, bool inBlock)
{
    int blocksAgo = nBestSeenHeight - entryHeight;  // 800003 - 800000 = 3

    unsigned int blockIndex = entryHeight % unconfTxs.size();  // 800000 % 12
    unconfTxs[blockIndex][bucketindex]--;  // 减去未确认计数

    // inBlock=true，所以不记录失败
    // 如果 inBlock=false 且 blocksAgo >= scale，会执行 failAvg[period][bucketindex]++
}
```

`Record()` 记录"25000 sat/KB 的交易等了 3 个区块确认"：

```cpp
// block_policy_estimator.cpp:217-229
void TxConfirmStats::Record(int blocksToConfirm, double feerate)
{
    // blocksToConfirm = 3, feerate = 25000
    if (blocksToConfirm < 1) return;

    // 对于 MED 统计器：scale=2，所以 periodsToConfirm = (3+2-1)/2 = 2
    int periodsToConfirm = (blocksToConfirm + scale - 1) / scale;

    unsigned int bucketindex = bucketMap.lower_bound(feerate)->second;

    // 对所有 >= periodsToConfirm 的周期，确认计数都 +1
    // 意思是"在 2 个周期内确认了，那在 3、4、...24 个周期内当然也确认了"
    for (size_t i = periodsToConfirm; i <= confAvg.size(); i++) {
        confAvg[i - 1][bucketindex]++;
    }
    txCtAvg[bucketindex]++;                 // 该桶总交易数 +1
    m_feerate_avg[bucketindex] += feerate;  // 累加费率，用于算平均值
}
```

**txA 贡献的数据**：在桶 167（约 25000 sat/KB）中，`confAvg[1..23][167]++`（周期 2 到 24 全标记确认），`txCtAvg[167]++`，`m_feerate_avg[167] += 25000`。

### 补充：如果 txA 没有被确认就被驱逐了

如果 txA 因为内存池满被驱逐（不是被区块打包），走另一条路：

```cpp
// block_policy_estimator.cpp:586-589
void CBlockPolicyEstimator::TransactionRemovedFromMempool(const CTransactionRef& tx,
    MemPoolRemovalReason, uint64_t)
{
    removeTx(tx->GetHash());  // inBlock = false
}
```

```cpp
// TxConfirmStats::removeTx 中（第513-519行）
if (!inBlock && (unsigned int)blocksAgo >= scale) {
    // inBlock=false，且等了足够久 → 记录为失败
    unsigned int periodsAgo = blocksAgo / scale;
    for (size_t i = 0; i < periodsAgo && i < failAvg.size(); i++) {
        failAvg[i][bucketindex]++;
    }
}
```

这会降低该桶的成功率，下次估算时导致估算往更高费率偏移。

### 第三步：用户查询"3 个区块确认需要多少费率"

调用 `estimatesmartfee 3` RPC，最终到 `estimateSmartFee()`（第871行）。

**核心：取三个子估算的最大值**：

```cpp
CFeeRate CBlockPolicyEstimator::estimateSmartFee(int confTarget, FeeCalculation *feeCalc,
                                                  bool conservative) const
{
    LOCK(m_cs_fee_estimator);

    if (confTarget == 1) confTarget = 2;  // 最小为2

    // 子估算1：target/2=1，60%阈值
    double halfEst = estimateCombinedFee(confTarget/2, HALF_SUCCESS_PCT, true, &tempResult);
    median = halfEst;

    // 子估算2：target=3，85%阈值
    double actualEst = estimateCombinedFee(confTarget, SUCCESS_PCT, true, &tempResult);
    if (actualEst > median) median = actualEst;

    // 子估算3：2*target=6，95%阈值
    double doubleEst = estimateCombinedFee(2 * confTarget, DOUBLE_SUCCESS_PCT, !conservative, &tempResult);
    if (doubleEst > median) median = doubleEst;

    // conservative 模式：额外检查长期窗口
    if (conservative || median == -1) {
        double consEst = estimateConservativeFee(2 * confTarget, &tempResult);
        if (consEst > median) median = consEst;
    }

    return CFeeRate(llround(median));
}
```

`estimateCombinedFee()` 选择最短的能覆盖目标的时间窗口：

```cpp
// block_policy_estimator.cpp:808-842
double CBlockPolicyEstimator::estimateCombinedFee(unsigned int confTarget,
    double successThreshold, bool checkShorterHorizon, EstimationResult *result) const
{
    double estimate = -1;
    if (confTarget <= shortStats->GetMaxConfirms()) {
        // confTarget=3 <= 12，用 SHORT 窗口
        estimate = shortStats->EstimateMedianVal(confTarget, SUFFICIENT_TXS_SHORT,
                                                  successThreshold, nBestSeenHeight, result);
    }
    // 还会检查更短窗口是否给出更低估算
}
```

核心计算 `EstimateMedianVal(confTarget=3, threshold=0.85)`（第245行）：

```cpp
double TxConfirmStats::EstimateMedianVal(int confTarget, double sufficientTxVal,
                                         double successBreakPoint, ...) const
{
    double nConf = 0;      // 在目标内确认的交易数
    double totalNum = 0;   // 已确认的交易总数
    int extraNum = 0;      // 还在内存池等待的交易数
    double failNum = 0;    // 离开内存池但未确认的交易数

    const int periodTarget = (confTarget + scale - 1) / scale;  // SHORT: scale=1, 所以=3

    // 从最高费率桶往低扫
    for (int bucket = maxbucketindex; bucket >= 0; --bucket) {
        nConf += confAvg[periodTarget - 1][bucket];
        totalNum += txCtAvg[bucket];
        failNum += failAvg[periodTarget - 1][bucket];
        for (unsigned int confct = confTarget; confct < GetMaxConfirms(); confct++)
            extraNum += unconfTxs[(nBlockHeight - confct) % bins][bucket];
        extraNum += oldUnconfTxs[bucket];

        // 数据点够了吗？
        if (partialNum < sufficientTxVal / (1 - decay)) {
            continue;  // 不够，继续合并下一个桶
        }

        // 够了，计算成功率
        double curPct = nConf / (totalNum + failNum + extraNum);

        if (curPct < successBreakPoint) {
            passing = false;  // 低于阈值，继续往低扫
        } else {
            foundAnswer = true;
            bestNearBucket = curNearBucket;
            bestFarBucket = curFarBucket;
            // 继续往低扫看还有没有更低的通过范围
        }
    }

    // 在最后通过的桶范围中，找中位交易所在的桶，取其平均费率
    // ...
    median = m_feerate_avg[j] / txCtAvg[j];
    return median;  // 比如返回 25000 (sat/KB) = 25 sat/vB
}
```

---

## 成功率公式

```
curPct = nConf / (totalNum + failNum + extraNum)
```

分母是**所有交易的完整样本**：

| 变量 | 含义 | 来源 |
|------|------|------|
| totalNum | 已经被确认的交易总数（不管等了多久） | `txCtAvg[bucket]`，`Record()` 中 +1 |
| nConf | 其中在目标周期内确认的交易数（`nConf <= totalNum`） | `confAvg[period][bucket]`，`Record()` 中 +1 |
| failNum | 离开内存池但未被确认的交易数 | `failAvg[period][bucket]`，`removeTx(inBlock=false)` 中 +1 |
| extraNum | 现在还在内存池里等着的交易数 | `unconfTxs + oldUnconfTxs`，`NewTx()` 中 +1 |

三类加起来 = 所有交易 = 已确认的 + 失败离开的 + 还在等的。

**为什么不能只用 nConf / totalNum？** 因为那会忽略失败和等待中的交易，严重高估成功率。极端例子：

```
某低费率桶：
  totalNum = 10    （侥幸确认了 10 笔）
  nConf = 9        （其中 9 笔在目标内确认）
  failNum = 500    （500 笔被驱逐了）
  extraNum = 200   （200 笔还在等）

如果用 nConf/totalNum        = 9/10  = 90%   → "费率够了"（错）
实际用 nConf/(10+500+200)    = 9/710 = 1.3%  → "几乎没法确认"（对）
```

---

## 环形缓冲区（unconfTxs）

`unconfTxs` 用来按区块高度追踪"还在内存池中等待的交易数量"。

```cpp
std::vector<std::vector<int>> unconfTxs;  // unconfTxs[height % 环长度][桶索引]
std::vector<int> oldUnconfTxs;            // 超出环长度的老交易
```

对 SHORT 统计器来说，`GetMaxConfirms() = 12`，所以 `unconfTxs` 有 12 个槽位。用 `高度 % 12` 做索引循环复用，只保留最近 12 个区块高度的精确分布，更老的统一归入 `oldUnconfTxs`。

### 具体数字示例

**高度 800000：txA 进入内存池**

```cpp
// NewTx()
unsigned int blockIndex = 800000 % 12;  // = 8
unconfTxs[8][167]++;  // 槽位8 的桶167 计数变为 1
```

```
槽位:   0  1  2  3  4  5  6  7 [8] 9  10  11
桶167:  0  0  0  0  0  0  0  0  1  0   0   0
                                 ↑ txA
```

**高度 800001：txB（同费率桶）进入内存池**

```cpp
unsigned int blockIndex = 800001 % 12;  // = 9
unconfTxs[9][167]++;
```

```
槽位:   0  1  2  3  4  5  6  7 [8][9] 10  11
桶167:  0  0  0  0  0  0  0  0  1  1   0   0
                                 ↑  ↑
                               txA txB
```

**高度 800003：区块到来，txA 被确认**

先执行 `ClearCurrent(800003)`——清空槽位 `800003 % 12 = 3`（本来就是 0）。

然后 `_removeTx(txA, inBlock=true)`：

```cpp
int blocksAgo = 800003 - 800000;            // = 3
unsigned int blockIndex = 800000 % 12;      // = 8（txA 进入时的槽位）
unconfTxs[8][167]--;                         // 1 → 0
```

```
槽位:   0  1  2  3  4  5  6  7 [8][9] 10  11
桶167:  0  0  0  0  0  0  0  0  0  1   0   0
                                 ↑  ↑
                             txA走了 txB还在等
```

**环形复用**：12 个区块后高度 800012 来了，`800012 % 12 = 0` 又用槽位 0。但 `ClearCurrent()` 会先把旧数据转移到 `oldUnconfTxs` 再清零。

### 估算时怎么用

`EstimateMedianVal()` 要算"等了 >= confTarget 个区块还没确认的交易"：

```cpp
// confTarget=3, nBlockHeight=800003
for (unsigned int confct = confTarget; confct < GetMaxConfirms(); confct++)
    extraNum += unconfTxs[(nBlockHeight - confct) % bins][bucket];
extraNum += oldUnconfTxs[bucket];
```

展开就是往回看每个槽位，把进入时间 <= 800000 的、至今还没确认的交易都算上。这些交易已经等了 >= 3 个区块还没确认，是"失败"的证据，算进分母会降低成功率。

---

## 全流程总结图

```
txA (25 sat/vB) 进入内存池 @ 高度 800000
    │
    ├─ processTransaction()
    │   ├─ mapMemPoolTxs[txA] = {height=800000, bucket=167}
    │   └─ unconfTxs[800000 % 12][167]++   (三个统计器)
    │
    │  ... 等待 3 个区块 ...
    │
    ▼ 区块 800003 到来，包含 txA
    │
    ├─ processBlock()
    │   ├─ 所有统计量 *= decay             (衰减旧数据)
    │   └─ processBlockTx(txA)
    │       ├─ _removeTx(txA, inBlock=true)
    │       │   └─ unconfTxs[...][167]--    (不再是未确认)
    │       ├─ blocksToConfirm = 800003 - 800000 = 3
    │       └─ Record(3, 25000)
    │           ├─ confAvg[2..23][167]++    (3个周期及以上全标记确认)
    │           ├─ txCtAvg[167]++
    │           └─ m_feerate_avg[167] += 25000
    │
    ▼ 用户查询 estimatesmartfee 3
    │
    └─ estimateSmartFee(3)
        ├─ estimateCombinedFee(1, 60%)  → halfEst
        ├─ estimateCombinedFee(3, 85%)  → actualEst
        ├─ estimateCombinedFee(6, 95%)  → doubleEst
        └─ return max(halfEst, actualEst, doubleEst)
            └─ EstimateMedianVal(): 从高桶往低扫
                找最后一个 成功率 >= 阈值 的桶范围
                返回该范围的平均费率
```

---

## Issue #27995 讨论的问题

### 问题1：只用历史数据，反应慢

算法只统计"过去交易等了多久才确认"，即使内存池已经清空、费率大降，估算值也要等衰减系数慢慢把旧数据衰减掉才会降下来。SHORT 窗口半衰期是 3 小时，MED 是 1 天——这期间估算可能严重偏高。

### 问题2：跟踪行为而非需求

如果很多用户习惯性付高费率（如 100 sat/vB），即使 10 sat/vB 就能确认，算法也会认为"100 sat/vB 的交易都很快确认了"，给出较高估算。它衡量的是"付这个费率确认有多快"，而不是"确认最少需要多少"。

### 提议的改进：引入内存池数据

Issue 提出可以直接看当前内存池的费率分布来做更快的估算，但面临策略分歧的问题：

- **本地策略比网络严格**（如 pre-taproot 节点）：看不到 taproot 交易，内存池缺少一部分交易，费率估算偏低。
- **本地策略比网络宽松**：内存池包含网络不会确认的交易，费率估算偏高。

所以 sipa 提出两个健全性检查：
1. 检查高费率交易的确认比例——如果大部分都能确认，说明本地内存池与全网接近。
2. 追踪"应该被打包但没被打包"的次数——如果某交易多次没被打包，从估算中排除。
