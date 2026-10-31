"""Answer a question from one voice transcript; no Discord or provider SDK types."""

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, ValidationError

from .conversation import Conversation, SourceMessage
from .privacy import PrivacyGuard
from .summary import SummaryError, SummaryInput, load_prompt
from .summary_workflow import FRAMING_TOKENS, MAX_CALLS, Budget, Notify, bounded_call

logger = logging.getLogger(__name__)
MAX_QUESTION_CHARS = 500
MAX_CITATIONS = 5
CITATION_CHARS = 120
# Groq's listed developer-plan limit; a smaller account limit still falls back to the 429 retry.
TOKENS_PER_MINUTE = 8000
WINDOW_SECONDS = 60.0
# The gateway does not report usage. Assumes output counts toward the limit (unconfirmed).
OUTPUT_TOKEN_ESTIMATE = 1000
UNCERTAIN_TEXT = "不確定：逐字稿中找不到足夠依據回答這個問題。"


class AnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["answered", "uncertain"]
    answer: str
    source_ids: list[str]


NO_ANSWER = AnswerResult(status="uncertain", answer="", source_ids=[])


def validate_answer(raw: str, request: SummaryInput) -> AnswerResult:
    """An answer stands only on citations the program can check; anything else is uncertain."""
    try:
        result = AnswerResult.model_validate_json(raw)
    except ValidationError:
        raise SummaryError("模型回覆不符合問答格式，未交付結果。") from None
    if result.status == "uncertain":
        return NO_ANSWER
    unknown = sum(source_id not in request.sources for source_id in result.source_ids)
    if unknown or not result.source_ids or not result.answer.strip():
        logger.warning("answer_unsupported cited=%d unknown=%d", len(result.source_ids), unknown)
        return NO_ANSWER
    return result


@dataclass(frozen=True)
class Answer:
    status: Literal["answered", "uncertain"]
    text: str
    citations: tuple[SourceMessage, ...]


class AnswerGateway(Protocol):
    async def answer(self, request: SummaryInput, *, api_key: str) -> AnswerResult: ...


