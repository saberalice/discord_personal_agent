# 單批 AI 摘要：本地驗收

本切片使用 LangChain + Groq `openai/gpt-oss-20b`，輸入與輸出各預設最多 4,000 token。API 整合已由模擬 HTTP 驗證；真實 Groq 連線與中文品質仍待設定本地金鑰後驗證。圖片／PDF、不足容量時分批、個人 Key、每日計次與排隊不在本切片。

## 啟動與第一筆測試

在原有 Miniconda 環境更新鎖定的依賴，不必重建環境：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate discord-personal-agent
cd ~/code/discord_personal_agent
UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX" uv sync --locked --inexact
```

到 [Groq Console](https://console.groq.com/keys) 建立 API Key，只在本地 `.env` 加上 `SHARED_LLM_API_KEY=你的金鑰`。原有 Discord Token、頻道白名單與機密規則保留，不要重新複製 example 覆蓋實際設定。請使用免費帳戶；Bot 不會替你判斷帳戶是否已啟用付費。

第一次 token 計數需要下載 tokenizer 詞表（不會上傳對話）；可先執行並保留本機快取：

```bash
python -c "import tiktoken; tiktoken.get_encoding('o200k_harmony'); print('tokenizer ready')"
python -m discord_personal_agent --check-config
python -m discord_personal_agent.llm_smoke
```

`llm_smoke` 會消耗一次 Groq 請求，只送內建虛構對話，不連 Discord、不讀取頻道、不保存 prompt。預期應區分「星期五部署提議」與「尚未確定日期」，並指出 Alex 負責權限檢查；顯示的來源連結是虛構 ID。缺 Key、格式不符或限流會明確失敗，不重試。

通過後啟動 Bot：

```bash
python -m discord_personal_agent
```

與 Bot 私訊 `/summary channel:<連結> limit:20`；先從少量訊息開始。`hours`、`limit`、`topic` 都選填，兩種範圍都不填仍取最新最多 500 則，但不保證這 500 則能放入單批 token 容量。重啟時會同步新的指令說明。

## Pipeline 與檔案入口

1. `bot.py` 接收指令並固定查詢截止時間，防止同一使用者重複執行。
2. `summary_service.py` 使用既有 `SummaryApplication.prepare`：白名單、雙方權限、`discord_adapter.py` 讀取歷史、排序並排除系統通知／空訊息、套用機密規則。
3. `summary.py:build_summary_input` 將遮罩後的訊息與 topic 組成 LangChain system／human messages。每則有短來源編號，例如 `m001`，以及 `Owen (15:03) : 訊息內容`；包含日期與時區，避免跨日混淆。Discord ID 與來源網址的對照保留在本地記憶體。
4. `summary.py:check_capacity` 使用 `o200k_harmony` 計算文字、schema，加上 256 token 格式預算。這是保守的工程估算，並非 Groq 實際計費數；超過輸入上限即停止，不刪減訊息。
5. `groq_gateway.py` 使用 LangChain `with_structured_output(..., method="json_schema", strict=True)` 的 Runnable `ainvoke`，呼叫固定 Groq HTTPS 端點。每次請求建立並關閉 client，關閉 LangSmith tracing、LangChain cache 與 SDK retry。
6. 解析 JSON、驗證狀態／來源 ID，拒絕輸出截斷或無效格式。程式依全部引用來源產生時間區間，依開始時間排序；每段只顯示最早引用訊息的「查看起點」連結，終點保留時間，不列出其餘來源連結；模型文字再次套用機密規則。
7. 再次查核權限，才交付私訊。3,500 字元以下使用 Embed；超過則提供預覽和完整 `summary.txt`。TXT 以記憶體產生並在傳送完成或失敗時關閉，8 MiB 超限明確失敗。

查詢最多 60 秒、模型階段最多 90 秒、最後權限查核及交付各最多 60 秒；不自動重試。這些是本切片的工程期限，不是模型延遲承諾。

自動回傳的 `conversation-preview.txt` 已移除；原有 `python -m discord_personal_agent.preview --demo` 本地預覽工具仍保留。Bot 不保存對話、prompt 或摘要歷史；任務結束後沒有應用層的歷史快取，但不保證 Python RSS 立即下降或記憶體安全抹除。

## 調整 Prompt 與容量

固定指示位於 [summary_system.txt](../src/discord_personal_agent/prompts/summary_system.txt)，由套件資源載入。不需要另外建立 agent skill；輸出格式由 `summary.py` 的 Pydantic schema 與程式驗證規範。調整重點是涵蓋主要討論、區分提議與決策、不補造結論、依 topic 聚焦，以及忽略對話內要求更改任務的指示。

目前不限制 8 個話題或每段 300 字；實際可涵蓋範圍仍受輸出 token 容量限制。來源驗證只證明 ID 存在，不證明每一句摘要語意正確，須人工回查。

`config.toml` 可選加入下列獨立區塊；省略時也是同樣預設：

```toml
[llm]
max_input_tokens = 4000
max_output_tokens = 4000
```

這是每次呼叫的預算，不是每分鐘額度。調高設定不會提升 Groq 免費額度。官方表列 30 RPM、1,000 RPD、8,000 TPM、200,000 TPD，以 organization 共用；2026-10-27 查核時該頁把這組數字標為 Developer plan 基本限額，免費方案實際值以帳戶 Limits 頁為準。4000 輸出也不是保證能產生同等數量的可見摘要文字。

## 驗收項目

- 有內容：繁體中文摘要、來源時間與可點擊連結，沒有附原始對話 TXT。
- 有 topic：聚焦相關討論；無相關內容則明確回報。
- 無可用對話：不呼叫 LLM。
- 機密阻擋／超量：零模型請求；不默默截取部分訊息。
- 模型期間撤權：不交付摘要。
- 無效 Key、429、截斷或錯誤 JSON：明確失敗，沒有自動修復／重試。
- 長摘要：完整 TXT，傳送失敗也會關閉緩衝區。

## 已查核來源

- [Groq structured outputs](https://console.groq.com/docs/structured-outputs)：gpt-oss-20b strict JSON schema 支援與必填欄位限制。
- [Groq rate limits](https://console.groq.com/docs/rate-limits)：免費額度與 organization 共用限制。
- [LangChain ChatGroq](https://docs.langchain.com/oss/python/integrations/chat/groq)：Python 整合方式。
- [OpenAI tiktoken 模型對照](https://github.com/openai/tiktoken/blob/main/tiktoken/model.py)：gpt-oss 對應 o200k_harmony。

依賴版本由 `uv.lock` 固定；官方能力確認不等於本帳戶已完成真實 API 驗證。

## 查詢錯誤日誌

`summary_failed type=InspectionError` 現在包含固定 `reason`，以及成功解析後的 `guild_id`、`channel_id`；解析失敗時 ID 為 None。API 失敗另附 `stage`、`http_status`、`discord_code`，非 API 錯誤時這些欄位為 None。ID 用於核對本地 `config.toml` 的白名單，不記錄正文、topic、金鑰或原始輸入連結。

| reason | 意義 |
|---|---|
| `guild_not_allowed` | 伺服器未設定於白名單 |
| `channel_not_allowed` | 伺服器已設定，但頻道不在該伺服器白名單 |
| `invalid_channel_link` | 連結格式不支援或 ID 無效 |
| `invalid_hours` / `invalid_limit` / `missing_timezone` | 查詢參數不正確 |
| `unsupported_channel_type` | 非一般文字頻道 |
| `bot_permission_denied` / `requester_permission_denied` | 指定一方缺檢視或讀取歷史權限 |
| `message_limit_exceeded` | 時段內超過 500 則且未設 limit |
| `privacy_check_failed` | 整理階段的機密檢查未通過（阻擋或檢查失敗） |
| `discord_api_failed` | 查 stage、http_status、discord_code 定位 API 失敗 |
| `bot_not_ready` | Bot 尚未就緒 |
| `inspection_error` | 未分類的 InspectionError，供舊有呼叫相容 |

使用者仍收到原本的中文錯誤說明；LLM 的 SummaryError 日誌暫未加入細分類。修改白名單後須重新啟動 Bot 才會載入。
