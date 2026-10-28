"""Explicit local-only export for reviewing conversation preparation, after local privacy rules."""

import argparse
import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

from .config import load_settings
from .conversation import Conversation, SourceMessage
from .discord_adapter import DiscordHistory
from .inspection import InspectionError, Query, SummaryApplication


class DemoHistory:
    async def authorize(self, query: Query, requester_id: int, *, voice_text=False) -> None:
        pass

    async def messages_newest_first(self, query: Query):
        yield SourceMessage(
            4, 999, "系統", datetime(2026, 9, 11, 7, 5, tzinfo=UTC), "", 0, True, None
        )
        yield SourceMessage(
            3, 456, "Alex", datetime(2026, 9, 11, 7, 4, tzinfo=UTC), "", 1, False, 2
        )
        yield SourceMessage(
            2,
            123,
            "Owen",
            datetime(2026, 9, 11, 7, 3, tzinfo=UTC),
            "今天討論部署方式",
            0,
            False,
            None,
        )
        yield SourceMessage(
            1,
            456,
            "Alex",
            datetime(2026, 9, 10, 15, 59, tzinfo=UTC),
            "參考 https://example.com\n明天再討論",
            0,
            False,
            None,
        )


def save_preview(result: Conversation, query: Query, timezone: ZoneInfo) -> Path:
    root = Path("local-exports")
    root.mkdir(mode=0o700, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="preview-", dir=root))
    messages = []
    for message in result.messages:
        item = asdict(message)
        item.update(
            id=str(message.id),
            author_id=str(message.author_id),
            created_at=message.created_at.isoformat(),
            reply_to_id=str(message.reply_to_id) if message.reply_to_id else None,
            source_url=result.source_url(message.id),
        )
        messages.append(item)
    data = {
        "notice": "本地預覽，已套用本機機密規則（不保證涵蓋全部機密），未送出至 LLM。",
        "guild_id": str(query.guild_id),
        "channel_id": str(query.channel_id),
        "start": query.start.isoformat() if query.start else None,
        "end": query.end.isoformat(),
        "limit": query.limit,
        "timezone": timezone.key,
        "selected_count": result.selected_count,
        "system_count": result.system_count,
        "empty_count": result.empty_count,
        "usable_count": len(result.messages),
        "messages": messages,
    }
    for name, content in (
        ("prompt-preview.txt", result.render(timezone)),
        ("sources.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n"),
    ):
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    return folder.resolve()


async def run_preview(args: argparse.Namespace) -> tuple[Conversation, Query, ZoneInfo]:
    if args.demo:
        query = Query.create(
            "https://discord.com/channels/111/222",
            hours=args.hours,
            limit=args.limit,
            received_at=datetime(2026, 9, 11, 8, tzinfo=UTC),
        )
        result = await SummaryApplication(DemoHistory(), {111: {222}}).prepare(
            query, requester_id=123
        )
        return result, query, ZoneInfo("Asia/Taipei")
    load_dotenv(Path.cwd() / ".env", override=False)
    settings = load_settings(Path(os.environ.get("BOT_CONFIG_PATH", "config.toml")))
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise InspectionError("請在本地設定 DISCORD_BOT_TOKEN。")
    query = Query.create(
        args.channel, hours=args.hours, limit=args.limit, received_at=datetime.now(UTC)
    )
    intents = discord.Intents.none()
    intents.guilds = True
    intents.message_content = True
    async with discord.Client(intents=intents) as client:
        # REST only: do not sync commands or start another Gateway connection.
        await client.login(token)
        result = await SummaryApplication(
            DiscordHistory(client, client.user.id),
            settings.allowed_channels,
            privacy=settings.privacy,
        ).prepare(query, requester_id=args.requester_id)
    return result, query, settings.timezone


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="本地對話預覽；已套用本機機密規則（不保證涵蓋全部機密），不呼叫 LLM"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="使用虛構資料，不需 Token 或連線")
    source.add_argument("--channel", help="Discord 文字頻道連結")
    parser.add_argument("--requester-id", type=int, help="請求者 Discord 使用者 ID，供權限檢查")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if not args.demo and (args.requester_id is None or not 0 < args.requester_id < 2**64):
        parser.error("真實頻道預覽必須提供有效的 --requester-id。")
    logging.basicConfig(level=logging.WARNING)

    async def bounded():
        async with asyncio.timeout(60):
            return await run_preview(args)

    try:
        result, query, timezone = asyncio.run(bounded())
        folder = save_preview(result, query, timezone)
    except (InspectionError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    except TimeoutError:
        parser.exit(1, "預覽逾時，請減少筆數後重試。\n")
    except (discord.HTTPException, discord.InvalidData, OSError) as exc:
        parser.exit(1, f"預覽失敗：{type(exc).__name__}；未回報敏感內容。\n")
    print(f"本地預覽：{folder}")
    print(
        f"原始 {result.selected_count} 則；系統 {result.system_count} 則；"
        f"空內容 {result.empty_count} 則；可用 {len(result.messages)} 則。"
    )
    if result.empty_count:
        print("部分非系統訊息沒有正文或附件；可能僅含其他媒體或正文存取受限，請核對來源與 Intent。")
    print("已套用本機機密規則（不保證涵蓋全部機密），未呼叫 LLM。")


if __name__ == "__main__":
    main()
