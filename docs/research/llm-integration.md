# LLM API 串接方案比較

查核／更新日期：2026-09-11。範圍：第一版 Python＋uv、單一 Linux Bot、兩個任務並行、共用 Groq 候選與個人 OpenRouter。

**第一版採用 LangChain 的 chat model、Prompt 與 Runnable，保留小型 LlmGateway 管理每次模型請求。** 這是使用者已選定的架構。LangChain 的提供者整合負責模型協定；應用負責權限、加密、遮罩、額度、取消與來源驗證。實作細節以 [技術設計](../design.md) 為準，本文件集中記錄來源與取捨。

尚未安裝套件、呼叫真實模型或量測效能；版本須在整合驗證後鎖入 uv.lock。

## 1. 選型結論

OpenRouter 是模型 API 平台，LangChain 是框架，SDK 是連線實作，三者不互斥。此次分層如下：

| 層次 | 決定 |
|---|---|
| 模型服務 | 個人 OpenRouter；共用 Groq 等候選經驗證後定案 |
| 模型介面 | LangChain `ChatGroq`／`ChatOpenRouter`，統一非同步呼叫與結構化資料處理 |
| 摘要流程 | LangChain Prompt／Runnable 組合單批與合併流程；應用排程控制批次與工作量 |
| 應用規則 | LlmGateway 與任務／帳戶模組處理權限、金鑰、免費配額及取消 |

