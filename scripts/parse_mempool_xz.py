#!/usr/bin/env python3
"""Analyze 2023 mempool dump .xz files for chain cluster statistics.

Binary file format (repeated until EOF):
  uint64_t timestamp   - microseconds since epoch, little-endian (8 bytes)
  <clusters...>        - DepGraphFormatter-encoded clusters
  0x00                 - empty cluster, marks end of this dump

DepGraphFormatter cluster:
  Per tx in topological order (sorted ascending by ancestor count, then by index):
    VARINT size         (0 = end of cluster)
    VARINT coded_fee    (zigzag: even→non-negative, odd→negative)
    VARINT diff         (initial skip count / parent encoding)
    ...more VARINTs for each direct parent found (one per parent)
    (last VARINT consumed also encodes position info, which we ignore)

Chain cluster definition:
  tx at topo-position i must have ancestor bitmask == (1<<i)-1

Uses cluster_parser.so (compiled from cluster_parser.c) for speed.
Falls back to pure Python if the .so is unavailable.

Usage:
  python3 scripts/parse_mempool_xz.py <mempool_dir> [--limit N] [--workers W]
"""

import sys
import os
import lzma
import time
from collections import Counter
from multiprocessing import Pool, cpu_count
import ctypes

# ── C extension (fast path) ───────────────────────────────────────────────────
_RESULT_SIZE = 128
_lib = None

def _load_lib():
    global _lib
    so_path = os.path.join(os.path.dirname(__file__), 'cluster_parser.so')
    if not os.path.exists(so_path):
        return False
    try:
        lib = ctypes.CDLL(so_path)
        lib.parse_clusters.restype = ctypes.c_int64
        lib.parse_clusters.argtypes = [
            ctypes.c_char_p,          # data
            ctypes.c_int64,           # n
            ctypes.POINTER(ctypes.c_int64),  # counts
            ctypes.POINTER(ctypes.c_int64),  # chain_counts
            ctypes.c_int64,           # result_size
        ]
        _lib = lib
        return True
    except Exception as e:
        print(f"Warning: could not load cluster_parser.so: {e}", file=sys.stderr)
        return False


def _parse_file_c(data: bytes):
    """Parse using C extension. Returns (all_dist, chain_dist, n_dumps)."""
    counts_arr = (ctypes.c_int64 * _RESULT_SIZE)(*([0] * _RESULT_SIZE))
    chain_arr  = (ctypes.c_int64 * _RESULT_SIZE)(*([0] * _RESULT_SIZE))
    n_dumps = _lib.parse_clusters(data, len(data), counts_arr, chain_arr, _RESULT_SIZE)
    all_dist   = Counter({i: counts_arr[i] for i in range(_RESULT_SIZE) if counts_arr[i]})
    chain_dist = Counter({i: chain_arr[i]  for i in range(_RESULT_SIZE) if chain_arr[i]})
    return all_dist, chain_dist, n_dumps


# ── Pure Python fallback ──────────────────────────────────────────────────────
def _read_varint(data: bytes, pos: int):
    b = data[pos]
    if b < 0x80:
        return b, pos + 1
    n = b & 0x7F
    pos += 1
    b = data[pos]
    if b < 0x80:
        return ((n + 1) << 7) | b, pos + 1
    n = ((n + 1) << 7) | (b & 0x7F)
    pos += 1
    b = data[pos]
    if b < 0x80:
        return ((n + 1) << 7) | b, pos + 1
    n = ((n + 1) << 7) | (b & 0x7F)
    pos += 1
    while True:
        b = data[pos]; pos += 1
        n = (n << 7) | (b & 0x7F)
        if b & 0x80:
            n += 1
        else:
            return n, pos


