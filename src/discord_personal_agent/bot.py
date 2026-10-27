"""DM commands for slice A. Each invocation owns its Discord adapter."""

import asyncio
import io
import logging
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

logger = logging.getLogger(__name__)
SUMMARY_MAX_BYTES = 8 * 1024 * 1024
HELP = (
    "以 Groq openai/gpt-oss-20b 整理文字對話，回傳含來源定位的 AI 摘要。\n"
    "私訊使用 `/summary channel:<複製的頻道連結>`，hours、limit、topic 選填。\n"
    "僅限管理員允許的測試頻道，你與 Bot 都須能檢視並讀取歷史。\n"
    "hours 為 1～24；limit 為 1～500。兩者不填取最新最多 500 則。\n"
    "兩者都填取時段內最新最多 limit 則；只填 hours 超過 500 則請縮短時間。\n"
    "topic 可指定關注主題；圖片、附件只提供佔位符，不讀取內容或外部頁面。\n"
    "先套用本機機密規則，再把對話、暱稱與 topic 送到 Groq；規則不保證涵蓋全部機密。\n"
    "單批容量不足時請縮小範圍；每人同時一筆，不自動重試；尚未實作每日計次。"
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


async def send_summary(user: discord.User, text: str) -> None:
    """Deliver an embed, or a short preview with the complete summary in memory."""
    if len(text) <= 3500:
        await user.send(embed=discord.Embed(title="頻道摘要", description=text))
        return
    payload = text.encode("utf-8")
    if len(payload) > SUMMARY_MAX_BYTES:
        raise SummaryError("摘要 TXT 超過 8 MiB 上限，請縮小範圍；未截斷交付。")
    preview = text[:3200].rsplit("\n", 1)[0] + "\n\n（僅預覽；完整摘要見 TXT）"
    with (
        io.BytesIO(payload) as buffer,
        closing(discord.File(buffer, filename="summary.txt")) as file,
    ):
        await user.send(embed=discord.Embed(title="頻道摘要", description=preview), file=file)


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
            if interaction.guild_id is not None:
                await interaction.response.send_message(
                    "請改用與 Bot 的一對一私訊。", ephemeral=True
                )
                return
            user_id = interaction.user.id
            if user_id in self.active_users:
                await interaction.response.send_message("你已有查詢進行中，請等候完成。")
                return
            self.active_users.add(user_id)
            query = None
            try:
                await interaction.response.defer(thinking=True)
                query = Query.create(
                    channel, hours=hours, limit=limit, received_at=interaction.created_at
                )
                if self.user is None:
                    raise InspectionError(
                        "Bot 尚未準備完成，請稍後再試。", code=InspectionCode.BOT_NOT_READY
                    )
                service = SummaryService(
                    DiscordHistory(self, self.user.id),
                    settings,
                    GroqGateway(
                        model="openai/gpt-oss-20b", max_output_tokens=settings.llm.max_output_tokens
                    ),
                )
                text = await service.run(
                    query, requester_id=user_id, topic=topic or "", api_key=self.api_key
                )
                async with asyncio.timeout(60):
                    with discord_operation("send_dm", "傳送結果私訊"):
                        await send_summary(interaction.user, text)
                await interaction.edit_original_response(content="摘要處理完成，結果已送到此私訊。")
            except (InspectionError, SummaryError, PrivacyError) as exc:
                if isinstance(exc, InspectionError):
                    logger.warning(
                        "summary_failed type=InspectionError reason=%s "
                        "guild_id=%s channel_id=%s stage=%s http_status=%s discord_code=%s",
                        exc.code.value,
                        query.guild_id if query else None,
                        query.channel_id if query else None,
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
                    content="Discord 讀取或私訊交付失敗；請確認連結、雙方權限及私訊設定後重試。"
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
