#!/usr/bin/env bash
# One-shot setup on a fresh machine. Safe to re-run: every step skips work already done.
#   scripts/setup.sh                 Python bot + voice program with CUDA
#   scripts/setup.sh --voice cpu     voice program on CPU (also: vulkan)
#   scripts/setup.sh --voice none    text summary only, no Rust toolchain needed
set -euo pipefail

VOICE=cuda
while [[ $# -gt 0 ]]; do
    case "$1" in
        --voice) VOICE="${2:?--voice needs cuda, cpu, vulkan or none}"; shift 2 ;;
        *) echo "未知參數：$1" >&2; exit 2 ;;
    esac
done
case "$VOICE" in cuda|cpu|vulkan|none) ;; *) echo "--voice 只接受 cuda、cpu、vulkan、none" >&2; exit 2 ;; esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME=discord-personal-agent
cd "$ROOT"

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1：$2" >&2; exit 1; }
}

echo "== 1/4 Conda 環境"
need conda "請先安裝 Miniconda，並重新開啟終端機"
# Conda's activation scripts are not written for `set -u`.
set +u
eval "$(conda shell.bash hook)"
# grep without -q reads all input, so pipefail cannot trip on awk's SIGPIPE.
if conda env list | awk '{print $1}' | grep -x "$ENV_NAME" >/dev/null; then
    conda env update --name "$ENV_NAME" --file environment.yml
else
    conda env create --file environment.yml
fi
conda activate "$ENV_NAME"
set -u

echo "== 2/4 Python 套件（依 uv.lock）"
# Installs into the Conda env itself, not a separate .venv, so `conda activate` is all a user needs.
UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX" uv sync --locked --inexact
python -m pytest -q

echo "== 3/4 設定檔"
if [[ ! -f .env ]]; then
    # Written as KEY= lines rather than copied: .env.example lists bare names, which the
    # voice program's dotenvy cannot parse.
    (umask 077; printf 'DISCORD_BOT_TOKEN=\nBOT_CONFIG_PATH=config.toml\nSHARED_LLM_API_KEY=\n' > .env)
    echo "已建立 .env，請填入 DISCORD_BOT_TOKEN 與 SHARED_LLM_API_KEY"
fi
if [[ ! -f config.toml ]]; then
    cp config.example.toml config.toml
    chmod 600 config.toml
    echo "已建立 config.toml，請換成你的伺服器與頻道 ID"
fi

echo "== 4/4 語音程式（$VOICE）"
if [[ "$VOICE" == none ]]; then
    echo "略過；啟動時請加 --no-voice"
    exit 0
fi
need cargo "請用 rustup 安裝 Rust：https://rustup.rs"
need cmake "sudo apt install cmake"
need pkg-config "sudo apt install pkg-config libssl-dev"
need g++ "sudo apt install build-essential"
need curl "sudo apt install curl"
if [[ "$VOICE" == cuda ]]; then
    need nvcc "請安裝 CUDA toolkit，或改用 --voice cpu"
fi
cd voice
python scripts/build-engine.py --backend "$VOICE"
python scripts/download-qwen.py
cargo build --release --locked
echo "完成。填好 .env 與 config.toml 後執行：conda activate $ENV_NAME && python -m discord_personal_agent"
