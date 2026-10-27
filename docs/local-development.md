# 本地啟動與驗收（切片 A）

> 新切片入口：[單批 AI 摘要驗收](single-batch-summary.md)。下方早期的「尚未接 LLM／回傳原文 TXT」驗收敘述屬歷史切片；目前 `/summary` 已改為 AI 摘要。

2026-09-11：已實作頻道讀取驗證、自動化測試與啟動入口。**尚未用真實 Token 登入 Discord，以下實機清單待 Owen 操作。**

此版 `/summary` 回覆選取條件和來源筆數；只有填 hours、未填 limit 的完整時段模式，超過 500 則才要求縮短時間。不呼叫 LLM、不保存訊息、不套用 topic。LangChain 接入保留在下一切片。

## 1. Miniconda 與 uv

本機已建立 `/home/owen/miniconda3/envs/discord-personal-agent`，安裝 Python 3.12 和鎖定依賴。直接使用：

```bash
source /home/owen/miniconda3/etc/profile.d/conda.sh
conda activate discord-personal-agent
cd /home/owen/code/discord_personal_agent
python --version
```

換機時才需要先建立環境（不要重複建立）：

```bash
conda create --name discord-personal-agent --override-channels --channel conda-forge python=3.12 pip --yes
conda activate discord-personal-agent
```

本機 `uv` 位於 `/home/owen/.local/bin/uv`。安裝／更新依賴前，先確認 `echo "$CONDA_DEFAULT_ENV"` 是 `discord-personal-agent`，再執行：

```bash
UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX" uv sync --locked --inexact
```

Conda 管 Python；uv 依 `uv.lock` 安裝專案和測試依賴到同一個環境，不額外建立 `.venv`。`--inexact` 保留 Conda 環境中非專案套件；不要把目標指向 base。這個做法已在本機執行驗證，設定依據見 [uv 官方文件](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)。

## 2. Discord 後台設定