def _parse_file_py(data: bytes):
    all_dist = Counter()
    chain_dist = Counter()
    length = len(data)
    pos = 0
    n_dumps = 0

    while pos < length:
        if pos + 8 > length:
            break
        pos += 8
        n_dumps += 1

        while pos < length:
            size, pos = _read_varint(data, pos)
            size &= 0x3FFFFF
            if size == 0:
                break

            ancestors = []

            # First tx: fee + position varint
            _, pos = _read_varint(data, pos)
            _, pos = _read_varint(data, pos)
            ancestors.append(0)

            while pos < length:
                size2, pos = _read_varint(data, pos)
                size2 &= 0x3FFFFF
                if size2 == 0:
                    break

                _, pos = _read_varint(data, pos)
                topo_idx = len(ancestors)
                anc_mask = 0
                diff, pos = _read_varint(data, pos)

                for dep_dist in range(topo_idx):
                    dep_topo_idx = topo_idx - 1 - dep_dist
                    if (anc_mask >> dep_topo_idx) & 1:
                        continue
                    if diff == 0:
                        anc_mask |= (1 << dep_topo_idx) | ancestors[dep_topo_idx]
                        diff, pos = _read_varint(data, pos)
                    else:
                        diff -= 1

                ancestors.append(anc_mask)

            n_txs = len(ancestors)
            all_dist[n_txs] += 1
            is_chain = True
            for i, mask in enumerate(ancestors):
                if mask != (1 << i) - 1:
                    is_chain = False
                    break
            if is_chain:
                chain_dist[n_txs] += 1

    return all_dist, chain_dist, n_dumps


# ── Worker function (called in subprocess) ────────────────────────────────────
def _worker(fpath: str):
    """Load and parse one .xz file. Must be top-level for pickling."""
    # Each worker reloads the .so (needed for multiprocessing)
    so_path = os.path.join(os.path.dirname(__file__), 'cluster_parser.so')
    use_c = False
    if os.path.exists(so_path):
        try:
            lib = ctypes.CDLL(so_path)
            lib.parse_clusters.restype = ctypes.c_int64
            lib.parse_clusters.argtypes = [
                ctypes.c_char_p, ctypes.c_int64,
                ctypes.POINTER(ctypes.c_int64),
                ctypes.POINTER(ctypes.c_int64),
                ctypes.c_int64,
            ]
            use_c = True
        except Exception:
            pass

    fname = os.path.basename(fpath)
    t0 = time.time()
    try:
        with lzma.open(fpath, 'rb') as f:
            data = f.read()
    except Exception as e:
        info = f"{fname} SKIPPED (decomp error: {e})"
        return Counter(), Counter(), 0, info
    t_decomp = time.time() - t0

    t1 = time.time()
    if use_c:
        counts_arr = (ctypes.c_int64 * _RESULT_SIZE)(*([0] * _RESULT_SIZE))
        chain_arr  = (ctypes.c_int64 * _RESULT_SIZE)(*([0] * _RESULT_SIZE))
        n_dumps = lib.parse_clusters(data, len(data), counts_arr, chain_arr, _RESULT_SIZE)
        all_dist   = Counter({i: counts_arr[i] for i in range(_RESULT_SIZE) if counts_arr[i]})
        chain_dist = Counter({i: chain_arr[i]  for i in range(_RESULT_SIZE) if chain_arr[i]})
    else:
        all_dist, chain_dist, n_dumps = _parse_file_py(data)

    t_parse = time.time() - t1
    sz_mb = len(data) / 1024 / 1024
    del data

    n_clusters = sum(all_dist.values())
    n_chains   = sum(chain_dist.values())
    pct = f"{100*n_chains/n_clusters:.1f}%" if n_clusters else "N/A"
    info = (f"{fname} ({sz_mb:.0f}MB) "
            f"decomp={t_decomp:.1f}s parse={t_parse:.1f}s | "
            f"{n_dumps} dumps, {n_clusters:,} clusters, chains={n_chains:,} ({pct})")
    return all_dist, chain_dist, int(n_dumps), info


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt_pct(num, den):
    return f"{100*num/den:.2f}%" if den else "N/A"


