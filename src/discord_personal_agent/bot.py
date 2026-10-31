"""DM commands for slice A. Each invocation owns its Discord adapter."""

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from contextlib import closing
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .config import Settings
from .discord_adapter import DiscordHistory, discord_operation
from .groq_gateway import GroqGateway
from .inspection import Inspection, InspectionCode, InspectionError, Query
from .privacy import PrivacyError
from .summary import SummaryError
from .summary_service import SummaryService
from .summary_workflow import Notify
from .transcript_question import MAX_QUESTION_CHARS
from .voice_transcript import FILENAME, TranscriptLink

logger = logging.getLogger(__name__)
SUMMARY_MAX_BYTES = 8 * 1024 * 1024
HELP = (
    "### 📝 頻道摘要 `/summary`\n"
    "私訊 Bot 使用，整理伺服器文字頻道的對話，回傳附來源連結的 AI 摘要。\n"
    "- **channel**（必填）：頻道連結，在頻道上按右鍵 →「複製連結」\n"
    "- **hours**（選填）：過去幾小時，1～24\n"
    "- **limit**（選填）：最新最多幾則，1～500\n"
    "- **topic**（選填）：想聚焦的主題\n"
    "都不填時，取最新最多 500 則；同時填 hours 和 limit 時，取該時段內最新的 limit 則；"
    "只填 hours 但超過 500 則時，請縮短時間。\n"
    "\n"
    "### 🎙️ 語音逐字稿 `/join`、`/leave`\n"
    "- 先進入語音頻道，再下 `/join` 開始錄音\n"
    "- 在同一個語音頻道下 `/leave` 停止\n"
    "- 逐字稿 TXT 會公開貼在下指令的頻道，並保存在本機。**錄音前請先告知參與者。**\n"
    "\n"
    "### 📄 逐字稿摘要 `/voicesummary`\n"
    "- **在逐字稿所在頻道下指令**：自動摘要最近 100 則內最新的逐字稿，結果公開貼在頻道\n"
    "- **私訊 Bot**：`/voicesummary message:<逐字稿訊息連結>`，結果只私訊給你\n"
    "在 TXT 那則訊息上按右鍵 →「複製訊息連結」；topic 選填，該頻道須在允許清單內。\n"
    "\n"
    "### ❓ 詢問逐字稿 `/asking`\n"
    "- `/asking question:<問題>`：問本頻道最新的逐字稿，也可用 message 指定本頻道的訊息連結\n"
    "- 或對 TXT 那則訊息按右鍵（手機長按）→ 應用程式 →「詢問逐字稿」\n"
    "問題與答案公開貼在頻道；找不到依據時回答不確定。長逐字稿會分段查詢，可能要等幾分鐘。\n"
    "\n"
    "### ⚠️ 限制與隱私\n"
    "- 只能摘要管理員允許的頻道，你和 Bot 都要能檢視並讀取歷史訊息\n"
    "- 圖片與附件只顯示佔位符，不讀取內容或外部網頁\n"
    "- 會先套用本機機密規則，再把對話、暱稱與 topic 送到 Groq；規則不保證能擋下所有機密\n"
    "- 內容太多會自動分批再合併；需要超過 12 次模型呼叫或超過 600 秒時，會直接失敗\n"
    "- 遇到 Groq 速率限制時，會照指示等待並重送一次；每人同時只能執行一筆\n"
    "-# 模型：Groq openai/gpt-oss-20b"
)
# Mirrors the Rust voice program's registration in voice/src/main.rs.
ServiceFactory = Callable[[], SummaryService]
VOICE_COMMANDS = (
    ("join", "加入你所在的語音頻道並開始錄音"),
    ("leave", "停止錄音並離開語音頻道"),
)


