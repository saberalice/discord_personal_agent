# 05：本機機密遮罩與阻擋

Status: ready-for-human
Blocked by: None

回到文字摘要原計畫，先實作外送前本機規則，不接 LLM。

- 內建常見格式遮罩，加上管理員自訂 literal／regex 規則，動作 mask 或 block。
- 處理整批可用對話正文、暱稱及 topic，block 優先，命中或檢測失敗不交付部分 TXT。
- 設定全伺服器共用；無效規則啟動失敗。自訂 regex 有處理期限，避免卡住。
- 遮罩完整取代命中值，不保存還原表；錯誤與日誌不含原始命中值。
- Discord TXT 與本地 preview 都套用相同規則；既有 config 不需覆寫即可啟用內建規則。
- 離線驗證虛構敏感資料，完成後停下 review；不宣稱覆蓋全部機密。

不實作：圖片／PDF 下載、Base64、多模態與 LLM。

完成：新增 privacy 模組，設定契約、SummaryApplication、Discord TXT 與本地 preview 共用規則；93 個測試通過。regex 已鎖定並安裝到既有專案 Conda 環境。未修改本地實際頻道清單、未發送真實訊息、未呼叫 LLM。
