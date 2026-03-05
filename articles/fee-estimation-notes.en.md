# Bitcoin Core Fee Estimation Mechanism Explained

[中文](fee-estimation-notes.zh.md)

## Background

Each Bitcoin block has a size limit (~4MB weight). Miners prioritize transactions with higher feerates (sat/vB) when building blocks. Users need to set an appropriate fee when sending transactions — too low and it won't confirm for a long time, too high and they overpay.

**The problem fee estimation solves**: What feerate should a user set to get confirmed within N blocks?

---

## Key Files

| File | Purpose |
|------|---------|
| `src/policy/fees/block_policy_estimator.h` | Main class definition, constants, data structures |
| `src/policy/fees/block_policy_estimator.cpp` | Algorithm implementation |
| `src/rpc/fees.cpp` | RPC interfaces (`estimatesmartfee`, `estimaterawfee`) |
| `src/kernel/mempool_entry.h` | `NewMempoolTransactionInfo`, `RemovedMempoolTransactionInfo` |
| `src/validationinterface.h` | Callback interface definitions |
| `src/policy/feerate.h` | `CFeeRate` class |

---

## Algorithm Overview

The core class `CBlockPolicyEstimator` inherits `CValidationInterface` and listens to mempool and block events through callbacks.

### Core Idea

1. **Bucket transactions by feerate**: From 1000 sat/KB to 1e7 sat/KB, divided into ~200 buckets with 1.05x exponential spacing.
2. **Track confirmation rates per bucket**: Record the block height when a transaction enters the mempool, compute "how many blocks it waited before confirmation" when it's included in a block, and update statistics.
3. **When estimating, scan from the highest feerate bucket downward**, finding the last bucket where "confirmation success rate >= threshold," and return its average feerate.

### Three Time Windows

To balance short-term fluctuations and long-term trends, the algorithm maintains three sets of statistics (`TxConfirmStats`):

| Window | Max Tracked Blocks | Blocks per Period (scale) | Decay Factor | Half-life |
|--------|-------------------|--------------------------|--------------|-----------|
| SHORT | 12 | 1 | 0.962 | ~3 hours |
| MED | 48 | 2 | 0.9952 | ~1 day |
| LONG | 1008 | 24 | 0.99931 | ~1 week |

With each new block, all statistics are multiplied by the decay factor (exponential moving average), so older data has diminishing influence.

### Success Rate Thresholds

| Threshold | Value | Used for |
|-----------|-------|----------|
| HALF_SUCCESS_PCT | 60% | target/2 sub-estimate |
| SUCCESS_PCT | 85% | target sub-estimate |
| DOUBLE_SUCCESS_PCT | 95% | 2*target sub-estimate |

---

## TxConfirmStats Data Structure

```cpp
txCtAvg[bucket]           — total transaction count per bucket (decayed moving average)
confAvg[period][bucket]   — transactions confirmed within period periods
failAvg[period][bucket]   — transactions that left the mempool unconfirmed after period periods
unconfTxs[age][bucket]    — unconfirmed transactions in mempool by bucket and age (ring buffer)
oldUnconfTxs[bucket]      — old unconfirmed transactions beyond ring buffer length
m_feerate_avg[bucket]     — feerate sum per bucket (for computing average feerate)
```

---

## Complete Flow with a Sample Transaction

Suppose transaction txA: fee 5000 sat, virtual size 200 vB, feerate = 25 sat/vB = 25000 sat/KB. Current block height 800000.

### Step 1: txA Enters the Mempool

The mempool notifies the estimator through the `ValidationInterface` callback:

```cpp
// block_policy_estimator.cpp:581-584
void CBlockPolicyEstimator::TransactionAddedToMempool(const NewMempoolTransactionInfo& tx, uint64_t)
{
    processTransaction(tx);
}
```

Entering `processTransaction()` (line 596):

