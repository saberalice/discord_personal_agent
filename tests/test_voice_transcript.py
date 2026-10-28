from datetime import UTC, datetime

import pytest

from discord_personal_agent.inspection import InspectionError
from discord_personal_agent.voice_transcript import TranscriptLink, parse_transcript


def test_lines_become_sources_with_taiwan_times_that_roll_over_midnight():
    data = "[23:59:58] 隊長: 推進右邊\n[23:59:59] Alex: 好: 我跟\n[00:00:03] 隊長: 補血\n".encode()
    messages = parse_transcript("2026-09-11_23-59-50.txt", data, message_id=555)
    assert [m.display_name for m in messages] == ["隊長", "Alex", "隊長"]
    assert [m.content for m in messages] == ["推進右邊", "好: 我跟", "補血"]
    assert [m.created_at for m in messages] == [
        datetime(2026, 9, 11, 15, 59, 58, tzinfo=UTC),
        datetime(2026, 9, 11, 15, 59, 59, tzinfo=UTC),
        datetime(2026, 9, 11, 16, 0, 3, tzinfo=UTC),
    ]
    # Every line points at the transcript message: that is its only Discord location.
    assert {m.id for m in messages} == {555}
    assert not any(m.is_system or m.attachment_count for m in messages)


def test_first_line_before_the_file_time_belongs_to_the_next_day():
    messages = parse_transcript("2026-09-11_23-59-59.txt", b"[00:00:01] A: x\n", message_id=1)
    assert messages[0].created_at == datetime(2026, 9, 11, 16, 0, 1, tzinfo=UTC)


def test_empty_transcript_has_no_sources():
    assert parse_transcript("2026-09-11_10-00-00.txt", b"", message_id=1) == ()


@pytest.mark.parametrize(
    "filename,data",
    [
        ("2026-09-11_10-00-00.txt", b"[10:00:01] A: ok\nnot a transcript line\n"),
        ("2026-09-11_10-00-00.txt", b"[25:00:00] A: bad clock\n"),
        ("2026-09-11_10-00-00.txt", b"[10:00:01] A: ok\r\n"),
        ("2026-09-11_10-00-00.txt", b"\xff\xfe"),
        ("notes.txt", b"[10:00:01] A: ok\n"),
        ("2026-13-11_10-00-00.txt", b"[10:00:01] A: ok\n"),
    ],
    ids=["free-text", "bad-clock", "crlf", "not-utf8", "filename", "bad-date"],
)
def test_anything_unparseable_fails_the_whole_transcript(filename, data):
    with pytest.raises(InspectionError) as failure:
        parse_transcript(filename, data, message_id=1)
    assert failure.value.code.value == "invalid_transcript"
    assert "ok" not in str(failure.value) and "bad clock" not in str(failure.value)


def test_message_link_is_required():
    target = TranscriptLink.parse("https://discord.com/channels/111/222/333")
    assert (target.guild_id, target.channel_id, target.message_id) == (111, 222, 333)
    assert target.url == "https://discord.com/channels/111/222/333"
    for link in ("https://discord.com/channels/111/222", "https://example.com/channels/1/2/3"):
        with pytest.raises(InspectionError) as failure:
            TranscriptLink.parse(link)
        assert failure.value.code.value == "invalid_channel_link"
