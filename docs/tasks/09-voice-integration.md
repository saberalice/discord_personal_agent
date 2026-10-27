# 09：語音逐字稿整合（雙程式）

Status: ready-for-agent
Blocked by: None（Owen 需先 `sudo apt install cmake`；rustup 由代理經同意後安裝）

依規格第 9 節與設計第 11 節，把 Rust 語音逐字稿程式整合到本 bot 身分，不改錄音與辨識流程。

- [ ] Rust 程式原樣放入 `voice/`；`.gitignore` 排除 `voice/models/`、`voice/vendor/`、`voice/target/`、`voice/data/`。原 `AGENT_HANDOFF.md`、`README.md` 保留於 `voice/`。
- [ ] Rust 只改兩處：`Transcribed` 日誌只記 id 與字數；Token 改讀 `DISCORD_BOT_TOKEN`。`cargo test --release --locked` 與 `cargo clippy --release --locked -- -D warnings` 通過。
- [ ] Python CommandTree 宣告 `/join`、`/leave`（guild-only，描述與 Rust 一致，不回應）；測試確認名稱與 context 和 Rust 一致，且 Python 不回應這兩個指令。
- [ ] `__main__` 啟動 Rust 子程序（工作目錄 `voice/`），Ctrl+C 時等待其結束；執行檔缺失或啟動失敗只跑文字功能並警告；`--no-voice` 不啟動。以假子程序測試啟動、缺檔與等待結束。
- [ ] `/help` 加入語音功能說明。
- [ ] 操作文件：建置步驟（CUDA）、啟動方式、本機資料位置與清理方式（設計 11.4）。
- [ ] 實機驗收（Owen）：兩人分流、短停頓合併、`/leave` 上傳 TXT、繁體與 UTC+8 時間、暫存 WAV 清除、Ctrl+C 後無殘留 llama-server；`/summary`、`/help` 不受影響；只開 Python 時 `/join` 顯示未回應。

範圍外：語音資料自動清理、錄音白名單、時區設定、多場錄音、語音摘要。

執行前另行確認：llama.cpp CUDA 建置與約 4.7 GB 模型下載。來源授權：Owen 確認有權發布，不另加授權條款。
