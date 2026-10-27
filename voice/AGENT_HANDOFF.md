# Discord 語音逐字稿功能：agent 合併指南

你收到的是 discord-asr-bot 0.9.2 的可建置來源快照。任務是將功能合併到使用者指定的另一個 bot。先讀目標 repository 的規則與架構，再實作；本套件不是通用 patch，也尚未在目標 bot 驗證。保持最小實作，不要為擴充性建立額外框架。

## 功能合約

- 指令加入發起者所在的一般語音頻道；停止指令限同一 guild／語音頻道。目前實作全程僅允許一個 active session。
- Discord SSRC → user ID 分流，每人獨立 Silero 狀態；未知 SSRC 略過，不猜測說話者。顯示名稱優先伺服器暱稱，其次帳號顯示名稱／username，最後 user ID。
- Songbird 解碼輸出必須為 16 kHz mono PCM16，每個 tick 約 20 ms。Silero 重組成連續 512 samples，不逐包補零。
- VAD 門檻 0.5，前置 200 ms，停頓 1,200 ms 切段，保留最後語音框後最多 200 ms 已收到的音訊；連續發言約 15 秒強制切段。不新增降噪。
- ASR 前整段固定增益：20 ms 框 RMS 排除低於 -50 dBFS；正常 -30～-16 dBFS 不動，其他朝 -23 dBFS 調整、最多 ±12 dB，受 -1 dBFS 峰值限制。近乎靜音不放大。音訊長度不變。
- Qwen3-ASR 1.7B，BF16 主模型與 BF16 mmproj GGUF。llama.cpp 常駐本機服務；預設中文，temperature 0，不沿用前段 prompt cache。禁止默默換成量化權重或外部 ASR API。
- 每行 TXT：`[21:35:08] 伺服器暱稱: 繁體逐字稿`。時間是臺灣 UTC+8 的語音開始時刻，跨午夜仍依相對時間排序。
- TXT 檔名：`yyyy-mm-dd_hh-mm-ss.txt`（臺灣時間）。只有辨識文字，無錯誤／短音訊等系統 log。
- 停止錄音先保存尾段，等待本場 pending ASR，再回傳 TXT 附件。暫存 WAV 在 DB 寫入結果或錯誤後刪除。保留 SQLite 文字索引，無最終音檔。

## 檔案與合併位置

| 來源 | 職責 | 合併方式 |
|---|---|---|
| src/main.rs / Receiver | Songbird 事件、SSRC 對應、Discord 名稱查詢、tick 時間 | 接到目標 bot 既有 voice lifecycle |
| src/main.rs / Handler、Active | 加入／停止、權限、TXT 附件、互動 defer 與錯誤處理 | 接到現有指令 router；不要整份覆蓋 main |
| src/main.rs / main | 建立 ASR、DB、背景 worker、Ctrl+C drain | 接到目標啟動與 shutdown hook，不建立第二個 Discord client |
| src/recording.rs | Input、Store、SQLite queue、切段與暫存檔 | 可先作為目標 crate 內部模組 |
| src/vad.rs | 每人 Silero 狀態與 20→32 ms 重組 | 與 recording 一起搬移 |
| src/asr.rs | Asr::load／transcribe、音量標準化、llama-server 子程序 | 共用一個常駐 instance，放在 blocking worker |
| src/transcript.rs | 臺灣時間、繁體轉換、export | 與 DB schema 一起搬移 |
| scripts、Cargo.lock | 固定版本引擎建置、模型下載校驗、依賴 | 合併相容依賴，不覆蓋目標 lockfile |

這是 binary crate，不是已抽出的 library。模組存在 crate::recording、crate::transcript、crate::vad 路徑耦合；若放進 asr_feature 子模組，必須調整這些路徑。非 Rust bot 可保留相同 llama-server HTTP 協定，但 Discord、VAD 與儲存整合需要移植；不能直接 import 此 Rust binary。

## 重要生命週期

