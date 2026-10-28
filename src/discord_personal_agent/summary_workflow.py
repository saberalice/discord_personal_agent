"""Batch planning, merging and the per-job request budget; no Discord or provider SDK types."""

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from .conversation import Conversation, SourceMessage
from .inspection import Query
from .privacy import PrivacyGuard
from .summary import (
    RateLimited,
    SummaryError,
    SummaryInput,
    SummaryResult,
    check_capacity,
    load_prompt,
    selection_payload,
    source_record,
)

logger = logging.getLogger(__name__)
MAX_CALLS = 12
JOB_SECONDS = 600
REQUEST_SECONDS = 90
# Same chat-framing allowance check_capacity adds; planning must not promise more room.
FRAMING_TOKENS = 256

Notify = Callable[[str], Awaitable[None]]
Partial = tuple[dict[str, SourceMessage], SummaryResult]


class LlmGateway(Protocol):
    async def generate(self, request: SummaryInput, *, api_key: str) -> SummaryResult: ...


@dataclass
class _Budget:
    deadline: float
    calls: int = 0


class SummaryWorkflow:
    def __init__(
        self,
        gateway: LlmGateway,
        *,
        max_input_tokens: int,
        timezone: ZoneInfo,
        privacy: PrivacyGuard,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.gateway = gateway
        self.max_input_tokens = max_input_tokens
        self.timezone = timezone
        self.privacy = privacy
        self.clock = clock
        self.sleep = sleep

    async def summarize(
        self,
        conversation: Conversation,
        query: Query,
        *,
        topic: str,
        api_key: str,
        deadline: float,
        notify: Notify,
    ) -> tuple[SummaryResult, dict[str, SourceMessage]]:
        sanitized = self.privacy.sanitize(conversation, topic=topic)
        sources = {
            f"m{index:03d}": message
            for index, message in enumerate(sanitized.conversation.messages, 1)
        }
        context = {
            "selection": selection_payload(query, conversation, len(sources)),
            "topic": sanitized.topic,
        }
        # Every batch is built and measured before the first request, so an impossible plan
        # sends nothing.
        requests = await asyncio.to_thread(self._chunk_requests, sources, context)
        planned = len(requests) + (len(requests) > 1)
        if planned > MAX_CALLS:
            raise SummaryError(
                f"需要至少 {planned} 次模型呼叫，超過每次 {MAX_CALLS} 次上限；"
                "請減少 limit 或縮短時間。未送出任何內容。"
            )
        budget = _Budget(deadline)
        started = self.clock()
        partials: list[Partial] = []
        for number, request in enumerate(requests, 1):
            label = f"第 {number}/{len(requests)} 批" if len(requests) > 1 else "本次請求"
            result = await self._generate(request, api_key, budget, notify, label)
            if result.status == "ok":
                partials.append((request.sources, result))
        result = (
            await self._merge(partials, context, api_key, budget, notify)
            if partials
            else SummaryResult(status="no_relevant_topic", segments=[])
        )
        # Counts only: enough to tune MAX_CALLS and JOB_SECONDS without logging any content.
        logger.info(
            "summary_done sources=%d batches=%d calls=%d seconds=%.1f",
            len(sources),
            len(requests),
            budget.calls,
            self.clock() - started,
        )
        return result, sources

    def _chunk_requests(self, sources: Mapping[str, SourceMessage], context: dict):
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_harmony")

        def count(record: dict) -> int:
            # +1 for the separator between records in the JSON list.
            return len(encoding.encode_ordinary(json.dumps(record, ensure_ascii=False))) + 1

        prompt = load_prompt("summary_system.txt")
        empty = json.dumps({**context, "records": []}, ensure_ascii=False)
        schema = json.dumps(SummaryResult.model_json_schema(), ensure_ascii=False)
        overhead = sum(len(encoding.encode_ordinary(text)) for text in (prompt, empty, schema))
        room = self.max_input_tokens - overhead - FRAMING_TOKENS
        if room <= 0:
            raise SummaryError("輸入 token 上限不足以容納固定指示，請調高 max_input_tokens。")
        batches, current, used = [], [], 0
        for record in self._records(sources, count, room):
            size = count(record)
            if current and used + size > room:
                batches.append(current)
                current, used = [], 0
            current.append(record)
            used += size
        if current:
            batches.append(current)
        requests = []
        for batch in batches:
            request = SummaryInput(
                (
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=json.dumps({**context, "records": batch}, ensure_ascii=False)
                    ),
                ),
                {record["source_id"]: sources[record["source_id"]] for record in batch},
                context["topic"],
            )
            # The additive estimate above is checked against the exact count before sending.
            check_capacity(request, self.max_input_tokens)
            requests.append(request)
        return requests

    def _records(self, sources: Mapping[str, SourceMessage], count, room: int):
        for source_id, message in sources.items():
            record = source_record(source_id, message, self.timezone)
            if count(record) <= room:
                yield record
                continue
            # Split an oversized message rather than truncating it; every piece keeps its ID.
            pieces = math.ceil(count(record) / room)
            while pieces <= len(message.content):
                size = math.ceil(len(message.content) / pieces)
                texts = [
                    message.content[start : start + size]
                    for start in range(0, len(message.content), size)
                ]
                parts = [
                    source_record(
                        source_id,
                        replace(
                            message,
                            content=text,
                            attachment_count=message.attachment_count if index == len(texts) else 0,
                        ),
                        self.timezone,
                        part=f"{index}/{len(texts)}",
                    )
                    for index, text in enumerate(texts, 1)
                ]
                if all(count(part) <= room for part in parts):
                    yield from parts
                    break
                pieces += 1
            else:
                raise SummaryError("單則訊息無法切分到單次輸入上限內，請調高 max_input_tokens。")

    async def _generate(
        self,
        request: SummaryInput,
        api_key: str,
        budget: _Budget,
        notify: Notify,
        label: str,
    ) -> SummaryResult:
        retried = False
        while True:
            remaining = budget.deadline - self.clock()
            if remaining <= 0:
                raise SummaryError(f"已達任務總期限（{JOB_SECONDS} 秒），未交付部分結果。")
            if budget.calls >= MAX_CALLS:
                raise SummaryError(
                    f"已達每次 {MAX_CALLS} 次模型呼叫上限，未交付部分結果；請縮小範圍。"
                )
            budget.calls += 1
            try:
                async with asyncio.timeout(min(REQUEST_SECONDS, remaining)):
                    return await self.gateway.generate(request, api_key=api_key)
            except TimeoutError:
                raise SummaryError(
                    f"模型請求逾時或已達任務總期限（{JOB_SECONDS} 秒），未交付部分結果。"
                ) from None
            except RateLimited as exc:
                wait = exc.retry_after
                if retried or wait is None or self.clock() + wait >= budget.deadline:
                    raise
                retried = True
                await notify(
                    f"Groq 速率限制：{label}等待 {math.ceil(wait)} 秒後重送（最多 1 次）。"
                )
                await self.sleep(wait)

    async def _merge(
        self,
        partials: list[Partial],
        context: dict,
        api_key: str,
        budget: _Budget,
        notify: Notify,
    ) -> SummaryResult:
        level = partials
        while len(level) > 1:
            groups = await asyncio.to_thread(self._merge_groups, level, context)
            if all(not isinstance(group, SummaryInput) for group in groups):
                raise SummaryError("各批摘要合併後超過單次輸入上限，未交付部分結果；請縮小範圍。")
            merged = []
            for group in groups:
                if not isinstance(group, SummaryInput):
                    merged.append(group)
                    continue
                result = await self._generate(group, api_key, budget, notify, "合併")
                if result.status == "ok":
                    merged.append((group.sources, result))
            level = merged
        if not level:
            return SummaryResult(status="no_relevant_topic", segments=[])
        return level[0][1]

    def _merge_groups(self, level: list[Partial], context: dict) -> list:
        """Consecutive partials packed into merge requests; a partial that fits nowhere stays."""
        groups, current = [], [level[0]]
        for partial in level[1:]:
            candidate = self._merge_request([*current, partial], context)
            if self._fits(candidate):
                current.append(partial)
                continue
            groups.append(self._merge_request(current, context) if len(current) > 1 else current[0])
            current = [partial]
        groups.append(self._merge_request(current, context) if len(current) > 1 else current[0])
        return groups

    def _fits(self, request: SummaryInput) -> bool:
        try:
            check_capacity(request, self.max_input_tokens)
        except SummaryError:
            return False
        return True

    def _merge_request(self, group: list[Partial], context: dict) -> SummaryInput:
        sources: dict[str, SourceMessage] = {}
        timelines = []
        for part_sources, result in group:
            sources.update(part_sources)
            segments = []
            for segment in result.segments:
                cited = sorted(part_sources[key].created_at for key in segment.source_ids)
                segments.append(
                    {
                        # Model output is checked again before it is sent back to a model.
                        "title": self._safe(segment.title),
                        "summary": self._safe(segment.summary),
                        "source_ids": segment.source_ids,
                        "time": f"{self._local(cited[0])} ～ {self._local(cited[-1])}",
                    }
                )
            timelines.append({"segments": segments})
        payload = {**context, "partial_timelines": timelines}
        return SummaryInput(
            (
                SystemMessage(content=load_prompt("merge_system.txt")),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ),
            sources,
            context["topic"],
        )

    def _safe(self, text: str) -> str:
        return self.privacy.sanitize(Conversation(0, 0, 0, ()), topic=text).topic

    def _local(self, moment) -> str:
        return moment.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")
