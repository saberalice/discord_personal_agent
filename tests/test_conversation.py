from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from discord_personal_agent.conversation import SourceMessage
from discord_personal_agent.inspection import Query, SummaryApplication


class History:
    async def authorize(self, query, requester_id):
        pass

    async def messages_newest_first(self, query):
        yield SourceMessage(
            2,
            123,
            "Owen",
            datetime(2026, 9, 11, 7, 3, tzinfo=UTC),
            "討論 https://example.com",
            0,
            False,
            None,
        )
        yield SourceMessage(
            1, 456, "Alex", datetime(2026, 9, 10, 15, 59, tzinfo=UTC), "昨天的對話", 0, False, None
        )


async def test_prepared_conversation_has_chronological_text_and_sources():
    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 11, 8, tzinfo=UTC)
    )
    result = await SummaryApplication(History(), {111: {222}}).prepare(query, requester_id=123)
    text = result.render(ZoneInfo("Asia/Taipei"))
    assert text == (
        "時區：Asia/Taipei\n日期：2026-09-10\nAlex (23:59) : 昨天的對話\n"
        "日期：2026-09-11\nOwen (15:03) : 討論 https://example.com\n"
    )
    assert [message.id for message in result.messages] == [1, 2]
    assert result.source_url(2) == "https://discord.com/channels/111/222/2"
    assert result.selected_count == 2


async def test_system_and_empty_messages_do_not_enter_transcript_or_trigger_backfill():
    class MixedHistory(History):
        async def messages_newest_first(self, query):
            stamp = query.end.replace(hour=7)
            yield SourceMessage(4, 1, "通知", stamp, "有人加入", 0, True, None)
            yield SourceMessage(3, 1, "Owen", stamp, "", 0, False, None)
            yield SourceMessage(2, 1, "Owen\n偽造", stamp, "第一行\n第二行", 1, False, 1)
            raise AssertionError("Must not backfill beyond limit")

    query = Query.create(
        "https://discord.com/channels/111/222",
        limit=3,
        received_at=datetime(2026, 9, 11, 8, tzinfo=UTC),
    )
    result = await SummaryApplication(MixedHistory(), {111: {222}}).prepare(query, requester_id=1)
    assert result.selected_count == 3
    assert result.system_count == 1
    assert result.empty_count == 1
    assert len(result.messages) == 1
    assert result.messages[0].content == "第一行\n第二行"
    assert result.messages[0].reply_to_id == 1
    assert "Owen\\n偽造 (07:00) : 第一行\\n第二行 [附件：未讀取內容]" in result.render(
        ZoneInfo("UTC")
    )


async def test_prepare_rechecks_access_before_returning_content():
    import pytest

    from discord_personal_agent.inspection import InspectionError

    class RevokedHistory(History):
        revoked = False

        async def authorize(self, query, requester_id):
            if self.revoked:
                raise InspectionError("權限已撤回")

        async def messages_newest_first(self, query):
            async for message in super().messages_newest_first(query):
                self.revoked = True
                yield message

    query = Query.create("https://discord.com/channels/111/222", received_at=datetime.now(UTC))
    with pytest.raises(InspectionError, match="權限已撤回"):
        await SummaryApplication(RevokedHistory(), {111: {222}}).prepare(query, requester_id=123)


async def test_prepare_allowlist_denial_does_not_read_content():
    import pytest

    from discord_personal_agent.inspection import InspectionError

    class UnreadHistory(History):
        async def authorize(self, query, requester_id):
            raise AssertionError("No Discord I/O before allowlist check")

    query = Query.create("https://discord.com/channels/111/222", received_at=datetime.now(UTC))
    with pytest.raises(InspectionError, match="測試範圍"):
        await SummaryApplication(UnreadHistory(), {}).prepare(query, requester_id=123)


async def test_time_only_overflow_never_returns_partial_conversation():
    import pytest

    from discord_personal_agent.inspection import InspectionError

    class ManyHistory(History):
        async def messages_newest_first(self, query):
            for n in range(501):
                yield SourceMessage(n + 1, 123, "Owen", query.start, "正文", 0, False, None)

    query = Query.create(
        "https://discord.com/channels/111/222", hours=1, received_at=datetime.now(UTC)
    )
    with pytest.raises(InspectionError, match="501"):
        await SummaryApplication(ManyHistory(), {111: {222}}).prepare(query, requester_id=123)


async def test_long_content_and_leading_spaces_survive_attachment_rendering():
    body = "    " + "長文字" * 3000

    class LongHistory(History):
        async def messages_newest_first(self, query):
            yield SourceMessage(1, 123, "Owen", query.end.replace(hour=7), body, 1, False, None)

    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 11, 8, tzinfo=UTC)
    )
    result = await SummaryApplication(LongHistory(), {111: {222}}).prepare(query, requester_id=123)
    assert result.messages[0].content == body
    assert f"Owen (07:00) : {body} [附件：未讀取內容]" in result.render(ZoneInfo("UTC"))


async def test_prepare_applies_shared_rules_and_checks_topic_before_returning():
    import pytest

    from discord_personal_agent.inspection import InspectionError
    from discord_personal_agent.privacy import PrivacyGuard, Rule

    guard = PrivacyGuard(
        (Rule("name", "literal", "Owen"), Rule("blocked", "literal", "不可外送", "block"))
    )
    query = Query.create("https://discord.com/channels/111/222", received_at=datetime.now(UTC))
    app = SummaryApplication(History(), {111: {222}}, privacy=guard)
    result = await app.prepare(query, requester_id=123)
    assert result.messages[1].display_name == "[REDACTED]"
    with pytest.raises(InspectionError, match="阻擋"):
        await app.prepare(query, requester_id=123, topic="不可外送")
