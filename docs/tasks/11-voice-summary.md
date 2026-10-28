# 11：逐字稿摘要 `/voicesummary`

Status: done（2026-10-28 Owen 實機驗收通過）
Blocked by: 09（需有真實逐字稿訊息驗收）、10（分批合併）

依規格第 10.1 節與設計第 12.1 節。

- [x] 私訊指令 `/voicesummary message:<連結> topic:<選填>`；結果私訊給請求者，沿用 Embed／TXT 交付。
- [x] 連結解析、允許清單、請求者與 Bot 權限檢查沿用 `/summary`。
- [x] 只下載本 bot 發出、檔名 `yyyy-mm-dd_hh-mm-ss.txt`、≤ 8 MiB 的附件；其他情況明確拒絕且不下載。
- [x] 解析 `[hh:mm:ss] 名稱: 內容`，跨午夜依檔名日期加一日；格式錯誤整次失敗。
- [x] 名稱與內容套用 PrivacyGuard；block 時零外送。
- [x] 每段顯示由來源行產生的起訖時間，附一個 TXT 訊息連結。
- [x] 測試：解析與跨午夜、非 bot 附件／錯誤檔名／過大拒絕、權限撤回不交付、機密阻擋、topic 無相關。
- [x] 實機驗收（Owen）：對 09 產生的真實逐字稿執行摘要（2026-10-28，71 行；`max_input_tokens = 4000` 時分 2 批，合併請求遇 Groq TPM 429 失敗，調為 6000 後單批成功）。

## 交付紀錄

新增 `voice_transcript.py`；`discord_adapter.py` 新增 `transcript` 與語音頻道文字聊天支援；`summary_service.py` 新增 `run_transcript`；`summary.py` 拆出共用時間線渲染；`bot.py` 新增 `/voicesummary` 並與 `/summary` 共用私訊流程。172 個離線測試通過。設計對照見 [design 12.4](../design.md#124-實作對照工作票-11)。
