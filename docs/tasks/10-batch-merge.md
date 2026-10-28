# 10：分批合併（切片 C）

Status: ready-for-human（程式與離線測試完成，待 Owen 實機驗收）
Blocked by: None（與 09 無相依；依決定順序在 09 之後進行）

依規格第 10.2 節與設計第 4、12.2 節，讓 `/summary` 不再受單批 4,000 token 限制，並供 11 重用。

- [x] 依時間切批，保持完整訊息；單則超過預算時拆片段並保留相同來源 ID。
- [x] ChunkSummary 各批產生局部時間線，MergeTimeline 合併；合併輸入超過預算時分層合併。來源 ID 驗證、程式產生時間與連結沿用單批。
- [x] 429 依 `retry-after` 等待，同一批最多重送 1 次，等待時更新使用者訊息；其他錯誤明確失敗。
- [x] 每任務最多 12 次模型呼叫、600 秒總期限；超過即失敗，不交付部分結果。
- [x] 所有批次與合併都經同一 gateway，外送前皆為遮罩後內容；不疊加 SDK／LangChain retry。
- [x] 以假 gateway 測試：單批不變、多批合併、429 等待與重送上限、呼叫數與期限上限、無相關主題、跨批來源 ID。
- [ ] 實機驗收（Owen）：超過單批容量的 `/summary` 能完整交付；記錄批數、呼叫數與耗時。

範圍外：計次、排隊、取消、模型切換。

## 交付紀錄

新增 `summary_workflow.py`（分批、分段、逐層合併、429 等待、12 次／600 秒上限）與 `prompts/merge_system.txt`；`summary_system.txt` 補充分批與 part 說明；`groq_gateway.py` 回報 `retry-after`；`summary_service.py` 改用 workflow；`bot.py` 以 `notify` 更新等待訊息並修改 `/help`。149 個離線測試通過。設計對照見 [design 12.3](../design.md#123-實作對照工作票-10)。
