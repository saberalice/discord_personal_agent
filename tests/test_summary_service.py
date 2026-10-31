from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from discord_personal_agent.config import Settings
from discord_personal_agent.conversation import SourceMessage
from discord_personal_agent.inspection import InspectionError, Query
from discord_personal_agent.privacy import PrivacyGuard, Rule
from discord_personal_agent.summary import SummaryError, SummaryResult
from discord_personal_agent.summary_service import SummaryService
from discord_personal_agent.transcript_question import AnswerResult


@pytest.mark.parametrize(
    "scenario", ["ok", "blocked", "revoked", "empty", "oversized", "beyond_limit"]
)
async def test_summary_pipeline_checks_privacy_capacity_and_final_permission(scenario):
    class History:
        checks = 0

        async def authorize(self, query, requester_id, *, voice_text=False):
            self.checks += 1
            if scenario == "revoked" and gateway.generate.await_count:
                raise InspectionError("權限已撤回")

        async def messages_newest_first(self, query):
            yield SourceMessage(
                1,
                2,
                "Owen",
                datetime(2026, 9, 11, 7, tzinfo=UTC),
                "SECRET"
                if scenario == "blocked"
                else {"oversized": "測試" * 10000, "beyond_limit": "測試" * 80000}.get(
                    scenario, "討論"
                ),
                0,
                scenario == "empty",
                None,
            )

    gateway = AsyncMock()
    gateway.generate.return_value = SummaryResult.model_validate(
        {
            "status": "ok",
            "segments": [{"title": "測試", "summary": "部署仍待確認", "source_ids": ["m001"]}],
        }
    )
    settings = Settings(
        ZoneInfo("Asia/Taipei"),
        {111: frozenset({222})},
        PrivacyGuard((Rule("stop", "literal", "SECRET", "block"),)),
    )
    service = SummaryService(History(), settings, gateway)
    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 11, 8, tzinfo=UTC)
    )
    if scenario in ("blocked", "revoked", "beyond_limit"):
        with pytest.raises((InspectionError, SummaryError)):
            await service.run(query, requester_id=2, topic="", api_key="fake")
    else:
        text = await service.run(query, requester_id=2, topic="", api_key="fake")
        assert ("沒有可整理" if scenario == "empty" else "部署仍待確認") in text
    if scenario == "oversized":
        # Split into parts of the same source and merged, instead of being refused.
        assert gateway.generate.await_count > 1
    else:
        assert gateway.generate.await_count == (1 if scenario in ("ok", "revoked") else 0)


async def test_rate_limit_wait_is_reported_through_notify(monkeypatch):
    from discord_personal_agent.summary import RateLimited

    class History:
        async def authorize(self, query, requester_id, *, voice_text=False):
            pass

        async def messages_newest_first(self, query):
            yield SourceMessage(
                1, 2, "Owen", datetime(2026, 9, 11, 7, tzinfo=UTC), "討論", 0, False, None
            )

    gateway = AsyncMock()
    gateway.generate.side_effect = [
        RateLimited("限流", retry_after=0.01),
        SummaryResult.model_validate(
            {
                "status": "ok",
                "segments": [{"title": "t", "summary": "完成", "source_ids": ["m001"]}],
            }
        ),
    ]
    notes = []

    async def notify(text):
        notes.append(text)

    service = SummaryService(
        History(), Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), gateway
    )
    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 11, 8, tzinfo=UTC)
    )
    text = await service.run(query, requester_id=2, topic="", api_key="fake", notify=notify)
    assert "完成" in text
    assert len(notes) == 1 and "速率限制" in notes[0]


class TranscriptHistory:
    def __init__(
        self,
        data=b"[10:00:01] Alex: \xe7\xa2\xba\xe5\xae\x9a\xe9\x80\xb1\xe5\x85\xad\n",
        revoke=None,
    ):
        from discord_personal_agent.voice_transcript import TranscriptFile

        self.file = TranscriptFile("2026-09-11_10-00-00.txt", data, 333)
        self.revoke = revoke
        self.authorized = []
        self.downloads = 0

    async def authorize(self, query, requester_id, *, voice_text=False):
        self.authorized.append((query.guild_id, query.channel_id, voice_text))
        if self.revoke and self.revoke():
            raise InspectionError("權限已撤回")

    async def transcript(self, message_id):
        assert message_id == 333
        self.downloads += 1
        return self.file

    async def latest_transcript(self):
        self.downloads += 1
        return self.file


def transcript_gateway():
    gateway = AsyncMock()
    gateway.generate.return_value = SummaryResult.model_validate(
        {
            "status": "ok",
            "segments": [{"title": "週六", "summary": "確定週六", "source_ids": ["m001"]}],
        }
    )
    return gateway


async def test_transcript_summary_cites_times_and_links_back_to_the_transcript():
    history = TranscriptHistory()
    gateway = transcript_gateway()
    service = SummaryService(
        history, Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), gateway
    )
    text = await service.run_transcript(
        "https://discord.com/channels/111/222/333", requester_id=2, topic="", api_key="fake"
    )
    request = gateway.generate.await_args.args[0]
    assert "Alex (10:00) : 確定週六" in request.messages[1].content
    assert "2026-09-11 10:00 ～ 2026-09-11 10:00" in text
    assert "確定週六" in text
    # One link for the whole summary: every segment points at the same TXT message.
    assert text.count("https://discord.com/channels/111/222/333") == 1
    assert text.splitlines()[-1] == "[查看逐字稿](https://discord.com/channels/111/222/333)"
    assert "最新最多" not in text
    assert history.authorized == [(111, 222, True), (111, 222, True)]


