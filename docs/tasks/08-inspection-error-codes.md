# 08：查詢錯誤的診斷日誌

Status: ready-for-review

需求：能從日誌區分白名單等 InspectionError，而不只知道例外類型。

設計：固定列舉錯誤代碼；區分伺服器／頻道白名單、參數、頻道類型、Bot／請求者權限、筆數超量、機密檢查及 Discord API。日誌附已解析的 guild_id/channel_id，API 失敗保留 stage/status/code。使用者回覆保持原本中文說明；不記錄原始連結、topic、正文、金鑰或供應商回覆。摘要模型錯誤暫維持現有類型日誌。

驗證沿用應用／Discord adapter／指令邊界，確認白名單零讀取、固定原因可辨識與原始輸入不進日誌。

已實作：inspection.py 定義列舉及例外診斷欄位，discord_adapter.py 指定權限／API 原因，bot.py 輸出固定欄位；中文回覆不變。完整離線回歸 118 項通過，另補強 adapter 診斷欄位斷言。
