from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import discord
import pytest

from discord_personal_agent.bot import PersonalAgent, render_inspection
from discord_personal_agent.config import Settings
from discord_personal_agent.conversation import SourceMessage
from discord_personal_agent.inspection import Inspection, Query


def test_command_registration_is_dm_only_with_bounded_hours():
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    commands = {
        command.name: command.to_dict(client.tree) for command in client.tree.get_commands()
    }
    assert set(commands) == {"summary", "help"}
    summary = commands["summary"]
    assert summary["contexts"] == [1]
    assert summary["integration_types"] == [0]
    options = {item["name"]: item for item in summary["options"]}
    assert options["hours"]["min_value"] == 1
    assert options["hours"]["max_value"] == 24
    assert options["topic"]["required"] is False


def test_render_is_explicit_about_overflow_and_topic_not_yet_supported():
    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=2,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    text = render_inspection(query, Inspection(501), ZoneInfo("Asia/Taipei"), has_topic=True)
    assert "2026-09-10 23:00" in text
    assert "2026-09-11 01:00" in text
    assert "至少 501" in text
    assert "縮短" in text
    assert "topic" in text


async def test_guild_invocation_never_starts_inspection():
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = 111
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("summary").callback(
        interaction, "https://discord.com/channels/111/222", 1
    )
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_awaited()


async def test_dm_success_delivers_summary_and_releases_user(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module
    from discord_personal_agent.summary import SummaryResult

    class History:
        async def authorize(self, query, requester_id):
            pass

        async def messages_newest_first(self, query):
            yield SourceMessage(
                1, 123, "Owen", query.start, "測試正文 alice@example.com", 0, False, None
            )

    async def generate(self, request, *, api_key):
        assert "alice@example.com" not in request.messages[1].content
        assert "[REDACTED]" in request.messages[1].content
        return SummaryResult.model_validate(
            {
                "status": "ok",
                "segments": [{"title": "討論", "summary": "摘要結果", "source_ids": ["m001"]}],
            }
        )

    monkeypatch.setattr(module, "DiscordHistory", lambda *args: History())
    monkeypatch.setattr(module.GroqGateway, "generate", generate)
    client = PersonalAgent(
        Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), api_key="fake"
    )
    client._connection.user = SimpleNamespace(id=999)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = None
    interaction.created_at = datetime(2026, 9, 10, 17, tzinfo=UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("summary").callback(
        interaction, "https://discord.com/channels/111/222", 1
    )
    delivered = interaction.user.send.call_args.kwargs
    assert "摘要結果" in delivered["embed"].description
    assert "測試正文" not in delivered["embed"].description
    assert "file" not in delivered
    assert not client.active_users


async def test_invalid_request_releases_user_without_delivery():
    from types import SimpleNamespace

    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = None
    interaction.created_at = datetime.now(UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("summary").callback(interaction, "invalid", 1)
    assert not client.active_users
    interaction.user.send.assert_not_awaited()
    assert "連結" in interaction.edit_original_response.call_args.kwargs["content"]


def test_hours_is_optional_and_default_reply_explains_latest_selection():
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    command = client.tree.get_command("summary").to_dict(client.tree)
    options = {item["name"]: item for item in command["options"]}
    assert options["hours"]["required"] is False
    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 10, 17, tzinfo=UTC)
    )
    text = render_inspection(query, Inspection(500), ZoneInfo("Asia/Taipei"), has_topic=False)
    assert "最新最多 500 則" in text
    assert "縮短" not in text


def test_limit_option_and_time_bounded_selection_are_explicit():
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    command = client.tree.get_command("summary").to_dict(client.tree)
    options = {item["name"]: item for item in command["options"]}
    assert options["limit"]["required"] is False
    assert options["limit"]["min_value"] == 1
    assert options["limit"]["max_value"] == 500
    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=1,
        limit=20,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    text = render_inspection(query, Inspection(20), ZoneInfo("Asia/Taipei"), has_topic=False)
    assert "最新最多 20 則" in text
    assert "2026-09-11 00:00" in text
    assert "縮短" not in text


@pytest.mark.parametrize("failure", [False, True])
async def test_long_summary_delivers_txt_and_closes_buffer_even_on_failure(failure):
    from types import SimpleNamespace

    from discord_personal_agent.bot import send_summary

    captured = {}
    text = "摘要段落\n" * 1000

    async def send(**kwargs):
        captured["buffer"] = kwargs["file"].fp
        assert kwargs["file"].filename == "summary.txt"
        assert kwargs["file"].fp.read().decode("utf-8") == text
        assert "僅預覽" in kwargs["embed"].description
        if failure:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "private response"
            )

    user = SimpleNamespace(send=send)
    if failure:
        with pytest.raises(discord.Forbidden):
            await send_summary(user, text)
    else:
        await send_summary(user, text)
    assert captured["buffer"].closed


async def test_blocked_topic_never_sends_dm_or_file(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module
    from discord_personal_agent.privacy import PrivacyGuard, Rule

    class History:
        async def authorize(self, query, requester_id):
            pass

        async def messages_newest_first(self, query):
            yield SourceMessage(1, 123, "Owen", query.start, "普通對話", 0, False, None)

    monkeypatch.setattr(module, "DiscordHistory", lambda *args: History())
    guard = PrivacyGuard((Rule("stop", "literal", "SECRET_PROJECT", "block"),))
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}, guard))
    client._connection.user = SimpleNamespace(id=999)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = None
    interaction.created_at = datetime(2026, 9, 10, 17, tzinfo=UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("summary").callback(
        interaction, "https://discord.com/channels/111/222", 1, topic="SECRET_PROJECT"
    )
    interaction.user.send.assert_not_awaited()
    assert "阻擋" in interaction.edit_original_response.call_args.kwargs["content"]
    assert "SECRET_PROJECT" not in interaction.edit_original_response.call_args.kwargs["content"]
    assert not client.active_users


@pytest.mark.parametrize(
    "link,reason",
    [
        ("https://discord.com/channels/111/333", "channel_not_allowed"),
        ("https://discord.com/channels/444/333", "guild_not_allowed"),
        ("PRIVATE_INVALID_LINK", "invalid_channel_link"),
    ],
)
async def test_inspection_logs_reason_without_echoing_input(link, reason, caplog):
    from types import SimpleNamespace

    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    client._connection.user = SimpleNamespace(id=999)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = None
    interaction.created_at = datetime(2026, 9, 12, tzinfo=UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("summary").callback(interaction, link, topic="PRIVATE_TOPIC")
    assert f"reason={reason}" in caplog.text
    assert "PRIVATE_INVALID_LINK" not in caplog.text
    assert "PRIVATE_TOPIC" not in caplog.text
    if reason != "invalid_channel_link":
        assert "channel_id=333" in caplog.text
    interaction.user.send.assert_not_awaited()
    assert not client.active_users
