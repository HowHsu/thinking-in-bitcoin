# Full-Year 2023 Mempool Measurement: Cluster Size and Topology Distribution

[中文版](mempool-cluster-distribution-2023.zh.md)

---

## Motivation

Cluster mempool groups transactions by their dependency relationships. Each cluster's **size**
(transaction count) and **topology** (whether it forms a strict linear chain) directly affect
the behavior and cost of linearization algorithms.

This article analyzes the full year of 2023 mempool snapshot data published by
[Pieter Wuille](https://github.com/sipa), characterizing the size distribution and chain
topology prevalence of clusters in a real mempool.

---

## Data Source

Data is from [bitcoin.sipa.be/mempool_dumps/sim2023/](https://bitcoin.sipa.be/mempool_dumps/sim2023/),
comprising 366 daily compressed files (`txgraph.YYYYMMDD.xz`) covering 2023-01-01 through
2024-01-01.

Each file is binary, containing multiple mempool snapshots (dumps):

```
[uint64_t timestamp  (8 bytes, little-endian)]
[DepGraphFormatter-encoded cluster sequence  ]
[0x00  empty cluster, marks end of this dump ]
... repeated until EOF
```

**Chain cluster definition**: N transactions forming a strict linear dependency chain
tx₀→tx₁→…→tx_{N−1}. Equivalently, in topological order, the i-th transaction has exactly
i ancestors.

Tooling: [`scripts/parse_mempool_xz.py`](../scripts/parse_mempool_xz.py) with a C extension
[`scripts/cluster_parser.c`](../scripts/cluster_parser.c) (~340× faster than pure Python).

---

## Overview

| Metric | Value |
|--------|------:|
| Files (days) | 366 |
| Total snapshots | 518,866 |
| Total cluster appearances | 27,606,344,453 |
| Total tx appearances | 38,673,857,477 |

> "Appearances" counts each cluster once per snapshot — the same cluster persisting across
> consecutive snapshots is counted multiple times, reflecting how long it stays in the mempool.

---

## Cluster Size Distribution

### The vast majority of clusters contain only 1–2 transactions

| Size range | Appearances | Share |
|------------|:---:|:---:|
| size = 1 | 25,660,782,979 | **92.97%** |
| size = 2 | 1,192,780,835 | **4.32%** |
| size ≥ 3 | 748,465,685 | 2.71% |

93% of clusters contain a single transaction — standalone transactions with no unconfirmed
dependencies. Adding size-2 clusters brings the total to 97.3%. At any given moment,
**the vast majority of transactions in the mempool are independent of each other.**

### Detailed distribution of multi-transaction clusters

| tx count | chains | non-chain | total | chain% |
|---------:|-------:|----------:|------:|-------:|
| 1 | 25,660,782,979 | 0 | 25,660,782,979 | 100% |
| 2 | 1,192,780,835 | 0 | 1,192,780,835 | 100% |
| 3 | 96,316,078 | 40,556,047 | 136,872,125 | 70.4% |
| 4 | 23,087,895 | 48,331,544 | 71,419,439 | 32.3% |
| 5 | 13,022,582 | 27,545,835 | 40,568,417 | 32.1% |
| 6 | 6,803,108 | 29,662,329 | 36,465,437 | 18.7% |
| 7 | 4,140,517 | 12,090,118 | 16,230,635 | 25.5% |
| 8 | 3,393,300 | 14,978,354 | 18,371,654 | 18.5% |
| 9 | 2,508,113 | 8,504,208 | 11,012,321 | 22.8% |
| 10 | 2,372,662 | 20,939,391 | 23,312,053 | 10.2% |
| 11 | 1,445,834 | 22,324,997 | 23,770,831 | 6.1% |
| 12 | 1,363,200 | 9,554,264 | 10,917,464 | 12.5% |
| 13 | 1,181,270 | 6,216,983 | 7,398,253 | 16.0% |
| 14 | 1,063,468 | 7,566,324 | 8,629,792 | 12.3% |
| 15 | 1,036,274 | 5,833,014 | 6,869,288 | 15.1% |
| 16 | 759,874 | 7,778,041 | 8,537,915 | 8.9% |
| 17 | 610,152 | 5,311,028 | 5,921,180 | 10.3% |
| 18 | 548,091 | 6,474,562 | 7,022,653 | 7.8% |
| 19 | 538,318 | 5,361,574 | 5,899,892 | 9.1% |
| 20 | 1,292,013 | 10,958,681 | 12,250,694 | 10.5% |
| 21 | 478,343 | 16,228,211 | 16,706,554 | 2.9% |
| 22 | 709,635 | 8,301,302 | 9,010,937 | 7.9% |
| 23 | 562,705 | 7,954,823 | 8,517,528 | 6.6% |
| 24 | 1,024,650 | 32,177,605 | 33,202,255 | 3.1% |
| 25 | 6,071,602 | 163,501,070 | 169,572,672 | 3.6% |
| 26 | 3 | 59,985,693 | 59,985,696 | ≈0% |
| 27–64 | 0 | ~4,900,000 | ~4,900,000 | 0% |

Aggregated by size range:

| range | chains | non-chain | total | chain% |
|------:|-------:|----------:|------:|-------:|
| 1 | 25,660,782,979 | 0 | 25,660,782,979 | 100% |
| 2 | 1,192,780,835 | 0 | 1,192,780,835 | 100% |
| 3–5 | 132,426,555 | 116,433,426 | 248,859,981 | 53.2% |
| 6–10 | 19,217,700 | 86,174,400 | 105,392,100 | 18.2% |
| 11–20 | 9,838,494 | 87,379,468 | 97,217,962 | 10.1% |
| 21–50 | 8,846,938 | 292,180,913 | 301,027,851 | 2.9% |
| 51–64 | 0 | 282,745 | 282,745 | 0% |

---

## Key Findings

### 1. Size 1–2: always chains

- **Size 1** (single transaction): no dependencies — trivially chain-shaped.
- **Size 2**: two transactions in the same cluster must share a dependency edge (otherwise
  they'd be separate clusters). A two-node DAG with one directed edge is by definition a chain.

### 2. Size 3–25: chain rate declines monotonically with size

From 3-tx clusters (70% chains) down to 25-tx clusters (3.6%), the chain rate drops
monotonically. Larger clusters are more likely to contain multi-parent transactions
(e.g., consolidations spending multiple unconfirmed parents) or forking structures
(a parent with multiple unconfirmed children), both of which break strict chain topology.

Looking at size ≥ 3 clusters only, the chain rate is **22.8%**.
For size ≥ 5 only, it drops further to **9.4%**.

### 3. The size-25 count anomaly

Size-25 clusters appeared **169.6 million** times — **5×** more than size-24 (33.2M).

This directly reflects Bitcoin Core's default **ancestor/descendant limit of 25 transactions**:
any transaction's unconfirmed ancestor chain (inclusive) is capped at 25. Size-25 is the
maximum CPFP chain length under standard mempool policy. These clusters tend to persist in
the mempool for extended periods (waiting to be mined), accumulating a disproportionately
high snapshot count.

Only 3.6% of size-25 clusters are strict chains — the majority carry forking or merging
structure, especially during the Ordinals/BRC-20 period of 2023.

### 4. Size 26: a hard cliff

Size-26 clusters appeared 59.9 million times, yet only **3 are chains** (chain% ≈ 0%).

A 26-transaction linear chain would require its last transaction to have 25 ancestors —
hitting the mempool policy limit exactly. Standard Bitcoin Core nodes reject such a
transaction from entering the mempool. Therefore, any size-26 cluster must contain a
**forking structure** where some transactions share ancestor "budget" without any single
transaction exceeding 25 ancestors.

### 5. Size 27+: zero chains

Clusters of 27+ transactions (~4.9M appearances) are all non-chains, for the same reason
as size-26. No linear chain of 27+ transactions can exist under default mempool policy.

---

## Monthly Trend

| Month | Cluster appearances | Chain% |
|-------|:---:|:---:|
| 2023-01 | 158M | 99.02% |
| 2023-02 | 374M | 99.31% |
| 2023-03 | 732M | 99.46% |
| 2023-04 | 702M | 94.85% |
| 2023-05 | 1,865M | 93.46% |
| 2023-06 | 2,763M | 97.07% |
| 2023-07 | 3,926M | 98.25% |
| 2023-08 | 6,394M | 99.42% |
| 2023-09 | 4,574M | 99.31% |
| 2023-10 | 368M | 96.75% |
| 2023-11 | 3,056M | 97.61% |
| 2023-12 | 2,679M | 95.88% |

The per-month chain% values are dominated by size-1 clusters (93% of all appearances) and
stay uniformly high. The more interesting signal is the **variation in total cluster volume**:

- **January–March 2023**: A quiet mempool, averaging ~5M cluster appearances per day.
- **April–May 2023**: The **BRC-20 token** explosion. Cluster volume surges from 700M to
  1.87B per month. Non-chain clusters (complex dependency structures from inscription
  transactions) flood in, pushing chain% from 99% down to 93%.
- **August 2023**: Peak volume for the year (6.4B cluster appearances), yet chain% rebounds
  to 99.4%. This indicates the bulk of traffic entered the mempool as independent
  transactions (each forming its own size-1 cluster), rather than as complex interdependent
  groups.
- **October 2023**: Mempool shrinks dramatically (368M — near January levels), but chain%
  is only 96.7%. The lower volume means fewer standalone transactions diluting the count,
  leaving a higher relative share of non-chain structures.
- **November–December 2023**: A second congestion wave (sustained Ordinals activity +
  Runes anticipation). Cluster volume rises back to 2.7–3.1B, with chain% at 96–98%.

---

## Summary

| Observation | Data |
|-------------|------|
| Most common cluster type in the mempool | Single transaction (93%) |
| Chain share among size ≥ 3 clusters | 22.8% |
| Chain share among size ≥ 5 clusters | 9.4% |
| Maximum chain length (mempool policy limit) | 25 |
| Correlation of non-chain cluster volume with BRC-20/Ordinals congestion | Strong |

**Key takeaway**: At any moment, the overwhelming majority of clusters in a mempool
snapshot are simple 1–2 transaction structures. Multi-transaction clusters (size ≥ 3)
account for only 2.7% of appearances, and most of those are not strict chains — especially
during the inscription congestion periods of 2023 when large non-chain clusters surged.
Chain clusters hit a hard ceiling at size 26 due to the 25-ancestor mempool policy limit;
no chain of 27+ transactions exists in the data.

---

## Reproduce

The parsing script and C extension live in `scripts/`:

```bash
# Build C extension
gcc -O3 -march=native -shared -fPIC \
    -o scripts/cluster_parser.so scripts/cluster_parser.c

# Run (--workers N for parallelism)
python3 scripts/parse_mempool_xz.py /path/to/2023_mempool/ --workers 7
```

Download the data:

```bash
# Generate URL list
python3 -c "
from datetime import date, timedelta
d, end = date(2023,1,1), date(2024,1,2)
while d < end:
    print(f'https://bitcoin.sipa.be/mempool_dumps/sim2023/txgraph.{d:%Y%m%d}.xz')
    d += timedelta(days=1)
" > /tmp/mempool_urls.txt

# Parallel download
cat /tmp/mempool_urls.txt | xargs -P8 -I{} wget -q -P /path/to/2023_mempool/ {}
```

---

## Related Articles

- [O(N) Fast Path for Chain-Shaped Clusters](chain-cluster-optimization.en.md)
- [Replay Benchmark: TryLinearizeChain on Real Mempool Data](chain-fast-path-replay-bench.en.md)
- [O(N²) Bottlenecks Beyond Relinearize](chain-beyond-relinearize.en.md)
