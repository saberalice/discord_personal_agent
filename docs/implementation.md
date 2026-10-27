# 實作與驗證計畫

更新：2026-09-11，採用 LangChain 的第一版架構。

切片 A、B1、本機機密規則及 B 單批摘要已實作；本切片已於 2026-10-27 在 Discord 頻道完成實機驗證，見 [工作票 06](tasks/06-single-batch-summary.md) 和 [本地驗收](single-batch-summary.md)。C～F 尚未開始。產品行為見規格，工程設計見 design.md；離線測試不代表模型品質已通過。

## 1. 先驗證會影響整體選型的介面

以虛構資料做最小連線驗證，不以朋友頻道原文作第一筆請求：

1. 鎖定可相容的 Python、discord.py、langchain-core、langchain-groq、langchain-openrouter、其底層 SDK、Pydantic 與 cryptography 版本，建立 uv.lock；確認 LangChain 整合及 SDK retry／timeout 控制，以及每任務模型實例的資源釋放。
2. 在測試伺服器驗證 global DM `/summary`、Portal intents、權限查核、歷史分頁、無成員快取時取得名稱、普通 DM 交付與選單／Modal。
3. 用虛構資料驗證 Groq 候選模型的非串流文字、結構化結果、真實 TPM 錯誤與免費方案；量測請求 token 與延遲，不只比較 RPD。
4. 驗證 OpenRouter headless PKCE 的瀏覽器流程、單次 code、過期／錯誤 verifier、Key 交換，以及零價格／資料政策篩選是否能找到可用端點。
5. 若免費端點不可用，回報候選未通過；調整候選清單，不自行啟用付費或降低外送政策。

通過條件：有一個共用與一個個人模式端點能處理虛構摘要資料；回覆與已知錯誤可轉成設計的結果型別。此階段不宣稱中文摘要品質通過。

LangChain 相容性另驗證：`ChunkSummary`／`MergeTimeline` 的 `ainvoke`、不同模型的 structured output 方法、截斷 metadata 保留、所有重試／修復仍走 gateway、trace／cache 關閉，以及兩人同時請求不串用金鑰。

## 2. 垂直切片

切片 A 工作票見 [01-channel-inspection](tasks/01-channel-inspection.md)。後續切片仍須各自建立本機工作票，本文件保留開發順序。

| 順序 | 可驗收結果 | 主要驗證 |
|---|---|---|
| A | `/summary` 接收連結，回覆固定範圍及來源筆數 | 時區／跨日、訊息第 500／501 則、Bot 與請求者權限；尚不送 LLM |
| B | 虛構／授權測試訊息經遮罩後，以 LangChain ChunkSummary 生成一批時間線 | 阻擋零外送、正確來源 ID、當前暱稱、附件佔位符 |
| C | LangChain MergeTimeline 分批合併＋Embed／TXT | 長單則全文覆蓋、重疊去重、合併容量／請求上限、無相關主題、輸出格式 |
| ~~D~~ | （2026-10-27 移至規格第 11 節，不實作）兩任務並行、有界排隊、每日 3 次、取消 | 競態、重試不重複計次、重啟次數仍有效、權限撤回、超過 interaction 期限仍交付 |
| ~~E~~ | （2026-10-27 移至規格第 11 節，不實作）個人授權、加密保存、模式／模型下拉選單、移除 | 跨使用者 Key 隔離、失效不回退、移除中斷未完成任務、重新授權 |
| F | Linux 常駐、維護重啟、可調設定、使用說明 | 停止受理與清理、無機密日誌、狀態存取限制、校方備份範圍確認 |

## 3. 自動化測試的最高介面

主要以 SummaryApplication 的 submit／cancel 與可觀察回覆驗證，注入固定時鐘、可控 ChannelHistory 與 LlmGateway 替身。不測私有函式呼叫次序、不鏡射 SDK 的內部實作；對加密儲存、計次交易、供應商協定另做必要 adapter 測試。

- **時間／範圍**：排隊後截止不移動，跨午夜，非台灣 IANA 時區，邊界 `[start,end)`；來源缺失與編輯不假裝歷史快照。
- **權限**：Bot 可看但請求者不可看、頻道覆寫、排隊中撤權、同一使用者跨 server 仍只有一個任務。
- **機密**：全文後段命中 block 時零外送；名稱、topic、URL 同樣處理；格式修復／合併／重試不能繞過；原文與檢測結果不寫入日誌。
- **來源**：未知來源 ID 拒絕；時間與網址由固定來源生成；同名成員不混淆；附件不被說成已讀取。
- **排程／取消**：10 個排隊上限、每人限制、取消釋放槽、在 retry／合併／投遞邊界取消、shutdown，私訊發送競態如實回報。
- **計次**：首次呼叫前扣一次、前置失敗不扣、成功／取消／失敗後不重複扣；SQLite 交易競態、重啟保留、跨日時區、刪 Key 不清空共用次數。
- **憑證**：兩使用者同時呼叫時各自使用正確 Authorization；共享 Key 不被覆寫；模型切換不影響已受理任務；Key 失效不 fallback；主金鑰錯誤停止解密。
- **API／費用**：LangChain wrapper／底層 SDK 的 provider 429、逾時、非免費模型、價格未知、無合規端點、wrapper／SDK retry 關閉與應用重試上限；不以成功 HTTP 就當成有效摘要。
- **輸出**：渲染後完整限制、TXT UTF-8／來源 URL、預覽無額外模型請求；超過檔案大小明確失敗。

核心邏輯依專案規則採 TDD。Discord 與供應商官方 API 的 live smoke test 只在需要驗證外部契約時執行，不讓單元測試日常消耗免費額度。

## 4. 人工效果評估

依 Owen 的決定，第一版可運行後，以熟悉的實際對話評估主要話題是否涵蓋、時間線是否好找、是否誤把未定論當結論，以及關注主題是否有幫助。先收集問題，再選擇改 Prompt、分批策略或模型；目前不設定未經討論的品質分數門檻。

效能評估記錄每任務訊息／token 數、LLM 呼叫數、遮罩耗時、排隊／模型等待、RSS 峰值；不記錄原文。2 vCPU／2 GB 只作試跑預算，不能作承諾。比較模型時使用同一組資料與相同外送政策。

## 5. 第二階段

2026-10-27 語音規格已確認（規格第 9、10 節，設計第 11、12 節）。開發順序：

1. [09 語音整合](tasks/09-voice-integration.md)：Rust 程式放入 `voice/`、兩項 Rust 小改、Python 宣告 `/join`／`/leave`、啟動器、`/help`。
2. [10 分批合併](tasks/10-batch-merge.md)：即上表切片 C，`/summary` 與後續 `/voicesummary` 共用。
3. [11 逐字稿摘要](tasks/11-voice-summary.md)：`/voicesummary`。

F（常駐部署）待決定部署地點後再排。
