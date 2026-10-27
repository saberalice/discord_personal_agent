# discord_personal_agent

> 工程代理開始工作前讀本檔。使用者當次授權與已確認決策優先於通用技能流程。

## 1. 專案狀態與文件入口

第一版是 Discord 時間範圍文字摘要，後續才做語音。2026-09-11 已依使用者決定採用 LangChain 並更新研究／設計文件；切片 A 已完成基本實機驗證；B 單批 LangChain／Groq 摘要已實作，2026-10-27 Owen 已在 Discord 頻道完成實機驗證；尚未部署常駐服務。

- 開發前讀 [功能規格](docs/spec.md)、[技術設計](docs/design.md) 及 [實作與驗證計畫](docs/implementation.md)。
- 比較或更換 LLM 串接方式時讀 [API 研究](docs/research/llm-integration.md)；涉及供應商額度、Discord 限制或授權時讀 [平台研究](docs/research/platform-constraints.md)。
- 處理機密外送時讀 [機密研究](docs/research/sensitive-data-handling.md)；命名使用 [CONTEXT.md](CONTEXT.md) 的詞彙。
- 規格是產品行為的單一依據；設計中的「工程預設」與「候選」不代表已實測。實作前查核外部介面與免費模型，不回復過時的 Claude API 預設。

## 2. 技術棧

已確認 Python + uv、實驗室 Linux 單一實例。已決定採用 LangChain 模型與 Runnable 架構，搭配 discord.py、LlmGateway、Pydantic、本機規則遮罩、SQLite 與 Fernet；Groq／OpenRouter 使用對應 LangChain 整合套件；理由及限制見技術設計。共用 Groq 為候選，個人授權先支援 OpenRouter；精確版本與免費模型清單須經整合驗證後鎖定。

## 3. 文件與程式結構

人類閱讀入口為 README.md。產品規格、技術設計、開發計畫與研究分開維護，不重複複製成多份規格。程式放 src/、測試放 tests/；模組切分依設計中的介面，確有必要時才新增 ADR。

## 4. 開發規則

### 4.1 流程

1. **先有規格再有程式碼**。需求不清楚就問，不要猜。任何超過一個檔案的功能，先用 `/to-spec` 產出規格、`/to-tickets` 切成可獨立驗收的小票。
2. **TDD 為預設**。核心邏輯（摘要組裝、訊息過濾、時間區間計算）一律先寫測試。走 `/tdd` 的 red-green-refactor 迴圈。
3. **一次一個垂直切片**。每個切片自己能跑、能測、能 commit，不要留半成品分支。
4. **commit 前跑 `/code-review`**。修完再提交。
5. **卡住不要硬幹**。同一個 bug 試兩次沒解決就改用 `/diagnosing-bugs`，建立能穩定重現的失敗案例再說。

### 4.2 程式碼

- 模組要「深」：對外介面小、實作細節藏在裡面。Discord 的 SDK 型別不要漏到業務邏輯層。
- 業務邏輯與 I/O 分離：摘要邏輯必須能在沒有 Discord 連線、沒有 LLM 呼叫的情況下被測試。
- 錯誤不要吞掉。Discord API 錯誤、LLM 逾時都要有明確處理路徑與日誌。
- 不寫防禦性 `try/except` 包全世界；只在真正會失敗的邊界處理。
- 註解寫「為什麼」，不寫「做什麼」。

### 4.3 安全（Discord Bot 特別注意）

- **Bot Token、共用 API Key 與加密主金鑰走環境變數**；個人授權 Key 依已確認規格加密存於 SQLite，絕不進 git。`.env` 與狀態資料庫必須在 `.gitignore` 裡。
- 若 token 曾經被貼進任何檔案或訊息，視為外洩，去 Discord Developer Portal 重新產生。
- Bot 只申請最小必要的 Intents 與權限。讀訊息需要 Message Content Intent；實作前必須查 Discord 官方文件確認目前規則。
- 摘要內容可能含私人對話：不要把原始訊息送到非必要的第三方服務，日誌不要印出訊息全文。
- 尊重 Discord rate limit，不要自己寫重試風暴。

### 4.4 Git

