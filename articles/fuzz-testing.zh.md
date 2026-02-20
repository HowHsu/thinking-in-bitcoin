# Bitcoin Core Fuzz 测试实践指南

[English](fuzz-testing.en.md)

---

## 什么是 Fuzz 测试？

Fuzz 测试（模糊测试）是一种自动化测试技术：向程序持续输入大量随机变异的数据，
并监控程序是否发生崩溃、断言失败、内存错误或未定义行为。
与只覆盖作者预想路径的手写单元测试不同，fuzzer 持续探索输入空间，
能触达人工测试难以覆盖的边界情况。

---

## 为什么要对 Bitcoin Core 进行 Fuzz 测试？

Bitcoin Core 是共识关键软件：一个内存安全漏洞可能被利用来崩溃节点，
甚至（极端情况下）破坏 UTXO 集。因此，项目将 fuzz 测试作为一等测试工具：

- 每个 PR 都会自动对 [`bitcoin-core/qa-assets`](https://github.com/bitcoin-core/qa-assets) 中的种子语料库进行测试。
- Bitcoin Core 参与 Google 的 [OSS-Fuzz](https://github.com/google/oss-fuzz) 持续 fuzz 基础设施。
- 目前有 200+ 个 fuzzing 测试覆盖网络、共识、密码学、mempool 等各个模块。

---

## 前置条件

| 需求 | 说明 |
|------|------|
| `clang` / `clang++` | `libfuzzer` preset 依赖 clang |
| `cmake` ≥ 3.22 | Bitcoin Core 使用 CMake 构建 |
| Linux（推荐） | 项目官方不支持在 macOS 上进行 fuzzing |

Ubuntu/Debian 安装：

```sh
sudo apt install clang cmake ninja-build
```

---

## Fuzz 二进制的工作方式

所有 fuzzing 测试目标被编译进**同一个二进制**（`build_fuzz/bin/fuzz`）。
运行时通过 `FUZZ` 环境变量选择目标：

```sh
FUZZ=<目标名称> build_fuzz/bin/fuzz [语料库目录] [libFuzzer 参数]
```

每个测试文件（`src/test/fuzz/*.cpp`）通过 `FUZZ_TARGET(name)` 宏注册一个或多个目标。

---

## 编译

Bitcoin Core 提供了两个用于 fuzzing 的 CMake preset：

### 带 sanitizer（推荐，用于发现 bug）

```sh
cmake --preset=libfuzzer          # 输出目录：build_fuzz/
cmake --build build_fuzz -j$(nproc)
```

该 preset 的配置：
- `BUILD_FOR_FUZZING=ON` — 确定性模式，只构建 fuzz 二进制
- `SANITIZERS=undefined,address,fuzzer` — UBSan + ASan + libFuzzer

Sanitizer 会使执行速度降低约 5–10 倍，但能发现更多 bug。

### 不带 sanitizer（用于提高吞吐量 / 扩充语料库）

```sh
cmake --preset=libfuzzer-nosan    # 输出目录：build_fuzz_nosan/
cmake --build build_fuzz_nosan -j$(nproc)
```

仅使用 `-fsanitize=fuzzer`（无 ASan/UBSan），运行更快，适合大规模生成语料库，
再切换回 sanitized 构建验证。

---

## 语料库

**语料库**（corpus）是一个包含种子输入的目录。
libFuzzer 以已有输入作为变异起点，而不是从随机字节开始。
这非常关键：没有好的语料库，fuzzer 会把大量时间花在生成在解析阶段就被拒绝的输入上，
永远无法到达深层逻辑。

Bitcoin Core 在 [`bitcoin-core/qa-assets`](https://github.com/bitcoin-core/qa-assets) 维护共享语料库：

```sh
git clone --depth=1 https://github.com/bitcoin-core/qa-assets
```

各目标的语料库位于 `qa-assets/fuzz_corpora/<目标名称>/`。

当某个输入触发新的代码覆盖时，libFuzzer 会自动将其保存到语料库目录，
语料库就这样随时间不断增长。

---

## 示例：对 `cluster_linearize` 进行 Fuzz 测试

`src/test/fuzz/cluster_linearize.cpp` 注册了以下目标（部分）：

| 目标名称 | 测试内容 |
|---------|---------|
| `clusterlin_depgraph_sim` | DepGraph 构造及祖先/后代查询 |
| `clusterlin_linearize` | 主函数 `Linearize()` 与 `SimpleLinearize` 对比 |
| `clusterlin_postlinearize` | `PostLinearize` 正确性 |
| `clusterlin_postlinearize_tree` | 树形图上的 `PostLinearize` |
| `clusterlin_chunking` | `ChunkLinearization` 正确性 |

验证 `Linearize()` 修改最重要的目标是 `clusterlin_linearize`：
它生成随机依赖图，同时运行生产代码 `Linearize()` 和简单参考实现 `SimpleLinearize`，
并断言结果一致。

### 不带语料库运行（快速冒烟测试）

```sh
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -runs=50000
```

libFuzzer 提供两种停止条件，可单独使用也可同时设置（任一满足即停止）：

| 参数 | 含义 |
|------|------|
| `-max_total_time=N` | 运行 **N 秒**后停止 |
| `-runs=N` | 执行 **N 次输入**后停止（`0` = 无限制） |

```sh
# 按时间限制（推荐）：跑 5 分钟
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -max_total_time=300 my_corpus/clusterlin_linearize/

# 按次数限制
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -runs=100000 my_corpus/clusterlin_linearize/

# 同时限制（先到先停）
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz -max_total_time=120 -runs=500000 my_corpus/clusterlin_linearize/
```

不指定任何限制时，fuzzer 持续运行直到手动 Ctrl-C 中止。

每次发现新的覆盖率提升输入时，libFuzzer 打印一行：

```
#2      INITED cov: 203 ft: 214 corp: 1/1b ...
#38     NEW    cov: 204 ft: 216 corp: 2/5b ...
```

- `cov` — 已覆盖的基本块数
- `corp` — 语料库大小（文件数/总字节数）
- `NEW` — 新输入已保存到语料库

### 带 qa-assets 语料库运行（更深的覆盖）

```sh
mkdir -p my_corpus/clusterlin_linearize

# 从 qa-assets 中复制种子（如果已克隆）
cp -r qa-assets/fuzz_corpora/clusterlin_linearize/* \
      my_corpus/clusterlin_linearize/ 2>/dev/null || true

FUZZ=clusterlin_linearize build_fuzz/bin/fuzz \
    my_corpus/clusterlin_linearize/
```

新的覆盖率提升输入会自动保存到 `my_corpus/clusterlin_linearize/`。

### 对 `txgraph` 进行 Fuzz 测试

```sh
mkdir -p my_corpus/txgraph
cp -r qa-assets/fuzz_corpora/txgraph/* my_corpus/txgraph/ 2>/dev/null || true

FUZZ=txgraph build_fuzz/bin/fuzz my_corpus/txgraph/
```

---

## 解读输出

```
#1000000  pulse  cov: 512 ft: 1203 corp: 87/14Kb exec/s: 9823 rss: 210Mb
```

| 字段 | 含义 |
|------|------|
| `#1000000` | 已执行的输入数量 |
| `cov` | 已覆盖的边/基本块数 |
| `ft` | feature 数量（比 cov 更细粒度的覆盖指标） |
| `corp` | 语料库：`<文件数>/<总字节数>` |
| `exec/s` | 执行吞吐量 |
| `rss` | 内存占用 |

如果 fuzzer 发现了 bug：

```
SUMMARY: AddressSanitizer: heap-buffer-overflow ...
Test unit written to ./crash-<hash>
```

崩溃输入保存到 `crash-<hash>`，可用以下命令复现：

```sh
FUZZ=clusterlin_linearize build_fuzz/bin/fuzz crash-<hash>
```

---

## 复现 CI 中的崩溃

如果 CI 报告了来自 qa-assets 输入的崩溃：

```sh
# 更新 qa-assets 到 CI 中显示的确切 commit
cd qa-assets && git pull

FUZZ=<目标名称> build_fuzz/bin/fuzz \
    qa-assets/fuzz_corpora/<目标名称>/<崩溃_hash>
```

---

## 贡献新语料库输入

如果你发现了触发新覆盖路径且在 sanitized 构建下不崩溃的输入：

1. 将其复制到对应的 `qa-assets/fuzz_corpora/<目标名称>/` 目录。
2. 向 `bitcoin-core/qa-assets` 提交 PR。

这有助于 CI 在你发现的新路径上发现未来的回归。

---

## 延伸阅读

- [一键 fuzz 脚本使用指南](fuzz-script.zh.md)——封装本文所有步骤的自动化脚本及配置文件说明

- [Bitcoin Core fuzzing 文档](https://github.com/bitcoin/bitcoin/blob/master/doc/fuzzing.md)
- [libFuzzer 文档](https://llvm.org/docs/LibFuzzer.html)
- [OSS-Fuzz Bitcoin Core 面板](https://oss-fuzz.com/coverage-report/job/libfuzzer_asan_bitcoin-core/latest)
