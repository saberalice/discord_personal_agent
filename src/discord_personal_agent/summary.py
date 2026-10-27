"""Provider-independent single-batch prompt and result contract."""

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .conversation import Conversation, SourceMessage
from .inspection import Query
from .privacy import PrivacyGuard


@dataclass(frozen=True)
class SummaryInput:
    messages: tuple[SystemMessage, HumanMessage]
    sources: dict[str, SourceMessage]
    topic: str


def build_summary_input(
    conversation: Conversation,
    query: Query,
    *,
    topic: str,
    timezone: ZoneInfo,
    privacy: PrivacyGuard,
) -> SummaryInput:
    sanitized = privacy.sanitize(conversation, topic=topic)
    sources = {
        f"m{index:03d}": message for index, message in enumerate(sanitized.conversation.messages, 1)
    }
    lines = []
    for source_id, message in sources.items():
        # Keep the same transcript format, with a stable short reference per source.
        single = Conversation(0, 0, 1, (message,))
        lines.append({"source_id": source_id, "transcript": single.render(timezone)})
    payload = {
        "selection": {
            "start_inclusive": query.start.isoformat() if query.start else None,
            "end_exclusive": query.end.isoformat(),
            "latest_limit": query.limit
            if query.limit is not None
            else (500 if query.start is None else None),
            "selected_count": conversation.selected_count,
            "usable_count": len(sources),
        },
        "topic": sanitized.topic,
        "records": lines,
    }
    system = (
        files("discord_personal_agent").joinpath("prompts/summary_system.txt").read_text("utf-8")
    )
    return SummaryInput(
        (
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ),
        sources,
        sanitized.topic,
    )


class SummaryError(Exception):
    """Safe summary failure without the model response attached."""


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class SummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok", "no_relevant_topic"]
    segments: list[Segment]

    @model_validator(mode="after")
    def consistent_status(self) -> "SummaryResult":
        if (self.status == "ok") != bool(self.segments):
            raise ValueError("Inconsistent summary status")
        return self


def validate_summary(raw: str, request: SummaryInput) -> SummaryResult:
    try:
        result = SummaryResult.model_validate_json(raw)
    except ValidationError:
        raise SummaryError("模型回覆不符合摘要格式，未交付結果。") from None
    if result.status == "no_relevant_topic" and not request.topic.strip():
        raise SummaryError("模型未依未指定主題的整理規則回覆。")
    for segment in result.segments:
        if any(source_id not in request.sources for source_id in segment.source_ids):
            raise SummaryError("模型引用了不存在的訊息來源，未交付結果。")
    return result


def check_capacity(request: SummaryInput, limit: int) -> int:
    """Local estimate including schema and a conservative chat framing allowance."""
    import tiktoken

    encoding = tiktoken.get_encoding("o200k_harmony")
    text = "\n".join(str(message.content) for message in request.messages)
    text += json.dumps(SummaryResult.model_json_schema(), ensure_ascii=False)
    count = len(encoding.encode_ordinary(text)) + 256
    if count > limit:
        raise SummaryError(
            f"估計輸入 {count} token，超過 {limit} 上限；請減少 limit 或縮短時間。未截斷內容。"
        )
    return count


def render_summary(
    result: SummaryResult,
    request: SummaryInput,
    query: Query,
    timezone: ZoneInfo,
    privacy: PrivacyGuard,
) -> str:
    end = query.end.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")
    start = (
        query.start.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")
        if query.start
        else "不限起始"
    )
    selection = f"範圍：{start} ～ {end}（{timezone.key}；不含截止時間）"
    if query.limit is not None or query.start is None:
        selection += f"；最新最多 {query.limit or 500} 則"
    lines = [
        f"頻道：https://discord.com/channels/{query.guild_id}/{query.channel_id}",
        selection,
        f"AI 摘要｜可用來源 {len(request.sources)} 則｜模型 openai/gpt-oss-20b",
        "已套用本機機密規則；摘要可能有誤，請核對來源。",
    ]
    if result.status == "no_relevant_topic":
        lines.append("所選訊息中沒有與指定主題相關的討論。")
    ordered = sorted(
        result.segments,
        key=lambda segment: min(request.sources[key].created_at for key in segment.source_ids),
    )
    for segment in ordered:
        sources = sorted(
            {key: request.sources[key] for key in segment.source_ids}.values(),
            key=lambda source: (source.created_at, source.id),
        )
        first = sources[0].created_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
        last = sources[-1].created_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
        # Check the whole generated segment before adding trusted source URLs.
        safe = privacy.sanitize(
            Conversation(0, 0, 0, ()), topic=segment.title + "\n" + segment.summary
        ).topic
        lines.extend(["", f"{first} ～ {last}", safe])
        lines.append(
            f"[查看起點](https://discord.com/channels/"
            f"{query.guild_id}/{query.channel_id}/{sources[0].id})"
        )
    return "\n".join(lines)
