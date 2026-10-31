from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import discord
import pytest

from discord_personal_agent.discord_adapter import DiscordHistory
from discord_personal_agent.inspection import InspectionError, Query, SummaryApplication


@pytest.fixture
def setup():
    client = discord.Client(intents=discord.Intents.none())
    client.http.get_guild = AsyncMock(
        return_value={
            "id": "111",
            "owner_id": "777",
            "roles": [{"id": "111", "name": "@everyone", "permissions": "66560"}],
        }
    )
    client.http.get_channel = AsyncMock(
        return_value={
            "id": "222",
            "guild_id": "111",
            "type": 0,
            "name": "test",
            "position": 0,
            "permission_overwrites": [],
        }
    )

    async def member(guild_id, user_id):
        return {
            "user": {
                "id": str(user_id),
                "username": "tester",
                "discriminator": "0",
                "avatar": None,
            },
            "roles": [],
            "flags": 0,
        }

    client.http.get_member = AsyncMock(side_effect=member)
    client.http.logs_from = AsyncMock(return_value=[])
    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=1,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    return client, query


@pytest.mark.parametrize("target,denied", [(123, 1024), (999, 65536)])
async def test_member_overwrite_denies_requester_or_bot(setup, target, denied):
    client, query = setup
    client.http.get_channel.return_value["permission_overwrites"] = [
        {"id": str(target), "type": 1, "allow": "0", "deny": str(denied)}
    ]
    with pytest.raises(InspectionError) as failure:
        await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).inspect(
            query, requester_id=123
        )
    client.http.logs_from.assert_not_awaited()

    assert failure.value.code.value == (
        "bot_permission_denied" if target == 999 else "requester_permission_denied"
    )


@pytest.mark.parametrize(
    "hours,limit,expected,pages",
    [
        (1, None, 501, [100, 100, 100, 100, 100, 1]),
        (None, None, 500, [100, 100, 100, 100, 100]),
        (None, 20, 20, [20]),
        (1, 101, 101, [100, 1]),
    ],
)
async def test_real_sdk_pagination_respects_selection(setup, hours, limit, expected, pages):
    client, original = setup
    query = Query.create(
        "https://discord.com/channels/111/222", hours=hours, limit=limit, received_at=original.end
    )
    ids = [discord.utils.time_snowflake(query.end - timedelta(seconds=n + 1)) for n in range(600)]
    calls = []

    async def logs(channel_id, limit, *, before):
        calls.append(limit)
        eligible = [n for n in ids if n < before][:limit]
        return [
            {
                "id": str(n),
                "channel_id": "222",
                "type": 0,
                "content": "",
                "author": {"id": "123", "username": "tester", "discriminator": "0", "avatar": None},
                "attachments": [],
                "embeds": [],
                "edited_timestamp": None,
                "pinned": False,
                "mention_everyone": False,
                "tts": False,
            }
            for n in eligible
        ]

    client.http.logs_from.side_effect = logs
    result = await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).inspect(
        query, requester_id=123
    )
    assert result.count == expected
    assert calls == pages


@pytest.mark.parametrize("channel_type", [2, 5, 15])
async def test_nonstandard_text_channels_are_rejected(setup, channel_type):
    client, query = setup
    client.http.get_channel.return_value.update(
        type=channel_type, bitrate=64000, user_limit=0, available_tags=[]
    )
    with pytest.raises(InspectionError):
        await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).inspect(
            query, requester_id=123
        )
    client.http.logs_from.assert_not_awaited()


async def test_sdk_rejects_channel_from_different_guild(setup):
    client, query = setup
    client.http.get_channel.return_value["guild_id"] = "444"
    with pytest.raises(discord.InvalidData):
        await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).inspect(
            query, requester_id=123
        )
    client.http.logs_from.assert_not_awaited()


@pytest.mark.parametrize(
    "method,label",
    [
        ("get_guild", "讀取伺服器"),
        ("get_channel", "讀取頻道"),
        ("get_member", "查核 Bot 成員"),
        ("logs_from", "讀取訊息歷史"),
    ],
)
async def test_forbidden_reports_operation_without_response_body(setup, caplog, method, label):
    from types import SimpleNamespace

    client, query = setup
    getattr(client.http, method).side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"code": 50001, "message": "PRIVATE_RESPONSE_BODY"},
    )
    with pytest.raises(InspectionError) as failure:
        await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).inspect(
            query, requester_id=123
        )
    assert failure.value.code.value == "discord_api_failed"
    assert failure.value.http_status == 403
    assert failure.value.discord_code == 50001
    assert failure.value.stage is not None
    assert label in str(failure.value)
    assert "50001" in str(failure.value)
    assert "status=403" in caplog.text
    assert "PRIVATE_RESPONSE_BODY" not in caplog.text + str(failure.value)