1. 在 [Developer Portal](https://discord.com/developers/applications) 建立專用測試 Application。
2. Bot 頁面產生 Token，只在本地保存。開啟 **Message Content Intent**；不需開 Server Members 或 Presence Intent。
3. Installation 設定 **Guild Install**，scopes 使用 `bot`、`applications.commands`。Bot 權限勾 **View Channels**、**Read Message History**；本版回覆私訊，不需要伺服器 Administrator 或 Send Messages 權限。
4. 使用安裝連結，由管理員加入測試伺服器。確認測試頻道的 permission overwrites 沒有覆蓋掉上述權限；請求者也要有這兩項權限。
5. 本程式使用 Gateway，**Interactions Endpoint URL 留空**，不需要開本機 HTTP port 或 ngrok。不要與其他程式共用同一 Application；啟動時會同步該 Application 的 global 指令。

後台欄位與安裝依據：[Discord 官方入門](https://docs.discord.com/developers/quick-start/getting-started)。Message Content 控制一般訊息內容的存取，包含 HTTP 回應；目前計數雖不使用正文，仍提前驗證下一切片需要的 intent：[Gateway 官方文件](https://docs.discord.com/developers/events/gateway#message-content-intent)。

## 3. 本地設定

在專案根目錄建立 `.env`，以編輯器填入：

```dotenv
DISCORD_BOT_TOKEN=在這裡填入你自己的BotToken
BOT_CONFIG_PATH=config.toml
```

`.env.example` 依專案規則只列變數名稱，不能直接複製後當成完整設定。環境中已存在的變數優先於 `.env`；設定路徑相對於執行時的目錄。

複製設定範本（第一次執行）：

```bash
cp config.example.toml config.toml
chmod 600 .env config.toml
```

編輯 `config.toml`，把範例 ID 換成你的實際伺服器／頻道 ID。頻道複製連結的格式是 `https://discord.com/channels/伺服器ID/頻道ID`；同時接受 `discord.com` 與 `discordapp.com`；也接受訊息連結，但只取其中的頻道，時間仍由 `hours` 決定。

```toml
timezone = "Asia/Taipei"

[allowed_channels]
"111111111111111111" = ["222222222222222222", "333333333333333333"]
"444444444444444444" = ["555555555555555555"]
```

上面 ID 全是範例。每個伺服器可以設定多個頻道；空清單表示不開放任何頻道。時區可改為其他 IANA 名稱，例如 `Europe/London`，修改設定後重新啟動。允許清單不會繞過 Discord 使用者權限。

Token 不貼到聊天或 review 內容；`.env`、實際 `config.toml` 已列入 `.gitignore`。

## 4. 離線測試與啟動

```bash
python -m pytest -q
ruff check src tests
ruff format --check src tests
python -m discord_personal_agent --check-config
python -m discord_personal_agent
```

尚未填實際設定時，也可先做純格式檢查：

```bash
BOT_CONFIG_PATH=config.example.toml python -m discord_personal_agent --check-config
```

`--check-config` 不連網，也不驗證 Token／ID 真實性。正常啟動後應看到 `global_commands_synced count=2`、`bot_ready`。按 Ctrl+C 停止；這次沒有安裝常駐服務。

開啟 Bot 的一對一私訊，輸入 `/help`，再選 `/summary`：

```text
/summary channel:https://discord.com/channels/你的伺服器ID/你的頻道ID hours:1
```

`hours` 與 `limit` 現在都可省略：

| 參數 | 行為 |
|---|---|
| 兩者都不填 | 最新最多 500 則，不限制起始時間 |
| 只填 `hours:1` | 最近 1 小時；超過 500 則要求縮短 |
| 只填 `limit:20` | 最新最多 20 則 |
| `hours:1 limit:20` | 最近 1 小時內最新最多 20 則 |

hours 為 1～24，limit 為 1～500；所有查詢以收到指令的時間為固定截止，不含該時刻。最新 N 則模式是有意選取部分歷史，回覆會明示此條件。更新程式後需重新啟動 Bot，讓 global 指令重新同步。

指令同步為 global DM 指令。剛同步未顯示時，確認日誌同步成功、Bot 與你同在測試伺服器，重新開啟 Discord／私訊後重試。允許此伺服器成員的私訊，才能收到普通 DM 結果。若出現 4014，檢查 Message Content Intent；登入失敗則檢查本地 Token。

## 5. 驗收清單

自動化已覆蓋：時間跨日、`[start,end)`、無效連結與時數、0／500／501 筆、允許清單、頻道類型、伺服器不符、使用者與 Bot 權限覆寫、讀取後撤權、SDK 每頁最多 100 則、DM 指令註冊及交付流程。HTTP 皆為替身，未存取你的訊息。

實機請在明確知情的測試頻道執行並記錄結果：

| 情境 | 預期 | 狀態 |
|---|---|---|
| 啟動及 `/help` | Bot ready，DM 顯示使用說明 | 待驗證 |
| 允許頻道有數則已知訊息 | 回覆固定起訖與正確筆數，無訊息正文 | 待驗證 |
| 範圍內沒有訊息 | 顯示 0 則 | 待驗證 |
| 測試頻道跨頁歷史 | 筆數正確，500 則可接受、501 則要求縮短 | 待驗證 |
| 查非允許頻道 | 拒絕讀取 | 待驗證 |
| 普通成員無權看、Bot 有權看 | 拒絕讀取；不要用管理員帳號測此項 | 待驗證 |
| 移除 Bot 的 Read Message History | 權限錯誤，不回傳資料 | 待驗證 |
| 時區修改後重啟 | 同一時刻以新時區顯示 | 待驗證 |

500／501 邊界已有離線測試，不必為基本啟動驗證大量洗頻；實機分頁可選擇已有足夠訊息、參與者知情的測試頻道。筆數包含 Bot、附件及其他頻道訊息；不是個人真正未讀數。歷史讀取並非交易快照，查詢中被刪除的訊息不保證納入。

目前沒有完整佇列／取消／每日額度，只限制每人同時一筆，Discord 查詢及結果投遞合計 60 秒；超時或權限錯誤會清除執行中狀態。大規模多人壓力測試留到切片 D。

## 6. Review 範圍

- `inspection.py`：與 Discord SDK 分離的時間範圍、授權流程與筆數上限。
- `discord_adapter.py`：每次查詢獨立 adapter，重新取得角色／成員／頻道覆寫，SDK 分頁。
- `bot.py`：DM 指令、回覆與同使用者執行中保護。
- `config.py`、`__main__.py`：設定驗證、啟動、離線檢查。
- `tests/`、`pyproject.toml`、`uv.lock`：測試和可重現依賴。

本切片完成後停下 review；通過後才接遮罩與 LangChain ChunkSummary，不變更既定 LangChain 架構。
