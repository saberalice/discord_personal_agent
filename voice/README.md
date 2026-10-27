> 接手整合請先讀 [AGENT_HANDOFF.md](AGENT_HANDOFF.md)。這是功能來源快照，不是可直接套用到任意 bot 的 patch。

# Discord ASR bot 0.9.2

Rust Serenity / Songbird → 每人獨立 Silero VAD → 暫存 WAV → Qwen3-ASR 1.7B BF16 GGUF / llama.cpp → 繁體 TXT。

## 模型設定

使用 [Qwen3-ASR 1.7B GGUF](https://huggingface.co/ggml-org/Qwen3-ASR-1.7B-GGUF)，BF16 主模型約 4.07 GB，加上 BF16 mmproj 音訊編碼器約 0.64 GB，均未量化。由 llama.cpp 執行音訊前處理、編碼及文字解碼。

Rust 自動啟動常駐 llama-server，僅綁定 127.0.0.1 的臨時埠，使用每次啟動產生的 API key。透過本機 HTTP 傳送 WAV；不需要 Python、MLX 或外部 ASR 服務。服務在 bot 結束時關閉，Ctrl+C 先完成佇列。

temperature 0、每段最多 1024 output tokens、context 4096、單一 slot，cache_prompt=false。使用模型原始音訊 prompt，預設 ASR_LANGUAGE=Chinese，可用 zh、zh-TW、zh-CN；English 或 auto 也可。OpenCC s2tw 統一臺灣繁體。短於 250 ms 的片段不送模型，達到生成上限視為錯誤，系統診斷不匯入 TXT。

M4 使用 Metal。移到 Linux / Windows 時，可重新建置 CPU、CUDA 或 Vulkan 引擎並保留 GGUF 及 Rust 呼叫方式；目前只在此 Mac 實測，整個 Discord bot 的其他平台建置尚未驗證。台語與遊戲術語準確度需實際錄音測試。

送 ASR 前以 20 ms 框估算 RMS（排除低於 -50 dBFS 的框）：-30 至 -16 dBFS 不調整，其餘朝 -23 dBFS 調整，增減幅度最多 12 dB，調整後峰值限制 -1 dBFS。近乎靜音不放大。整段使用固定增益、不壓縮動態、不加降噪；音訊長度與時間戳不變，只在記憶體重建送往引擎的 WAV。這是初始設定，並非已驗證能提高遊戲語音準確率。

## 啟動

先 Ctrl+C 關閉舊 bot，等待退出，再執行：

```sh
cd discord-asr-integration-0.9.2
./target/release/discord-asr-bot --version
./target/release/discord-asr-bot
```

版本應為 0.9.2。Token 讀取 DISCORD_BOT_TOKEN（本專案根目錄 .env，或由 Python 啟動器傳入）；其他設定：

```dotenv
ASR_MODEL=models/qwen3-asr-gguf/Qwen3-ASR-1.7B-bf16.gguf
ASR_LANGUAGE=Chinese
VAD_MODEL=models/silero_vad.onnx
DATA_DIR=data
```

可用 ASR_ENGINE 指定 llama-server 路徑、ASR_MMPROJ 指定音訊編碼器、ASR_GPU_LAYERS 指定 GPU 層數（預設 99；CPU 設為 0）。Windows 預設查找 vendor/llama.cpp/build/bin/Release/llama-server.exe。

## Discord 操作

用 bot、applications.commands scopes 邀請至伺服器，權限包含 View Channels、Connect、Send Messages、Attach Files。不需要 Message Content 或 Server Members 特權 intent。

1. 先加入一般語音頻道，再輸入 /join。一次只錄一個頻道，請告知參與者。
2. 在同一語音頻道輸入 /leave；保存尾段、等待本場 ASR，再上傳 TXT。
3. Ctrl+C 保存尾段並處理佇列，但不自動上傳附件。

## 建置與下載

首次安裝需要 Rust、CMake、C++ 編譯器、curl 與 Python 3.12+（Python 僅用於建置／下載腳本）。

```sh
python3 scripts/build-engine.py --backend metal
python3 scripts/download-qwen.py
cargo build --release --locked
cargo test --release --locked
```

跨平台建置可改為 --backend cpu、cuda 或 vulkan，GPU 後端需先安裝對應 SDK。執行時保留 llama-server 所在 bin 目錄的動態函式庫。

模型 revision 與檔案校驗值固定於 scripts/qwen-manifest.json。下載腳本校驗大型檔 SHA256、小型 Git 檔 blob SHA1，再將 .part 改為正式檔名。Silero 的來源與 SHA256 也已固定。llama.cpp 原始碼版本與 SHA256 固定於 scripts/build-engine.py。

## 驗證與限制

14 個 Rust 一般測試及 1 個 GGUF 真實引擎測試、release 建置與 clippy 通過。測試覆蓋錄音分流、Silero 切段、停止尾段、繁體與 TXT 過濾。

M4 離線實測 4.744 秒中文合成音訊：第一次辨識 1.41 秒、第二次 0.93 秒（不含啟動載入）。輸出「右邊有一個敵人，我需要補血。等一下，再一起進攻。」這不是台語／多人負載 benchmark。舊 FireRed、MLX 與 8-bit 下載權重已清除，models/ 僅保留兩個 BF16 GGUF 與 Silero。

實際引擎測試（需要已下載的模型）：

```sh
ASR_TEST_WAV=/absolute/path/16k-mono.wav cargo test --release real_engine_reuses_model_and_skips_short_audio -- --ignored --nocapture
```

離線 ASR 測試可直接傳入 16 kHz mono PCM16 WAV（見下方），不連接 Discord。Discord 真實連線與附件需重啟後實測。模型更換不會修復 Songbird Opus InvalidPacket；遺失的聲音仍可能造成漏字。

## 輸出與查詢

- `data/<session_id>/yyyy-mm-dd_hh-mm-ss.txt`：`/leave` 後自動產生的 UTF-8 純文字紀錄，也會上傳到 Discord。每行格式為 `[hh:mm:ss] 伺服器暱稱: ASR result`，時間為該段語音開始時的臺灣時間（UTC+8），依起始時間排序。優先使用伺服器暱稱（沒有時用 Discord 顯示名稱／帳號名稱）；查不到名稱才顯示數字 user ID。
- 沒有可匯出的文字時，Discord 另回覆說明，不把系統提示放進 TXT。
- `/leave` 等待的是本場 pending 片段，其他場紀錄不會混入。若等待超過互動 token 有效期，會改用一般頻道訊息上傳；上傳失敗時檔案仍在本機。
- Ctrl+C 保存錄音並完成 ASR，但不自動回傳 Discord 文字檔；需要附件時請先用 `/leave`。
- `data/log.sqlite3`：sessions、session_clocks、users、utterances。
- `data/<session_id>/<user_id>-<序號>.wav` 僅暫存供 ASR 使用；結果或錯誤寫入 SQLite 後刪除，不作為最終輸出。SQLite 保留文字與時間索引，`wav_path` 為歷史暫存路徑，處理完成後不再指向現存音檔。
- `session_id` 是建立錄音工作階段時的 Unix 毫秒時間；`start_ms/end_ms` 是相對 bot 成功加入語音頻道的時間，從 `00:00:00` 起算，保留開頭沒人說話的時間；不是遊戲內時間。舊版既有 session 的時間不會回溯更改。TXT 以成功加入頻道時保存的 UTC 時間加上 start_ms，換算臺灣時間，顯示整秒；跨午夜回到 00:00:00，仍依實際先後排序。SQLite 保留相對毫秒。舊場次若重新匯出，缺少加入時間時以 session ID 近似換算；現存 TXT 不會自動改寫。
- `user_id` 以文字保存，避免大型 Discord ID 的精度問題。名稱由 Discord 查詢，失敗時保持 NULL，仍保留正確 ID；每場保存最後查得的名稱。
- `status`：pending / done / error。音檔先落地，再排 ASR；下次正常啟動會繼續處理資料庫中的 pending。error 不會自動重試；原音檔會刪除。

```sh
sqlite3 -header -column data/log.sqlite3 '
SELECT u.session_id, u.start_ms, u.end_ms,
       COALESCE(p.display_name,p.username,u.user_id) AS speaker,
       u.text, u.status, u.wav_path
FROM utterances u
LEFT JOIN users p ON p.session_id=u.session_id AND p.user_id=u.user_id
ORDER BY u.session_id, u.start_ms;'
```

## 最小版行為與限制

- 使用 Discord 提供的 SSRC → user ID 對應分流；未知 SSRC 會略過並記錄警告，避免猜錯人。
- 使用 Silero VAD（16 kHz、512 samples / 32 ms、機率門檻 0.5），每位使用者有獨立模型狀態與緩衝。Discord 20 ms 封包連續重組，不逐包補零；停止時只有模型輸入的不足框補零，保存音訊不加長。語音開始保留 200 ms 前置緩衝；最後一個語音框後約 1,200 ms 切段，連續語音約 15 秒強制切段（最多超出一個 32 ms 框）。VAD 仍可能漏掉輕聲或把噪音判成語音，遊戲實況需要調整。
- 片段內無封包的 tick 補零，最後語音框後保留最多 200 ms 已收到的音訊，其餘尾端靜音裁除；多人同時講話分開保存。時間是接收／播放時間的近似值，受 Discord jitter buffer 與網路延遲影響。
- 目前每人最多約 15 秒未切段音訊在記憶體；正常 `/leave` 或 Ctrl+C 保存尾段。強制結束／斷電可能丟失未切段音訊。已完成 WAV 若剛好在寫 DB 前中斷，可能留下沒有資料列的音檔。
- ASR 失敗保存錯誤文字，仍刪除暫存音檔；接收佇列滿或磁碟失敗會報錯停止。不要把測試版視為無遺漏錄音保證。
- 依賴 Songbird 處理一般連線恢復，尚未實測長時間斷線、移動 bot 或多人進出造成的 DAVE 換鑰；第一輪先測兩人交談、重疊發言與進出頻道。

## 不連 Discord 的 ASR 測試

傳入 16 kHz mono PCM16 WAV 路徑，只做一次本機辨識，不讀 bot token：

```sh
ASR_MODEL=models/qwen3-asr-gguf/Qwen3-ASR-1.7B-bf16.gguf \
  ./target/release/discord-asr-bot /absolute/path/audio.wav
```

錄音測試覆蓋多人音軌分離、片段中間補靜音、SSRC 重用、離線時保存、15 秒切段與正常停止尾段。Discord 真實連線需要自己的 token、邀請 bot 到伺服器後才能驗證。
