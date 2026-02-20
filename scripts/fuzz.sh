#!/usr/bin/env bash
# fuzz.sh — 通用 Bitcoin Core fuzz 测试脚本
#
# 用法:
#   ./scripts/fuzz.sh [选项]
#
# 选项:
#   --bitcoin-dir DIR   Bitcoin Core 源码根目录（默认: 环境变量 BITCOIN_DIR，
#                       或脚本所在目录的上两级目录下的 bitcoin/bitcoin/）
#   --build-dir DIR     构建输出目录（默认: <bitcoin-dir>/build_fuzz）
#   --corpus-dir DIR    语料库根目录（默认: <bitcoin-dir>/fuzz_corpora）
#   --qa-assets DIR     qa-assets 目录（默认: <bitcoin-dir>/qa-assets）
#                       若目录不存在且未设置 --skip-qa-assets 则自动克隆
#   --config FILE       指定目标配置文件（默认: 脚本同目录下的 tests.config）
#   --nosan             不带 sanitizer 构建（更快，适合语料库生成）
#   --skip-build        跳过编译步骤（直接使用已有构建）
#   --skip-qa-assets    不克隆/更新 qa-assets 语料库
#   --time N            每个目标最多运行 N 秒（默认: 不限时）
#   --runs N            每个目标最多执行 N 次输入（默认: 0 = 无限制）
#                       --time 和 --runs 可同时设置，任一条件满足即停止
#   --jobs N            编译并行度（默认: nproc）
#   -h, --help          显示此帮助
#
# 配置文件格式 (tests.config):
#   每行一个 fuzz 目标名称；# 开头的行为注释；空行忽略。

set -euo pipefail

# ──────────────────────────────────────────────
# 默认值
# ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BITCOIN_DIR="${BITCOIN_DIR:-}"
BUILD_DIR=""
CORPUS_DIR=""
QA_ASSETS_DIR=""
CONFIG_FILE="$SCRIPT_DIR/tests.config"
USE_NOSAN=false
SKIP_BUILD=false
SKIP_QA_ASSETS=false
TIME=0      # 0 = 不限时
RUNS=0      # 0 = 无限制
JOBS=$(nproc 2>/dev/null || echo 4)

# ──────────────────────────────────────────────
# 解析参数
# ──────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bitcoin-dir)    BITCOIN_DIR="$2"; shift 2 ;;
        --build-dir)      BUILD_DIR="$2"; shift 2 ;;
        --corpus-dir)     CORPUS_DIR="$2"; shift 2 ;;
        --qa-assets)      QA_ASSETS_DIR="$2"; shift 2 ;;
        --config)         CONFIG_FILE="$2"; shift 2 ;;
        --nosan)          USE_NOSAN=true; shift ;;
        --skip-build)     SKIP_BUILD=true; shift ;;
        --skip-qa-assets) SKIP_QA_ASSETS=true; shift ;;
        --time)           TIME="$2"; shift 2 ;;
        --runs)           RUNS="$2"; shift 2 ;;
        --jobs)           JOBS="$2"; shift 2 ;;
        -h|--help)
            sed -n '3,28p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "未知参数: $1" >&2; exit 1 ;;
    esac
done

# ──────────────────────────────────────────────
# 推断 BITCOIN_DIR
# ──────────────────────────────────────────────
if [[ -z "$BITCOIN_DIR" ]]; then
    # 尝试脚本目录上两级的 bitcoin/bitcoin/
    CANDIDATE="$(cd "$SCRIPT_DIR/../.." && pwd)/bitcoin/bitcoin"
    if [[ -f "$CANDIDATE/CMakePresets.json" ]]; then
        BITCOIN_DIR="$CANDIDATE"
    else
        echo "[ERROR] 无法自动推断 Bitcoin Core 路径，请通过 --bitcoin-dir 或环境变量 BITCOIN_DIR 指定" >&2
        exit 1
    fi
fi

# 根据是否带 sanitizer 决定构建目录
if [[ -z "$BUILD_DIR" ]]; then
    $USE_NOSAN && BUILD_DIR="$BITCOIN_DIR/build_fuzz_nosan" \
               || BUILD_DIR="$BITCOIN_DIR/build_fuzz"
fi

FUZZ_BIN="$BUILD_DIR/bin/fuzz"

[[ -z "$CORPUS_DIR" ]]    && CORPUS_DIR="$BITCOIN_DIR/fuzz_corpora"
[[ -z "$QA_ASSETS_DIR" ]] && QA_ASSETS_DIR="$BITCOIN_DIR/qa-assets"

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }
hr()    { echo "──────────────────────────────────────────"; }

# ──────────────────────────────────────────────
# 1. 读取目标配置文件
# ──────────────────────────────────────────────
[[ -f "$CONFIG_FILE" ]] || error "找不到配置文件: $CONFIG_FILE"

