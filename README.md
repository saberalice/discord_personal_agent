# discord_personal_agent

透過 Discord 私訊，將指定頻道最近一段時間的對話整理成可回查原文的討論時間線。

**目前狀態：單批 AI 摘要已接入，2026-10-27 已在 Discord 頻道完成實機驗證。** `/summary` 透過 LangChain 呼叫 Groq gpt-oss-20b，回傳來源時間線與 Embed／摘要 TXT；預設輸入及輸出各 4,000 token。先依 [單批摘要驗收](docs/single-batch-summary.md) 設定本地金鑰並執行虛構資料測試。

先讀 [Miniconda 本地啟動與驗收](docs/local-development.md)。本機已建立 `discord-personal-agent` Conda 環境；填入本地 Token 與頻道設定後即可啟動。

新增 [對話整理預覽](docs/conversation-preview.md)：執行 `python -m discord_personal_agent.preview --demo` 可查看虛構對話與來源 JSON。

架構決定（2026-09-11）：第一版採用 LangChain 模型整合與 Runnable 流程，將個人金鑰、額度和外送政策集中於 LlmGateway。

## 建議閱讀順序

| 想知道什麼 | 文件 |
|---|---|
| 第一版做什麼、使用者怎麼操作 | [功能規格](docs/spec.md) |
| 如何實作、資料怎麼流動、設定與錯誤怎麼處理 | [技術設計](docs/design.md) |
| 為什麼選這種 API 串接方式、LangChain 如何運用與擴充 | [LLM 串接比較](docs/research/llm-integration.md) |
| 開發順序、必要驗證、還沒驗證的項目 | [實作與驗證計畫](docs/implementation.md) |

補充來源：[平台限制與模型候選](docs/research/platform-constraints.md)、[機密處理研究](docs/research/sensitive-data-handling.md)。詞彙有疑義時查 [CONTEXT.md](CONTEXT.md)；工程代理開始工作前讀 [CLAUDE.md](CLAUDE.md)。

## 文件如何維護

- `spec.md` 是產品行為的單一依據；`design.md` 是實作設計的單一依據。
- 研究文件保留來源、查證日期與選型理由，不作為第二份產品規格。
- 使用者已確認的需求與本次補齊的工程決策分別標示；候選模型不代表已實測品質。
- 第一版完成後以真實對話人工評估摘要，再決定改善；語音錄音與摘要留到第二階段。
- 本次依使用者要求，規格與研究只存於專案本機文件。尚未建立 Git repository 或外部 issue；工作票見 [切片 A](docs/tasks/01-channel-inspection.md)。

本機機密遮罩／阻擋已接入，設定方式見 [規則指南](docs/privacy-rules.md)。圖片／PDF 構想暫緩實作。
