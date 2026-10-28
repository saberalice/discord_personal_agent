# 12：Docker 打包（Windows＋NVIDIA GPU）

Status: ready-for-human（檔案完成，尚未實際 build；待 Owen 在目標機器驗收）
Blocked by: 09、11

Owen 2026-10-28 決定：目標為 Windows＋NVIDIA GPU，在目標機器上直接 build，不推映像檔到 registry。使用說明見 [Docker 指南](../docker.md)。

- [x] 單一映像檔包含 Python bot 與語音程式，啟動行為同 `python -m discord_personal_agent`（含 `--no-voice`、`--check-config`）。
- [x] Token 與 Groq Key 只由 `.env` 在執行時傳入；`.env`、`config.toml`、模型、錄音資料不進映像檔與 build context。
- [x] 模型放 volume，第一次啟動下載並校驗；錄音資料放 volume，附清理方式。
- [x] `docker compose stop` 送 SIGINT 並等待最多 5 分鐘，語音程式完成辨識後才結束。
- [x] llama.cpp 與本機相同固定版本及 SHA256；因 build 時無 GPU，改以 `GGML_NATIVE=OFF` 建置預設多架構。
- [x] `.gitattributes` 固定 LF，避免 Windows clone 破壞容器內腳本。
- [ ] 實機驗收（Owen）：目標機器 `docker compose up -d --build` 成功；`/summary`、`/join`→`/leave`、`/voicesummary` 正常；`docker compose stop` 後 TXT 尾段沒有遺失。
