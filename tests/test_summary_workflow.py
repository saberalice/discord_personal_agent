import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from discord_personal_agent.conversation import Conversation, SourceMessage
from discord_personal_agent.inspection import Query
from discord_personal_agent.privacy import PrivacyError, PrivacyGuard, Rule
from discord_personal_agent.summary import RateLimited, SummaryError, SummaryResult
from discord_personal_agent.summary_workflow import SummaryWorkflow

START = datetime(2026, 9, 11, 7, tzinfo=UTC)
QUERY = Query.create("https://discord.com/channels/111/222", received_at=START + timedelta(hours=1))


def conversation(*contents):
    messages = tuple(
        SourceMessage(1000 + i, 7, "Owen", START + timedelta(minutes=i), text, 0, False, None)
        for i, text in enumerate(contents)
    )
    return Conversation(111, 222, len(messages), messages)


def records(request):
    return json.loads(request.messages[1].content).get("records")


class Gateway:
    """Summarises every request with one segment citing all of its sources."""

    def __init__(self, *, status="ok", summary="摘要", failures=()):
        self.requests = []
        self.status = status
        self.summary = summary
        self.failures = list(failures)

    async def generate(self, request, *, api_key):
        self.requests.append(request)
        if self.failures:
            raise self.failures.pop(0)
        if records(request) is not None and self.status != "ok":
            return SummaryResult(status=self.status, segments=[])
        return SummaryResult.model_validate(
            {
                "status": "ok",
                "segments": [
                    {
                        "title": "討論",
                        "summary": self.summary,
                        "source_ids": sorted(request.sources),
                    }
                ],
            }
        )


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def workflow(gateway, *, tokens=4000, privacy=None, clock=None):
    clock = clock or Clock()
    return SummaryWorkflow(
        gateway,
        max_input_tokens=tokens,
        timezone=ZoneInfo("Asia/Taipei"),
        privacy=privacy or PrivacyGuard(),
        clock=clock,
        sleep=clock.sleep,
    )


async def summarize(flow, source, *, topic="", deadline=600.0):
    notes = []

    async def notify(text):
        notes.append(text)

    result, sources = await flow.summarize(
        source, QUERY, topic=topic, api_key="k", deadline=deadline, notify=notify
    )
    return result, sources, notes


async def test_small_conversation_is_one_request_with_the_existing_prompt():
    gateway = Gateway()
    result, sources, _ = await summarize(workflow(gateway), conversation("甲", "乙"))
    assert len(gateway.requests) == 1
    assert [r["source_id"] for r in records(gateway.requests[0])] == ["m001", "m002"]
    assert result.segments[0].source_ids == ["m001", "m002"]
    assert sources["m002"].id == 1001


async def test_long_conversation_is_split_in_time_order_and_merged_without_raw_text():
    gateway = Gateway()
    texts = [f"第{i}則討論部署細節與測試排程" * 8 for i in range(60)]
    result, sources, _ = await summarize(workflow(gateway, tokens=2500), conversation(*texts))
    chunks = [r for r in gateway.requests if records(r) is not None]
    merges = [r for r in gateway.requests if records(r) is None]
    assert len(chunks) > 1 and merges
    seen = [r["source_id"] for chunk in chunks for r in records(chunk)]
    assert seen == [f"m{i:03d}" for i in range(1, 61)]
    for chunk in chunks:
        assert set(chunk.sources) == {r["source_id"] for r in records(chunk)}
    for merge in merges:
        assert "第0則" not in merge.messages[1].content
        assert "partial_timelines" in merge.messages[1].content
    assert set(result.segments[0].source_ids) <= set(sources)
    assert len(sources) == 60


async def test_oversized_message_is_split_into_parts_with_the_same_source_id():
    gateway = Gateway()
    text = "".join(f"段落{i:04d}" for i in range(1500))
    await summarize(workflow(gateway, tokens=2500), conversation(text))
    chunks = [r for r in gateway.requests if records(r) is not None]
    parts = [r for chunk in chunks for r in records(chunk)]
    assert len(parts) > 1
    assert {p["source_id"] for p in parts} == {"m001"}
    assert [p["part"] for p in parts] == [f"{i}/{len(parts)}" for i in range(1, len(parts) + 1)]
    covered = "".join(p["transcript"].split(" : ", 1)[1].rstrip("\n") for p in parts)
    assert covered == text


