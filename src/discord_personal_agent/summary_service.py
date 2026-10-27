"""Single-batch application boundary; no Discord or provider SDK types."""

import asyncio
from typing import Protocol

from .config import Settings
from .inspection import ChannelHistory, Query, SummaryApplication
from .summary import (
    SummaryError,
    SummaryInput,
    SummaryResult,
    build_summary_input,
    check_capacity,
    render_summary,
)


class LlmGateway(Protocol):
    async def generate(self, request: SummaryInput, *, api_key: str) -> SummaryResult: ...


class SummaryService:
    def __init__(self, history: ChannelHistory, settings: Settings, gateway: LlmGateway):
        self.history = history
        self.settings = settings
        self.gateway = gateway

    async def run(self, query: Query, *, requester_id: int, topic: str, api_key: str) -> str:
        application = SummaryApplication(
            self.history, self.settings.allowed_channels, privacy=self.settings.privacy
        )
        async with asyncio.timeout(60):
            conversation = await application.prepare(query, requester_id=requester_id, topic=topic)
        if not conversation.messages:
            return "此範圍沒有可整理的對話，未呼叫 LLM。"
        if not api_key.strip():
            raise SummaryError("尚未設定 Groq 金鑰，請管理員在本地設定 SHARED_LLM_API_KEY 後重啟。")
        request = build_summary_input(
            conversation,
            query,
            topic=topic,
            timezone=self.settings.timezone,
            privacy=self.settings.privacy,
        )
        await asyncio.to_thread(check_capacity, request, self.settings.llm.max_input_tokens)
        async with asyncio.timeout(90):
            result = await self.gateway.generate(request, api_key=api_key)
        text = render_summary(result, request, query, self.settings.timezone, self.settings.privacy)
        async with asyncio.timeout(60):
            await self.history.authorize(query, requester_id)
        return text
