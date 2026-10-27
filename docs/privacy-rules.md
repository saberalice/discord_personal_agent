# 本機機密規則（切片 05）

目前 `/summary` 的 LLM 輸入、模型輸出與本地 preview 都會套用機密規則。命中 mask 以 `[REDACTED]` 完整取代，命中 block 或檢測失敗則停止整次輸出。系統通知和本來不使用的空內容仍先略過。

## 使用方式

內建規則自動啟用，既有 `config.toml` 不必更動。若新增自訂規則，直接在檔案最後加入；不要用 cp 覆蓋已設定的頻道清單。

```toml
[[privacy_rules]]
id = "private-project"
kind = "literal"
pattern = "虛構專案代號"
action = "block"

[[privacy_rules]]
id = "internal-code"
kind = "regex"
pattern = 'INTERNAL-\d{4}'
action = "mask"
```

- `id`：1～64 個英數字、底線或連字號，全檔不得重複，也不能與內建 ID 重複。
- `kind`：literal 為大小寫敏感的完整片段比對；regex 使用 regex 套件語法，忽略大小寫可寫 `(?i)`。
- `pattern`：非空，最多 4096 字元。TOML 單引號字串可保留正則反斜線。
- `action`：mask 或 block。block 優先於遮罩；依原始文字查找所有模式，不讓先遮罩的規則掩蓋阻擋條件。

最多 100 條自訂規則。規則由本機管理員設定，全伺服器一致，Discord 使用者無法停用。修改後先檢查，再重啟 Bot：

```bash
python -m discord_personal_agent --check-config
python -m discord_personal_agent
```

此切片新增 regex 依賴；本機 Conda 環境已安裝。其他環境需啟用專案 Conda env 後執行 `UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX" uv sync --locked --inexact`。

## 內建範圍及限制

| 類別 | 目前比對 |
|---|---|
| email | 常見 `name@example.com` 形式 |
| 台灣手機 | `09xx-xxx-xxx`、空白分隔／不分隔、`+886` 形式 |
| 部分憑證前綴 | sk-、部分 GitHub token 前綴、mfa. 等模式 |
| Bearer | Bearer 後的憑證字元 |
| 憑證賦值 | api_key、token、password、secret 後的 `=`／`:` 值 |
| PEM 私鑰 | BEGIN／END PRIVATE KEY 區段 |
| URL | 含 userinfo，或查詢／fragment 出現核准敏感鍵（token、signature 等）的 URL 整段替換，包含基本百分比編碼 |

一般非敏感網址保留。不保存命中原值對照表、不回填機密；來源 ID、時間、固定 Discord 原文定位仍保留。內建規則不是供應商完整金鑰辨識器，不涵蓋所有電話、個資、編碼／混淆、商業機密或語境。自訂詞彙只會匹配你定義的字串，不能證明周圍內容全數安全。

目前檢查對話正文、當前暱稱及 topic。topic 已參與摘要，命中 block 時停止。檢查在任何 TXT 傳送或 preview 檔案寫入前完成，因此後段才命中阻擋也不會交付前段。單批 LLM 輸入與輸出已接入；多批合併與重試尚未實作。

自訂正則單次比對上限 50 ms，整批欄位掃描預算 2 秒；逾時或零長度遮罩命中會停止。加入 regex 是為了使用比對期限，依據 [regex 官方套件文件](https://pypi.org/project/regex/#timeout)。這不是整個程序記憶體／CPU 的硬性隔離。

圖片／PDF 仍為佔位符，不讀取其內容。附件構想已記入 [待研究筆記](research/attachment-backlog.md)。

## 測試方式

在授權測試頻道使用虛構資料，例如：

```text
聯絡 test@example.com，手機 0912-345-678
參考 https://example.com/?token=fake-test-value
內部編號 INTERNAL-1234
```

可透過本地 preview 匯出檢查 `[REDACTED]`；目前 `/summary` 只交付摘要，不回傳原文。設定上述自訂 block 後，再放入「虛構專案代號」，應只收到阻擋提示，不收到 TXT。正常文字需仍能讀取。

舊的 Discord TXT 及 local-exports 匯出不會被追溯修改或自動刪除；新產生的資料才套用此流程。未進行真實 LLM 呼叫，也未宣稱所有機密均能被識別。
