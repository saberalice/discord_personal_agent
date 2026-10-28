# 語音逐字稿：建置、啟動與清理

規格見 [spec 第 9 節](spec.md#9-第二階段語音逐字稿)，設計見 [design 第 11 節](design.md#11-語音整合雙程式)。語音程式是 `voice/` 的 Rust 程式，與 Python bot 共用同一個 Token；錄音與辨識流程沿用來源 0.9.2，原說明見 [voice/README.md](../voice/README.md)。

## 1. 一次性建置（本機 WSL＋CUDA）

需要：rustup 安裝的 Rust、`cmake`、`pkg-config`、`libssl-dev`、gcc／g++、CUDA toolkit（`nvcc`）、python3.12、curl。OpenSSL 只在建置時供 onnxruntime 下載使用，最終執行檔不依賴它。

```bash
cd voice
python3 scripts/build-engine.py --backend cuda   # 下載固定版本 llama.cpp 並編譯 llama-server
python3 scripts/download-qwen.py                 # Qwen3-ASR BF16（約 4.7 GB）與 Silero，逐檔校驗
cargo build --release --locked
cargo test --release --locked
```

`vendor/`、`models/`、`target/`、`data/` 都不進 Git。不要建立 `voice/.env`：dotenvy 只載入找到的第一份 `.env`，會讓語音程式讀不到根目錄的 `DISCORD_BOT_TOKEN`。

## 2. 啟動與停止

在專案根目錄啟用 Conda 環境後：

```bash
python -m discord_personal_agent              # 文字摘要＋語音程式
python -m discord_personal_agent --no-voice   # 只有文字摘要
```

- 語音程式以子程序在 `voice/` 啟動，兩者日誌輸出在同一個終端機；Rust 的日誌以 `discord_asr_bot` 標示。
- 找不到 `voice/target/release/discord-asr-bot` 或無法執行時，記錄 `voice_disabled` 並只跑文字功能。
- 啟動器把 `SHARED_LLM_API_KEY` 以空值傳給語音程式，Groq 金鑰不會進入它與 llama-server 的環境。單獨執行 Rust 時沒有這層保護。
- Ctrl+C：語音程式保存錄音尾段、完成辨識佇列後才結束，Python 會等它。期間再按 Ctrl+C 只會提示稍候。Ctrl+C 不會上傳 TXT，需要附件請先 `/leave`。
- 只有 Ctrl+C（SIGINT）會走上述收尾；以 SIGTERM／`kill` 結束 Python 時不會通知語音程式。日後用 systemd 等工具管理時須另外處理。
- 語音程式意外結束時日誌出現 `voice_exited_unexpectedly`，文字功能照常，重啟後才恢復錄音。

Discord Developer Portal 不需要新增 intent；語音程式自行使用 `GUILDS`、`GUILD_VOICE_STATES`。Bot 在錄音伺服器需要 View Channels、Connect、Send Messages、Attach Files 權限。

## 3. 使用

1. 在伺服器先加入一般語音頻道，下 `/join`，bot 在頻道發出開始錄音告知。錄音前請先告知參與者。
2. 在同一語音頻道下 `/leave`；等待辨識完成後，TXT 公開貼在下指令的文字頻道。
3. 只開 Python（`--no-voice` 或語音程式未啟動）時，`/join`、`/leave` 會顯示「應用程式未回應」。
4. 摘要逐字稿：在 Discord 對 TXT 那則訊息按右鍵「複製訊息連結」，私訊 bot `/voicesummary message:<連結>`，`topic` 選填。該頻道（一般文字頻道或語音頻道的文字聊天）須加入 `config.toml` 允許清單；只開 Python 時也能使用。

## 4. 本機資料與清理

語音程式不自動清理，資料在 `voice/data/`：

| 檔案 | 內容 |
|---|---|
| `log.sqlite3`、`-wal`、`-shm` | 場次、發言者 ID 與暱稱、每段逐字稿文字、時間、狀態 |
| `<session_id>/yyyy-mm-dd_hh-mm-ss.txt` | 該場 TXT，與上傳內容相同 |
| `<session_id>/<user_id>-<序號>.wav` | 暫存音檔，辨識完成或失敗後自動刪除 |

清理前先停止 bot（Ctrl+C 並等待結束）。

全部清除（下次啟動會重建空資料庫）：

```bash
rm -rf voice/data
```

刪除單一場次（`<session_id>` 為 `voice/data/` 下的目錄名稱）：

```bash
S=<session_id>
sqlite3 voice/data/log.sqlite3 "
DELETE FROM utterances     WHERE session_id='$S';
DELETE FROM users          WHERE session_id='$S';
DELETE FROM session_clocks WHERE session_id='$S';
DELETE FROM sessions       WHERE id='$S';
VACUUM;"
rm -rf "voice/data/$S"
```

`VACUUM` 移除資料庫內已刪除內容殘留的頁面；`rm` 不保證儲存媒體層級抹除。Discord 頻道裡的 TXT 需在 Discord 自行刪除。
