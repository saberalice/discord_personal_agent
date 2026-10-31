"""Summary application boundary; no Discord or provider SDK types."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .config import Settings
from .conversation import Conversation
from .inspection import ChannelHistory, Query, SummaryApplication
from .summary import SummaryError, render_summary, render_transcript_summary
from .summary_workflow import JOB_SECONDS, LlmGateway, Notify, SummaryWorkflow
from .voice_transcript import TranscriptFile, TranscriptLink, parse_transcript


async def _quiet(text: str) -> None:
    pass


class TranscriptHistory(ChannelHistory, Protocol):
    async def transcript(self, message_id: int) -> TranscriptFile: ...

    async def latest_transcript(self) -> TranscriptFile: ...


class SummaryService:
    def __init__(self, history: TranscriptHistory, settings: Settings, gateway: LlmGateway):
        self.history = history
        self.settings = settings
        self.gateway = gateway

    async def run(
        self,
        query: Query,
        *,
        requester_id: int,
        topic: str,
        api_key: str,
        notify: Notify = _quiet,
    ) -> str:
        deadline = time.monotonic() + JOB_SECONDS
        application = SummaryApplication(
            self.history, self.settings.allowed_channels, privacy=self.settings.privacy
        )
        async with asyncio.timeout(60):
            conversation = await application.prepare(query, requester_id=requester_id, topic=topic)
        if not conversation.messages:
            return "此範圍沒有可整理的對話，未呼叫 LLM。"
        result, sources = await self._summarize(
            conversation, query, topic=topic, api_key=api_key, deadline=deadline, notify=notify
        )
        text = render_summary(result, sources, query, self.settings.timezone, self.settings.privacy)
        async with asyncio.timeout(60):
            await self.history.authorize(query, requester_id)
        return text

    async def run_transcript(
        self,
        link: str,
        *,
        requester_id: int,
        topic: str,
        api_key: str,
        notify: Notify = _quiet,
    ) -> str:
        target = TranscriptLink.parse(link)
        return await self._run_located_transcript(
            target.guild_id,
            target.channel_id,
            lambda: self.history.transcript(target.message_id),
            requester_id=requester_id,
            topic=topic,
            api_key=api_key,
            notify=notify,
        )

    async def run_latest_transcript(
        self,
        guild_id: int,
        channel_id: int,
        *,
        requester_id: int,
        topic: str,
        api_key: str,
        notify: Notify = _quiet,
    ) -> str:
        return await self._run_located_transcript(
            guild_id,
            channel_id,
            self.history.latest_transcript,
            requester_id=requester_id,
            topic=topic,
            api_key=api_key,
            notify=notify,
        )

    async def _run_located_transcript(
        self,
        guild_id: int,
        channel_id: int,
        fetch: Callable[[], Awaitable[TranscriptFile]],
        *,
        requester_id: int,
        topic: str,
        api_key: str,
        notify: Notify,
    ) -> str:
        deadline = time.monotonic() + JOB_SECONDS
        located, file, messages = await self._load_transcript(
            guild_id, channel_id, fetch, requester_id
        )
        if not messages:
            return "逐字稿沒有可摘要的內容，未呼叫 LLM。"
        query = Query(
            guild_id,
            channel_id,
            messages[0].created_at,
            messages[-1].created_at + timedelta(seconds=1),
        )
        conversation = Conversation(guild_id, channel_id, len(messages), messages)
        result, sources = await self._summarize(
            conversation, query, topic=topic, api_key=api_key, deadline=deadline, notify=notify
        )
        text = render_transcript_summary(
            result,
            sources,
            query,
            TranscriptLink(guild_id, channel_id, file.message_id).url,
            self.settings.timezone,
            self.settings.privacy,
        )
        async with asyncio.timeout(60):
            await self.history.authorize(located, requester_id, voice_text=True)
        return text

    async def _load_transcript(self, guild_id, channel_id, fetch, requester_id):
        # Only locates the channel for the permission checks; the transcript sets the times.
        located = Query(guild_id, channel_id, None, datetime.now(UTC))
        application = SummaryApplication(
            self.history, self.settings.allowed_channels, privacy=self.settings.privacy
        )
        async with asyncio.timeout(60):
            await application.authorize(located, requester_id=requester_id, voice_text=True)
            file = await fetch()
        messages = parse_transcript(file.filename, file.data, message_id=file.message_id)
        return located, file, messages

    async def _summarize(self, conversation, query, *, topic, api_key, deadline, notify):
        if not api_key.strip():
            raise SummaryError("尚未設定 Groq 金鑰，請管理員在本地設定 SHARED_LLM_API_KEY 後重啟。")
        workflow = SummaryWorkflow(
            self.gateway,
            max_input_tokens=self.settings.llm.max_input_tokens,
            timezone=self.settings.timezone,
            privacy=self.settings.privacy,
        )
        return await workflow.summarize(
            conversation, query, topic=topic, api_key=api_key, deadline=deadline, notify=notify
        )