def render_inspection(
    query: Query, result: Inspection, timezone: ZoneInfo, *, has_topic: bool
) -> str:
    end = query.end.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if query.start is None:
        selection = (
            f"範圍：{end}（{timezone.key}）之前最新最多 {query.fetch_limit} 則（不含截止時間）。"
        )
    else:
        start = query.start.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        selection = f"範圍：{start} ～ {end}（{timezone.key}；含起點、不含終點）"
        if query.limit is not None:
            selection += f"；此範圍內最新最多 {query.limit} 則。"
    count = (
        "至少 501 則（超過 500 則上限），請縮短時間範圍。"
        if result.overflow
        else (f"{result.count} 則。" if result.count else "0 則，此範圍沒有訊息。")
    )
    text = (
        f"頻道：https://discord.com/channels/{query.guild_id}/{query.channel_id}\n"
        f"{selection}\n"
        f"來源訊息：{count}\n"
        "這是讀取驗證結果，尚未呼叫 LLM；筆數包含附件訊息與 Bot 訊息。"
    )
    if has_topic:
        text += "\n本版尚未套用 topic 篩選。"
    return text


async def send_summary(
    send: Callable[..., Awaitable[object]], text: str, *, title: str = "頻道摘要"
) -> None:
    """Deliver an embed, or a short preview with the complete summary in memory."""
    if len(text) <= 3500:
        await send(embed=discord.Embed(title=title, description=text))
        return
    payload = text.encode("utf-8")
    if len(payload) > SUMMARY_MAX_BYTES:
        raise SummaryError("摘要 TXT 超過 8 MiB 上限，請縮小範圍；未截斷交付。")
    preview = text[:3200].rsplit("\n", 1)[0] + "\n\n（僅預覽；完整摘要見 TXT）"
    with (
        io.BytesIO(payload) as buffer,
        closing(discord.File(buffer, filename="summary.txt")) as file,
    ):
        await send(embed=discord.Embed(title=title, description=preview), file=file)


def _voice_command(name: str, description: str) -> app_commands.Command:
    # The Rust voice program answers these on the same bot token. Declaring them keeps
    # tree.sync (a full overwrite) from deleting them; replying here would steal the
    # interaction, which accepts only one response.
    async def leave_to_voice_program(interaction: discord.Interaction) -> None:
        pass

    command = app_commands.Command(
        name=name, description=description, callback=leave_to_voice_program
    )
    command = app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)(command)
    return app_commands.allowed_installs(guilds=True, users=False)(command)


