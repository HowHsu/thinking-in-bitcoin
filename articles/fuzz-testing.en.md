# Fuzz Testing Bitcoin Core: A Practical Guide

[中文版](fuzz-testing.zh.md)

---

## What Is Fuzzing?

Fuzzing is an automated testing technique that feeds a program with a large
volume of randomly mutated inputs and monitors it for crashes, assertion
failures, memory errors, or undefined behaviour. Unlike hand-written unit
tests that only exercise paths the author thought of, a fuzzer explores the
input space continuously and can reach corner cases that no human anticipated.

---

## Why Fuzz Bitcoin Core?

Bitcoin Core is consensus-critical software: a single memory-safety bug can be
exploited to crash nodes or (in extreme cases) corrupt the UTXO set. The
project therefore treats fuzzing as a first-class testing tool:

- Every PR is automatically tested against the seed corpora in
  [`bitcoin-core/qa-assets`](https://github.com/bitcoin-core/qa-assets).
- Bitcoin Core participates in [OSS-Fuzz](https://github.com/google/oss-fuzz),
  Google's continuous fuzzing infrastructure for open-source projects.
- There are currently 200+ fuzzing harnesses covering networking, consensus,
  cryptography, mempool, and more.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| `clang` / `clang++` | The `libfuzzer` preset requires clang. |
| `cmake` ≥ 3.22 | Bitcoin Core uses CMake. |
| Linux (recommended) | macOS is unsupported for fuzzing by the project. |

Install on Ubuntu/Debian:

```sh
sudo apt install clang cmake ninja-build
```

---

## How the Fuzz Binary Works

All fuzzing harnesses are compiled into a **single binary** (`build_fuzz/bin/fuzz`).
The target to run is selected at runtime via the `FUZZ` environment variable:

```sh
FUZZ=<target_name> build_fuzz/bin/fuzz [corpus_dir] [libFuzzer flags]
```

Each harness (`src/test/fuzz/*.cpp`) registers one or more targets with the
`FUZZ_TARGET(name)` macro.

---

## Build

Bitcoin Core ships two CMake presets for fuzzing:

### With sanitizers (recommended for finding bugs)

```sh
cmake --preset=libfuzzer          # configures in build_fuzz/
cmake --build build_fuzz -j$(nproc)
```

This preset sets:
- `BUILD_FOR_FUZZING=ON` — deterministic mode, only the fuzz binary is built
- `SANITIZERS=undefined,address,fuzzer` — UBSan + ASan + libFuzzer

Sanitizers slow execution by ~5–10× but catch far more bugs.

### Without sanitizers (for throughput / corpus growth)

```sh
cmake --preset=libfuzzer-nosan    # configures in build_fuzz_nosan/
cmake --build build_fuzz_nosan -j$(nproc)
```

Uses only `-fsanitize=fuzzer` (no ASan/UBSan). Runs much faster, useful for
generating large corpora before switching back to the sanitized build.

---

## The Corpus

A **corpus** is a directory of seed inputs. libFuzzer uses existing inputs as
mutation starting points instead of starting from random bytes. This is
critical: without a good corpus, the fuzzer spends most of its time generating
inputs that fail at the first parsing step and never reach the interesting
logic.

Bitcoin Core maintains a shared corpus in
[`bitcoin-core/qa-assets`](https://github.com/bitcoin-core/qa-assets):

```sh
git clone --depth=1 https://github.com/bitcoin-core/qa-assets
```

The corpora live in `qa-assets/fuzz_corpora/<target_name>/`.

When a new input increases code coverage, libFuzzer saves it to the corpus
directory automatically. This is how the corpus grows over time.

---

## Example: Fuzzing `cluster_linearize`

`src/test/fuzz/cluster_linearize.cpp` registers the following targets (among
others):

| Target | What it tests |
|--------|--------------|
| `clusterlin_depgraph_sim` | DepGraph construction and ancestor/descendant queries |
| `clusterlin_linearize` | The main `Linearize()` function against `SimpleLinearize` |
| `clusterlin_postlinearize` | `PostLinearize` correctness |
| `clusterlin_postlinearize_tree` | `PostLinearize` on tree-shaped graphs |
| `clusterlin_chunking` | `ChunkLinearization` correctness |

The most important target for reviewing `Linearize()` changes is
`clusterlin_linearize`. It generates random dependency graphs, runs both
the production `Linearize()` and a simple reference implementation
(`SimpleLinearize`), and asserts that the results are consistent.

### Run without corpus (quick smoke test)

```sh
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -runs=50000
```

libFuzzer offers two stopping conditions; they can be used individually or
together (the fuzzer stops as soon as either is satisfied):

| Flag | Meaning |
|------|---------|
| `-max_total_time=N` | Stop after **N seconds** |
| `-runs=N` | Stop after **N executions** (`0` = unlimited) |

```sh
# Time-based limit (recommended): run for 5 minutes
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -max_total_time=300 my_corpus/clusterlin_linearize/

# Execution-count limit
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -runs=100000 my_corpus/clusterlin_linearize/

# Both at once (stops at whichever comes first)
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -max_total_time=120 -runs=500000 my_corpus/clusterlin_linearize/
```

Without either flag the fuzzer runs indefinitely until interrupted with Ctrl-C.

libFuzzer prints a line each time it finds a new coverage-increasing input:

```
#2      INITED cov: 203 ft: 214 corp: 1/1b ...
#38     NEW    cov: 204 ft: 216 corp: 2/5b ...
```

- `cov` — number of basic blocks covered
- `corp` — corpus size (files / total bytes)
- `NEW` — a new input was saved to the corpus

### Run with the qa-assets corpus (deeper coverage)

```sh
mkdir -p my_corpus/clusterlin_linearize

# Seed from qa-assets (if available)
cp -r qa-assets/fuzz_corpora/clusterlin_linearize/* \
      my_corpus/clusterlin_linearize/ 2>/dev/null || true

FUZZ=clusterlin_linearize build_fuzz/bin/fuzz \
    my_corpus/clusterlin_linearize/
```

New coverage-increasing inputs are saved to `my_corpus/clusterlin_linearize/`
automatically.

### Run `txgraph` fuzzing

```sh
mkdir -p my_corpus/txgraph
cp -r qa-assets/fuzz_corpora/txgraph/* my_corpus/txgraph/ 2>/dev/null || true

FUZZ=txgraph build_fuzz/bin/fuzz my_corpus/txgraph/
```

---

## Interpreting the Output

```
#1000000  pulse  cov: 512 ft: 1203 corp: 87/14Kb exec/s: 9823 rss: 210Mb
```

| Field | Meaning |
|-------|---------|
| `#1000000` | Number of inputs executed |
| `cov` | Edges/blocks covered |
| `ft` | Feature count (finer-grained coverage) |
| `corp` | Corpus: `<files>/<total bytes>` |
| `exec/s` | Throughput |
| `rss` | Resident memory |

If the fuzzer finds a bug:

```
SUMMARY: AddressSanitizer: heap-buffer-overflow ...
Test unit written to ./crash-<hash>
```

The crashing input is saved to `crash-<hash>`. Reproduce it with:

```sh
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz crash-<hash>
```

---

## Reproducing a CI Crash

If the CI reports a crash from a qa-assets input:

```sh
# Update qa-assets to the exact commit shown in CI
cd qa-assets && git pull

FUZZ=<target> build_fuzz/bin/fuzz \
    qa-assets/fuzz_corpora/<target>/<crash_hash>
```

---

## Contributing New Corpus Inputs

If you find coverage-increasing inputs that survive the sanitized build without
crashing:

1. Copy them into the appropriate `qa-assets/fuzz_corpora/<target>/` directory.
2. Submit a PR to `bitcoin-core/qa-assets`.

This helps CI catch regressions in the new paths you discovered.

---

## Further Reading

- [Fuzz Script Guide](fuzz-script.en.md) — the automation script and config file that wrap all the steps above

- [Bitcoin Core fuzzing documentation](https://github.com/bitcoin/bitcoin/blob/master/doc/fuzzing.md)
- [libFuzzer documentation](https://llvm.org/docs/LibFuzzer.html)
- [OSS-Fuzz Bitcoin Core dashboard](https://oss-fuzz.com/coverage-report/job/libfuzzer_asan_bitcoin-core/latest)
