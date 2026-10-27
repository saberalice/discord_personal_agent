"""Source data and local transcript rendering, independent of Discord and LLM SDKs."""

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SourceMessage:
    id: int
    author_id: int
    display_name: str
    created_at: datetime
    content: str
    attachment_count: int
    is_system: bool
    reply_to_id: int | None


@dataclass(frozen=True)
class Conversation:
    guild_id: int
    channel_id: int
    selected_count: int
    messages: tuple[SourceMessage, ...]
    system_count: int = 0
    empty_count: int = 0

    def source_url(self, message_id: int) -> str:
        if message_id not in {message.id for message in self.messages}:
            raise ValueError("Unknown source message")
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{message_id}"

    def render(self, timezone: ZoneInfo) -> str:
        lines = [f"時區：{timezone.key}"]
        previous_date = None
        for message in self.messages:
            local = message.created_at.astimezone(timezone)
            date = local.date()
            if date != previous_date:
                lines.append(f"日期：{date}")
                previous_date = date
            name = json.dumps(message.display_name, ensure_ascii=False)[1:-1]
            content = json.dumps(message.content, ensure_ascii=False)[1:-1]
            placeholders = " ".join(["[附件：未讀取內容]"] * message.attachment_count)
            body = content + (" " if content and placeholders else "") + placeholders
            lines.append(f"{name} ({local:%H:%M}) : {body}")
        return "\n".join(lines) + "\n"