class QuestionModal(discord.ui.Modal, title="詢問逐字稿"):
    question = discord.ui.TextInput(
        label="問題",
        style=discord.TextStyle.paragraph,
        placeholder="例如：誰提到週六要開會？",
        max_length=MAX_QUESTION_CHARS,
    )

    def __init__(self, submit, failed):
        super().__init__(timeout=600)
        self.submit = submit
        self.failed = failed

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.submit(interaction, self.question.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # The default handler logs the traceback; keep this to the same safe path as commands.
        await self.failed(interaction, error)


class PersonalAgent(discord.Client):
    def __init__(self, settings: Settings, *, api_key: str = ""):
        intents = discord.Intents.none()
        intents.guilds = True
        # Enable now to validate Portal configuration needed by the next text slice.
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.api_key = api_key
        self.active_users: set[int] = set()
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.command_error

        @self.tree.command(name="help", description="查看測試版使用說明")
        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        async def help_command(interaction: discord.Interaction):
            await interaction.response.send_message(HELP, ephemeral=True)

        @self.tree.command(name="summary", description="AI 頻道摘要：時間、則數與主題皆選填")
        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.describe(
            channel="複製的伺服器文字頻道連結",
            hours="選填：過去幾小時（1～24）；不填不限制起始時間",
            limit="選填：取最新最多幾則（1～500）",
            topic="選填：希望摘要聚焦的主題",
        )
        async def summary(
            interaction: discord.Interaction,
            channel: str,
            hours: app_commands.Range[int, 1, 24] | None = None,
            topic: str | None = None,
            limit: app_commands.Range[int, 1, 500] | None = None,
        ):

            async def job(service: ServiceFactory, notify: Notify, located: dict) -> str:
                query = Query.create(
                    channel, hours=hours, limit=limit, received_at=interaction.created_at
                )
                located.update(guild_id=query.guild_id, channel_id=query.channel_id)
                return await service().run(
                    query,
                    requester_id=interaction.user.id,
                    topic=topic or "",
                    api_key=self.api_key,
                    notify=notify,
                )

            await self._run_summary_job(interaction, job)

        @self.tree.command(name="voicesummary", description="AI 摘要語音逐字稿 TXT")
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.describe(
            message="逐字稿訊息連結；在頻道中省略時摘要最近的逐字稿，私訊中必填",
            topic="選填：希望摘要聚焦的主題",
        )
        async def voicesummary(
            interaction: discord.Interaction,
            message: str | None = None,
            topic: str | None = None,
        ):
            guild_id, channel_id = interaction.guild_id, interaction.channel_id

            async def job(service: ServiceFactory, notify: Notify, located: dict) -> str:
                common = dict(
                    requester_id=interaction.user.id,
                    topic=topic or "",
                    api_key=self.api_key,
                    notify=notify,
                )
                if message:
                    self._link_target(message, guild_id, channel_id, located)
                    return await service().run_transcript(message, **common)
                if guild_id is None:
                    raise InspectionError(
                        "私訊中請提供逐字稿訊息連結，或到逐字稿所在頻道直接下指令。",
                        code=InspectionCode.INVALID_LINK,
                    )
                located.update(guild_id=guild_id, channel_id=channel_id)
                return await service().run_latest_transcript(guild_id, channel_id, **common)

            await self._run_summary_job(interaction, job, public_in_guild=True)

        @self.tree.command(name="asking", description="詢問語音逐字稿的內容；預設問最新一份")
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.describe(
            question="想問的問題，例如：誰提到週六要開會？",
            message="選填：本頻道的逐字稿訊息連結；省略時問最近 100 則內最新的逐字稿",
        )
        async def asking(
            interaction: discord.Interaction,
            question: app_commands.Range[str, 1, MAX_QUESTION_CHARS],
            message: str | None = None,
        ):
            await self._ask(interaction, question, link=message)

        @self.tree.context_menu(name="詢問逐字稿")
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        async def ask_about_message(interaction: discord.Interaction, message: discord.Message):
            if not self._is_transcript(message):
                await interaction.response.send_message(
                    "這則訊息不是本 Bot 貼出的語音逐字稿。", ephemeral=True
                )
                return

            async def submit(submitted: discord.Interaction, question: str) -> None:
                await self._ask(submitted, question, message_id=message.id)

            await interaction.response.send_modal(QuestionModal(submit, self.command_error))

        for name, description in VOICE_COMMANDS:
            self.tree.add_command(_voice_command(name, description))

    def _is_transcript(self, message: discord.Message) -> bool:
        return (
            self.user is not None
            and message.author.id == self.user.id
            and any(FILENAME.fullmatch(item.filename) for item in message.attachments)
        )

    @staticmethod
    def _link_target(
        link: str, guild_id: int | None, channel_id: int | None, located: dict
    ) -> TranscriptLink:
        target = TranscriptLink.parse(link)
        located.update(guild_id=target.guild_id, channel_id=target.channel_id)
        # The result is public in a channel, so another channel's content must not reach it.
        if guild_id is not None and (target.guild_id, target.channel_id) != (guild_id, channel_id):
            raise InspectionError(
                "在頻道中只能處理本頻道的逐字稿；其他頻道的逐字稿請到該頻道操作或改用私訊摘要。",
                code=InspectionCode.INVALID_LINK,
            )
        return target

    async def _ask(
        self,
        interaction: discord.Interaction,
        question: str,
        *,
        link: str | None = None,
        message_id: int | None = None,
    ) -> None:
        guild_id, channel_id = interaction.guild_id, interaction.channel_id

        async def job(service: ServiceFactory, notify: Notify, located: dict) -> str:
            located.update(guild_id=guild_id, channel_id=channel_id)
            chosen = message_id
            if link:
                chosen = self._link_target(link, guild_id, channel_id, located).message_id
            return await service().answer_transcript(
                guild_id,
                channel_id,
                chosen,
                question,
                requester_id=interaction.user.id,
                api_key=self.api_key,
                notify=notify,
            )

        await self._run_summary_job(interaction, job, public_in_guild=True, title="逐字稿問答")

    async def _run_summary_job(
        self,
        interaction: discord.Interaction,
        job: Callable[[ServiceFactory, Notify, dict], Awaitable[str]],
        *,
        public_in_guild: bool = False,
        title: str = "頻道摘要",
    ) -> None:
        """Lifecycle shared by summary commands: one job per user, delivered by DM or in place."""
        in_guild = interaction.guild_id is not None
        if in_guild and not public_in_guild:
            await interaction.response.send_message("請改用與 Bot 的一對一私訊。", ephemeral=True)
            return
        user_id = interaction.user.id
        if user_id in self.active_users:
            await interaction.response.send_message(
                "你已有查詢進行中，請等候完成。", ephemeral=in_guild
            )
            return
        self.active_users.add(user_id)
        # Filled by the job once its link is parsed, so failures can name the channel.
        located: dict = {}
        try:
            await interaction.response.defer(thinking=True)

            def service() -> SummaryService:
                # Built after the job validates its input, so input errors are reported first.
                if self.user is None:
                    raise InspectionError(
                        "Bot 尚未準備完成，請稍後再試。", code=InspectionCode.BOT_NOT_READY
                    )
                return SummaryService(
                    DiscordHistory(self, self.user.id),
                    self.settings,
                    GroqGateway(
                        model="openai/gpt-oss-20b",
                        max_output_tokens=self.settings.llm.max_output_tokens,
                    ),
                )

            async def notify(text: str) -> None:
                # Long waits (provider rate limits) replace the "thinking" placeholder.
                await interaction.edit_original_response(content=text)

            text = await job(service, notify, located)
            async with asyncio.timeout(60):
                if in_guild:

                    async def replace_placeholder(*, embed, file=None) -> None:
                        await interaction.edit_original_response(
                            content=None, embed=embed, attachments=[file] if file else []
                        )

                    with discord_operation("send_channel", "傳送結果到頻道"):
                        await send_summary(replace_placeholder, text, title=title)
                    return
                with discord_operation("send_dm", "傳送結果私訊"):
                    await send_summary(interaction.user.send, text, title=title)
            await interaction.edit_original_response(content="摘要處理完成，結果已送到此私訊。")
        except (InspectionError, SummaryError, PrivacyError) as exc:
            if isinstance(exc, InspectionError):
                logger.warning(
                    "summary_failed type=InspectionError reason=%s "
                    "guild_id=%s channel_id=%s stage=%s http_status=%s discord_code=%s",
                    exc.code.value,
                    located.get("guild_id"),
                    located.get("channel_id"),
                    exc.stage,
                    exc.http_status,
                    exc.discord_code,
                )
            else:
                logger.warning("summary_failed type=%s", type(exc).__name__)
            await interaction.edit_original_response(content=str(exc))
        except TimeoutError:
            logger.warning("inspection_timeout")
            await interaction.edit_original_response(content="查詢逾時，請縮短範圍或稍後再試。")
        except (discord.HTTPException, discord.InvalidData) as exc:
            logger.warning(
                "discord_request_failed stage=interaction_or_channel_data "
                "type=%s status=%s code=%s",
                type(exc).__name__,
                getattr(exc, "status", None),
                getattr(exc, "code", None),
            )
            await interaction.edit_original_response(
                content="Discord 讀取或結果交付失敗；請確認連結、雙方權限及私訊設定後重試。"
            )
        finally:
            self.active_users.discard(user_id)

    async def setup_hook(self) -> None:
        commands = await self.tree.sync()
        logger.info("global_commands_synced count=%d", len(commands))

    async def on_ready(self) -> None:
        logger.info("bot_ready")

    async def command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        # Do not print exception bodies, interaction parameters or message content.
        logger.error("command_failed type=%s", type(error).__name__)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content="指令未能完成，請稍後再試。")
            else:
                await interaction.response.send_message(
                    "指令未能完成，請稍後再試。", ephemeral=True
                )
        except discord.HTTPException as exc:
            logger.warning("error_reply_failed type=%s", type(exc).__name__)