async def test_plan_over_the_call_limit_fails_before_any_request():
    gateway = Gateway()
    text = "".join(f"段落{i:05d}" for i in range(12000))
    with pytest.raises(SummaryError, match="12"):
        await summarize(workflow(gateway, tokens=2500), conversation(text))
    assert gateway.requests == []


async def test_rate_limit_waits_retry_after_once_and_tells_the_user():
    clock = Clock()
    gateway = Gateway(failures=[RateLimited("限流", retry_after=7.0)])
    result, _, notes = await summarize(workflow(gateway, clock=clock), conversation("甲"))
    assert clock.sleeps == [7.0]
    assert len(gateway.requests) == 2
    assert len(notes) == 1 and "7" in notes[0]
    assert result.status == "ok"


@pytest.mark.parametrize(
    "failures",
    [
        [RateLimited("限流", retry_after=1.0), RateLimited("限流", retry_after=1.0)],
        [RateLimited("限流", retry_after=None)],
        [RateLimited("限流", retry_after=900.0)],
    ],
    ids=["second-429", "no-retry-after", "beyond-deadline"],
)
async def test_rate_limit_fails_when_retry_is_not_possible(failures):
    clock = Clock()
    gateway = Gateway(failures=failures)
    with pytest.raises(RateLimited):
        await summarize(workflow(gateway, clock=clock), conversation("甲"))
    assert len(gateway.requests) == len(failures)
    assert sum(clock.sleeps) < 600


async def test_deadline_stops_further_requests():
    clock = Clock()
    clock.now = 600.0
    gateway = Gateway()
    with pytest.raises(SummaryError, match="600"):
        await summarize(workflow(gateway, clock=clock), conversation("甲"), deadline=600.0)
    assert gateway.requests == []


async def test_no_relevant_topic_in_every_batch_needs_no_merge():
    gateway = Gateway(status="no_relevant_topic")
    texts = [f"第{i}則閒聊" * 20 for i in range(40)]
    result, _, _ = await summarize(
        workflow(gateway, tokens=2500), conversation(*texts), topic="預算"
    )
    assert result.status == "no_relevant_topic"
    assert all(records(r) is not None for r in gateway.requests)


async def test_blocked_text_in_a_batch_summary_never_reaches_the_merge():
    gateway = Gateway(summary="提到 SECRET")
    texts = [f"第{i}則討論" * 20 for i in range(40)]
    guard = PrivacyGuard((Rule("stop", "literal", "SECRET", "block"),))
    with pytest.raises(PrivacyError):
        await summarize(workflow(gateway, tokens=2500, privacy=guard), conversation(*texts))
    assert all(records(r) is not None for r in gateway.requests)


async def test_merge_that_does_not_fit_once_is_done_in_layers():
    gateway = Gateway(summary="這一批討論了部署、測試與排程的細節。" * 25)
    texts = [f"第{i}則討論部署細節與測試排程" * 8 for i in range(60)]
    result, _, _ = await summarize(workflow(gateway, tokens=2500), conversation(*texts))
    merges = [r for r in gateway.requests if records(r) is None]
    assert len(merges) > 1
    assert result.status == "ok"
    assert len(gateway.requests) <= 12


async def test_job_statistics_are_logged_without_content(caplog):
    import logging

    caplog.set_level(logging.INFO)
    gateway = Gateway(summary="PRIVATE_SUMMARY")
    texts = [f"PRIVATE_TEXT{i}" * 40 for i in range(40)]
    await summarize(workflow(gateway, tokens=2500), conversation(*texts))
    line = next(r.getMessage() for r in caplog.records if "summary_done" in r.getMessage())
    assert f"calls={len(gateway.requests)}" in line
    assert "batches=" in line and "seconds=" in line
    assert "PRIVATE" not in caplog.text
