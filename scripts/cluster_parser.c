/*
 * cluster_parser.c — fast parser for Bitcoin Core mempool dump files
 *
 * Binary format per file:
 *   Repeated until EOF:
 *     uint64_t timestamp  (little-endian, 8 bytes)
 *     <clusters ...>      (DepGraphFormatter-encoded)
 *     0x00                (empty cluster = end of dump)
 *
 * Each cluster:
 *   Per tx in topological order:
 *     VARINT size   (0 = end of cluster)
 *     VARINT fee    (zigzag, ignored here)
 *     VARINT diff   + further VARINTs for each direct parent
 *                   (last VARINT also encodes position, ignored here)
 *
 * Compile:
 *   gcc -O3 -march=native -shared -fPIC -o cluster_parser.so cluster_parser.c
 */

#include <stdint.h>
#include <string.h>

/* Read Bitcoin Core VARINT from data[pos..n), advance *pos. */
static inline uint64_t read_varint(const uint8_t *data, int64_t n, int64_t *pos)
{
    uint64_t v = 0;
    while (*pos < n) {
        uint8_t b = data[(*pos)++];
        v = (v << 7) | (b & 0x7F);
        if (b & 0x80) {
            v += 1;
        } else {
            break;
        }
    }
    return v;
}

/*
 * parse_clusters — scan an entire decompressed dump file.
 *
 * data           : pointer to decompressed bytes
 * n              : byte count
 * counts         : output array, counts[i] += number of clusters with i txs
 * chain_counts   : output array, chain_counts[i] += chain clusters of size i
 * result_size    : length of counts / chain_counts arrays (must be >= 128)
 *
 * Returns: number of dumps (snapshots) found in the file.
 *
 * Chain definition: cluster of N txs where ancestors[i] == (1<<i)-1
 * (each tx has exactly all previous txs as its ancestor set).
 * This is equivalent to a linear chain for N <= 64.
 */
int64_t parse_clusters(const uint8_t *data, int64_t n,
                       int64_t *counts, int64_t *chain_counts,
                       int64_t result_size)
{
    int64_t pos = 0;
    int64_t n_dumps = 0;

    while (pos + 8 <= n) {
        pos += 8;   /* skip timestamp */
        n_dumps++;

        /* ---------- scan clusters in this dump ---------- */
        while (pos < n) {
            /* size of first tx — 0 means empty cluster (end of dump) */
            uint64_t size = read_varint(data, n, &pos) & 0x3FFFFF;
            if (size == 0) break;

            /* ancestor bitmask per topo-index (supports up to 64 txs) */
            uint64_t ancestors[64];
            int n_txs = 0;

            /* First tx: read fee + position varint (no dep loop) */
            read_varint(data, n, &pos);   /* fee  (ignored) */
            read_varint(data, n, &pos);   /* position varint */
            ancestors[n_txs++] = 0;

            /* Remaining txs */
            while (pos < n) {
                uint64_t size2 = read_varint(data, n, &pos) & 0x3FFFFF;
                if (size2 == 0) break;

                read_varint(data, n, &pos);   /* fee (ignored) */

                int topo_idx = n_txs;
                uint64_t anc_mask = 0;
                uint64_t diff = read_varint(data, n, &pos);

                for (int dep_dist = 0; dep_dist < topo_idx; dep_dist++) {
                    int dep_topo_idx = topo_idx - 1 - dep_dist;
                    if ((anc_mask >> dep_topo_idx) & 1)
                        continue;  /* already a known ancestor */
                    if (diff == 0) {
                        /* dep_topo_idx is a direct parent */
                        anc_mask |= ((uint64_t)1 << dep_topo_idx)
                                  | ancestors[dep_topo_idx];
                        diff = read_varint(data, n, &pos);
                    } else {
                        diff--;
                    }
                }

                if (n_txs < 64) {
                    ancestors[n_txs] = anc_mask;
                }
                n_txs++;
            }

            /* Record cluster stats (skip oversized clusters) */
            if (n_txs < (int)result_size) {
                counts[n_txs]++;

                /* Chain check: ancestors[i] must equal exactly (1<<i)-1 */
                int is_chain = 1;
                if (n_txs > 64) {
                    is_chain = 0;  /* can't check with 64-bit bitmask */
                } else {
                    for (int i = 0; i < n_txs; i++) {
                        uint64_t expected = (i == 0) ? 0 : (((uint64_t)1 << i) - 1);
                        if (ancestors[i] != expected) {
                            is_chain = 0;
                            break;
                        }
                    }
                }
                if (is_chain) chain_counts[n_txs]++;
            }
        }
    }

    return n_dumps;
}
