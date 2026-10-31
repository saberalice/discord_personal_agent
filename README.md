# discord_personal_agent

個人用的 Discord 摘要 Bot，提供三種功能：

- **`/summary`**：私訊 Bot，把指定文字頻道一段時間內的對話整理成可回查原文的 AI 時間線（Groq `openai/gpt-oss-20b`，經 LangChain）。內容超過單次容量時自動分批再合併。
- **`/join`、`/leave`**：在語音頻道錄音，於本機辨識後把逐字稿 TXT 貼回頻道（Qwen3-ASR，經 llama.cpp）。
- **`/voicesummary`**：摘要上述逐字稿 TXT。在頻道下指令時預設摘要最新一份並公開貼出，私訊則需提供訊息連結、結果只給你。

送出前會先套用本機機密規則；Bot Token、Groq Key 只放在本機 `.env`，不進 Git。

## 在新機器上安裝與執行

### 0. 需要先準備

| 項目 | 用途 | 安裝方式（Ubuntu／WSL） |
|---|---|---|
| Git、curl | 下載程式與模型 | `sudo apt install git curl` |
| [Miniconda](https://docs.conda.io/en/latest/miniconda.html) | Python 3.12 與 uv | 依官方說明安裝後重新開啟終端機 |
| 以下只有語音功能需要 | | |
| [Rust（rustup）](https://rustup.rs) | 編譯語音程式 | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| 編譯工具 | llama.cpp 與相依套件 | `sudo apt install build-essential cmake pkg-config libssl-dev` |
| NVIDIA 驅動＋CUDA toolkit | GPU 辨識（有 `nvcc` 即可） | 依 NVIDIA 官方說明；沒有 GPU 可改用 CPU |

rustup 裝完後執行 `source ~/.cargo/env` 或重新開啟終端機。磁碟請預留約 10 GB：目前開發機上模型 4.4 GB、llama.cpp 約 1 GB、Rust 編譯產物 1.5 GB，另加 Conda 環境。

### 1. 下載並執行安裝腳本

```bash
git clone git@github.com:saberalice/discord_personal_agent.git
cd discord_personal_agent
scripts/setup.sh               # 文字摘要＋語音（CUDA）
```

其他選項：`scripts/setup.sh --voice cpu`（無 GPU，辨識較慢）、`--voice vulkan`、`--voice none`（只裝文字摘要，不需 Rust 與編譯工具）。

腳本依序：建立 `discord-personal-agent` Conda 環境（[environment.yml](environment.yml)）→ 依 [uv.lock](uv.lock) 安裝固定版本的 Python 套件並跑測試 → 建立空白 `.env` 與 `config.toml` → 下載並編譯固定版本的 llama.cpp、下載並校驗模型、編譯語音程式。可重複執行：已有的 `.env`、`config.toml`、llama.cpp 原始碼與模型不會重新下載或覆蓋；環境同步、測試與編譯每次都會跑（編譯是增量的）。

模型、llama.cpp 與編譯產物太大，不放在 Git，一律由腳本下載；下載來源都固定版本並校驗雜湊。

### 2. 填入設定

最簡單的做法是把舊機器上的 `.env` 和 `config.toml` 直接複製過來（兩者都不在 Git 裡）：

```bash
scp 舊機器:~/code/discord_personal_agent/{.env,config.toml} .
chmod 600 .env config.toml
```

或手動編輯腳本建立的檔案：

- `.env`：填入 `DISCORD_BOT_TOKEN`（Developer Portal → Bot → Token）與 `SHARED_LLM_API_KEY`（Groq Key）。`BOT_CONFIG_PATH=config.toml` 不用改。
- `config.toml`：把範例 ID 換成允許摘要的伺服器與頻道 ID；逐字稿所在的頻道也要列入。範本的 `[llm] max_input_tokens = 6000` 可讓約 80 行（依每行長度而定）的逐字稿一次送出，減少分批造成的 Groq 速率限制。格式說明見 [本地開發](docs/local-development.md#3-本地設定)，機密規則見 [規則指南](docs/privacy-rules.md)。

不要建立 `voice/.env`；語音程式會讀根目錄的 `.env`。

### 3. 啟動

```bash
conda activate discord-personal-agent
python -m discord_personal_agent --check-config   # 離線檢查設定，不連線
python -m discord_personal_agent                  # 文字摘要＋語音
python -m discord_personal_agent --no-voice       # 只有文字摘要
```

一定要先 `conda activate`；否則會用到 base 環境的 Python，出現 `No module named discord_personal_agent`。

- 停止：按一次 Ctrl+C，等語音程式完成辨識後自動結束。
- 同一個 Token 同時只能在一台機器上執行，換機前先停掉舊機器的 Bot。
- Discord Developer Portal 不需要因換機調整；啟動時會自動同步斜線指令。

### 用 Docker（Windows＋NVIDIA GPU）

不想在主機安裝 Conda、Rust 與 CUDA toolkit 時，可改用 Docker Desktop：放好 `.env` 與 `config.toml` 後執行 `docker compose up -d --build`。準備步驟、停止與資料清理見 [Docker 指南](docs/docker.md)（尚未實際 build 驗證）。

## 使用

在 Discord 私訊 Bot 輸入 `/help` 查看完整說明。語音的錄音、資料保存與清理方式見 [語音逐字稿](docs/voice-transcript.md)。錄音前請先告知參與者。

## 開發

```bash
conda activate discord-personal-agent
python -m pytest -q
ruff check src tests && ruff format --check src tests
cd voice && cargo test --release --locked && cargo clippy --release --locked -- -D warnings
```

| 想知道什麼 | 文件 |
|---|---|
| 產品行為（單一依據） | [功能規格](docs/spec.md) |
| 架構、資料流、設定與錯誤處理 | [技術設計](docs/design.md) |
| 開發順序與驗證狀態 | [實作與驗證計畫](docs/implementation.md)、[工作票](docs/tasks/) |
| LLM 串接選型 | [LLM 串接比較](docs/research/llm-integration.md) |
| Discord／Groq 限制 | [平台限制](docs/research/platform-constraints.md) |
| 機密處理 | [機密研究](docs/research/sensitive-data-handling.md) |

詞彙見 [CONTEXT.md](CONTEXT.md)；工程代理開始工作前讀 [CLAUDE.md](CLAUDE.md)。