@pytest.mark.parametrize("departed", [False, True])
async def test_prepare_reads_content_and_resolves_current_nickname_once(setup, departed):
    client, query = setup
    message_id = discord.utils.time_snowflake(query.end - timedelta(seconds=1))
    client.http.get_member.return_value = None
    original_member = client.http.get_member.side_effect

    async def member(guild_id, user_id):
        if departed and user_id == 456:
            from types import SimpleNamespace

            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10007, "message": "Unknown Member"},
            )
        data = await original_member(guild_id, user_id)
        data["nick"] = "現在的暱稱"
        return data

    client.http.get_member.side_effect = member
    client.http.logs_from.return_value = [
        {
            "id": str(message_id - n),
            "channel_id": "222",
            "type": 0,
            "content": "正文全文",
            "author": {
                "id": "456",
                "username": "fallback",
                "global_name": "全域名稱",
                "discriminator": "0",
                "avatar": None,
            },
            "attachments": [],
            "embeds": [],
            "edited_timestamp": None,
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
        }
        for n in range(2)
    ]
    result = await SummaryApplication(DiscordHistory(client, 999), {111: {222}}).prepare(
        query, requester_id=123
    )
    assert [m.content for m in result.messages] == ["正文全文", "正文全文"]
    assert {m.display_name for m in result.messages} == {"全域名稱" if departed else "現在的暱稱"}
    assert sum(call.args == (111, 456) for call in client.http.get_member.await_args_list) == 1


def voice_channel(channel_type):
    extra = {"bitrate": 64000, "user_limit": 0, "rtc_region": None} if channel_type == 2 else {}
    return {"type": channel_type, **extra}


def transcript_message(author_id=999, filename="2026-09-11_10-00-00.txt", size=20):
    return {
        "id": "333",
        "channel_id": "222",
        "author": {"id": str(author_id), "username": "bot", "discriminator": "0", "avatar": None},
        "content": "本場語音紀錄已完成，請下載文字檔。",
        "timestamp": "2026-09-11T02:00:00+00:00",
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [
            {
                "id": "444",
                "filename": filename,
                "size": size,
                "url": "https://cdn.discordapp.com/attachments/222/444/file.txt",
                "proxy_url": "https://media.discordapp.net/attachments/222/444/file.txt",
            }
        ],
        "embeds": [],
        "pinned": False,
        "type": 0,
    }


@pytest.mark.parametrize("channel_type", [0, 2])
async def test_transcript_is_read_from_the_bots_own_attachment(setup, channel_type):
    client, query = setup
    client.http.get_channel.return_value.update(voice_channel(channel_type))
    client.http.get_message = AsyncMock(return_value=transcript_message())
    client.http.get_from_cdn = AsyncMock(return_value=b"[10:00:01] A: ok\n")
    history = DiscordHistory(client, 999)
    await history.authorize(query, 123, voice_text=True)
    file = await history.transcript(333)
    assert (file.filename, file.data) == ("2026-09-11_10-00-00.txt", b"[10:00:01] A: ok\n")
    assert file.message_id == 333
    client.http.get_message.assert_awaited_once_with(222, 333)


async def test_voice_channel_is_still_rejected_for_channel_summaries(setup):
    client, query = setup
    client.http.get_channel.return_value.update(voice_channel(2))
    with pytest.raises(InspectionError) as failure:
        await DiscordHistory(client, 999).authorize(query, 123)
    assert failure.value.code.value == "unsupported_channel_type"


@pytest.mark.parametrize(
    "message",
    [
        transcript_message(author_id=555),
        transcript_message(filename="notes.txt"),
        transcript_message(size=8 * 1024 * 1024 + 1),
    ],
    ids=["other-author", "other-file", "too-large"],
)
async def test_other_messages_are_refused_without_downloading(setup, message):
    client, query = setup
    client.http.get_message = AsyncMock(return_value=message)
    client.http.get_from_cdn = AsyncMock()
    history = DiscordHistory(client, 999)
    await history.authorize(query, 123, voice_text=True)
    with pytest.raises(InspectionError) as failure:
        await history.transcript(333)
    assert failure.value.code.value == "invalid_transcript"
    client.http.get_from_cdn.assert_not_awaited()


def history_page(*messages):
    async def logs(channel_id, limit, **kwargs):
        logs.limits.append(limit)
        return list(messages)

    logs.limits = []
    return logs


async def test_latest_transcript_skips_chatter_and_takes_the_newest(setup):
    client, query = setup
    chatter = {**transcript_message(author_id=555), "id": "400"}
    plain_bot = {**transcript_message(), "id": "399", "attachments": []}
    newest = {**transcript_message(), "id": "398"}
    older = {**transcript_message(filename="2026-09-10_10-00-00.txt"), "id": "397"}
    client.http.logs_from = history_page(chatter, plain_bot, newest, older)
    client.http.get_from_cdn = AsyncMock(return_value=b"[10:00:01] A: ok\n")
    history = DiscordHistory(client, 999)
    await history.authorize(query, 123, voice_text=True)
    file = await history.latest_transcript()
    assert (file.filename, file.message_id) == ("2026-09-11_10-00-00.txt", 398)
    assert client.http.logs_from.limits == [100]
    client.http.get_from_cdn.assert_awaited_once()


async def test_latest_transcript_missing_fails_without_downloading(setup):
    client, query = setup
    client.http.logs_from = history_page({**transcript_message(author_id=555), "id": "400"})
    client.http.get_from_cdn = AsyncMock()
    history = DiscordHistory(client, 999)
    await history.authorize(query, 123, voice_text=True)
    with pytest.raises(InspectionError) as failure:
        await history.latest_transcript()
    assert failure.value.code.value == "invalid_transcript"
    assert "100" in str(failure.value)
    client.http.get_from_cdn.assert_not_awaited()