```cpp
void CBlockPolicyEstimator::processTransaction(const NewMempoolTransactionInfo& tx)
{
    LOCK(m_cs_fee_estimator);
    const unsigned int txHeight = tx.info.txHeight;       // = 800000
    const auto& hash = tx.info.m_tx->GetHash();           // = txA's txid

    // Check if height matches, ensuring chain is synced
    if (txHeight != nBestSeenHeight) {
        return;
    }

    // All four conditions must be met to include in estimation
    const bool validForFeeEstimation = !tx.m_mempool_limit_bypassed  // didn't bypass limits
                                    && !tx.m_submitted_in_package     // not submitted as package
                                    && tx.m_chainstate_is_current     // chain is synced
                                    && tx.m_has_no_mempool_parents;   // no unconfirmed parents

    if (!validForFeeEstimation) {
        untrackedTxs++;
        return;
    }
    trackedTxs++;

    // Calculate feerate: 5000 sat / 200 vB = 25000 sat/KB
    const CFeeRate feeRate(tx.info.m_fee, tx.info.m_virtual_transaction_size);

    // Record to mapMemPoolTxs: save entry height and bucket index
    mapMemPoolTxs[hash].blockHeight = txHeight;  // 800000

    // Register this transaction in all three stats trackers
    unsigned int bucketIndex = feeStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
    mapMemPoolTxs[hash].bucketIndex = bucketIndex;
    shortStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
    longStats->NewTx(txHeight, (double)feeRate.GetFeePerK());
}
```

`NewTx()` places txA into the "unconfirmed transactions" ring buffer:

```cpp
// block_policy_estimator.cpp:477-483
unsigned int TxConfirmStats::NewTx(unsigned int nBlockHeight, double val)
{
    // val = 25000 (sat/KB)
    // Find the smallest bucket upper bound >= 25000 in bucketMap, approximately buckets[167]
    unsigned int bucketindex = bucketMap.lower_bound(val)->second;

    // Increment count for this bucket in the ring slot corresponding to height 800000
    unsigned int blockIndex = nBlockHeight % unconfTxs.size();
    unconfTxs[blockIndex][bucketindex]++;
    return bucketindex;
}
```

**txA's state at this point**: In `mapMemPoolTxs` with `{blockHeight=800000, bucketIndex=167}`, and +1 count in the `unconfTxs` ring buffer of each of the three `TxConfirmStats`.

### Step 2: Block 800003 Arrives, txA Is Included

A miner includes txA in block 800003. The mempool removes txA and triggers the callback:

```cpp
// block_policy_estimator.cpp:591-594
void CBlockPolicyEstimator::MempoolTransactionsRemovedForBlock(
    const std::vector<RemovedMempoolTransactionInfo>& txs_removed_for_block,
    unsigned int nBlockHeight)
{
    processBlock(txs_removed_for_block, nBlockHeight);
}
```

Entering `processBlock()` (line 669):

```cpp
void CBlockPolicyEstimator::processBlock(
    const std::vector<RemovedMempoolTransactionInfo>& txs_removed_for_block,
    unsigned int nBlockHeight)  // = 800003
{
    LOCK(m_cs_fee_estimator);
    if (nBlockHeight <= nBestSeenHeight) {
        return;  // ignore side chains and reorgs
    }

    nBestSeenHeight = nBlockHeight;  // update to 800003

    // Operation 1: Roll the ring buffer
    feeStats->ClearCurrent(nBlockHeight);
    shortStats->ClearCurrent(nBlockHeight);
    longStats->ClearCurrent(nBlockHeight);

    // Operation 2: Decay all historical statistics (each stat *= decay)
    feeStats->UpdateMovingAverages();
    shortStats->UpdateMovingAverages();
    longStats->UpdateMovingAverages();

    // Operation 3: Process each transaction in the block
    unsigned int countedTxs = 0;
    for (const auto& tx : txs_removed_for_block) {
        if (processBlockTx(nBlockHeight, tx))
            countedTxs++;
    }
}
```

