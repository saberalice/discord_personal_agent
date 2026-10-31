from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import discord
import pytest

from discord_personal_agent.bot import PersonalAgent, render_inspection
from discord_personal_agent.config import Settings
from discord_personal_agent.conversation import SourceMessage
from discord_personal_agent.inspection import Inspection, Query


def test_command_registration_limits_contexts_and_hours():
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    commands = {
        command.name: command.to_dict(client.tree) for command in client.tree.get_commands()
    }
    assert set(commands) == {"summary", "help", "join", "leave", "voicesummary"}
    voice_summary = commands["voicesummary"]
    assert sorted(voice_summary["contexts"]) == [0, 1]
    voice_options = {item["name"]: item for item in voice_summary["options"]}
    assert voice_options["message"]["required"] is False
    assert voice_options["topic"]["required"] is False
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
        async def authorize(self, query, requester_id, *, voice_text=False):
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
            await send_summary(user.send, text)
    else:
        await send_summary(user.send, text)
    assert captured["buffer"].closed


async def test_blocked_topic_never_sends_dm_or_file(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module
    from discord_personal_agent.privacy import PrivacyGuard, Rule

    class History:
        async def authorize(self, query, requester_id, *, voice_text=False):
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


def test_voice_commands_mirror_the_rust_voice_program():
    import re
    from pathlib import Path

    rust = (Path(__file__).parents[1] / "voice/src/main.rs").read_text(encoding="utf-8")
    expected = dict(re.findall(r'\("(join|leave)", "([^"]+)"\)', rust))
    assert set(expected) == {"join", "leave"}
    assert "InteractionContext::Guild]" in rust
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    for name, description in expected.items():
        command = client.tree.get_command(name).to_dict(client.tree)
        assert command["description"] == description
        assert command["contexts"] == [0]
        assert command["integration_types"] == [0]


@pytest.mark.parametrize("name", ["join", "leave"])
async def test_voice_commands_leave_the_interaction_to_the_voice_program(name):
    client = PersonalAgent(Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}))
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command(name).callback(interaction)
    assert interaction.response.method_calls == []
    assert interaction.method_calls == []


def test_help_explains_voice_usage():
    from discord_personal_agent.bot import HELP

    for text in ("/join", "/leave", "/voicesummary message:", "錄音前請先告知參與者"):
        assert text in HELP
    assert "尚未提供" not in HELP


