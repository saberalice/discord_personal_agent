from datetime import UTC, datetime

import pytest

from discord_personal_agent.inspection import InspectionError, Query, SummaryApplication


def test_query_keeps_received_time_across_midnight():
    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=2,
        received_at=datetime(2026, 9, 10, 17, 0, tzinfo=UTC),
    )
    assert query.guild_id == 111
    assert query.channel_id == 222
    assert query.start == datetime(2026, 9, 10, 15, 0, tzinfo=UTC)
    assert query.end == datetime(2026, 9, 10, 17, 0, tzinfo=UTC)


class History:
    def __init__(self, timestamps, *, denied=False):
        self.timestamps = timestamps
        self.denied = denied
        self.read = 0
        self.checks = 0

    async def authorize(self, query, requester_id, *, voice_text=False):
        self.checks += 1
        if self.denied:
            raise InspectionError("無權限")

    async def timestamps_newest_first(self, query):
        for stamp in self.timestamps:
            self.read += 1
            yield stamp


@pytest.mark.parametrize("count", [0, 1, 500, 501, 600])
async def test_inspection_counts_until_overflow(count):
    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=2,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    history = History([query.start] * count)
    result = await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)
    assert result.count == min(count, 501)
    assert result.overflow is (count > 500)
    assert history.read == min(count, 501)
    assert history.checks == 2


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.test/channels/111/222",
        "https://discordapp.com.evil.test/channels/111/222",
        "https://discordapp.com@evil.test/channels/111/222",
        "https://discord.com/channels/@me/222",
        "https://discord.com/channels/111/0",
    ],
)
def test_invalid_links_are_rejected(link):
    with pytest.raises(InspectionError):
        Query.create(link, hours=1, received_at=datetime.now(UTC))


@pytest.mark.parametrize("hours", [0, 25, True, 1.5])
def test_invalid_hours_are_rejected(hours):
    with pytest.raises(InspectionError):
        Query.create(
            "https://discord.com/channels/111/222", hours=hours, received_at=datetime.now(UTC)
        )


async def test_range_includes_start_excludes_end_and_stops_before_older_messages():
    from datetime import timedelta

    query = Query.create(
        "https://discord.com/channels/111/222/333",
        hours=1,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    history = History([query.end, query.start, query.start - timedelta(seconds=1)])
    result = await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)
    assert result.count == 1


@pytest.mark.parametrize("whitelist,denied", [({}, False), ({111: {222}}, True)])
async def test_denied_requests_never_read_history(whitelist, denied):
    history = History([], denied=denied)
    query = Query.create(
        "https://discord.com/channels/111/222", hours=1, received_at=datetime.now(UTC)
    )
    with pytest.raises(InspectionError):
        await SummaryApplication(history, whitelist).inspect(query, requester_id=123)
    assert history.read == 0
    assert history.checks == int(bool(whitelist))


async def test_revoked_permission_prevents_result():
    class RevokedHistory(History):
        async def timestamps_newest_first(self, query):
            self.denied = True
            yield query.start

    history = RevokedHistory([])
    query = Query.create(
        "https://discord.com/channels/111/222", hours=1, received_at=datetime.now(UTC)
    )
    with pytest.raises(InspectionError):
        await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)


@pytest.mark.parametrize("host", ["discord.com", "discordapp.com"])
@pytest.mark.parametrize("suffix", ["", "/333"])
def test_channel_links_accept_both_discord_hosts(host, suffix):
    query = Query.create(
        f"https://{host}/channels/111/222{suffix}",
        hours=1,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    assert (query.guild_id, query.channel_id) == (111, 222)


async def test_omitting_hours_reads_latest_500_without_overflow():
    from datetime import timedelta

    query = Query.create(
        "https://discord.com/channels/111/222", received_at=datetime(2026, 9, 10, 17, tzinfo=UTC)
    )
    assert query.start is None
    history = History([query.end - timedelta(days=n + 1) for n in range(600)])
    result = await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)
    assert result.count == 500
    assert not result.overflow
    assert history.read == 500


@pytest.mark.parametrize("hours", [None, 1])
@pytest.mark.parametrize("limit", [1, 100, 500])
async def test_explicit_limit_selects_latest_with_optional_time(hours, limit):
    from datetime import timedelta

    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=hours,
        limit=limit,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    history = History([query.end - timedelta(seconds=n + 1) for n in range(600)])
    result = await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)
    assert result.count == limit
    assert history.read == limit
    assert not result.overflow


@pytest.mark.parametrize("limit", [0, -1, 501, True, 1.5])
def test_invalid_message_limits_rejected(limit):
    with pytest.raises(InspectionError):
        Query.create(
            "https://discord.com/channels/111/222", limit=limit, received_at=datetime.now(UTC)
        )


async def test_time_with_limit_still_excludes_older_messages():
    from datetime import timedelta

    query = Query.create(
        "https://discord.com/channels/111/222",
        hours=1,
        limit=100,
        received_at=datetime(2026, 9, 10, 17, tzinfo=UTC),
    )
    history = History([query.end, query.start, query.start - timedelta(seconds=1)])
    result = await SummaryApplication(history, {111: {222}}).inspect(query, requester_id=123)
    assert result.count == 1