`UpdateMovingAverages()` performs exponential decay:

```cpp
// block_policy_estimator.cpp:231-242
void TxConfirmStats::UpdateMovingAverages()
{
    for (unsigned int j = 0; j < buckets.size(); j++) {
        for (unsigned int i = 0; i < confAvg.size(); i++) {
            confAvg[i][j] *= decay;   // e.g., MED: *= 0.9952
            failAvg[i][j] *= decay;
        }
        m_feerate_avg[j] *= decay;
        txCtAvg[j] *= decay;
    }
}
```

Then `processBlockTx()` is called for txA (line 641):

```cpp
bool CBlockPolicyEstimator::processBlockTx(unsigned int nBlockHeight,
                                           const RemovedMempoolTransactionInfo& tx)
{
    // First remove from mapMemPoolTxs and update unconfTxs count
    if (!_removeTx(tx.info.m_tx->GetHash(), true)) {
        return false;
    }

    // txA: 800003 - 800000 = 3 blocks to confirm
    int blocksToConfirm = nBlockHeight - tx.info.txHeight;  // = 3

    CFeeRate feeRate(tx.info.m_fee, tx.info.m_virtual_transaction_size);
    // = 25000 sat/KB

    // Record confirmation data in all three stats trackers
    feeStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    shortStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    longStats->Record(blocksToConfirm, (double)feeRate.GetFeePerK());
    return true;
}
```

`_removeTx()` removes txA from the unconfirmed count (`inBlock=true` means included in a block, not counted as failure):

```cpp
// block_policy_estimator.cpp:485-520
void TxConfirmStats::removeTx(unsigned int entryHeight, unsigned int nBestSeenHeight,
                               unsigned int bucketindex, bool inBlock)
{
    int blocksAgo = nBestSeenHeight - entryHeight;  // 800003 - 800000 = 3

    unsigned int blockIndex = entryHeight % unconfTxs.size();  // 800000 % 12
    unconfTxs[blockIndex][bucketindex]--;  // decrement unconfirmed count

    // inBlock=true, so no failure is recorded
    // If inBlock=false and blocksAgo >= scale, failAvg[period][bucketindex]++ would execute
}
```

`Record()` records "a transaction at 25000 sat/KB waited 3 blocks to confirm":

```cpp
// block_policy_estimator.cpp:217-229
void TxConfirmStats::Record(int blocksToConfirm, double feerate)
{
    // blocksToConfirm = 3, feerate = 25000
    if (blocksToConfirm < 1) return;

    // For the MED tracker: scale=2, so periodsToConfirm = (3+2-1)/2 = 2
    int periodsToConfirm = (blocksToConfirm + scale - 1) / scale;

    unsigned int bucketindex = bucketMap.lower_bound(feerate)->second;

    // For all periods >= periodsToConfirm, increment confirmation count
    // Meaning: "confirmed within 2 periods, so of course confirmed within 3, 4, ... 24 periods too"
    for (size_t i = periodsToConfirm; i <= confAvg.size(); i++) {
        confAvg[i - 1][bucketindex]++;
    }
    txCtAvg[bucketindex]++;                 // total tx count for this bucket +1
    m_feerate_avg[bucketindex] += feerate;  // accumulate feerate for averaging
}
```

**Data contributed by txA**: In bucket 167 (~25000 sat/KB), `confAvg[1..23][167]++` (periods 2 through 24 all marked as confirmed), `txCtAvg[167]++`, `m_feerate_avg[167] += 25000`.

### Aside: If txA Was Evicted Without Confirmation

If txA was evicted because the mempool was full (not included in a block), it takes a different path:

```cpp
// block_policy_estimator.cpp:586-589
void CBlockPolicyEstimator::TransactionRemovedFromMempool(const CTransactionRef& tx,
    MemPoolRemovalReason, uint64_t)
{
    removeTx(tx->GetHash());  // inBlock = false
}
```

