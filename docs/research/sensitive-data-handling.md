# Discord 摘要：外送前機密處理研究

查證日期：2026-09-10。本文區分官方機制與本專案建議；尚未實作功能。

## 結論

可採用「本機偵測 → 依規則遮罩或阻擋 → 外部 LLM」；但查到的 Hermes 與 OpenAI 機制有不同範圍，不能宣稱業界有單一預設，也不能保證偵測全部機密。以下本專案政策是基於來源的設計建議，不是任一產品原封不動的預設。

## Hermes Agent：憑證遮罩與個資處理分開

- `security.redact_secrets` 預設開啟，針對工具輸出中看似 API Key、Token、密碼的模式，在進入對話 context 與日誌前遮罩。這項說明不等於承諾對所有原始聊天內容做完整機密分類。[官方設定文件](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/#security)
- `privacy.redact_pii` 預設關閉；開啟後只對支援平台的 gateway system prompt 部分身分欄位做確定性雜湊，包括 user/chat ID 與 WhatsApp／Signal 以電話表示的 ID。名稱不受影響；Discord、Slack 明確排除，因 mention 需要真實 ID。不能把這個開關當成 Discord 聊天全文 PII 遮罩器。[官方設定文件](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/#privacy)
- `agent/redact.py` 是本機規則實作，涵蓋已知憑證前綴、認證標頭、私鑰、資料庫連線密碼、JWT 等。函式回傳替換後文字，這個機制本身並非「命中任一機密即阻擋整次模型請求」。[官方原始碼](https://github.com/NousResearch/hermes-agent/blob/main/agent/redact.py)
- 不能直接照抄全部預設：`redact_url_credentials` 預設為 `False`，原始碼解釋是為了讓 OAuth callback、magic link、預簽網址在一般工具流程保持可用；非導航的外送邊界可使用更嚴格的 URL 憑證遮罩。部分遮罩保留憑證頭尾，檔案讀取則可改用不可用的替代標記。[官方原始碼](https://github.com/NousResearch/hermes-agent/blob/main/agent/redact.py)

## OpenAI：PII 檢查支援遮罩或阻擋

- OpenAI Guardrails Python 的 `Contains PII` 使用 Microsoft Presidio，並可設定偵測的實體種類。`block=false` 是預設；pre-flight 階段會將命中內容替換成如 `<EMAIL_ADDRESS>` 的標記，不觸發 tripwire。`block=true` 則觸發阻擋。文件另指出 output 階段不支援自動遮罩，建議採 `block=true`。[官方 Contains PII 文件](https://openai.github.io/openai-guardrails-python/ref/checks/pii/)
- 文件安裝步驟使用小型英文 spaCy 模型 `en_core_web_sm`；不能據此認定繁體中文姓名、台灣身分證或所有台灣電話格式已受到可靠涵蓋。需另外選擇／實作辨識規則並驗證。檢查結果 `info.detected_entities` 含原始命中值，不能將整個結果直接寫入日誌。[官方 Contains PII 文件](https://openai.github.io/openai-guardrails-python/ref/checks/pii/)
- OpenAI Agents SDK 的 input guardrail 預設可與 agent 平行執行，可能在檢查完成前已啟動模型或工具；`run_in_parallel=False` 才先完成檢查再啟動。這與上述 Guardrails Python 是不同元件，不能混稱為自動具備的同一功能。[官方 Agents SDK Guardrails 文件](https://openai.github.io/openai-agents-python/guardrails/)
- OpenAI 的 agent 安全指南建議清理輸入 PII，同時明確指出 guardrails 並非萬無一失。[官方 Agent Builder 安全指南](https://developers.openai.com/api/docs/guides/agent-builder-safety)
- Presidio 官方說明預設模型與 recognizer 以英文為主，其他語言需調整 NLP 模型、辨識器與語境詞。支援清單中的電話辨識可設定地區；未列出台灣身分證。不能把套件支援多語言解讀成已驗證所有台灣資料格式。[官方語言文件](https://presidio.dataprivacystack.org/analyzer/languages/)、[官方實體清單](https://presidio.dataprivacystack.org/supported_entities/)

## 套用至本專案的建議政策

以下是設計推論，採納情況以 [技術設計](../design.md) 為準，並非聲稱 Hermes 或 OpenAI 預設如此。

1. **分開設定偵測與動作**：內建已知憑證格式、電子郵件及經驗證的電話規則；由 Owen 在本機設定自訂詞彙／模式，以及 `mask` 或 `block` 動作。不把所有私人資訊當成相同風險。
2. **一般欄位遮罩後繼續**：命中憑證或個資後完整替換為型別標記，例如 `[REDACTED_SECRET]`。摘要不需要憑證頭尾，不保留可還原映射，也不在最終回覆還原機密。
3. **禁止外送主題阻擋整次任務**：若 Owen 明確將某規則設成 `block`，命中後不送任何該次內容；不能只遮專案名稱就宣稱周圍討論已無機密。規則只辨識明確定義的模式，不保證語境機密全數命中。
4. **所有外送欄位都處理**：不只聊天正文，也包含使用者關注主題、必要的名稱與中繼資料、保留的 URL。附件第一版只給通用佔位符；不必把檔名或下載網址送給模型。URL 含認證資訊時遮罩，摘要 bot 不需要保留登入能力。
5. **先檢查整批，再開始第一個分批請求**：避免後段才命中阻擋時，前段已經外送。每次外部模型呼叫前仍需確保實際 payload 已處理，包含分批合併與重試；不可讓原始資料先進入模型或外部 tracing。
6. **檢查出錯就停止外送**：設定無效或處理失敗時，不回退到送原文。通知只包含規則識別與數量，不回顯命中值；日誌也不記錄全文或完整偵測物件。
7. **本機管理**：規則由 Owen 維護，一般 Discord 使用者不能停用；含實際敏感詞的設定不進版本控制。Bot Token、共用 LLM API Key 與加密主金鑰走環境變數；後續訪談已確認個人授權 Key 加密保存於本機資料庫。

## 驗證限制與後續

- 本次只有官方文件與原始碼查證，沒有部署 Hermes、安裝 Presidio 或測試真實聊天辨識效果。
- 摘要品質依 Owen 決定，待第一階段完成後人工評估；資料外送控制仍需用虛構憑證與假個資檢查，不能只依摘要讀起來正常判定。
- 實作前要針對選定套件版本、繁體中文／台灣格式、URL 與所有分批路徑驗證。上游 `main` 與線上文件可能變動。
- 此次只新增研究筆記；未修改功能程式碼。專案目錄目前執行 `git status` 回報不是 Git repository，未 commit 或 push。
