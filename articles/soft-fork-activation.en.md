# Bitcoin Soft Fork Activation: From BIP9 Signaling to BuriedDeployment

[中文](soft-fork-activation.zh.md)

---

## Introduction

This article grew out of the author's review of PR [#26201](https://github.com/bitcoin/bitcoin/pull/26201) (Remove Taproot activation height). The PR completely removes Taproot from BIP9 deployment tracking — a seemingly simple cleanup that, upon closer inspection, touches the entire soft fork activation machinery. To truly understand what this PR does and why, the author dug into the full picture from BIP9 signaling to BuriedDeployment, and compiled the findings here.

Bitcoin consensus rule upgrades are deployed via "soft forks" — new rules that are a subset of old rules, so un-upgraded nodes can still validate blocks but may accept transactions that violate the new rules. The core challenge of a soft fork is not the technical implementation but **coordinating activation**: how to safely switch to new rules across a decentralized network.

---

## 1. BIP9: Miner-Signaled Activation

### 1.1 Why BIP9 Was Needed

Early soft forks (BIP34, BIP65, BIP66) coordinated activation by incrementing the block version number: v1→v2→v3→v4, activating new rules when 950/1000 blocks used the new version.

The problem: **no parallelism**. The version number is a monotonically increasing integer, so only one soft fork could be in the voting phase at a time.

BIP9 (proposed in 2015 by Pieter Wuille, Peter Todd, and Greg Maxwell) solved this by using individual bits in the block header's `nVersion` field as independent signals, supporting up to 29 concurrent soft fork votes.

### 1.2 nVersion Bit Layout

`nVersion` is a 32-bit integer:

```
bit 31 30 29 | 28 27 26 ... 2 1 0
     0  0  1 |  ← 29 signal bits →
     ^^^
  fixed prefix = 0x20000000
```

Defined in `src/versionbits.h`:

```cpp
static const int32_t VERSIONBITS_TOP_BITS = 0x20000000UL;  // prefix 001
static const int32_t VERSIONBITS_TOP_MASK = 0xE0000000UL;  // top 3-bit mask
```

Nodes first check whether the top 3 bits are `001`; only then is the version treated as a BIP9 version. The 29 bits do not limit Bitcoin to 29 upgrades — once a soft fork completes (ACTIVE or FAILED), its bit is released for reuse. The constraint is only that at most 29 votes can proceed **simultaneously**.

### 1.3 How Miners Vote

Each BIP9 deployment is assigned a bit. Miners set that bit in `nVersion` when producing a block to signal support. For example, Taproot used bit 2:

```
nVersion = 0x20000000 | (1 << 2) = 0x20000004
```

Signal checking logic (`src/versionbits_impl.h`):

```cpp
bool Condition(int32_t nVersion) const {
    return (((nVersion & VERSIONBITS_TOP_MASK) == VERSIONBITS_TOP_BITS)  // correct prefix
            && (nVersion & Mask()) != 0);                                // target bit set
}
```

**Voting power is proportional to hashrate**: if a miner produces 100 blocks in a period and sets the bit on all of them, that counts as 100 votes. This is intentional — BIP9 measures "how much hashrate is ready for the new rules," not "how many people support it."

### 1.4 The State Machine

BIP9 defines five states, with transitions evaluated at the boundary of each 2016-block period (the same as the difficulty adjustment interval):

```
DEFINED → STARTED → LOCKED_IN → ACTIVE
                 ↘ FAILED
```

Each deployment is configured with a full parameter set in `chainparams.cpp`:

```cpp
struct BIP9Deployment {
    int bit;                     // bit position in nVersion
    int64_t nStartTime;          // start accepting signals
    int64_t nTimeout;            // expiry time
    int min_activation_height;   // earliest activation height after lock-in
    uint32_t period;             // counting period (usually 2016)
    uint32_t threshold;          // required signal count for lock-in
};
```

The core state transition logic lives in `GetStateFor()` in `src/versionbits.cpp`:

| Current State | Transition Condition | Next State |
|--------------|---------------------|-----------|
| DEFINED | Period's MTP >= nStartTime | STARTED |
| STARTED | Signal count in period >= threshold | LOCKED_IN |
| STARTED | MTP >= nTimeout without reaching threshold | FAILED |
| LOCKED_IN | Next period height >= min_activation_height | ACTIVE |
| ACTIVE | — | ACTIVE (terminal) |
| FAILED | — | FAILED (terminal) |

### 1.5 Taproot's Actual Activation Timeline

Taproot mainnet parameters: bit=2, threshold=1815/2016 (≈90%), nStartTime=2021-04-24, nTimeout=2021-08-11, min_activation_height=709632.

```
2021-04-24   MTP reaches nStartTime → enters STARTED
             Miners begin signaling on bit 2

2021-06-12   A 2016-block period reaches 1815+ signaling blocks
             → enters LOCKED_IN

2021-06~11   min_activation_height = 709632 not yet reached
             → remains LOCKED_IN

2021-11-14   Height 709632 reached
             → enters ACTIVE, Taproot rules enforced
```

### 1.6 Who Decides the Threshold

The threshold is set by the soft fork proposers in the code and shipped with the release. Different networks can use different values:

| Soft Fork | Mainnet Threshold |
|----------|------------------|
| CSV, SegWit | 1916/2016 ≈ 95% |
| Taproot (Speedy Trial) | 1815/2016 ≈ 90% |

Taproot's reduction from 95% to 90% was a community decision — at 95%, a small minority of hashrate could indefinitely block activation.

But the ultimate decision lies in **how many nodes choose to run code containing that threshold**. If the community disagrees, they can refuse to run the release or fork a modified version. The most famous example is the **SegWit activation battle** (2016–2017): some large mining pools refused to signal support for months, leading the community to propose BIP148 (UASF — User Activated Soft Fork), where node operators would bypass miner voting and force activation by a set date. Miners ultimately capitulated under UASF pressure. This proved that **ultimate power rests with full-node operators** — full nodes decide what chain is valid, and blocks that nodes reject are worthless regardless of the hashrate behind them.

---

## 2. BuriedDeployment: Hardcoding Ancient History

### 2.1 Motivation

After a BIP9 deployment activates, the state machine computation still runs on every check: walking back through blocks period by period, counting signals. For BIP34, which activated in 2012, this means traversing over a decade of history every time just to confirm "yes, it's active" — pure waste.

BIP90 (proposed in 2016 by Suhas Daftuar) offered a dead-simple fix:

> For soft forks that activated long ago and cannot possibly be reorged away, hardcode the activation height directly into the code.

### 2.2 Code Structure

BuriedDeployment uses a simple enum plus hardcoded heights:

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

The activation check is reduced from a state machine traversal to an **O(1) height comparison**:

```cpp
// BuriedDeployment: simple height comparison
inline bool DeploymentActiveAfter(..., BuriedDeployment dep, ...) {
    return (pindexPrev->nHeight + 1) >= params.DeploymentHeight(dep);
}

// BIP9 DeploymentPos: state machine + cache
inline bool DeploymentActiveAfter(..., DeploymentPos dep, VersionBitsCache& cache) {
    return cache.IsActiveAfter(pindexPrev, params, dep);
}
```

### 2.3 Unified Call Interface

`src/deploymentstatus.h` provides identically-named overloaded functions, so callers don't need to know which mechanism is used underneath:

```cpp
DeploymentActiveAt(block, params, Consensus::DEPLOYMENT_SEGWIT, cache);    // → height comparison
DeploymentActiveAt(block, params, Consensus::DEPLOYMENT_TESTDUMMY, cache); // → state machine
```

---

## 3. The Full Lifecycle of a Soft Fork

```
Stage 1: Proposal
  Add a new DeploymentPos enum value
  Configure bit, startTime, timeout, threshold in chainparams.cpp
  Add conditional checks in validation logic

Stage 2: BIP9 Voting
  Miners signal via nVersion bits
  Nodes track state: DEFINED → STARTED → LOCKED_IN → ACTIVE

Stage 3: Activated, Still Tracked via BIP9
  State machine always returns ACTIVE
  Redundant computation, but harmless

Stage 4: Buried (BuriedDeployment)
  Move from DeploymentPos to BuriedDeployment
  Add hardcoded height, remove BIP9 config
  Validation simplified to height comparison

Stage 5: Enforced from Genesis
  Rules treated as "always valid"
  Completely removed from deployment tracking
```

---

## 4. PR #26201: Taproot Goes Directly from Stage 3 to Stage 5

PR #26201 completely removes Taproot from BIP9 deployment tracking, but **skips the BuriedDeployment stage**.

### Why SegWit Needs BuriedDeployment but Taproot Does Not

SegWit remains in `BuriedDeployment` because code still needs to know the activation height:
- Pre-activation blocks don't carry witness data; nodes need to know from which height to require witness downloads
- BIP147 relies on the hardcoded activation height

Taproot has no such structural boundary — its validation rules (Schnorr signatures, tapscript) operate at the script execution layer. `SCRIPT_VERIFY_TAPROOT` can simply be always enabled; there is no block-structure distinction to make based on height.

### Specific Changes

| File | Change |
|------|--------|
| `consensus/params.h` | Remove `DEPLOYMENT_TAPROOT`, bump `MinBIP9WarningHeight` |
| `kernel/chainparams.cpp` | Remove Taproot BIP9 config from all networks |
| `deploymentinfo.cpp` | Remove taproot `VBDeploymentInfo` entry |
| `rpc/blockchain.cpp` | `getdeploymentinfo` no longer returns taproot |
| `rpc/mining.cpp` | Explicitly add `"taproot"` to `getblocktemplate` rules |
| `mining_basic.py` | Assert rules = `['csv', '!segwit', 'taproot']` |

`MinBIP9WarningHeight` is bumped from post-segwit (483840) to post-taproot (711648), preventing nodes from issuing "unknown deployment" warnings for historical Taproot BIP9 signals.

---

## Summary

Bitcoin's soft fork activation mechanism embodies the core tension of decentralized systems: upgrades must be coordinated, yet no central authority can mandate them. BIP9 uses hashrate signaling to measure readiness, BuriedDeployment uses hardcoded heights to clean up historical baggage, and ultimate authority is distributed across every node operator who chooses which version of the code to run.