mapfile -t TARGETS < <(
    grep -v '^\s*#' "$CONFIG_FILE" | grep -v '^\s*$'
)

[[ ${#TARGETS[@]} -gt 0 ]] || error "配置文件中没有有效的 fuzz 目标: $CONFIG_FILE"

# ──────────────────────────────────────────────
# 2. 检查前置条件
# ──────────────────────────────────────────────
hr
info "Bitcoin Core 源码目录: $BITCOIN_DIR"
info "配置文件: $CONFIG_FILE"
info "目标列表: ${TARGETS[*]}"

[[ -f "$BITCOIN_DIR/CMakePresets.json" ]] \
    || error "找不到 CMakePresets.json，请确认 --bitcoin-dir 路径正确"

if ! $SKIP_BUILD; then
    command -v clang   >/dev/null 2>&1 || error "未找到 clang，请安装后再运行"
    command -v clang++ >/dev/null 2>&1 || error "未找到 clang++，请安装后再运行"
    command -v cmake   >/dev/null 2>&1 || error "未找到 cmake，请安装后再运行"
fi

# ──────────────────────────────────────────────
# 3. 编译
# ──────────────────────────────────────────────
if $SKIP_BUILD; then
    info "跳过编译（--skip-build）"
    [[ -x "$FUZZ_BIN" ]] || error "fuzz 二进制不存在: $FUZZ_BIN"
else
    PRESET="libfuzzer"
    $USE_NOSAN && PRESET="libfuzzer-nosan"

    hr
    info "使用 preset: $PRESET"
    info "构建目录: $BUILD_DIR"

    (
        cd "$BITCOIN_DIR"
        cmake --preset="$PRESET" -B "$BUILD_DIR"
        cmake --build "$BUILD_DIR" -j"$JOBS" --target fuzz
    )

    [[ -x "$FUZZ_BIN" ]] || error "编译完成但未找到 fuzz 二进制: $FUZZ_BIN"
    info "编译成功: $FUZZ_BIN"
fi

# ──────────────────────────────────────────────
# 4. 准备语料库（可选 qa-assets）
# ──────────────────────────────────────────────
hr
info "语料库根目录: $CORPUS_DIR"

if ! $SKIP_QA_ASSETS; then
    if [[ -d "$QA_ASSETS_DIR/.git" ]]; then
        info "更新 qa-assets..."
        git -C "$QA_ASSETS_DIR" pull --ff-only || warn "qa-assets 更新失败，继续使用本地版本"
    else
        info "克隆 qa-assets 到 $QA_ASSETS_DIR ..."
        git clone --depth=1 https://github.com/bitcoin-core/qa-assets "$QA_ASSETS_DIR" \
            || warn "克隆 qa-assets 失败，将以空语料库运行"
    fi
fi

for target in "${TARGETS[@]}"; do
    target_corpus="$CORPUS_DIR/$target"
    mkdir -p "$target_corpus"

    qa_corpus="$QA_ASSETS_DIR/fuzz_corpora/$target"
    if [[ -d "$qa_corpus" ]]; then
        seed_count=$(find "$qa_corpus" -maxdepth 1 -type f | wc -l)
        if [[ $seed_count -gt 0 ]]; then
            info "[$target] 复制 $seed_count 个种子输入..."
            cp -rn "$qa_corpus/." "$target_corpus/" 2>/dev/null || true
        fi
    else
        info "[$target] qa-assets 中无预置语料库，从空语料库开始"
    fi
done

# ──────────────────────────────────────────────
# 5. 运行 Fuzz 测试
# ──────────────────────────────────────────────
hr
info "开始 fuzz 测试"
[[ "$TIME" -gt 0 ]] && info "每目标时间限制: ${TIME} 秒"
[[ "$RUNS" -gt 0 ]] && info "每目标执行次数上限: ${RUNS} 次"
[[ "$TIME" -eq 0 && "$RUNS" -eq 0 ]] && info "运行时间/次数: 无限制（Ctrl-C 中止）"
hr

LIMIT_FLAGS=()
[[ "$TIME" -gt 0 ]] && LIMIT_FLAGS+=("-max_total_time=$TIME")
[[ "$RUNS" -gt 0 ]] && LIMIT_FLAGS+=("-runs=$RUNS")

for target in "${TARGETS[@]}"; do
    target_corpus="$CORPUS_DIR/$target"
    hr
    info "▶ 目标: $target"
    info "  语料库: $target_corpus"

    FUZZ="$target" "$FUZZ_BIN" \
        "${LIMIT_FLAGS[@]}" \
        "$target_corpus" \
        || {
            warn "[$target] fuzzer 异常退出（可能发现了 crash）"
            info "崩溃文件保存在当前目录（crash-* 或 leak-*）"
            info "复现命令: FUZZ=$target $FUZZ_BIN crash-<hash>"
        }
done

hr
info "所有目标已完成"
info "新输入已保存到: $CORPUS_DIR"
