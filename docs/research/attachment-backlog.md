# 圖片／PDF 構想（暫緩）

使用者目前決定只記錄，不實作。現有附件仍使用通用佔位符。

構想：從授權頻道下載選中的附件，限制單檔／任務總大小與並行數；呼叫模型前才 Base64 編碼，任務結束釋放資料。Base64 約增加三分之一大小，處理過程還可能存在 bytes／字串／JSON 多份副本。

LangChain 支援 text、image 與 file content blocks，圖片／PDF 的 Base64 應放在對應型別與 MIME 欄位，不是把字串直接拼進普通文字。模型端點仍須支援該輸入型別：[LangChain 訊息文件](https://docs.langchain.com/oss/python/langchain/messages#multimodal-content)。

PDF 可能走模型原生輸入或供應商解析器；需另確認解析費用及資料政策，不因模型免費就假定 PDF 解析免費：[OpenRouter PDF](https://openrouter.ai/docs/guides/overview/multimodal/pdfs)。

待決：格式／大小／頁數／解析度限制、免費端點相容性、圖片及 PDF 內機密檢查、超量處理、跨模態來源定位、記憶體峰值與清理。文字遮罩不能視為附件已遮罩。這些都不是現有功能。
