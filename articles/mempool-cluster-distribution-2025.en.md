# 2025 Mempool Measurement: Cluster Size and Topology Distribution

[中文版](mempool-cluster-distribution-2025.zh.md)

---

## Motivation

The [full-year 2023 analysis](mempool-cluster-distribution-2023.en.md) revealed a mempool
dominated by single-transaction clusters (93%), with only 22.8% of size ≥ 3 clusters being
chain-shaped.

The Bitcoin ecosystem in 2025 looks very different from 2023 — the Ordinals/BRC-20 frenzy has
subsided, the Runes protocol has launched, and the Lightning Network continues to evolve.
This article applies the same methodology to mempool snapshots from October 2025 through
February 2026, comparing results against the 2023 baseline.

---

## Data Source

Data is from [bitcoin.sipa.be/mempool_dumps/recent/](https://bitcoin.sipa.be/mempool_dumps/recent/),
comprising 134 daily compressed files (`YYYYMMDD_60s.dat.xz`) covering 2025-10-11 through
2026-02-21. Each file contains snapshots taken **every 60 seconds**, making the decompressed
data quite large (long-lived transactions are recorded repeatedly across many snapshots).

The binary format is identical to the 2023 data (uint64 timestamp + DepGraphFormatter cluster
sequence + 0x00 terminator).

Tooling: [`scripts/parse_mempool_xz.py`](../scripts/parse_mempool_xz.py) +
[`scripts/cluster_parser.c`](../scripts/cluster_parser.c).

---

## Overview

| Metric | Oct 2025–Feb 2026 | Full-year 2023 |
|--------|---:|---:|
| Files (days) | 134 | 366 |
| Total snapshots | 191,614 | 518,866 |
| Total cluster appearances | 1,090,769,462 | 27,606,344,453 |
| Total tx appearances | 6,272,243,670 | 38,673,857,477 |
| Avg clusters per snapshot | ~5,700 | ~53,200 |
| Avg transactions per snapshot | ~32,700 | ~74,500 |

The 2025 mempool is **significantly smaller** than 2023: only ~5,700 clusters per snapshot
on average (vs ~53,200 in 2023). The 2023 data was produced by @sdaftuar by replaying
historical p2p messages; the 2025 data comes from live sampling on a running node. Both
use 60-second snapshot intervals, making them directly comparable. The difference in
scale primarily reflects the actual mempool conditions of each period: 2023 was dominated
by the BRC-20/Ordinals frenzy that kept the mempool in extreme congestion, while the
2025 period covered here (October through February) was relatively quiet.

> **Counting note**: All "appearance" counts in this article are snapshot-weighted — the
> same cluster is counted once per 60-second snapshot while it remains in the mempool.
> The statistics therefore measure the **time-weighted** distribution of mempool state,
> not the number of distinct clusters that entered the mempool; clusters with longer
> residence times are correspondingly amplified. Since both datasets use the same method,
> cross-year comparisons are unaffected by this.

---

## Cluster Size Distribution

### Structure significantly different from 2023

| Size range | This Period | This Period Share | 2023 Share |
|------------|:---:|:---:|:---:|
| size = 1 | 664,891,983 | **60.9%** | 93.0% |
| size = 2 | 53,714,497 | 4.9% | 4.3% |
| size ≥ 3 | 372,162,982 | **34.1%** | 2.7% |

The most striking change in this period is that **size-1 share dropped from 93% to 61%**,
while size ≥ 3 surged from 2.7% to 34.1% — multi-transaction clusters now occupy a far
larger fraction of the mempool.

### Chain rate by size: a bimodal distribution

| tx count | chains | non-chain | total | chain% |
|---------:|-------:|----------:|------:|-------:|
| 1 | 664,891,983 | 0 | 664,891,983 | 100% |
| 2 | 53,714,497 | 0 | 53,714,497 | 100% |
| 3 | 13,632,457 | 16,002,661 | 29,635,118 | 46.0% |
| 4 | 8,924,556 | 2,107,428 | 11,031,984 | 80.9% |
| 5 | 23,243,351 | 2,977,073 | 26,220,424 | 88.7% |
| 6 | 43,612,014 | 4,109,181 | 47,721,195 | **91.4%** |
| 7 | 12,010,019 | 4,953,896 | 16,963,915 | 70.8% |
| 8 | 1,647,388 | 4,432,887 | 6,080,275 | **27.1%** |
| 9 | 1,464,277 | 4,386,348 | 5,850,625 | 25.0% |
| 10 | 1,591,279 | 5,009,819 | 6,601,098 | 24.1% |
| 11 | 1,590,235 | 6,354,722 | 7,944,957 | 20.0% |
| 12 | 1,264,123 | 7,286,173 | 8,550,296 | 14.8% |
| 13 | 1,434,126 | 8,733,530 | 10,167,656 | 14.1% |
| 14 | 1,667,312 | 9,597,103 | 11,264,415 | 14.8% |
| 15 | 4,831,110 | 8,154,671 | 12,985,781 | **37.2%** |
| 16 | 2,231,780 | 485,393 | 2,717,173 | **82.1%** |
| 17 | 2,390,234 | 493,084 | 2,883,318 | 82.9% |
| 18 | 2,138,792 | 455,771 | 2,594,563 | 82.4% |
| 19 | 2,871,023 | 380,882 | 3,251,905 | 88.3% |
| 20 | 21,565,556 | 394,162 | 21,959,718 | **98.2%** |
| 21 | 8,401,911 | 340,207 | 8,742,118 | 96.1% |
| 22 | 4,404,703 | 219,462 | 4,624,165 | 95.3% |
| 23 | 15,038,821 | 278,214 | 15,317,035 | 98.2% |
| 24 | 17,696,519 | 294,021 | 17,990,540 | 98.4% |
| 25 | 88,406,057 | 426,461 | 88,832,518 | **99.5%** |
| 26 | 75 | 2,205,101 | 2,205,176 | ≈0% |
| 27–64 | 307 | ~22,000 | ~22,300 | ~1% |

Aggregated by size range:

| range | chains | non-chain | total | chain% |
|------:|-------:|----------:|------:|-------:|
| 1 | 664,891,983 | 0 | 664,891,983 | 100% |
| 2 | 53,714,497 | 0 | 53,714,497 | 100% |
| 3–5 | 45,800,364 | 21,087,162 | 66,887,526 | 68.5% |
| 6–10 | 60,324,977 | 22,892,131 | 83,217,108 | 72.5% |
| 11–20 | 41,984,291 | 42,335,491 | 84,319,782 | 49.8% |
| 21–50 | 133,948,318 | 3,785,354 | 137,733,672 | **97.3%** |
| 51–64 | 0 | 4,894 | 4,894 | 0% |

---

## Key Findings

### 1. Multi-transaction clusters increased dramatically

In this period, size ≥ 3 clusters account for 34% of all appearances (vs 2.7% in 2023).
Even in absolute daily terms, the count is higher (2.78M/day in this period vs 2.05M/day
in 2023). Dependency relationships between transactions are richer, with more transactions
forming non-trivial clusters.

### 2. Chain rate shows a bimodal distribution

Unlike 2023's monotonically declining chain rate, this period exhibits a clear **bimodal pattern**:

```
chain%
100% ┤ ■  ■                                                                     ■
 98% ┤                                                          ■        ■  ■
 95% ┤                                                             ■  ■
 90% ┤             ■  ■                                      ■
 80% ┤          ■                                   ■  ■  ■
 70% ┤                   ■
 45% ┤       ■
 37% ┤                                           ■
 25% ┤                      ■  ■  ■
 20% ┤                               ■
 15% ┤                                  ■  ■  ■
     └─┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──
       1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25
```

- **First peak (size 4–7)**: chain% is 71–91%, peaking at size 6 (47.7M appearances).
  (Claude Code speculation:) This may correspond to standard **CPFP fee-bumping** patterns —
  a low-fee transaction with several child transactions to boost the overall fee rate.
- **Valley (size 8–14)**: chain% plummets to 14–27%. (Claude Code speculation:) These clusters
  may originate from **batch payments, Runes protocol operations, or multi-party protocols**
  that involve complex fork/merge dependencies.
- **Second peak (size 16–25)**: chain% climbs back to 82–99%, peaking at size 25
  (88.8M appearances, 99.5% chain). Long-chain clusters are almost entirely chain-shaped —
  (Claude Code speculation:) suggesting that **many services or protocols systematically
  build deep CPFP chains** close to or at the 25-transaction limit.

### 3. Size 6 is the most frequent multi-transaction cluster

The most common non-trivial cluster size in this period is **size 6** (47.7M appearances,
91.4% chain), whereas in 2023 it was size 3. This may suggest that transaction senders
(wallets, exchanges, payment processors) tend to build longer CPFP chains in this period,
though the underlying reason is unclear.

### 4. Size 25 dominance

Size-25 clusters appeared **88.8 million** times, accounting for **23.9%** of all size ≥ 3
clusters, with 99.5% being chain-shaped. Out of the 372M total size ≥ 3 cluster appearances,
size 25 alone contributes nearly a quarter.

Notably, by October 2025 Bitcoin Core has merged cluster mempool, replacing the old
per-transaction ancestor/descendant limits (`-limitancestorcount=25`) with a more relaxed
cluster size limit (default 100 transactions). Yet the size-25 spike persists. One possible explanation is that **wallets and services
still target the legacy 25-ancestor cap** when building CPFP chains; another is that many
nodes on the network have not yet upgraded, so senders continue targeting the old limit for
compatibility.

### 5. Size 26 cliff with rare exceptions

Similar to 2023, chain% drops to near 0% at size 26. However, the 2025 data shows a
**tiny number of chain clusters in the size 27–40 range** (about 300 total). Since cluster
mempool has raised the cluster size limit to 100 transactions, these long chains are now
permitted at the node level — they may come from services that have adapted to the new policy. Their rarity perhaps
suggests that most senders have not yet adjusted their chain length limits.

### 6. Chain rate of 76.2% for size ≥ 3

| Size threshold | Cluster count | Cluster chain% | Tx chain% | 2023 Comparison |
|----------------|:---:|:---:|:---:|:---:|
| size ≥ 3 | 372M | **76.2%** | **83.3%** | 22.8% |
| size ≥ 5 | 331M | **78.3%** | **84.0%** | 9.4% |
| size ≥ 10 | 226M | **78.4%** | **85.0%** | 5.0% |

In this period, 76.2% of size ≥ 3 clusters are chain-shaped by cluster count, and **83.3%
by transaction appearances** — the gap arises because large chain clusters (especially the
88.8M size-25 appearances at 99.5% chain) contribute disproportionately many transactions.
Both metrics are far above 2023 levels, reflecting the structural shift after BRC-20/Ordinals
congestion subsided.

---

## Monthly Trend

| Month | Days | Cluster appearances | Chain% |
|-------|:---:|:---:|:---:|
| 2025-10 | 21 | 258M | 83.11% |
| 2025-11 | 30 | 262M | 92.26% |
| 2025-12 | 31 | 245M | 98.37% |
| 2026-01 | 31 | 213M | 90.10% |
| 2026-02 | 21 | 114M | 98.89% |

- **October 2025**: Lowest chain% (83.1%), with a higher share of non-chain clusters.
- **December 2025 & February 2026**: Highest chain% (~98–99%), approaching pure chain structure.
- Monthly cluster volumes are relatively stable (200–260M/month), without the order-of-magnitude
  swings seen in 2023.

---

## Comparison with 2023

| Metric | Full-year 2023 | Oct 2025–Feb 2026 |
|--------|------|------|
| Avg clusters per snapshot | ~53,200 | ~5,700 |
| Size = 1 share | 93.0% | 60.9% |
| Size ≥ 3 share | 2.7% | 34.1% |
| Size ≥ 3 chain% | 22.8% | **76.2%** |
| Most frequent multi-tx size | 3 | **6** |
| Size 25 appearances | 170M | 88.8M |
| Size 25 chain% | 3.6% | **99.5%** |
| Chain rate vs size | Monotonically declining | **Bimodal** |

Core differences:

1. **Smaller mempool but richer structure**: This period's mempool did not experience the
   extreme congestion of 2023. Total transaction volume is lower, but a much higher proportion
   of transactions participate in multi-transaction clusters.
2. **Chain structure returns to dominance**: With Ordinals/BRC-20 fading, CPFP chains have
   reasserted themselves as the primary multi-transaction cluster pattern (76.2% vs 22.8%).
3. **Bimodal replaces monotonic decline**: The chain rate in this period exhibits a valley at
   sizes 8–14, suggesting these mid-sized clusters originate from specific protocols or batch
   operations.

---

## Reproduce

```bash
# Build C extension
gcc -O3 -march=native -shared -fPIC \
    -o scripts/cluster_parser.so scripts/cluster_parser.c

# Download 2025 data
python3 -c "
from datetime import date, timedelta
d, end = date(2025,10,11), date(2026,2,22)
while d <= end:
    print(f'https://bitcoin.sipa.be/mempool_dumps/recent/{d:%Y%m%d}_60s.dat.xz')
    d += timedelta(days=1)
" > /tmp/2025_urls.txt
mkdir -p /path/to/2025_mempool
cat /tmp/2025_urls.txt | xargs -P8 -I{} wget -q -P /path/to/2025_mempool/ {}

# Analyze
python3 scripts/parse_mempool_xz.py /path/to/2025_mempool/ --workers 7
```

---

## Related Articles

- [Full-Year 2023 Mempool Measurement: Cluster Size and Topology Distribution](mempool-cluster-distribution-2023.en.md)
- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
- [Replay Benchmark: TryLinearizeChain on Real Mempool Data](chain-fast-path-replay-bench.en.md)