1. 初始化資料庫，載入 Asr；註冊既有 Songbird manager 與需要的事件。
2. 建立 Store、bounded channel 及接收器。成功加入後，先送 Input::Clock(UTC Unix ms)，再啟用對應 Instant 起點；避免連線等待時間被算進逐字稿。
3. SpeakingStateUpdate → Map，非同步查名 → Identity；VoiceTick → Tick(relative ms, per-SSRC PCM)；ClientDisconnect → Disconnect。
4. 停止時封鎖新的音訊輸入，離開 voice，送 Stop，await recorder 保存尾段。
5. 背景 ASR 依序處理 pending，先 commit done/error，再 remove_audio。export 等本場 pending 清空。
6. 互動先 defer；上傳使用既有附件邏輯，超過互動 token 時效可用一般頻道訊息。空逐字稿只回狀態說明。
7. shutdown 先完成錄音與 ASR queue，最後 drop Asr 關閉其子程序。不要先殺引擎。Ctrl+C 本身不會上傳 TXT。

## 目標 bot 必須檢查的衝突

- `/join`、`/leave` 可能已有用途。沿用既有 router，可改為 `/record start`、`/record stop`；不要覆蓋原指令。
- 共用既有 Songbird manager。目前 Active::stop 會 remove 整個 guild voice connection；目標 bot 若同時播放音樂，需調整停止策略與事件註冊／移除，避免中斷既有功能或重複接收。
- 不新增第二個 Ctrl+C handler、Discord client 或重複 command registration。
- 使用 crate 版本及 receive／DAVE 能力做相容性檢查；不要盲目替換目標 Cargo.toml／Cargo.lock。
- SQLite 表名 sessions/users/utterances/session_clocks 可能衝突；優先使用獨立 DATA_DIR 與 log.sqlite3，否則寫適當 migration。
- 預設路徑依工作目錄解析。目標若從其他 cwd／service 啟動，改成明確的設定路徑，包含 ASR_ENGINE 與 models。
- 仍維持單一 active recording，除非使用者另行要求多 guild。不要移除容量限制與錯誤回報。

## 設定、重建與驗證

複製 .env.example 為 .env，自行填入目標 bot token；套件沒有 token。

```sh
python3.12 scripts/build-engine.py --backend metal
python3 scripts/download-qwen.py
cargo build --release --locked
cargo test --release --locked
cargo clippy --release --locked -- -D warnings
```

`--backend` 可選 metal/cpu/cuda/vulkan，需對應 SDK；Windows 建置可能需要指定 ASR_ENGINE 的實際 executable 路徑。CPU 時 ASR_GPU_LAYERS=0，同時停用 mmproj offload。Mac M4 已實測，其他平台與目標 bot 尚未驗證。

```sh
ASR_TEST_WAV=/absolute/path/16k-mono.wav cargo test --release real_engine_reuses_model_and_skips_short_audio -- --ignored --nocapture
./target/release/discord-asr-bot /absolute/path/16k-mono.wav
```

一般測試包含真正 Silero 推論，因此要先下載 Silero 權重。無權重可先跑 `cargo test --release transcript::tests`。fixtures/speech.wav 是公開測試素材，來源見 tests/fixtures/README.md，不是使用者 Discord 錄音。

合併後驗收：目標原功能不受影響、兩人同時說話仍分流、短停頓合併、字尾保留、停止尾段、臺灣時間跨午夜、只輸出繁體文字、附件可下載、暫存 WAV 清除、shutdown 無殘留引擎。實際 Discord 測試使用目標使用者授權的測試頻道，不使用來源 bot 身分。

## 套件範圍與限制

不包含 .env、data、使用者錄音／逐字稿、模型權重、vendor、target、.venv、個人絕對路徑或原始 Git 歷史。模型及引擎透過固定 revision／checksum 重建。

來源程式未附專案授權條款；不要自行替使用者加上 MIT 等授權。對外發布時另由擁有者決定授權，第三方模型與依賴依其各自授權。

目前語音封包遺失不能靠 ASR 補回；台語／多人負載精度仍需實錄驗證。此快照的自動測試不等於已完成與目標 bot 的合併。
