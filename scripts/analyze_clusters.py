#!/usr/bin/env python3
"""Analyze mempool cluster dump files.

Parses the dump format produced by the temporary Relinearize() instrumentation
and reports chain cluster statistics.

Dump format (one line per Relinearize call):
  <depgraph_hex> <is_topo> <max_iters> <rng_seed> <lin_count> <idx0> <idx1> ...

Usage:
  python3 scripts/analyze_clusters.py /path/to/mempool_clusters.txt
"""

import sys
from collections import Counter
from typing import Tuple, List, Set


def read_varint(data, pos):
    # type: (bytes, int) -> Tuple[int, int]
    """Read a Bitcoin Core VARINT from bytes, return (value, new_pos)."""
    n = 0
    while True:
        if pos >= len(data):
            raise ValueError("unexpected end of data in varint")
        ch = data[pos]
        pos += 1
        n = (n << 7) | (ch & 0x7F)
        if ch & 0x80:
            n += 1
        else:
            return n, pos


def unsigned_to_signed(x: int) -> int:
    """Decode zigzag: even x -> x/2, odd x -> -(x/2)-1."""
    if x & 1:
        return -(x >> 1) - 1
    return x >> 1


def parse_depgraph(hex_str: str):
    """Parse a DepGraphFormatter-serialized DepGraph from hex.

    Returns a list of (fee, size, ancestors_set) per transaction in topological
    serialization order, and a list of direct-parent sets.
    """
    data = bytes.fromhex(hex_str)
    pos = 0
    txs = []          # list of (fee, size, ancestors_set)  -- ancestors includes indirect
    direct_parents = []  # list of set of direct-parent topo indices

    while pos < len(data):
        # Read size (NONNEGATIVE_SIGNED — same wire format, just signed int)
        size, pos = read_varint(data, pos)
        size &= 0x3FFFFF
        if size == 0:
            break  # end marker

        # Read fee (zigzag encoded)
        coded_fee, pos = read_varint(data, pos)
        coded_fee &= 0xFFFFFFFFFFFFF
        fee = unsigned_to_signed(coded_fee)

        topo_idx = len(txs)
        new_ancestors = set()  # topo indices
        new_direct_parents = set()

        # Read dependency information
        diff, pos = read_varint(data, pos)
        for dep_dist in range(topo_idx):
            dep_topo_idx = topo_idx - 1 - dep_dist
            # Skip if already known ancestor
            if dep_topo_idx in new_ancestors:
                continue
            if diff == 0:
                # This is a parent
                new_direct_parents.add(dep_topo_idx)
                # Add it and all its ancestors
                new_ancestors.add(dep_topo_idx)
                new_ancestors |= txs[dep_topo_idx][2]  # ancestors of parent
                # Read next diff
                diff, pos = read_varint(data, pos)
            else:
                diff -= 1

        # The remaining diff encodes position information; we consume it but
        # don't need the cluster-order mapping for chain detection.
        # (diff was already read as the last varint in the dependency loop above,
        #  and it doubles as the position skip count — no extra read needed.)

        txs.append((fee, size, new_ancestors))
        direct_parents.append(new_direct_parents)

    return txs, direct_parents


def is_chain_cluster(txs, direct_parents) -> bool:
    """Check if cluster is a chain by verifying ancestor set sizes form {1,...,N}."""
    n = len(txs)
    if n == 0:
        return False
    # Ancestor counts (including self): should be {1, 2, ..., N}
    ancestor_counts = set()
    for i, (fee, size, ancestors) in enumerate(txs):
        count = len(ancestors) + 1  # +1 for self
        ancestor_counts.add(count)
    return ancestor_counts == set(range(1, n + 1))


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <mempool_clusters.txt>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    total_clusters = 0
    total_txs = 0
    chain_clusters = 0
    chain_txs = 0
    parse_errors = 0

    # Count chain clusters by tx count
    chain_size_dist = Counter()   # tx_count -> number of chain clusters
    all_size_dist = Counter()     # tx_count -> number of all clusters

    with open(filepath) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                parse_errors += 1
                continue

            hex_str = parts[0]
            # is_topo = int(parts[1])
            # max_iters = int(parts[2])
            # rng_seed = int(parts[3])
            # lin_count = int(parts[4])
            # old_lin = [int(x) for x in parts[5:5+lin_count]]

            try:
                txs, direct_parents = parse_depgraph(hex_str)
            except Exception as e:
                parse_errors += 1
                if parse_errors <= 5:
                    print(f"  line {lineno}: {e}", file=sys.stderr)
                continue

            n = len(txs)
            total_clusters += 1
            total_txs += n
            all_size_dist[n] += 1

            if is_chain_cluster(txs, direct_parents):
                chain_clusters += 1
                chain_txs += n
                chain_size_dist[n] += 1

            if total_clusters % 10000 == 0:
                print(f"  processed {total_clusters} clusters...", file=sys.stderr)

    # Print summary
    print("=" * 60)
    print("Mempool Cluster Analysis")
    print("=" * 60)
    print(f"Total clusters:   {total_clusters:>10,}")
    print(f"Total txs:        {total_txs:>10,}")
    print(f"Chain clusters:   {chain_clusters:>10,}  ({100*chain_clusters/total_clusters:.1f}%)" if total_clusters else "")
    print(f"Chain txs:        {chain_txs:>10,}  ({100*chain_txs/total_txs:.1f}%)" if total_txs else "")
    if parse_errors:
        print(f"Parse errors:     {parse_errors:>10,}")
    print()

    non_chain_size_dist = Counter()
    for n in all_size_dist:
        nc = all_size_dist[n] - chain_size_dist.get(n, 0)
        if nc > 0:
            non_chain_size_dist[n] = nc

    # Cluster size distribution
    print("Clusters by tx count:")
    print(f"  {'tx_count':>8}  {'chains':>8}  {'non-chain':>9}  {'all':>8}  {'chain%':>7}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*7}")
    for n in sorted(all_size_dist.keys()):
        c = chain_size_dist.get(n, 0)
        nc = non_chain_size_dist.get(n, 0)
        a = all_size_dist[n]
        pct = 100 * c / a if a else 0
        print(f"  {n:>8}  {c:>8,}  {nc:>9,}  {a:>8,}  {pct:>6.1f}%")

    # Aggregated ranges
    print()
    print("Clusters by size range:")
    ranges = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 20), (21, 64)]
    print(f"  {'range':>10}  {'chains':>8}  {'non-chain':>9}  {'all':>8}  {'chain%':>7}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*7}")
    for lo, hi in ranges:
        c = sum(chain_size_dist.get(n, 0) for n in range(lo, hi + 1))
        a = sum(all_size_dist.get(n, 0) for n in range(lo, hi + 1))
        nc = a - c
        pct = 100 * c / a if a else 0
        label = f"{lo}" if lo == hi else f"{lo}-{hi}"
        print(f"  {label:>10}  {c:>8,}  {nc:>9,}  {a:>8,}  {pct:>6.1f}%")


if __name__ == "__main__":
    main()
