# Docker：在 Windows＋NVIDIA GPU 機器上執行

一個容器同時跑 Python bot 與語音程式（同一 Token，行為與 `python -m discord_personal_agent` 相同）。在目標機器上直接 build；模型與錄音資料放 Docker volume，Token 與 Groq Key 由 `.env` 在執行時傳入，不進映像檔。

> 狀態（2026-10-28）：檔案已寫好，尚未在任何機器上實際 build 或執行；開發機沒有安裝 Docker。第一次在目標機器 build 時請把錯誤訊息貼回來。

## 1. 一次性準備（Windows）

1. 安裝最新 NVIDIA 驅動。PowerShell 執行 `nvidia-smi`，右上角 `CUDA Version` 需 ≥ 12.9（映像檔預設 CUDA 12.9.1）。
2. PowerShell（系統管理員）：`wsl --install`，已安裝過則 `wsl --update`。
3. 安裝 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，設定中確認使用 WSL 2 backend。
4. 確認容器看得到 GPU（[官方說明](https://docs.docker.com/desktop/features/gpu/)）：
   ```powershell
   docker run --rm --gpus=all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi
   ```
   顯示 GPU 表格即可。
5. 安裝 [Git for Windows](https://git-scm.com/download/win)。倉庫的 `.gitattributes` 會讓檔案保持 LF 換行，容器內的腳本才能執行。

磁碟：建置映像檔（CUDA 開發映像檔、llama.cpp、Rust）約需 15～20 GB 暫存空間（未實測，依官方映像檔大小估計），模型 volume 另需約 5 GB。

## 2. 下載與設定

```powershell
git clone https://github.com/saberalice/discord_personal_agent.git
cd discord_personal_agent
```

把舊機器的 `.env`、`config.toml` 複製到這個資料夾，或從範本建立：

```powershell
copy config.example.toml config.toml
notepad config.toml
notepad .env
```

`.env` 內容（每行都要有 `=`）：

```dotenv
DISCORD_BOT_TOKEN=你的 Bot Token
SHARED_LLM_API_KEY=你的 Groq Key
```

`config.toml` 必須在啟動前存在；不存在時 Docker 會在原處建立同名的空資料夾，bot 會因設定無效而失敗（刪掉該資料夾、放入檔案即可）。

## 3. 建置與啟動

```powershell
docker compose up -d --build
docker compose logs -f
```

- 第一次 build 會編譯 llama.cpp 的多個 GPU 架構版本，需要一段時間。
- 第一次啟動會下載並校驗 Qwen3-ASR 與 Silero 模型（約 4.7 GB）到 `voice-models` volume，完成後才登入 Discord。之後只在模型清單更新或檔案缺少時重新校驗。下載校驗失敗時會刪除未完成的檔案並結束，自動重啟後重新下載該檔。
- 只想先檢查設定：`docker compose run --rm bot --check-config`（仍需 `.env` 與 GPU 設定才能建立容器）。
- 暫時只跑文字摘要：先 `docker compose stop`，再 `docker compose run --rm bot --no-voice`（前景執行、不下載模型，Ctrl+C 結束）。不先停止常駐服務會讓同一 Token 登入兩次。

同一個 Token 同時只能在一台機器上執行，啟動前先停掉舊機器的 bot。

## 4. 停止、更新與重新啟動

```powershell
docker compose stop          # 送 SIGINT：語音程式保存錄音尾段並完成辨識後結束（最多等 5 分鐘）
docker compose up -d         # 再次啟動
git pull; docker compose up -d --build   # 更新程式
```

`restart: unless-stopped`：Docker Desktop 或電腦重開後自動啟動，手動 `stop` 後則不會。換機時在舊機器執行 `docker compose down`，避免舊機器重開後又用同一 Token 上線。

最多等 5 分鐘只保證於 `docker compose stop`／`down`。直接關閉 Docker Desktop 或 Windows 時，Docker 的關機等待時間可能較短（未查證），要關機前請先 `docker compose stop`。不要用 `docker kill` 或在 Docker Desktop 強制刪除執行中的容器，那樣會丟失尚未辨識的錄音。

## 5. 資料與清理

| Volume | 內容 |
|---|---|
| `voice-models` | 模型（可隨時刪除，下次啟動重新下載） |
| `voice-data` | 錄音資料庫與 TXT，內容同 [語音逐字稿第 4 節](voice-transcript.md#4-本機資料與清理) |

以下操作前都先 `docker compose stop`，避免資料庫寫到一半。

```powershell
# 備份錄音資料到目前資料夾的 voice-data.tgz
docker compose run --rm -v "${PWD}:/backup" --entrypoint tar bot czf /backup/voice-data.tgz -C /app/voice/data .

# 刪除單一場次：進入容器 shell，在 /app 下執行語音逐字稿第 4 節的「刪除單一場次」指令
docker compose run --rm --entrypoint bash bot

docker compose down                                  # 刪除容器，保留 volume
docker compose down -v                               # 刪除容器與兩個 volume（模型與全部錄音資料）
docker volume ls                                     # 實際名稱為 <資料夾名>_voice-data 等
docker volume rm discord_personal_agent_voice-data   # 只刪除全部錄音資料
```

## 6. 與本機安裝的差異

- llama.cpp 使用與 `voice/scripts/build-engine.py` 相同的固定版本與 SHA256，但在 Dockerfile 內以 `GGML_NATIVE=OFF` 建置：`docker build` 時看不到 GPU，無法偵測本機架構，改編譯 llama.cpp 預設的多架構版本。語音程式其餘原始碼不變。
- 驅動不支援 CUDA 12.9 時，可改用較舊版本建置：`docker compose build --build-arg CUDA_VERSION=12.6.3`（RTX 50 系列需 CUDA 12.8 以上，不能用 12.6）。
- 容器內沒有 `.env` 檔，Token 只以環境變數傳入；啟動器仍會把 `SHARED_LLM_API_KEY` 以空值傳給語音程式。
