"""Discord-independent channel inspection use case."""

import re
from collections.abc import AsyncIterator, Mapping, Set
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .conversation import Conversation, SourceMessage
from .privacy import PrivacyError, PrivacyGuard


class InspectionCode(StrEnum):
    UNKNOWN = "inspection_error"
    INVALID_LINK = "invalid_channel_link"
    INVALID_HOURS = "invalid_hours"
    INVALID_LIMIT = "invalid_limit"
    MISSING_TIMEZONE = "missing_timezone"
    GUILD_NOT_ALLOWED = "guild_not_allowed"
    CHANNEL_NOT_ALLOWED = "channel_not_allowed"
    UNSUPPORTED_CHANNEL = "unsupported_channel_type"
    BOT_PERMISSION = "bot_permission_denied"
    REQUESTER_PERMISSION = "requester_permission_denied"
    MESSAGE_LIMIT = "message_limit_exceeded"
    PRIVACY_CHECK = "privacy_check_failed"
    DISCORD_API = "discord_api_failed"
    INVALID_TRANSCRIPT = "invalid_transcript"
    BOT_NOT_READY = "bot_not_ready"


class InspectionError(Exception):
    """User-facing message plus fixed, content-free diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        code: InspectionCode = InspectionCode.UNKNOWN,
        stage: str | None = None,
        http_status: int | None = None,
        discord_code: int | None = None,
    ):
        super().__init__(message)
        self.code = InspectionCode(code)
        self.stage = stage
        self.http_status = http_status
        self.discord_code = discord_code


@dataclass(frozen=True)
class Query:
    guild_id: int
    channel_id: int
    start: datetime | None
    end: datetime
    limit: int | None = None

    @classmethod
    def create(
        cls, link: str, *, hours: int | None = None, limit: int | None = None, received_at: datetime
    ) -> "Query":
        match = re.fullmatch(
            r"https://discord(?:app)?\.com/channels/([1-9][0-9]*)/([1-9][0-9]*)(?:/[1-9][0-9]*)?",
            link.strip(),
        )
        if not match or any(int(x) >= 2**64 for x in match.groups()):
            raise InspectionError(
                "請提供 Discord 伺服器文字頻道連結。", code=InspectionCode.INVALID_LINK
            )
        if hours is not None and (type(hours) is not int or not 1 <= hours <= 24):
            raise InspectionError("時間範圍須為 1～24 小時。", code=InspectionCode.INVALID_HOURS)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
            raise InspectionError("訊息數須為 1～500 則。", code=InspectionCode.INVALID_LIMIT)
        if received_at.utcoffset() is None:
            raise InspectionError("查詢時間缺少時區。", code=InspectionCode.MISSING_TIMEZONE)
        end = received_at.astimezone(UTC)
        start = end - timedelta(hours=hours) if hours is not None else None
        return cls(int(match[1]), int(match[2]), start, end, limit)

    @property
    def fetch_limit(self) -> int:
        if self.limit is not None:
            return self.limit
        return 501 if self.start is not None else 500


class ChannelHistory(Protocol):
    # voice_text also accepts a voice channel's built-in chat, where transcripts can be posted.
    async def authorize(
        self, query: Query, requester_id: int, *, voice_text: bool = False
    ) -> None: ...

    def timestamps_newest_first(self, query: Query) -> AsyncIterator[datetime]: ...

    def messages_newest_first(self, query: Query) -> AsyncIterator[SourceMessage]: ...


@dataclass(frozen=True)
class Inspection:
    count: int

    @property
    def overflow(self) -> bool:
        return self.count > 500


class SummaryApplication:
    def __init__(
        self,
        history: ChannelHistory,
        allowed_channels: Mapping[int, Set[int]],
        *,
        privacy: PrivacyGuard | None = None,
    ):
        self.history = history
        self.allowed_channels = allowed_channels
        self.privacy = privacy if privacy is not None else PrivacyGuard()

    async def authorize(self, query: Query, *, requester_id: int, voice_text: bool = False) -> None:
        """Allowlist first, so channels outside it are never touched on Discord."""
        if query.guild_id not in self.allowed_channels:
            raise InspectionError(
                "此頻道不在管理員允許的測試範圍內。", code=InspectionCode.GUILD_NOT_ALLOWED
            )
        if query.channel_id not in self.allowed_channels[query.guild_id]:
            raise InspectionError(
                "此頻道不在管理員允許的測試範圍內。", code=InspectionCode.CHANNEL_NOT_ALLOWED
            )
        await self.history.authorize(query, requester_id, voice_text=voice_text)

    async def inspect(self, query: Query, *, requester_id: int) -> Inspection:
        await self.authorize(query, requester_id=requester_id)
        count = 0
        async for stamp in self.history.timestamps_newest_first(query):
            if query.start is not None and stamp < query.start:
                break
            if stamp < query.end:
                count += 1
            if count >= query.fetch_limit:
                break
        # Recheck after I/O so revoked access cannot return an inspection result.
        await self.history.authorize(query, requester_id)
        return Inspection(count)

    async def prepare(self, query: Query, *, requester_id: int, topic: str = "") -> Conversation:
        await self.authorize(query, requester_id=requester_id)
        selected = []
        async for message in self.history.messages_newest_first(query):
            if query.start is not None and message.created_at < query.start:
                break
            if message.created_at < query.end:
                selected.append(message)
            if len(selected) >= query.fetch_limit:
                break
        if len(selected) > 500:
            raise InspectionError(
                "範圍內至少 501 則，請縮短時間或設定 limit。", code=InspectionCode.MESSAGE_LIMIT
            )
        await self.history.authorize(query, requester_id)
        system_count = sum(m.is_system for m in selected)
        empty_count = sum(
            not m.is_system and not m.content and not m.attachment_count for m in selected
        )
        usable = [m for m in selected if not m.is_system and (m.content or m.attachment_count)]
        conversation = Conversation(
            query.guild_id,
            query.channel_id,
            len(selected),
            tuple(sorted(usable, key=lambda m: (m.created_at, m.id))),
            system_count,
            empty_count,
        )

        try:
            return self.privacy.sanitize(conversation, topic=topic).conversation
        except PrivacyError as exc:
            raise InspectionError(str(exc), code=InspectionCode.PRIVACY_CHECK) from exc
