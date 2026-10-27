# Discord 與免費模型平台限制

查核日期：2026-09-10。本文是官方文件研究，未建立帳戶、交換真實金鑰或執行模型／Discord 整合測試。價格與限額會變動；正式行為以 [第一版規格](../spec.md) 為準。

## 1. 共用免費模型候選

Groq 適合列為共用供應商的先行測試候選；不能僅憑每日請求數認定為最佳選擇。

| Groq 模型 ID | 官方狀態 | 上下文 tokens | 免費限額：RPM / RPD / TPM / TPD |
|---|---|---:|---|
| `openai/gpt-oss-20b` | Production | 131,072 | 30 / 1,000 / 8,000 / 200,000 |
| `openai/gpt-oss-120b` | Production | 131,072 | 30 / 1,000 / 8,000 / 200,000 |
| `qwen/qwen3.6-27b` | Preview | 131,072 | 30 / 1,000 / 8,000 / 200,000 |

模型狀態與上下文取自 [Groq Models](https://console.groq.com/docs/models)；免費方案限額取自 [Rate Limits](https://console.groq.com/docs/rate-limits)，不要誤用模型頁的 Developer Plan 數字。Groq 也列有 `qwen/qwen3.8-27b`，目前同屬 Preview；不擴大第一輪測試名單。Preview 可能短期下架。繁體中文品質、時間定位與格式穩定性尚未實測，表格不是品質排名。

Groq 限額以 organization 計算，先碰到任一門檻就受限。131k 上下文不表示免費方案能一次送入 131k tokens；分批要同時考慮 TPM、預留輸出與多使用者共用。使用回應的剩餘額度／重置標頭與 429 的 `retry-after`，避免只限制並行數。[Groq Rate Limits](https://console.groq.com/docs/rate-limits)

建議先測 GPT OSS 20B，再比較 120B 與 Qwen 候選；這是縮小驗證範圍的設計選擇。保持 Groq 免費方案，不自動升級，不使用需額外收費的工具系統。

## 2. OpenRouter 免費使用與資料處理

官方支援文件目前列未付款帳戶每日 50 次、每分鐘 20 次，失敗嘗試也可能計入每日限額。此限制屬帳戶使用，不是每綁定一把 key 就新增額度。FAQ 的網頁擷取缺失數值，因此本次數字以官方支援頁為引用；上線前再查帳戶實際限制。[OpenRouter Rate Limits](https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know)

建議使用管理員核准的明確免費模型 ID，送出前核對價格；不使用會變動選型的自動 router、不配置付費 model fallback。額外用 `provider.max_price` 的 `prompt`、`completion`、`request` 零價格上限防止非零單價路由；用 `only` 限定核准端點。`allow_fallbacks` 管供應商回退，不等於禁止付費模型。找不到符合條件端點時回報不可用，不放寬條件。這是依官方參數組合出的設計，仍須驗證零值在選定端點的實際行為。[Provider Selection](https://openrouter.ai/docs/guides/routing/provider-selection)

OpenRouter 本身預設不保存全文，opt-in logging／資料改善設定可改變此行為；仍保存使用 metadata。模型供應商是另一個資料接觸點，不能將平台預設描述成端到端不保存。[Data Collection](https://openrouter.ai/docs/guides/privacy/data-collection)

全伺服器統一外送政策的建議預設是 `data_collection: deny`；若要求零保存，另外使用 `zdr: true`。兩者不是同義詞；免費且符合條件的端點可能不存在。使用者自己的 key 也採相同政策，不為了成功而自動降低保護。[Provider Selection](https://openrouter.ai/docs/guides/routing/provider-selection)

Groq 預設推論不保存全文，但可靠性／濫用調查可能保存至多 30 天；所有客戶可在 Data Controls 開啟 ZDR。metadata 仍保留。共用帳戶啟用前要確認設定，不把「一般不保存」寫成無條件保證。[Groq Your Data](https://console.groq.com/docs/your-data)

## 3. OpenRouter 個人授權

[OAuth PKCE 官方指南](https://openrouter.ai/docs/guides/overview/auth/oauth#headless-apps-ssh-servers-containers) 明確支援 headless：省略 `callback_url`，提供必要的 `code_challenge`、`code_challenge_method=S256`，可帶 `key_label`；使用者從官方頁複製一次性 code，十分鐘內以原 `code_verifier` 呼叫 `POST /api/v1/auth/keys` 換成 key。

本專案的套用設計：`/settoken` 建立綁定 Discord user ID 的短期 session，verifier 只留記憶體；按鈕開啟私訊 Modal 收 code。不同使用者不能提交別人的 session；過期或重啟後重新開始。應用自行產生 challenge，不使用第三方線上產生器。Modal 只是收取 code，不是端到端加密通道；長效 key 不出現在 Discord，交換後才加密保存。

這與把供應商 key 交給 OpenRouter 的 BYOK 不是同一件事。`/removetoken` 刪除 Bot 本機授權不等於遠端撤銷；提供官方 key 設定連結讓使用者撤銷。官方支援以 key 的 SHA-256 雜湊產生設定頁連結。實作仍須用新帳戶驗證 headless UI、交換錯誤、可用免費模型與撤銷效果，不直接照 API schema 猜測頁面互動。[OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth)

## 4. Discord 接入與呈現

查閱官方 [User](https://docs.discord.com/developers/resources/user)、[Channel](https://docs.discord.com/developers/resources/channel)、[Message](https://docs.discord.com/developers/resources/message) 公開 Bot 介面，未找到其他使用者最後已讀位置，因此規格採時間範圍。這是對公開介面查核的結論，不聲稱官方有一段明文禁止或保證不存在任何非公開介面。

| 約束 | 官方事實與設計含義 |
|---|---|
| 私訊 slash command | 必須註冊 global command 並開啟 BOT_DM context；guild command 不出現在私訊。Bot 與使用者有共同伺服器可使用 global DM command。測試不能只做 guild sync。[Application Commands](https://docs.discord.com/developers/interactions/application-commands) |
| 互動期限 | 3 秒內初始回應／defer，interaction token 15 分鐘有效；長任務完成應走普通 Bot DM 發送，不只依賴舊 followup token。[Interactions](https://docs.discord.com/developers/interactions/receiving-and-responding) |
| 文字內容 | Message Content Intent 也影響 HTTP 取得的 content、附件等；一般 DM 例外不代表能讀其他伺服器完整歷史。Developer Portal 與程式 intents 要正確設定。[Gateway](https://docs.discord.com/developers/events/gateway#message-content-intent) |
| 歷史分頁 | Get Channel Messages 每次最多 100 則，可用 before/after 游標；500 則是產品上限，應讀至第 501 則以辨識超量，按截止時間排除新訊息。[Message](https://docs.discord.com/developers/resources/message#get-channel-messages) |
| 成員暱稱 | `Guild.fetch_member(id)` 是 HTTP 按 ID 取得成員的方式，不需要為每個查詢載入全部成員。使用當前可取得暱稱，不能宣稱還原發言當時暱稱。[discord.py Intents](https://discordpy.readthedocs.io/en/stable/intents.html#retrieving-members) |
| 模式與模型選單 | `discord.ui.View`、`Select`、`Button` 與 `Modal` 可組成操作流程；單個 Select 最多 25 項，超過需分頁／縮小清單。設定操作與取消按鈕必須檢查操作者，完成後停用。[discord.py Interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.ui.Select) |
| 卡片與檔案 | 一般訊息 2,000 字元，Embed description 4,096，單訊息 Embed 全部文字合計 6,000；3,500 為產品門檻。TXT 可作附件，輸出後由 Discord 保存，不算 Bot 本機摘要歷史。[Message](https://docs.discord.com/developers/resources/message#embed-limits) |

以上支持第一版使用 discord.py 的基本 View／Embed／檔案，不需要 Components V2。取消按鈕的 view 存活時間必須涵蓋任務期限；不能依賴 SDK 預設逾時。每次外送及交付前重新查核目標頻道權限，避免排隊期間權限改變；這是本專案的存取控制設計。

尚未驗證的必要整合點：global DM 指令實機可見、歷史文字與附件可讀、請求者權限正確、沒有成員快取時暱稱取得、超過 15 分鐘後仍可普通 DM 交付、取消競態、Embed／TXT 完整呈現。第二階段語音接收能力需另做研究，不能從 discord.py 能加入語音頻道推論已能錄音。
