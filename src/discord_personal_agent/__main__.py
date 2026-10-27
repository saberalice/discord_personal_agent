"""Local entry point; configuration check never connects to Discord."""

import argparse
import logging
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

from .bot import PersonalAgent
from .config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Discord personal agent — slice A")
    parser.add_argument("--check-config", action="store_true", help="檢查設定，不連線")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        settings = load_settings(Path(os.environ.get("BOT_CONFIG_PATH", "config.toml")))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    if args.check_config:
        print(
            f"設定格式正確：{len(settings.allowed_channels)} 個伺服器，"
            f"時區 {settings.timezone.key}。"
        )
        print("未連線；Token、頻道是否存在及實際權限尚未驗證。")
        return
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        parser.exit(1, "請在本地 .env 或環境變數設定 DISCORD_BOT_TOKEN。\n")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        PersonalAgent(settings, api_key=os.environ.get("SHARED_LLM_API_KEY", "").strip()).run(
            token, log_handler=None
        )
    except discord.LoginFailure:
        parser.exit(1, "Bot 登入失敗，請在本地確認 Token。\n")
    except discord.PrivilegedIntentsRequired:
        parser.exit(1, "請在 Developer Portal 啟用 Message Content Intent 後重新啟動。\n")
    except (discord.HTTPException, OSError) as exc:
        parser.exit(1, f"Discord 啟動失敗（{type(exc).__name__}），請確認網路及應用程式設定。\n")


if __name__ == "__main__":
    main()