```cpp
// TxConfirmStats::removeTx (lines 513-519)
if (!inBlock && (unsigned int)blocksAgo >= scale) {
    // inBlock=false and waited long enough → record as failure
    unsigned int periodsAgo = blocksAgo / scale;
    for (size_t i = 0; i < periodsAgo && i < failAvg.size(); i++) {
        failAvg[i][bucketindex]++;
    }
}
```

This lowers the success rate for that bucket, causing the next estimation to shift toward higher feerates.

### Step 3: User Queries "What Feerate for 3-Block Confirmation"

Calling `estimatesmartfee 3` RPC, which ultimately reaches `estimateSmartFee()` (line 871).

**Core logic: take the maximum of three sub-estimates**:

```cpp
CFeeRate CBlockPolicyEstimator::estimateSmartFee(int confTarget, FeeCalculation *feeCalc,
                                                  bool conservative) const
{
    LOCK(m_cs_fee_estimator);

    if (confTarget == 1) confTarget = 2;  // minimum is 2

    // Sub-estimate 1: target/2=1, 60% threshold
    double halfEst = estimateCombinedFee(confTarget/2, HALF_SUCCESS_PCT, true, &tempResult);
    median = halfEst;

    // Sub-estimate 2: target=3, 85% threshold
    double actualEst = estimateCombinedFee(confTarget, SUCCESS_PCT, true, &tempResult);
    if (actualEst > median) median = actualEst;

    // Sub-estimate 3: 2*target=6, 95% threshold
    double doubleEst = estimateCombinedFee(2 * confTarget, DOUBLE_SUCCESS_PCT, !conservative, &tempResult);
    if (doubleEst > median) median = doubleEst;

    // Conservative mode: additionally check the long-term window
    if (conservative || median == -1) {
        double consEst = estimateConservativeFee(2 * confTarget, &tempResult);
        if (consEst > median) median = consEst;
    }

    return CFeeRate(llround(median));
}
```

`estimateCombinedFee()` selects the shortest time window that can cover the target:

```cpp
// block_policy_estimator.cpp:808-842
double CBlockPolicyEstimator::estimateCombinedFee(unsigned int confTarget,
    double successThreshold, bool checkShorterHorizon, EstimationResult *result) const
{
    double estimate = -1;
    if (confTarget <= shortStats->GetMaxConfirms()) {
        // confTarget=3 <= 12, use SHORT window
        estimate = shortStats->EstimateMedianVal(confTarget, SUFFICIENT_TXS_SHORT,
                                                  successThreshold, nBestSeenHeight, result);
    }
    // Also checks if a shorter window gives a lower estimate
}
```

The core calculation `EstimateMedianVal(confTarget=3, threshold=0.85)` (line 245):

```cpp
double TxConfirmStats::EstimateMedianVal(int confTarget, double sufficientTxVal,
                                         double successBreakPoint, ...) const
{
    double nConf = 0;      // transactions confirmed within target
    double totalNum = 0;   // total confirmed transactions
    int extraNum = 0;      // transactions still waiting in mempool
    double failNum = 0;    // transactions that left mempool unconfirmed

    const int periodTarget = (confTarget + scale - 1) / scale;  // SHORT: scale=1, so =3

    // Scan from highest feerate bucket downward
    for (int bucket = maxbucketindex; bucket >= 0; --bucket) {
        nConf += confAvg[periodTarget - 1][bucket];
        totalNum += txCtAvg[bucket];
        failNum += failAvg[periodTarget - 1][bucket];
        for (unsigned int confct = confTarget; confct < GetMaxConfirms(); confct++)
            extraNum += unconfTxs[(nBlockHeight - confct) % bins][bucket];
        extraNum += oldUnconfTxs[bucket];

        // Enough data points?
        if (partialNum < sufficientTxVal / (1 - decay)) {
            continue;  // not enough, continue merging next bucket
        }

        // Enough, calculate success rate
        double curPct = nConf / (totalNum + failNum + extraNum);

        if (curPct < successBreakPoint) {
            passing = false;  // below threshold, continue scanning down
        } else {
            foundAnswer = true;
            bestNearBucket = curNearBucket;
            bestFarBucket = curFarBucket;
            // continue scanning down to see if there's a lower passing range
        }
    }

    // In the last passing bucket range, find the bucket containing the median transaction,
    // return its average feerate
    // ...
    median = m_feerate_avg[j] / txCtAvg[j];
    return median;  // e.g., returns 25000 (sat/KB) = 25 sat/vB
}
```

