from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from discord_personal_agent.conversation import SourceMessage
from discord_personal_agent.privacy import PrivacyError, PrivacyGuard, Rule
from discord_personal_agent.summary import RateLimited, SummaryError
from discord_personal_agent.transcript_question import (
    MAX_CITATIONS,
    TOKENS_PER_MINUTE,
    Answer,
    AnswerResult,
    TranscriptQuestion,
    render_answer,
    validate_answer,
)

TAIPEI = ZoneInfo("Asia/Taipei")
START = datetime(2026, 9, 11, 2, tzinfo=UTC)


def line(index, name="Alex", text="週六開會"):
    return SourceMessage(333, 0, name, START + timedelta(seconds=index), text, 0, False, None)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def workflow(gateway, *, tokens=2000, privacy=None, clock=None):
    clock = clock or Clock()
    return TranscriptQuestion(
        gateway,
        max_input_tokens=tokens,
        timezone=TAIPEI,
        privacy=privacy or PrivacyGuard(),
        clock=clock,
        sleep=clock.sleep,
    )


def answered(*ids, text="Alex 提到週六"):
    return AnswerResult(status="answered", answer=text, source_ids=list(ids))


UNCERTAIN = AnswerResult(status="uncertain", answer="", source_ids=[])


async def ask(flow, messages, question="誰提到週六？", notify=None):
    return await flow.ask(
        messages,
        question,
        api_key="fake",
        deadline=10_000.0,
        notify=notify or AsyncMock(),
    )


async def test_short_transcript_is_one_compact_request_with_the_question():
    gateway = AsyncMock()
    gateway.answer.return_value = answered("m001")
    answer = await ask(workflow(gateway), (line(0),))
    gateway.answer.assert_awaited_once()
    system, human = gateway.answer.await_args.args[0].messages
    assert "不確定" in system.content
    assert "m001 [10:00:00] Alex: 週六開會" in human.content
    assert "誰提到週六？" in human.content
    assert answer.status == "answered"
    assert [source.display_name for source in answer.citations] == ["Alex"]


async def test_uncertain_batches_never_hide_an_answer_from_another_batch():
    gateway = AsyncMock()
    gateway.answer.side_effect = lambda request, *, api_key: (
        answered("m001") if "m001" in request.sources else UNCERTAIN
    )
    answer = await ask(workflow(gateway, tokens=1200), tuple(line(i) for i in range(60)))
    assert gateway.answer.await_count > 1
    assert answer.status == "answered"
    assert answer.text == "Alex 提到週六"


async def test_every_batch_uncertain_is_uncertain_without_a_merge():
    gateway = AsyncMock()
    gateway.answer.return_value = UNCERTAIN
    answer = await ask(workflow(gateway, tokens=1200), tuple(line(i) for i in range(60)))
    assert answer.status == "uncertain"
    assert answer.citations == ()
    assert not any(
        "partial_answers" in call.args[0].messages[1].content
        for call in gateway.answer.await_args_list
    )


async def test_two_answering_batches_are_merged_from_their_cited_lines_only():
    gateway = AsyncMock()
    requests = []

    async def respond(request, *, api_key):
        requests.append(request)
        if "partial_answers" in request.messages[1].content:
            return answered("m001", "m060", text="合併答案")
        if "m001" in request.sources:
            return answered("m001", text="前段")
        if "m060" in request.sources:
            return answered("m060", text="後段")
        return UNCERTAIN

    gateway.answer.side_effect = respond
    answer = await ask(workflow(gateway, tokens=1200), tuple(line(i) for i in range(60)))
    merge = requests[-1]
    assert set(merge.sources) == {"m001", "m060"}
    assert answer.text == "合併答案"
    assert len(answer.citations) == 2


async def test_batches_wait_for_the_token_window_and_say_so():
    clock = Clock()
    gateway = AsyncMock()
    gateway.answer.return_value = UNCERTAIN
    notify = AsyncMock()
    flow = workflow(gateway, tokens=TOKENS_PER_MINUTE - 1500, clock=clock)
    text = "週六開會，" * 40
    await ask(flow, tuple(line(i, text=text) for i in range(120)), notify=notify)
    assert gateway.answer.await_count > 1
    assert clock.sleeps and all(0 < seconds <= 60 for seconds in clock.sleeps)
    assert "等待" in notify.await_args_list[0].args[0]


