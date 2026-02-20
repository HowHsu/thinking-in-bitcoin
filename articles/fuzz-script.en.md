# Fuzz Script Guide

[中文版](fuzz-script.zh.md)

This article explains the companion script [`scripts/fuzz.sh`](../scripts/fuzz.sh)
and its configuration file [`scripts/tests.config`](../scripts/tests.config).

The script wraps every step of the [fuzz testing workflow](fuzz-testing.en.md) —
building, seeding the corpus, and running each target — into a single command.

---

## Quickstart

```sh
# Clone Bitcoin Core if you haven't already
git clone https://github.com/bitcoin/bitcoin /path/to/bitcoin

# Run each target for 5 minutes
./scripts/fuzz.sh --bitcoin-dir /path/to/bitcoin --time 300
```

The script performs these steps in order:

1. Build the fuzz binary with `cmake --preset=libfuzzer`
2. Clone or update the `bitcoin-core/qa-assets` corpus
3. Run the fuzzer on each target listed in `tests.config`

---

## Configuration file: `tests.config`

[`scripts/tests.config`](../scripts/tests.config) controls which fuzz targets
are run.

### Format

```
# This is a comment
clusterlin_linearize
clusterlin_postlinearize
txgraph
```

- One target name per line, matching the `name` in `FUZZ_TARGET(name)` inside
  `src/test/fuzz/`
- Lines starting with `#` and blank lines are ignored
- Targets run in the order they appear in the file

### Adding a new target

Append the target name to the file:

```sh
echo "feefrac" >> scripts/tests.config
```

### Using a separate config file

Use `--config` to point at any config file, making it easy to maintain
multiple target lists for different scenarios:

```sh
# Test only txgraph
echo "txgraph" > /tmp/txgraph.config
./scripts/fuzz.sh --bitcoin-dir /path/to/bitcoin \
    --config /tmp/txgraph.config --skip-build --time 120
```

---

## Command-line options

| Option | Default | Description |
|--------|---------|-------------|
| `--bitcoin-dir DIR` | `$BITCOIN_DIR` env var or auto-detected | Bitcoin Core source root |
| `--build-dir DIR` | `<bitcoin-dir>/build_fuzz` | Build output directory |
| `--corpus-dir DIR` | `<bitcoin-dir>/fuzz_corpora` | Local corpus root |
| `--qa-assets DIR` | `<bitcoin-dir>/qa-assets` | qa-assets directory |
| `--config FILE` | `scripts/tests.config` | Fuzz target config file |
| `--nosan` | off | Build without sanitizers (higher throughput) |
| `--skip-build` | off | Skip compilation, use existing build |
| `--skip-qa-assets` | off | Do not clone/update qa-assets |
| `--time N` | 0 (unlimited) | Stop each target after **N seconds** |
| `--runs N` | 0 (unlimited) | Stop each target after **N executions** |
| `--jobs N` | `nproc` | Parallel build jobs |
| `-h, --help` | — | Show help |

`--time` and `--runs` can be combined; the fuzzer stops at whichever limit
is reached first and then moves on to the next target.

---

## Usage examples

### Quick smoke test (skip build)

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --skip-build \
    --time 60
```

### Long-running corpus growth (no sanitizers, maximum throughput)

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --nosan \
    --runs 0    # run indefinitely; Ctrl-C to stop
```

### Bug-finding mode with sanitizers

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --time 3600   # 1 hour per target
```

### Custom corpus directory

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --corpus-dir ~/my_corpora \
    --skip-qa-assets \
    --time 300
```

---

## Reading the output

At the start of each target the script prints:

```
──────────────────────────────────────────
[INFO]  ▶ 目标: clusterlin_linearize
[INFO]    语料库: /path/to/bitcoin/fuzz_corpora/clusterlin_linearize
```

If the fuzzer finds a crash, the script prints a reproduction command:

```
[WARN]  [clusterlin_linearize] fuzzer exited abnormally (possible crash)
[INFO]  Reproduce with: FUZZ=clusterlin_linearize build_fuzz/bin/fuzz crash-<hash>
```

Crash files (`crash-*` or `leak-*`) are written to **the directory from which
the script was invoked**.

---

## Script files

- [`scripts/fuzz.sh`](../scripts/fuzz.sh) — the script itself
- [`scripts/tests.config`](../scripts/tests.config) — default target list