---

## Success Rate Formula

```
curPct = nConf / (totalNum + failNum + extraNum)
```

The denominator represents **the complete sample of all transactions**:

| Variable | Meaning | Source |
|----------|---------|--------|
| totalNum | Total confirmed transactions (regardless of wait time) | `txCtAvg[bucket]`, +1 in `Record()` |
| nConf | Of those, transactions confirmed within the target period (`nConf <= totalNum`) | `confAvg[period][bucket]`, +1 in `Record()` |
| failNum | Transactions that left the mempool without being confirmed | `failAvg[period][bucket]`, +1 in `removeTx(inBlock=false)` |
| extraNum | Transactions currently still waiting in the mempool | `unconfTxs + oldUnconfTxs`, +1 in `NewTx()` |

The three categories together = all transactions = confirmed + failed/evicted + still waiting.

**Why not just use nConf / totalNum?** Because that would ignore failed and waiting transactions, severely overestimating the success rate. Extreme example:

```
A low-feerate bucket:
  totalNum = 10    (10 happened to confirm)
  nConf = 9        (9 of those confirmed within target)
  failNum = 500    (500 were evicted)
  extraNum = 200   (200 are still waiting)

Using nConf/totalNum        = 9/10  = 90%   → "feerate is sufficient" (wrong)
Actually nConf/(10+500+200) = 9/710 = 1.3%  → "almost no chance of confirming" (correct)
```

---

## Ring Buffer (unconfTxs)

`unconfTxs` tracks "the count of transactions still waiting in the mempool" by block height.

```cpp
std::vector<std::vector<int>> unconfTxs;  // unconfTxs[height % ring_length][bucket_index]
std::vector<int> oldUnconfTxs;            // old transactions beyond ring buffer length
```

For the SHORT tracker, `GetMaxConfirms() = 12`, so `unconfTxs` has 12 slots. Using `height % 12` as the index for cyclic reuse, it keeps precise distributions for only the most recent 12 block heights; older ones are consolidated into `oldUnconfTxs`.

### Concrete Numerical Example

**Height 800000: txA enters the mempool**

```cpp
// NewTx()
unsigned int blockIndex = 800000 % 12;  // = 8
unconfTxs[8][167]++;  // slot 8, bucket 167 count becomes 1
```

```
Slot:     0  1  2  3  4  5  6  7 [8] 9  10  11
Bucket167: 0  0  0  0  0  0  0  0  1  0   0   0
                                    ↑ txA
```

**Height 800001: txB (same feerate bucket) enters the mempool**

```cpp
unsigned int blockIndex = 800001 % 12;  // = 9
unconfTxs[9][167]++;
```

```
Slot:     0  1  2  3  4  5  6  7 [8][9] 10  11
Bucket167: 0  0  0  0  0  0  0  0  1  1   0   0
                                    ↑  ↑
                                  txA txB
```

**Height 800003: Block arrives, txA is confirmed**

First `ClearCurrent(800003)` is executed — clears slot `800003 % 12 = 3` (already 0).

Then `_removeTx(txA, inBlock=true)`:

```cpp
int blocksAgo = 800003 - 800000;            // = 3
unsigned int blockIndex = 800000 % 12;      // = 8 (txA's entry slot)
unconfTxs[8][167]--;                         // 1 → 0
```

