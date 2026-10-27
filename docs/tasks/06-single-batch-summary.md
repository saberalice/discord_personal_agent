# 06：LangChain 單批摘要

Status: ready-for-review
實機驗收：2026-10-27 Owen 已在 Discord 頻道完成 `/summary` 實機驗證。

使用者授權實作上一輪討論的單批摘要與 Prompt，並要求設計選擇交由使用者判斷。

已確認：固定系統指示、遮罩後的對話／topic、短來源 ID、結構化輸出、未知來源拒絕、程式生成時間／連結、輸出機密檢查與交付前權限複查；不存 Prompt 歷史、不做多批合併，不處理附件。接入 LLM 後移除自動原文 TXT。

已決定：Groq 免費方案、固定 openai/gpt-oss-20b；可設定的單次 4,000 輸入／4,000 輸出 token。不要硬限制 8 個話題或每段 300 字；超量、截斷、格式失敗、限流明確失敗，不重試、不默默截斷、不自動換模型。

驗證邊界沿用實作計畫：摘要應用（含機密阻擋、來源定位、權限撤回）、供應商 HTTP 契約（虛構資料）、Discord 私訊交付。原始對話不寫入測試日誌。

先行工作：Prompt 文件、來源索引、輸出 schema 與行為驗證不依賴供應商，可先實作。API key 僅在本地設定，整合測試先用虛構資料。

實作結果：Prompt/schema、容量檢查、LangChain Groq gateway、SummaryService、來源時間線、輸出機密檢查與權限複查、Embed／摘要 TXT、可選 llm 設定及虛構資料 CLI。操作入口見 [單批摘要指南](../single-batch-summary.md)。

驗證：114 項離線測試通過，ruff lint／format 通過；現有兩個伺服器設定格式檢查通過。Groq mock HTTP 測試在沙箱內卡住、沙箱外通過，未據此改動 LangChain 版本。真實 smoke test 因本地缺 Key 在外送前停止，未宣稱 API／中文品質已通過。