def main():
    args = sys.argv[1:]
    limit = None
    n_workers = max(1, cpu_count() - 1)
    mempool_dir = None
    i = 0
    while i < len(args):
        if args[i] == '--limit' and i + 1 < len(args):
            limit = int(args[i+1]); i += 2
        elif args[i] == '--workers' and i + 1 < len(args):
            n_workers = int(args[i+1]); i += 2
        else:
            mempool_dir = args[i]; i += 1

    if not mempool_dir:
        print(f"Usage: {sys.argv[0]} <mempool_dir> [--limit N] [--workers W]",
              file=sys.stderr)
        sys.exit(1)

    xz_files = sorted(f for f in os.listdir(mempool_dir) if f.endswith('.xz'))
    if limit:
        xz_files = xz_files[:limit]

    fpaths = [os.path.join(mempool_dir, f) for f in xz_files]

    # Check if C library is available
    so_path = os.path.join(os.path.dirname(__file__), 'cluster_parser.so')
    use_c = os.path.exists(so_path)
    print(f"Parser: {'C extension (fast)' if use_c else 'pure Python (slow)'}", file=sys.stderr)
    print(f"Workers: {n_workers}  Files: {len(fpaths)}", file=sys.stderr)

    total_all   = Counter()
    total_chain = Counter()
    total_dumps = 0
    t_start = time.time()
    done = 0

    with Pool(processes=n_workers) as pool:
        for result in pool.imap_unordered(_worker, fpaths):
            all_dist, chain_dist, n_dumps, info = result
            total_all   += all_dist
            total_chain += chain_dist
            total_dumps += n_dumps
            done += 1
            elapsed = time.time() - t_start
            print(f"[{done:3d}/{len(fpaths)}] {info}  (total {elapsed:.0f}s)",
                  file=sys.stderr)

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    # ── Print results ─────────────────────────────────────────────────────────
    total_clusters    = sum(total_all.values())
    total_chain_count = sum(total_chain.values())
    total_txs         = sum(n * c for n, c in total_all.items())
    chain_txs         = sum(n * c for n, c in total_chain.items())

    print()
    print("=" * 70)
    print("2023 Mempool Chain Cluster Analysis")
    print("=" * 70)
    print(f"Files processed:  {len(fpaths):>12,}")
    print(f"Total dumps:      {total_dumps:>12,}")
    print(f"Total clusters:   {total_clusters:>12,}")
    print(f"Total txs:        {total_txs:>12,}")
    print(f"Chain clusters:   {total_chain_count:>12,}  ({_fmt_pct(total_chain_count, total_clusters)})")
    print(f"Chain txs:        {chain_txs:>12,}  ({_fmt_pct(chain_txs, total_txs)})")
    print()

    # Per-size distribution
    print("Clusters by tx count:")
    print(f"  {'tx':>5}  {'chains':>12}  {'non-chain':>12}  {'total':>12}  {'chain%':>7}")
    print(f"  {'-'*5}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*7}")
    for n in sorted(total_all.keys()):
        c = total_chain.get(n, 0)
        t = total_all[n]
        nc = t - c
        print(f"  {n:>5}  {c:>12,}  {nc:>12,}  {t:>12,}  {_fmt_pct(c,t):>7}")

    # Aggregated ranges
    print()
    print("Clusters by size range:")
    ranges = [(1,1),(2,2),(3,5),(6,10),(11,20),(21,50),(51,100),(101,127)]
    print(f"  {'range':>8}  {'chains':>12}  {'non-chain':>12}  {'total':>12}  {'chain%':>7}")
    print(f"  {'-'*8}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*7}")
    for lo, hi in ranges:
        c = sum(total_chain.get(n, 0) for n in range(lo, hi+1))
        t = sum(total_all.get(n, 0)   for n in range(lo, hi+1))
        if t == 0:
            continue
        nc = t - c
        label = str(lo) if lo == hi else f"{lo}-{hi}"
        print(f"  {label:>8}  {c:>12,}  {nc:>12,}  {t:>12,}  {_fmt_pct(c,t):>7}")


if __name__ == '__main__':
    main()