async def test_latest_transcript_is_summarized_and_links_to_the_message_it_found():
    history = TranscriptHistory()
    gateway = transcript_gateway()
    service = SummaryService(
        history, Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), gateway
    )
    text = await service.run_latest_transcript(111, 222, requester_id=2, topic="", api_key="fake")
    assert "確定週六" in text
    assert text.splitlines()[-1] == "[查看逐字稿](https://discord.com/channels/111/222/333)"
    assert history.authorized == [(111, 222, True), (111, 222, True)]


async def question_service(history):
    gateway = AsyncMock()
    gateway.answer.return_value = AnswerResult(
        status="answered", answer="Alex 說週六", source_ids=["m001"]
    )
    return (
        SummaryService(
            history, Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), gateway
        ),
        gateway,
    )


async def test_question_on_a_chosen_message_answers_and_rechecks_permissions():
    history = TranscriptHistory()
    service, gateway = await question_service(history)
    text = await service.answer_transcript(
        111, 222, 333, "誰說週六？", requester_id=2, api_key="fake"
    )
    assert "Alex 說週六" in text
    assert text.splitlines()[-1] == "[查看逐字稿](https://discord.com/channels/111/222/333)"
    assert history.authorized == [(111, 222, True), (111, 222, True)]


async def test_question_without_a_message_uses_the_latest_transcript():
    history = TranscriptHistory()
    service, _ = await question_service(history)
    text = await service.answer_transcript(
        111, 222, None, "誰說週六？", requester_id=2, api_key="fake"
    )
    assert "Alex 說週六" in text
    assert history.downloads == 1


async def test_question_on_an_empty_transcript_never_calls_the_model():
    history = TranscriptHistory(data=b"")
    service, gateway = await question_service(history)
    text = await service.answer_transcript(111, 222, 333, "誰？", requester_id=2, api_key="fake")
    assert "未呼叫 LLM" in text
    gateway.answer.assert_not_awaited()


async def test_question_without_a_key_fails_clearly():
    history = TranscriptHistory()
    service, gateway = await question_service(history)
    with pytest.raises(SummaryError, match="金鑰"):
        await service.answer_transcript(111, 222, 333, "誰？", requester_id=2, api_key=" ")
    gateway.answer.assert_not_awaited()


async def test_latest_transcript_outside_the_allowlist_is_never_read():
    history = TranscriptHistory()
    gateway = transcript_gateway()
    service = SummaryService(
        history, Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({999})}), gateway
    )
    with pytest.raises(InspectionError):
        await service.run_latest_transcript(111, 222, requester_id=2, topic="", api_key="fake")
    assert history.downloads == 0
    gateway.generate.assert_not_awaited()


async def test_transcript_outside_the_allowlist_is_never_downloaded():
    history = TranscriptHistory()
    gateway = transcript_gateway()
    service = SummaryService(
        history, Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({999})}), gateway
    )
    with pytest.raises(InspectionError) as failure:
        await service.run_transcript(
            "https://discord.com/channels/111/222/333", requester_id=2, topic="", api_key="fake"
        )
    assert failure.value.code.value == "channel_not_allowed"
    assert history.authorized == [] and history.downloads == 0
    gateway.generate.assert_not_awaited()


@pytest.mark.parametrize("scenario", ["blocked", "empty", "revoked"])
async def test_transcript_privacy_empty_and_revocation(scenario):
    gateway = transcript_gateway()
    history = TranscriptHistory(
        data={"blocked": b"[10:00:01] A: SECRET\n", "empty": b""}.get(
            scenario, b"[10:00:01] A: ok\n"
        ),
        revoke=(lambda: gateway.generate.await_count > 0) if scenario == "revoked" else None,
    )
    settings = Settings(
        ZoneInfo("Asia/Taipei"),
        {111: frozenset({222})},
        PrivacyGuard((Rule("stop", "literal", "SECRET", "block"),)),
    )
    service = SummaryService(history, settings, gateway)
    run = service.run_transcript(
        "https://discord.com/channels/111/222/333", requester_id=2, topic="", api_key="fake"
    )
    if scenario == "empty":
        assert "沒有可摘要" in await run
    else:
        from discord_personal_agent.privacy import PrivacyError

        with pytest.raises((PrivacyError, InspectionError)):
            await run
    assert gateway.generate.await_count == (1 if scenario == "revoked" else 0)


async def test_transcript_without_the_topic_says_so():
    gateway = AsyncMock()
    gateway.generate.return_value = SummaryResult(status="no_relevant_topic", segments=[])
    service = SummaryService(
        TranscriptHistory(), Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), gateway
    )
    text = await service.run_transcript(
        "https://discord.com/channels/111/222/333", requester_id=2, topic="預算", api_key="fake"
    )
    assert "逐字稿中沒有與指定主題相關的內容" in text