async def test_a_wait_past_the_deadline_fails_before_sending_more():
    clock = Clock()
    gateway = AsyncMock()
    gateway.answer.return_value = UNCERTAIN
    flow = workflow(gateway, tokens=TOKENS_PER_MINUTE - 1500, clock=clock)
    text = "週六開會，" * 40
    with pytest.raises(SummaryError):
        await flow.ask(
            tuple(line(i, text=text) for i in range(120)),
            "問題",
            api_key="fake",
            deadline=1.0,
            notify=AsyncMock(),
        )
    assert gateway.answer.await_count == 1


async def test_rate_limit_retries_once_then_fails_without_a_partial_answer():
    clock = Clock()
    gateway = AsyncMock()
    gateway.answer.side_effect = RateLimited("限流", retry_after=3.0)
    with pytest.raises(RateLimited):
        await ask(workflow(gateway, clock=clock), (line(0),))
    assert gateway.answer.await_count == 2
    assert clock.sleeps == [3.0]


async def test_plan_over_the_call_limit_sends_nothing():
    gateway = AsyncMock()
    text = "週六開會，" * 100
    with pytest.raises(SummaryError, match="12"):
        await ask(workflow(gateway, tokens=1500), tuple(line(i, text=text) for i in range(200)))
    gateway.answer.assert_not_awaited()


async def test_a_line_too_long_for_one_request_fails_before_sending():
    gateway = AsyncMock()
    with pytest.raises(SummaryError):
        await ask(workflow(gateway, tokens=1200), (line(0, text="週六開會，" * 2000),))
    gateway.answer.assert_not_awaited()


async def test_blocked_question_or_line_sends_nothing():
    guard = PrivacyGuard((Rule("secret", "literal", "機密代號", "block"),))
    gateway = AsyncMock()
    for messages, question in [
        ((line(0),), "機密代號是誰？"),
        ((line(0, text="機密代號"),), "誰？"),
    ]:
        with pytest.raises(PrivacyError):
            await ask(workflow(gateway, privacy=guard), messages, question)
    gateway.answer.assert_not_awaited()


async def test_masked_content_is_what_the_model_and_citations_see():
    guard = PrivacyGuard((Rule("code", "literal", "週六", "mask"),))
    gateway = AsyncMock()
    gateway.answer.return_value = answered("m001")
    answer = await ask(workflow(gateway, privacy=guard), (line(0),))
    assert "週六" not in gateway.answer.await_args.args[0].messages[1].content
    assert "週六" not in answer.citations[0].content


class Request:
    sources = {"m001": line(0)}


@pytest.mark.parametrize(
    "raw",
    [
        '{"status":"uncertain","answer":"猜是 Alex","source_ids":["m001"]}',
        '{"status":"answered","answer":"Alex","source_ids":[]}',
        '{"status":"answered","answer":"Alex","source_ids":["m999"]}',
        '{"status":"answered","answer":"Alex","source_ids":["m001","m999"]}',
        '{"status":"answered","answer":"  ","source_ids":["m001"]}',
    ],
    ids=["model-unsure", "no-citation", "unknown-id", "partly-unknown", "blank"],
)
def test_program_forces_uncertain_when_the_answer_is_unsupported(raw):
    assert validate_answer(raw, Request()) == UNCERTAIN


def test_malformed_reply_is_an_error_not_an_answer():
    with pytest.raises(SummaryError):
        validate_answer('{"status":"maybe"}', Request())


def test_answer_cites_time_speaker_and_line_then_one_link():
    answer = Answer("answered", "Alex 提到週六", (line(0),))
    text = render_answer(
        answer, "誰提到週六？", "https://discord.com/channels/1/2/3", TAIPEI, PrivacyGuard()
    )
    assert "誰提到週六？" in text
    assert "Alex 提到週六" in text
    assert "[10:00:00] Alex: 週六開會" in text
    assert text.splitlines()[-1] == "[查看逐字稿](https://discord.com/channels/1/2/3)"


def test_citations_are_capped_and_the_rest_counted():
    answer = Answer("answered", "答案", tuple(line(i) for i in range(MAX_CITATIONS + 2)))
    text = render_answer(answer, "問題", "https://u", TAIPEI, PrivacyGuard())
    assert text.count("Alex: 週六開會") == MAX_CITATIONS
    assert "另有 2 行" in text


def test_uncertain_says_so_and_shows_no_citations():
    text = render_answer(Answer("uncertain", "", ()), "問題", "https://u", TAIPEI, PrivacyGuard())
    assert "不確定" in text
    assert "**依據**" not in text


def test_generated_answer_is_checked_before_delivery():
    guard = PrivacyGuard((Rule("code", "literal", "週六", "mask"),))
    text = render_answer(
        Answer("answered", "週六開會", (line(0, text="x"),)), "問題", "https://u", TAIPEI, guard
    )
    assert "週六" not in text
