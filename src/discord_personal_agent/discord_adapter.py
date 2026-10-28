"""SDK access scoped to one request; SDK data is converted to domain records."""

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime

import discord

from .conversation import SourceMessage
from .inspection import InspectionCode, InspectionError, Query
from .voice_transcript import FILENAME, MAX_BYTES, TranscriptFile

logger = logging.getLogger(__name__)


@contextmanager
def discord_operation(stage: str, label: str) -> Iterator[None]:
    """Expose only operation and numeric error metadata, never response bodies."""
    try:
        yield
    except discord.HTTPException as exc:
        logger.warning(
            "discord_request_failed stage=%s type=%s status=%d code=%d",
            stage,
            type(exc).__name__,
            exc.status,
            exc.code,
        )
        raise InspectionError(
            f"Discord「{label}」失敗（HTTP {exc.status}，錯誤碼 {exc.code}）。"
            "請將此階段與錯誤碼提供給 Bot 管理員。",
            code=InspectionCode.DISCORD_API,
            stage=stage,
            http_status=exc.status,
            discord_code=exc.code,
        ) from exc


class DiscordHistory:
    def __init__(self, client: discord.Client, bot_id: int):
        self.client = client
        self.bot_id = bot_id
        self.channel: discord.TextChannel | discord.VoiceChannel | None = None

    async def authorize(self, query: Query, requester_id: int, *, voice_text: bool = False) -> None:
        # Fetch roles, overwrites and both members afresh; no privileged member cache needed.
        with discord_operation("fetch_guild", "讀取伺服器"):
            guild = await self.client.fetch_guild(query.guild_id)
        with discord_operation("fetch_channel", "讀取頻道"):
            channel = await guild.fetch_channel(query.channel_id)
        text_channel = (
            isinstance(channel, discord.TextChannel) and channel.type == discord.ChannelType.text
        )
        voice_chat = voice_text and isinstance(channel, discord.VoiceChannel)
        if not (text_channel or voice_chat):
            raise InspectionError(
                "目前只支援一般伺服器文字頻道，不支援討論串或其他頻道。",
                code=InspectionCode.UNSUPPORTED_CHANNEL,
            )
        for user_id in (self.bot_id, requester_id):
            is_bot = user_id == self.bot_id
            with discord_operation(
                "fetch_bot_member" if is_bot else "fetch_requester_member",
                "查核 Bot 成員" if is_bot else "查核請求者成員",
            ):
                member = await guild.fetch_member(user_id)
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.read_message_history:
                raise InspectionError(
                    "你或 Bot 缺少檢視頻道／讀取訊息歷史權限。",
                    code=InspectionCode.BOT_PERMISSION
                    if is_bot
                    else InspectionCode.REQUESTER_PERMISSION,
                )
        self.channel = channel

    async def timestamps_newest_first(self, query: Query) -> AsyncIterator[datetime]:
        if self.channel is None:
            raise RuntimeError("History must be authorized before reading")
        # No `after=datetime`: SDK rounds it to the highest snowflake in that millisecond,
        # which would exclude messages exactly at the inclusive start boundary.
        with discord_operation("read_history", "讀取訊息歷史"):
            async for message in self.channel.history(
                limit=query.fetch_limit, before=query.end, oldest_first=False
            ):
                yield message.created_at

    async def messages_newest_first(self, query: Query) -> AsyncIterator[SourceMessage]:
        if self.channel is None:
            raise RuntimeError("History must be authorized before reading")
        names: dict[int, str] = {}
        with discord_operation("read_history", "讀取訊息歷史"):
            async for message in self.channel.history(
                limit=query.fetch_limit, before=query.end, oldest_first=False
            ):
                if query.start is not None and message.created_at < query.start:
                    break
                system = message.is_system()
                author = message.author
                if (
                    not system
                    and (message.content or message.attachments)
                    and author.id not in names
                ):
                    with discord_operation("resolve_author", "取得發言者暱稱"):
                        try:
                            member = await self.channel.guild.fetch_member(author.id)
                            names[author.id] = member.display_name
                        except discord.NotFound as exc:
                            if exc.code != 10007:
                                raise
                            # Departed members and webhook authors have no current guild nickname.
                            names[author.id] = author.global_name or author.name
                yield SourceMessage(
                    message.id,
                    author.id,
                    names.get(author.id, author.global_name or author.name),
                    message.created_at,
                    message.content,
                    len(message.attachments),
                    system,
                    message.reference.message_id if message.reference else None,
                )

    async def transcript(self, message_id: int) -> TranscriptFile:
        """Download the voice program's TXT; nothing else in the channel is ever read."""
        if self.channel is None:
            raise RuntimeError("History must be authorized before reading")
        with discord_operation("fetch_transcript", "讀取逐字稿訊息"):
            message = await self.channel.fetch_message(message_id)
        if message.author.id != self.bot_id:
            raise InspectionError(
                "只能摘要本 Bot 貼出的語音逐字稿。", code=InspectionCode.INVALID_TRANSCRIPT
            )
        files = [item for item in message.attachments if FILENAME.fullmatch(item.filename)]
        if len(files) != 1:
            raise InspectionError(
                "這則訊息沒有語音逐字稿 TXT 附件。", code=InspectionCode.INVALID_TRANSCRIPT
            )
        if files[0].size > MAX_BYTES:
            raise InspectionError(
                "逐字稿超過 8 MiB 上限，未下載。", code=InspectionCode.INVALID_TRANSCRIPT
            )
        with discord_operation("download_transcript", "下載逐字稿"):
            data = await files[0].read()
        return TranscriptFile(files[0].filename, data)