- 不要在未經要求的情況下 commit 或 push。
- commit 或 push 前，先顯示變更內容與預定的完整 commit message，取得 Owen 明確同意。
- 一個 commit 一件事，訊息使用全域規則指定的 Conventional Commits 格式。
- 標題明確寫出這個 commit 改了什麼，不使用內部代號（如「切片 A」「票 06」）。
- 不加 `Co-Authored-By` 等 AI 署名行。
- 不 force push 到共用分支。

### 4.5 與 Owen 互動

- 用繁體中文回覆。
- 有兩種以上合理做法時，給出建議並說明取捨，不要只列一長串選項。
- 做不到或不確定就直說，不要編造 API 行為。Discord SDK 與 Anthropic API 的用法必須查官方文件確認。

## 5. 可用的 Skills

### 5.1 mattpocock skills（user scope plugin）

以 `/skill-name` 明確呼叫；符合任務描述時也可能自動使用。

規劃與規格：

- `/grill-me` — 把模糊需求問清楚。
- `/grill-with-docs` — 訪談需求並同步整理領域文件。
- `/to-spec` — 把對話結論變成規格文件。
- `/to-tickets` — 把規格切成有相依關係的小票。
- `/wayfinder` — 大到一個 session 裝不下的工作，先做決策地圖。
- `/domain-modeling` — 建立並維護 `CONTEXT.md` 領域詞彙。
- `/codebase-design` — 深模組設計的共同語彙。

實作與品質：

- `/tdd` — red-green-refactor 迴圈（本專案預設開發方式）。
- `/implement` — 依規格或票單實作，內含 TDD 與 code review 收尾。
- `/code-review` — 對 diff 做標準面與規格面雙軸審查。
- `/diagnosing-bugs` — 難解 bug 與效能回歸的診斷流程。
- `/resolving-merge-conflicts` — 逐 hunk 解衝突。
- `/improve-codebase-architecture` — 掃描架構改善點。

其他：

- `/research` — 針對問題查一手資料並產出附引用的 Markdown。
- `/prototype` — 建立丟棄式原型以回答設計問題。
- `/handoff` — 把目前進度交接給下一個 session。
- `/ask-matt` — 不知道該用哪個 mattpocock skill 時使用。

本專案已設定本機文件工作流，見下方 Agent skills；使用者已指定規格寫入專案，不需為文件工作重新詢問外部 issue tracker。

### 5.2 Claude Code 內建

- `/claude-api` — 呼叫 Claude API 前必讀：model ID、定價、streaming、tool use 與 caching。
- `/security-review` — 提交前的安全審查（本專案處理 token 與私人訊息，建議定期跑）。
- `/run` — 實際把 bot 跑起來驗證改動。
- `/schedule`、`/loop` — 需要定時任務或輪詢時使用。

`/code-review` 同時存在於 mattpocock plugin 與內建，本專案預設用 mattpocock 版（雙軸審查）。

## 6. 常用指令

啟用 `discord-personal-agent` Conda 環境後，在專案根目錄執行 `python -m pytest -q`、`ruff check src tests`、`ruff format --check src tests`。離線設定檢查為 `python -m discord_personal_agent --check-config`，啟動為 `python -m discord_personal_agent`；安裝與實機驗收見 [本地開發](docs/local-development.md)。

## 7. 設定與憑證

設定契約集中於 [技術設計第 8 節](docs/design.md#8-設定契約與部署)。Bot 與共用供應商 Key、加密主金鑰走部署環境變數；使用者授權 Key 依規格加密保存。實際金鑰、資料庫與敏感規則不進 Git；未來 .env.example 只列名稱，不含值。

## Agent skills

本機規格／工作票的位置與狀態見 [issue-tracker.md](docs/agents/issue-tracker.md)、[triage-labels.md](docs/agents/triage-labels.md)；詞彙與文件規則見 [domain.md](docs/agents/domain.md)。最近完成的工作票為 [單批摘要](docs/tasks/06-single-batch-summary.md)（已實機驗證），Pipeline 見 [單批摘要指南](docs/single-batch-summary.md)。本機機密規則見 [規則指南](docs/privacy-rules.md)。下一步為後續分批切片；附件暫緩。
