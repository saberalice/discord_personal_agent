# 01：本地 Bot 與頻道時間範圍讀取

Status: ready-for-human
Blocked by: None

使用者已授權本次只完成切片 A，完成後 review 再繼續。透過 Miniconda 獨立環境在本地啟動 Bot，以私訊 `/summary` 回覆固定範圍的訊息數，不產生 LLM 摘要。

- [x] Conda 管 Python，uv 鎖定與安裝本切片依賴；提供離線設定檢查入口。
- [x] 私訊 global 指令接受頻道連結、1～24 小時及選填 topic（本切片明示未使用）。
- [x] 固定收到指令的時間，回覆起訖與時區。
- [x] 驗證允許清單、伺服器／頻道類型、Bot 與請求者閱讀權限。
- [x] 分頁讀取，空頻道、500 則、501 則以上皆有明確結果，不回傳正文。
- [x] 自動化驗證時間邊界、跨日、越權、分頁與錯誤；文件提供 Discord 實機驗證清單。

既有設計已授權的測試介面：SummaryApplication 對外查詢與 ChannelHistory adapter；設定解析屬啟動契約。使用可控時鐘與 Discord HTTP 替身，先測失敗再實作，不呼叫真實 Discord／LLM 作單元測試。

範圍外：LangChain 呼叫、遮罩、佇列、取消 UI、計次、個人金鑰、模型選單及部署服務。只增加避免同一人重複查詢的執行中保護及讀取期限；不提前實作完整排程。

實機登入需要 Owen 在本地提供 Bot Token、建立測試頻道允許清單與完成 Developer Portal 設定；不把未完成實機驗證標為成功。

## 交付狀態

程式與離線驗證已完成，待 Owen review；實機驗證另見 [本地開發清單](../local-development.md)，尚未登入 Discord。LangChain 尚未安裝或呼叫，待下一切片。

驗證紀錄（2026-09-11）：35 個 pytest 測試通過；Ruff lint／格式通過；實際啟用 Conda 後 `uv sync --locked --inexact` 成功；範例設定離線檢查成功；缺少 Token 時啟動明確退出。discord.py 載入語音支援模組時有 Python `audioop` 棄用警告，本切片不使用語音。

Forbidden 診斷更新：補上各 Discord HTTP 階段、status 與 code 的安全日誌和私訊錯誤；加入 4 個 403 回歸案例，45 個測試通過。2026-09-11 以本地憑證唯讀查核：伺服器可存取，三個設定頻道中兩個 Bot 有 View Channel／Read Message History，另一個在 fetch_channel 回傳 403／50001；需管理員調整該頻道存取權限。未發送訊息，未驗證完整摘要指令成功。