```
Slot:     0  1  2  3  4  5  6  7 [8][9] 10  11
Bucket167: 0  0  0  0  0  0  0  0  0  1   0   0
                                    ↑  ↑
                              txA gone txB still waiting
```

**Ring reuse**: After 12 blocks, height 800012 arrives, `800012 % 12 = 0` reuses slot 0. But `ClearCurrent()` first transfers old data to `oldUnconfTxs` before zeroing out.

### How It's Used During Estimation

`EstimateMedianVal()` needs to count "transactions that have waited >= confTarget blocks without confirming":

```cpp
// confTarget=3, nBlockHeight=800003
for (unsigned int confct = confTarget; confct < GetMaxConfirms(); confct++)
    extraNum += unconfTxs[(nBlockHeight - confct) % bins][bucket];
extraNum += oldUnconfTxs[bucket];
```

Expanded, this looks back at each slot, counting all transactions that entered at height <= 800000 and still haven't confirmed. These transactions have waited >= 3 blocks without confirming — evidence of "failure" that goes into the denominator, lowering the success rate.

---

## Complete Flow Summary

```
txA (25 sat/vB) enters mempool @ height 800000
    │
    ├─ processTransaction()
    │   ├─ mapMemPoolTxs[txA] = {height=800000, bucket=167}
    │   └─ unconfTxs[800000 % 12][167]++   (all three trackers)
    │
    │  ... waits 3 blocks ...
    │
    ▼ Block 800003 arrives, contains txA
    │
    ├─ processBlock()
    │   ├─ all stats *= decay             (decay old data)
    │   └─ processBlockTx(txA)
    │       ├─ _removeTx(txA, inBlock=true)
    │       │   └─ unconfTxs[...][167]--    (no longer unconfirmed)
    │       ├─ blocksToConfirm = 800003 - 800000 = 3
    │       └─ Record(3, 25000)
    │           ├─ confAvg[2..23][167]++    (period 3+ all marked confirmed)
    │           ├─ txCtAvg[167]++
    │           └─ m_feerate_avg[167] += 25000
    │
    ▼ User queries estimatesmartfee 3
    │
    └─ estimateSmartFee(3)
        ├─ estimateCombinedFee(1, 60%)  → halfEst
        ├─ estimateCombinedFee(3, 85%)  → actualEst
        ├─ estimateCombinedFee(6, 95%)  → doubleEst
        └─ return max(halfEst, actualEst, doubleEst)
            └─ EstimateMedianVal(): scan from high buckets downward
                find the last bucket range where success rate >= threshold
                return that range's average feerate
```

---

## Issues Discussed in Issue #27995

### Problem 1: Only Uses Historical Data, Slow to React

The algorithm only tracks "how long past transactions waited before confirming." Even if the mempool has cleared and feerates have dropped significantly, the estimate must wait for the decay factor to gradually reduce old data. The SHORT window has a half-life of 3 hours, MED is 1 day — during this period, estimates may be significantly too high.

### Problem 2: Tracks Behavior, Not Demand

If many users habitually pay high feerates (e.g., 100 sat/vB) even when 10 sat/vB would suffice, the algorithm concludes "100 sat/vB transactions all confirmed quickly" and gives high estimates. It measures "how fast does this feerate confirm," not "what's the minimum needed to confirm."

### Proposed Improvement: Incorporating Mempool Data

The issue proposes looking directly at the current mempool's feerate distribution for faster estimation, but faces policy divergence challenges:

- **Local policy stricter than network** (e.g., pre-taproot node): Can't see taproot transactions, mempool is missing some transactions, feerate estimate skews low.
- **Local policy more permissive than network**: Mempool contains transactions the network won't confirm, feerate estimate skews high.

So sipa proposed two sanity checks:
1. Check confirmation rates of high-feerate transactions — if most confirm, the local mempool is close to the network's.
2. Track "should have been included but wasn't" events — if a transaction is repeatedly not included, exclude it from the estimate.