async def test_rate_limit_wait_is_shown_on_the_pending_reply(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module

    async def run(self, query, *, requester_id, topic, api_key, notify):
        await notify("Groq 速率限制：等待 5 秒後重送")
        return "摘要結果"

    monkeypatch.setattr(module.SummaryService, "run", run)
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
    edits = [call.kwargs["content"] for call in interaction.edit_original_response.await_args_list]
    assert edits[0] == "Groq 速率限制：等待 5 秒後重送"
    assert "完成" in edits[-1]


async def test_voicesummary_delivers_by_dm_and_releases_user(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module

    seen = {}

    async def run_transcript(self, link, *, requester_id, topic, api_key, notify):
        seen.update(link=link, requester_id=requester_id, topic=topic)
        return "逐字稿摘要結果"

    monkeypatch.setattr(module.SummaryService, "run_transcript", run_transcript)
    client = PersonalAgent(
        Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), api_key="fake"
    )
    client._connection.user = SimpleNamespace(id=999)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = None
    interaction.created_at = datetime(2026, 9, 10, 17, tzinfo=UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    await client.tree.get_command("voicesummary").callback(
        interaction, "https://discord.com/channels/111/222/333", "週六"
    )
    assert seen == {
        "link": "https://discord.com/channels/111/222/333",
        "requester_id": 123,
        "topic": "週六",
    }
    assert "逐字稿摘要結果" in interaction.user.send.call_args.kwargs["embed"].description
    assert not client.active_users


def voice_summary_client(monkeypatch):
    from types import SimpleNamespace

    import discord_personal_agent.bot as module

    calls = []

    async def run_transcript(self, link, *, requester_id, topic, api_key, notify):
        calls.append(("link", link))
        return "逐字稿摘要結果"

    async def run_latest_transcript(
        self, guild_id, channel_id, *, requester_id, topic, api_key, notify
    ):
        calls.append(("latest", guild_id, channel_id))
        return "逐字稿摘要結果"

    monkeypatch.setattr(module.SummaryService, "run_transcript", run_transcript)
    monkeypatch.setattr(module.SummaryService, "run_latest_transcript", run_latest_transcript)
    client = PersonalAgent(
        Settings(ZoneInfo("Asia/Taipei"), {111: frozenset({222})}), api_key="fake"
    )
    client._connection.user = SimpleNamespace(id=999)
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = 111
    interaction.channel_id = 222
    interaction.created_at = datetime(2026, 9, 10, 17, tzinfo=UTC)
    interaction.user = SimpleNamespace(id=123, send=AsyncMock())
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    return client, interaction, calls


async def test_voicesummary_in_a_channel_defaults_to_latest_and_posts_publicly(monkeypatch):
    client, interaction, calls = voice_summary_client(monkeypatch)
    await client.tree.get_command("voicesummary").callback(interaction)
    assert calls == [("latest", 111, 222)]
    assert interaction.response.defer.call_args.kwargs.get("ephemeral", False) is False
    interaction.user.send.assert_not_awaited()
    edit = interaction.edit_original_response.call_args.kwargs
    assert "逐字稿摘要結果" in edit["embed"].description
    assert edit["content"] is None
    assert not client.active_users


async def test_voicesummary_in_a_channel_accepts_a_link_to_that_channel(monkeypatch):
    client, interaction, calls = voice_summary_client(monkeypatch)
    link = "https://discord.com/channels/111/222/333"
    await client.tree.get_command("voicesummary").callback(interaction, link)
    assert calls == [("link", link)]


@pytest.mark.parametrize(
    "link",
    ["https://discord.com/channels/111/999/333", "https://discord.com/channels/888/222/333"],
    ids=["other-channel", "other-guild"],
)
async def test_voicesummary_in_a_channel_never_publishes_another_channel(monkeypatch, link):
    client, interaction, calls = voice_summary_client(monkeypatch)
    await client.tree.get_command("voicesummary").callback(interaction, link)
    assert calls == []
    assert "私訊" in interaction.edit_original_response.call_args.kwargs["content"]
    assert not client.active_users


async def test_voicesummary_in_a_dm_requires_a_link(monkeypatch):
    client, interaction, calls = voice_summary_client(monkeypatch)
    interaction.guild_id = None
    interaction.channel_id = 777
    await client.tree.get_command("voicesummary").callback(interaction)
    assert calls == []
    assert "連結" in interaction.edit_original_response.call_args.kwargs["content"]
    interaction.user.send.assert_not_awaited()


async def test_long_channel_summary_replaces_the_placeholder_with_a_txt(monkeypatch):
    import discord_personal_agent.bot as module

    client, interaction, _ = voice_summary_client(monkeypatch)

    async def long_result(self, *args, **kwargs):
        return "段落\n" * 2000

    monkeypatch.setattr(module.SummaryService, "run_latest_transcript", long_result)
    await client.tree.get_command("voicesummary").callback(interaction)
    edit = interaction.edit_original_response.call_args.kwargs
    assert [file.filename for file in edit["attachments"]] == ["summary.txt"]
    assert "僅預覽" in edit["embed"].description


async def test_second_channel_request_is_refused_only_to_the_requester(monkeypatch):
    client, interaction, calls = voice_summary_client(monkeypatch)
    client.active_users.add(123)
    await client.tree.get_command("voicesummary").callback(interaction)
    assert calls == []
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
