"""Explicit live smoke test using only built-in fictional dialogue; never reads Discord."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .config import load_settings
from .conversation import Conversation, SourceMessage
from .groq_gateway import GroqGateway
from .inspection import Query
from .privacy import PrivacyError
from .summary import SummaryError, build_summary_input, check_capacity, render_summary


async def run() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    settings = load_settings(Path(os.environ.get("BOT_CONFIG_PATH", "config.toml")))
    key = os.environ.get("SHARED_LLM_API_KEY", "").strip()
    if not key:
        raise SummaryError("請先在本地 .env 設定 SHARED_LLM_API_KEY；未發出 API 請求。")
    end = datetime(2026, 9, 11, 8, tzinfo=UTC)
    query = Query.create("https://discord.com/channels/111/222", received_at=end)
    messages = (
        SourceMessage(
            1,
            10,
            "Owen",
            end.replace(hour=7, minute=3),
            "提議星期五部署，但還沒確認。",
            0,
            False,
            None,
        ),
        SourceMessage(
            2,
            11,
            "Alex",
            end.replace(hour=7, minute=5),
            "測試未通過，先修好權限問題再決定日期。我負責檢查權限。",
            0,
            False,
            1,
        ),
    )
    request = build_summary_input(
        Conversation(111, 222, 2, messages),
        query,
        topic="",
        timezone=settings.timezone,
        privacy=settings.privacy,
    )
    estimated = check_capacity(request, settings.llm.max_input_tokens)
    print(f"僅送出內建虛構對話；估計輸入 {estimated} token。")
    gateway = GroqGateway(
        model="openai/gpt-oss-20b", max_output_tokens=settings.llm.max_output_tokens
    )
    async with asyncio.timeout(90):
        result = await gateway.generate(request, api_key=key)
    print(render_summary(result, request.sources, query, settings.timezone, settings.privacy))
    print("來源連結為虛構測試 ID，不是真實 Discord 對話。")


def main() -> None:
    try:
        asyncio.run(run())
    except (SummaryError, PrivacyError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except TimeoutError:
        raise SystemExit("模型請求逾時，未自動重試。") from None


if __name__ == "__main__":
    main()
