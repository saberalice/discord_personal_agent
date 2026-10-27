# 對話整理預覽（切片 B1）

本地 CLI 取得訊息正文與來源資料，套用機密規則後輸出可 review 的文字；此工具不呼叫 LLM。Discord 自動原文 TXT 已移除，目前 `/summary` 行為見 [單批 AI 摘要](single-batch-summary.md)。

## 直接看虛構範例

啟用既有 Conda 環境並進入專案根目錄：

```bash
conda activate discord-personal-agent
python -m discord_personal_agent.preview --demo
```

不需要 Token 或網路。程式印出輸出資料夾，不印正文；每次會建立新的 `local-exports/preview-*`，包含：

- `prompt-preview.txt`：時區、日期與 `暱稱 (HH:mm) : 內容` 格式。
- `sources.json`：套用規則後的正文、作者 ID、訊息 ID、目前顯示名稱、UTC 時間、附件數、回覆目標 ID 與原文連結，以及選取條件和統計。

TXT 的多行／控制字元使用 JSON 字串跳脫，例如換行顯示為 `\n`，避免看起來像新的發言。JSON 中正文可解碼回套用機密規則後的文字；命中的原值不保留。TXT 與 JSON 訊息順序一致，依時間再依訊息 ID 排序；同名使用者可由 JSON 的 author_id 區分。這是經本機規則處理的資料預覽，仍不是完整 LLM Prompt，不保證辨識全部機密或抵擋提示注入。

## 讀取實際測試頻道

沿用 `.env`、`config.toml` 的 Token、允許清單與時區。明確提供請求者 ID，程式會檢查 Bot 與該使用者的閱讀權限；這是本機管理者工具，不對外提供可代填他人 ID 的介面。

```bash
python -m discord_personal_agent.preview \
  --channel 'https://discord.com/channels/伺服器ID/頻道ID' \
  --requester-id 你的Discord使用者ID \
  --limit 10
```

把上述中文佔位內容換成實際值。hours、limit 與 `/summary` 同規則：省略兩者取最新最多 500 則；只填 hours 為完整時段、超過 500 則拒絕；指定 limit 則取最新最多 N 則。此 CLI 僅使用 REST，不啟動第二個 Gateway 連線、不同步 slash 指令，可以在原 Bot 執行中使用。

真實預覽總期限 60 秒。逐個發言者解析暱稱可能增加 API 等待；大量不同發言者時可減少 limit。Discord 429 等候交由 SDK 處理，未新增應用層重試。

## 訊息如何處理

1. 先依既有時間／筆數條件選原始訊息；系統通知也占選取筆數。
2. 依 discord.py `Message.is_system()` 排除系統訊息。一般文字、一般回覆與 Bot 對話保留，不以 Bot 身分排除。
3. 非系統訊息若沒有正文也沒有附件，另外計為「空內容」並略過；不補抓更舊訊息。空內容可能是只有其他媒體的訊息或正文存取受限，不自動判定缺 Intent。
4. 附件只轉成 `[附件：未讀取內容]`，不下載、不讀檔名、不推測圖片內容。網址原樣保留，不抓外部頁面。嵌入內容、貼圖及投票的獨立欄位尚未解析。
5. 每次查詢每個可用發言者只取得一次當前伺服器暱稱。Discord 回報 Unknown Member 時回退全域顯示名稱／username；其他 API 失敗明確停止，不當作離開伺服器。
6. 產出前重新查核閱讀權限；取得來源時間不等於還原歷史快照，名稱也不是當年發言時的暱稱。

輸出留在已忽略 Git 的 `local-exports`，資料夾僅擁有者可存取，檔案權限為 600。這是明確執行指令才產生的本地匯出；檔案不會自動刪除，review 完可自行移除。機密規則範圍見 [規則指南](privacy-rules.md)；LLM 外送流程見單批摘要指南。

## 本次驗證與待驗收

- 自動化：跨日／時區、時間排序、正文與附件、系統／空訊息統計、不補抓、來源定位、暱稱與離開成員回退、權限撤回、時段超量拒絕、CLI 虛構匯出與檔案權限。
- 已執行 `--demo`，成功產生 4 則原始訊息 → 1 則系統通知排除 → 3 則可用對話。
- 真實正文非空、實際伺服器暱稱及附件呈現，仍需以授權測試頻道驗收。先前 general 抓到的 5 則都是成員加入通知，不能用來宣稱正文讀取通過。

建議在測試頻道準備兩位使用者的虛構文字、一則回覆、一個附件及多行文字，以 `--limit 10` 匯出後核對。本機機密規則已接入；LangChain 單批摘要已另行接入，待實機驗收。
