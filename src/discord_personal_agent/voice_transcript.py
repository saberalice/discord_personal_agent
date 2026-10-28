"""Voice transcript TXT produced by the Rust voice program; no Discord SDK types."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

from .conversation import SourceMessage
from .inspection import InspectionCode, InspectionError

# The voice program writes file names and line clocks in fixed UTC+8 (spec §9.2).
TAIWAN = timezone(timedelta(hours=8))
FILENAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.txt")
LINE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\] (.+?): ([^\r]*)")
MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class TranscriptFile:
    filename: str
    data: bytes


@dataclass(frozen=True)
class TranscriptLink:
    guild_id: int
    channel_id: int
    message_id: int

    @classmethod
    def parse(cls, link: str) -> "TranscriptLink":
        match = re.fullmatch(
            r"https://discord(?:app)?\.com/channels/([1-9][0-9]*)/([1-9][0-9]*)/([1-9][0-9]*)",
            link.strip(),
        )
        if not match or any(int(x) >= 2**64 for x in match.groups()):
            raise InspectionError(
                "請提供逐字稿 TXT 那則訊息的連結（右鍵訊息 → 複製訊息連結）。",
                code=InspectionCode.INVALID_LINK,
            )
        return cls(*map(int, match.groups()))

    @property
    def url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"


def _invalid(message: str) -> InspectionError:
    return InspectionError(message, code=InspectionCode.INVALID_TRANSCRIPT)


def parse_transcript(filename: str, data: bytes, *, message_id: int) -> tuple[SourceMessage, ...]:
    """Each line becomes a source located at the transcript message, its only Discord link."""
    match = FILENAME.fullmatch(filename)
    if not match:
        raise _invalid("附件檔名不是語音逐字稿格式，未送出任何內容。")
    try:
        previous = datetime(*map(int, match.groups()), tzinfo=TAIWAN)
        text = data.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise _invalid("逐字稿日期或編碼無法解析，未送出任何內容。") from None
    messages = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line:
            continue
        parsed = LINE.fullmatch(line)
        if not parsed:
            # Skipping a line would silently drop speech; refuse instead. Never echo the line.
            raise _invalid(f"逐字稿第 {number} 行格式無法解析，未送出任何內容。")
        hour, minute, second, name, content = parsed.groups()
        try:
            clock = time(int(hour), int(minute), int(second))
        except ValueError:
            raise _invalid(f"逐字稿第 {number} 行時間無效，未送出任何內容。") from None
        moment = datetime.combine(previous.date(), clock, TAIWAN)
        # Lines are in speaking order, so an earlier clock means the recording passed midnight.
        if moment < previous:
            moment += timedelta(days=1)
        previous = moment
        messages.append(
            SourceMessage(message_id, 0, name, moment.astimezone(UTC), content, 0, False, None)
        )
    return tuple(messages)