官方明確支援模型在 agent loop 外單獨使用，因此採用 LangChain 不要求第一版就由模型自主選工具。[Models](https://docs.langchain.com/oss/python/langchain/models)

## 2. 方案比較

下表的適用時機與維護成本是本專案的工程判斷；功能事實連至各官方來源。

| 方案 | 官方提供的能力 | 本專案需要承擔的工作 | 適用時機／結論 |
|---|---|---|---|
| 原生 HTTP：`httpx.AsyncClient` | 非同步 HTTP、連線池、可明確關閉資源。[HTTPX](https://www.python-httpx.org/async/) | 自行處理 API payload、錯誤映射、回應型別與供應商差異 | 適合 API 很少或需要特殊端點；保留用於授權等 REST 操作，摘要主路徑使用 LangChain 模型整合 |
| 供應商 SDK：如 `AsyncGroq` | 供應商維護型別、非同步介面、錯誤及重試設定。[Groq Python](https://github.com/groq/groq-python) | 每家 adapter 使用不同 SDK 型別，對外再統一 | 需要供應商獨有功能時採用；目前純文字摘要沒有確認必須使用的獨有功能 |
| OpenAI-compatible：`AsyncOpenAI` | 非同步、明確 `api_key`／`base_url`、timeout／重試設定、型別化回應。[SDK](https://github.com/openai/openai-python) | 仍需處理各模型能力與非標準參數 | 直接 SDK 方案可行；本次改採 LangChain，這些相容能力留在 provider integration 內 |
| LangChain model／Runnable | 跨供應商模型介面、獨立模型呼叫、結構化輸出、批次與並行控制；可以不用 agent。[Models](https://docs.langchain.com/oss/python/langchain/models) | 仍需自己的 Discord 權限、個人金鑰、額度、引用驗證；需理解 wrapper 與供應商參數 | **第一版採用**；使用者優先考慮後續多功能擴充與一致模型／流程介面，接受整合層與版本維護成本 |
| LangGraph | 狀態圖、durable execution、interrupt、checkpoint 與長期執行協調，可不依賴 LangChain。[Overview](https://docs.langchain.com/oss/python/langgraph/overview) | 設計狀態、恢復及儲存政策；checkpoint 若含聊天就會新增持久化副本 | 需要跨重啟恢復、多輪工具操作或人工批准再續跑時重新評估；第一版不使用 |
| LiteLLM Python SDK | 多供應商請求／錯誤格式統一，另有 Router、重試與 fallback。[SDK](https://docs.litellm.ai/docs/) | 設定金鑰隔離、停用不需要的 fallback／callbacks，確認轉換後參數 | 供應商與非相容 API 明顯增多時優先重評；第一版不再疊一層轉譯 |
| LiteLLM Proxy | 獨立 gateway，集中管理 virtual keys、預算、路由及管理介面。[Proxy](https://docs.litellm.ai/docs/proxy/quick_start) | 增加服務部署、存取控制、資料流與維運面 | 多個應用程式共用 LLM 基礎設施時適合；單 Bot 不採用 |
| PydanticAI | 型別化 output、validator、模型互動與重試；支援 OpenAI-compatible provider。[Output](https://ai.pydantic.dev/output/)、[Provider](https://ai.pydantic.dev/models/openai/) | 仍要限制重試／呼叫次數並實測免費模型支援的輸出模式 | 若未來多種結構化任務共用驗證與工具功能，可重新評估；現在只使用 Pydantic 資料驗證即可 |

LangChain 的 client-side `batch()` 是平行發出多次請求，不等於供應商提供折扣或非同步離線處理的 Batch API；也不會使免費請求數增加。[LangChain batching](https://docs.langchain.com/oss/python/langchain/models#batch)

沒有實測依據宣稱 LangChain「很慢」或 LiteLLM「一定比較耗 RAM」。此次取捨依據是介面重用與維護成本，沒有量測支持任何性能排名。

## 3. LangChain 套用方式與來源

| 能力 | 官方依據 | 本專案取捨 |
|---|---|---|
| 共用候選模型 | [ChatGroq](https://docs.langchain.com/oss/python/integrations/chat/groq) 提供 `langchain-groq` 整合及非同步／結構化能力 | 使用 ChatGroq；供應商免費限制與品質仍需各別驗證 |
| 個人模型 | [ChatOpenRouter](https://docs.langchain.com/oss/python/integrations/chat/openrouter) 提供 `langchain-openrouter`、非同步、結構化與 provider routing | 使用 ChatOpenRouter；OAuth 另行處理，不把模型 wrapper 當帳戶授權系統 |
| Prompt 與流程組合 | [LangChain 模型介面](https://docs.langchain.com/oss/python/langchain/models) | 單批和合併採 Prompt → 受控模型呼叫 → 驗證，不重寫通用模型框架 |
| 回覆結構 | ChatOpenRouter 文件明列 `with_structured_output` 的 `function_calling` 預設與 `json_schema` 方法 | 按目錄指定能力；保留 usage／finish reason／解析錯誤，schema 成功不代表未截斷或內容正確 |
| LangGraph | [Overview](https://docs.langchain.com/oss/python/langgraph/overview) 提供狀態圖與持久執行 | 第一版不啟用 checkpoint；有跨重啟恢復與工具決策需求時再設計保存政策 |

主要依賴使用 `langchain-core`、對應 provider integration 與 Pydantic；若使用頂層 `langchain` API 再明確加入。模組化使用是 LangChain 架構的一部分，不要求現在就啟用 Agent、LangSmith 或向量資料庫。

每個任務明確綁定專用模型實例與憑證，避免修改全域 key。Groq／OpenRouter 的特有參數、例外與資源生命週期留在 adapter。工程 smoke test 必須確認路由政策確實傳到上游，不因 wrapper 接受參數就判定供應商已執行。

## 4. 保留小型 LlmGateway 的理由

LangChain 已處理共同模型介面，LlmGateway 不再重做此層。它只承接本 Bot 的政策：每次請求前的取消／權限／免費檢查、隔離憑證、限速與有限重試，再將結果轉成專案資料。這些規則必須同時涵蓋單批、合併、格式修復與未來功能。

模型的 `ainvoke` 不直接從 Discord 指令呼叫；摘要流程經 gateway 才可出網。不要疊加 Runnable `with_retry`、框架 fallback、SDK retry 與應用 retry。第一版只保留一處重試預算，實際關閉方式按鎖定的整合套件驗證。

原始聊天在進入 chain 前先整批遮罩；停用外部 tracing／callbacks、verbose、cache 和 checkpoint。LangSmith 是可另行設定的觀測服務，不是使用 LangChain 的必要條件。[Tracing 設定](https://docs.langchain.com/langsmith/trace-with-langchain)

## 5. 擴充與實際代價

| 擴充 | 哪裡需要改 |
|---|---|
| 新增模型供應商 | 增加 LangChain provider integration 與必要政策轉換，通過相容性測試 |
| 新增摘要、分類或資料擷取指令 | 新的 Prompt／Runnable 與資料型別，重用 gateway、授權與排程 |
| ASR 後做語音摘要 | 語音接收／辨識是新流程；轉成文字後重用摘要能力 |
| 自主工具與可恢復工作 | 評估 LangGraph／Agent，另設權限、工具預算與保存政策 |

選擇 LangChain 是提早統一模型與流程概念，不能保證未來所有功能都不用改設計。代價是需要理解 Runnable／訊息型別、維護 integration 與底層 SDK 的版本，遇到問題可能需追查多層；它不增加 API 免費額度，也不保證免費模型的結構化能力。

本次不再推薦等到之後才引入 LangChain。其餘框架保留為比較選項，不與第一版架構並列成多套可任意切換的預設。

## 6. 必要驗證

- 兩個任務使用不同模型／金鑰並行，所有底層請求正確隔離。
- Runnable 組合確實經受控呼叫；批次、重試、合併與格式修復不繞過取消／遮罩／免費政策。
- 各核准模型的 structured output 方法、截斷 metadata、usage、429 header 與 provider 路由參數正確傳遞。
- LangChain integration 與底層 SDK 的重試關閉，client／連線資源可清理；沒有外部 trace 或原文日誌。
- 部署主機的真實 RAM、CPU、延遲與可完成摘要數須量測，不能從框架名稱推論。

驗證順序與切片見 [實作計畫](../implementation.md)。研究來源描述可用介面，不代表本專案已安裝或驗證。