class _TokenWindow:
    """Sliding per-minute spend, so batches wait up front instead of relying on a 429."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.spent: deque[tuple[float, int]] = deque()

    def wait_for(self, cost: int) -> float:
        now = self.clock()
        while self.spent and now - self.spent[0][0] >= WINDOW_SECONDS:
            self.spent.popleft()
        over = sum(tokens for _, tokens in self.spent) + cost - TOKENS_PER_MINUTE
        wait = 0.0
        # A single request larger than the limit still goes once the window has emptied.
        for moment, tokens in self.spent:
            if over <= 0:
                break
            over -= tokens
            wait = moment + WINDOW_SECONDS - now
        return wait

    def spend(self, cost: int) -> None:
        self.spent.append((self.clock(), cost))


class TranscriptQuestion:
    def __init__(
        self,
        gateway: AnswerGateway,
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

    async def ask(
        self,
        messages: tuple[SourceMessage, ...],
        question: str,
        *,
        api_key: str,
        deadline: float,
        notify: Notify,
    ) -> Answer:
        sanitized = self.privacy.sanitize(
            Conversation(0, 0, len(messages), messages), topic=question
        )
        sources = {
            f"m{index:03d}": message
            for index, message in enumerate(sanitized.conversation.messages, 1)
        }
        # Measured before the first request, so an impossible plan sends nothing.
        planned = await asyncio.to_thread(self._plan, sources, sanitized.topic)
        calls = len(planned) + (len(planned) > 1)
        if calls > MAX_CALLS:
            raise SummaryError(
                f"這份逐字稿需要至少 {calls} 次模型呼叫，超過每次 {MAX_CALLS} 次上限。"
                "未送出任何內容。"
            )
        budget = Budget(deadline)
        window = _TokenWindow(self.clock)
        started = self.clock()
        partials: list[tuple[SummaryInput, AnswerResult]] = []
        for number, (request, tokens) in enumerate(planned, 1):
            label = f"第 {number}/{len(planned)} 段" if len(planned) > 1 else "本次請求"
            result = await self._paced(request, tokens, api_key, budget, window, notify, label)
            if result.status == "answered":
                partials.append((request, result))
        answer = await self._combine(partials, sanitized.topic, api_key, budget, window, notify)
        # Counts only: enough to tune the pacing without logging the question or the answer.
        logger.info(
            "question_done sources=%d batches=%d calls=%d seconds=%.1f status=%s",
            len(sources),
            len(planned),
            budget.calls,
            self.clock() - started,
            answer.status,
        )
        return answer

    def _plan(
        self, sources: Mapping[str, SourceMessage], question: str
    ) -> list[tuple[SummaryInput, int]]:
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_harmony")

        def tokens(text: str) -> int:
            return len(encoding.encode_ordinary(text))

        prompt = load_prompt("question_system.txt")
        schema = json.dumps(AnswerResult.model_json_schema(), ensure_ascii=False)
        empty = json.dumps({"question": question, "transcript": ""}, ensure_ascii=False)
        overhead = tokens(prompt) + tokens(schema) + tokens(empty) + FRAMING_TOKENS
        room = self.max_input_tokens - overhead
        if room <= 0:
            raise SummaryError("輸入 token 上限不足以容納固定指示，請調高 max_input_tokens。")
        batches: list[list[tuple[str, str]]] = []
        used = 0
        for source_id, message in sources.items():
            line = f"{source_id} {self._line(message)}"
            # Measured as JSON: that is how the line is sent, and escaping can add tokens.
            size = tokens(json.dumps(line, ensure_ascii=False)) + 1
            if size > room:
                raise SummaryError("逐字稿有單行超過單次輸入上限，請調高 max_input_tokens。")
            if not batches or used + size > room:
                batches.append([])
                used = 0
            batches[-1].append((source_id, line))
            used += size
        planned = []
        for batch in batches:
            transcript = "\n".join(line for _, line in batch)
            payload = json.dumps(
                {"question": question, "transcript": transcript}, ensure_ascii=False
            )
            request = SummaryInput(
                (SystemMessage(content=prompt), HumanMessage(content=payload)),
                {source_id: sources[source_id] for source_id, _ in batch},
                question,
            )
            planned.append(
                (request, tokens(prompt) + tokens(schema) + tokens(payload) + FRAMING_TOKENS)
            )
        return planned

    async def _paced(self, request, tokens, api_key, budget, window, notify, label) -> AnswerResult:
        cost = tokens + OUTPUT_TOKEN_ESTIMATE
        wait = window.wait_for(cost)
        if wait > 0:
            if self.clock() + wait >= budget.deadline:
                raise SummaryError("等待 Groq 額度會超過任務總期限，未交付部分結果。")
            await notify(
                f"長逐字稿分段查詢中：{label}等待 {math.ceil(wait)} 秒，避開 Groq 速率限制。"
            )
            await self.sleep(wait)
        window.spend(cost)
        return await bounded_call(
            lambda: self.gateway.answer(request, api_key=api_key),
            budget,
            notify,
            label,
            clock=self.clock,
            sleep=self.sleep,
        )

    async def _combine(self, partials, question, api_key, budget, window, notify) -> Answer:
        if not partials:
            return Answer("uncertain", "", ())
        if len(partials) == 1:
            return self._answer(partials[0][1], partials[0][0].sources)
        cited: dict[str, SourceMessage] = {}
        parts = []
        for request, result in partials:
            lines = {key: request.sources[key] for key in result.source_ids}
            cited.update(lines)
            parts.append(
                {
                    # Model output is checked again before it is sent back to a model.
                    "answer": self._safe(result.answer),
                    "cited_lines": [f"{key} {self._line(line)}" for key, line in lines.items()],
                }
            )
        payload = json.dumps({"question": question, "partial_answers": parts}, ensure_ascii=False)
        prompt = load_prompt("question_merge_system.txt")
        merge = SummaryInput(
            (SystemMessage(content=prompt), HumanMessage(content=payload)), cited, question
        )
        tokens = await asyncio.to_thread(self._measure, merge)
        if tokens > self.max_input_tokens:
            raise SummaryError("各段答案合併後超過單次輸入上限，未交付部分結果。")
        result = await self._paced(merge, tokens, api_key, budget, window, notify, "合併")
        return self._answer(result, cited)

    def _measure(self, request: SummaryInput) -> int:
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_harmony")
        text = "\n".join(str(message.content) for message in request.messages)
        text += json.dumps(AnswerResult.model_json_schema(), ensure_ascii=False)
        return len(encoding.encode_ordinary(text)) + FRAMING_TOKENS

    def _answer(self, result: AnswerResult, sources: Mapping[str, SourceMessage]) -> Answer:
        if result.status == "uncertain":
            return Answer("uncertain", "", ())
        cited = sorted(
            {key: sources[key] for key in result.source_ids}.values(),
            key=lambda source: source.created_at,
        )
        return Answer("answered", result.answer.strip(), tuple(cited))

    def _line(self, message: SourceMessage) -> str:
        return format_line(message, self.timezone)

    def _safe(self, text: str) -> str:
        return self.privacy.sanitize(Conversation(0, 0, 0, ()), topic=text).topic


def format_line(message: SourceMessage, timezone: ZoneInfo) -> str:
    clock = message.created_at.astimezone(timezone).strftime("%H:%M:%S")
    return f"[{clock}] {message.display_name}: {message.content}"


def render_answer(
    answer: Answer,
    question: str,
    transcript_url: str,
    timezone: ZoneInfo,
    privacy: PrivacyGuard,
) -> str:
    def safe(text: str) -> str:
        return privacy.sanitize(Conversation(0, 0, 0, ()), topic=text).topic

    lines = [f"**問題**：{safe(question)}", ""]
    if answer.status == "uncertain":
        lines.append(UNCERTAIN_TEXT)
    else:
        lines += [safe(answer.text), "", "**依據**（語音辨識可能有誤，請核對逐字稿）："]
        for source in answer.citations[:MAX_CITATIONS]:
            line = format_line(source, timezone)
            lines.append(
                f"- {line[:CITATION_CHARS]}…" if len(line) > CITATION_CHARS else f"- {line}"
            )
        if len(answer.citations) > MAX_CITATIONS:
            lines.append(f"- …另有 {len(answer.citations) - MAX_CITATIONS} 行")
    lines += ["", f"[查看逐字稿]({transcript_url})"]
    return "\n".join(lines)
