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


@pytest.mark.parametrize("scenario", ["ok", "blocked", "revoked", "empty", "oversized"])
async def test_summary_pipeline_checks_privacy_capacity_and_final_permission(scenario):
    class History:
        checks = 0

        async def authorize(self, query, requester_id):
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
                else ("測試" * 10000 if scenario == "oversized" else "討論"),
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
    if scenario in ("blocked", "revoked", "oversized"):
        with pytest.raises((InspectionError, SummaryError)):
            await service.run(query, requester_id=2, topic="", api_key="fake")
    else:
        text = await service.run(query, requester_id=2, topic="", api_key="fake")
        assert ("部署仍待確認" if scenario == "ok" else "沒有可整理") in text
    assert gateway.generate.await_count == (1 if scenario in ("ok", "revoked") else 0)
