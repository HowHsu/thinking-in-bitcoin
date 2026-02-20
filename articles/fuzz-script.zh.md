# 一键 Fuzz 脚本使用指南

[English](fuzz-script.en.md)

本文介绍配套脚本 [`scripts/fuzz.sh`](../scripts/fuzz.sh) 及配置文件
[`scripts/tests.config`](../scripts/tests.config) 的使用方法。

脚本将 [fuzz 测试流程](fuzz-testing.zh.md)中的编译、语料库准备、逐目标运行等步骤
全部封装为一条命令，无需手动操作。

---

## 快速开始

```sh
# 克隆仓库（如果还没有）
git clone https://github.com/bitcoin/bitcoin /path/to/bitcoin

# 每个目标各跑 5 分钟
./scripts/fuzz.sh --bitcoin-dir /path/to/bitcoin --time 300
```

脚本会依次完成：

1. 用 `cmake --preset=libfuzzer` 编译 fuzz 二进制
2. 克隆/更新 `bitcoin-core/qa-assets` 语料库
3. 按照 `tests.config` 中列出的目标顺序逐一运行 fuzzer

---

## 配置文件：`tests.config`

[`scripts/tests.config`](../scripts/tests.config) 决定运行哪些 fuzz 目标。

### 格式

```
# 这是注释
clusterlin_linearize
clusterlin_postlinearize
txgraph
```

- 每行一个目标名，对应 `src/test/fuzz/` 中 `FUZZ_TARGET(name)` 宏的 `name`
- `#` 开头的行为注释，空行忽略
- 目标按文件中的顺序依次运行

### 添加新目标

在文件中追加目标名即可：

```sh
echo "feefrac" >> scripts/tests.config
```

### 使用独立配置文件

通过 `--config` 指定任意配置文件，方便针对不同场景维护多套目标列表：

```sh
# 只测试 txgraph
echo "txgraph" > /tmp/txgraph.config
./scripts/fuzz.sh --bitcoin-dir /path/to/bitcoin \
    --config /tmp/txgraph.config --skip-build --time 120
```

---

## 命令行选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--bitcoin-dir DIR` | 环境变量 `BITCOIN_DIR` 或自动推断 | Bitcoin Core 源码根目录 |
| `--build-dir DIR` | `<bitcoin-dir>/build_fuzz` | 编译输出目录 |
| `--corpus-dir DIR` | `<bitcoin-dir>/fuzz_corpora` | 本地语料库根目录 |
| `--qa-assets DIR` | `<bitcoin-dir>/qa-assets` | qa-assets 目录 |
| `--config FILE` | `scripts/tests.config` | fuzz 目标配置文件 |
| `--nosan` | 关闭 | 不带 sanitizer 构建（提升吞吐量） |
| `--skip-build` | 关闭 | 跳过编译，直接使用已有构建 |
| `--skip-qa-assets` | 关闭 | 不克隆/更新 qa-assets |
| `--time N` | 0（不限时） | 每个目标最多运行 **N 秒** |
| `--runs N` | 0（无限制） | 每个目标最多执行 **N 次**输入 |
| `--jobs N` | `nproc` | 编译并行度 |
| `-h, --help` | — | 显示帮助 |

`--time` 和 `--runs` 可同时设置，任一条件满足即停止当前目标并进入下一个。

---

## 使用场景示例

### 快速冒烟测试（跳过编译）

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --skip-build \
    --time 60
```

### 长期语料库积累（不带 sanitizer，提高吞吐量）

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --nosan \
    --runs 0    # 无限运行，Ctrl-C 中止
```

### 带 sanitizer 的 bug 发现模式

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --time 3600   # 每个目标跑 1 小时
```

### 使用自定义语料库目录

```sh
./scripts/fuzz.sh \
    --bitcoin-dir /path/to/bitcoin \
    --corpus-dir ~/my_corpora \
    --skip-qa-assets \
    --time 300
```

---

## 输出说明

正常运行时，每个目标开始前会打印：

```
──────────────────────────────────────────
[INFO]  ▶ 目标: clusterlin_linearize
[INFO]    语料库: /path/to/bitcoin/fuzz_corpora/clusterlin_linearize
```

如果 fuzzer 发现崩溃，脚本会打印复现命令：

```
[WARN]  [clusterlin_linearize] fuzzer 异常退出（可能发现了 crash）
[INFO]  复现命令: FUZZ=clusterlin_linearize build_fuzz/bin/fuzz crash-<hash>
```

崩溃文件（`crash-*` 或 `leak-*`）保存在**脚本运行时的当前目录**。

---

## 脚本文件

- [`scripts/fuzz.sh`](../scripts/fuzz.sh) — 主脚本
- [`scripts/tests.config`](../scripts/tests.config) — 默认目标配置
