import os
import re
import time
import random
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.vip_manager import vip_manager, require_bot_admin
from core.memory_service import MemoryService
from core.proactive_judge import ProactiveJudge
from core.memory.models import normalize_persona_id, normalize_scope_key, normalize_user_id
from core.context_manager import ContextManager
from utils.persona_manager import PersonaManager, PERSONA_DIR
from utils.context_builder import ContextBuilder, build_bot_name_overrides, resolve_discord_mentions
from utils.message_delivery import MessageDeliveryService
from utils.gemini_dialog_service import GeminiDialogService

logger = logging.getLogger("bot.ai_roleplay")


class KeepTyping:
    """Async context manager to continuously send 'typing...' status to Discord every 4.5 seconds until exiting."""

    def __init__(self, channel: discord.abc.Messageable):
        self.channel = channel
        self.task: Optional[asyncio.Task] = None

    async def _loop(self):
        try:
            while True:
                await self.channel.typing()
                await asyncio.sleep(4.5)
        except (asyncio.CancelledError, Exception):
            pass

    async def __aenter__(self):
        self.task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


# --- Interactive Discord UI Select Menu Components ---

class PersonaSelectMenu(discord.ui.Select):
    def __init__(self, cog: "AIRoleplayCog", files: List[str]):
        options = []
        for f in files[:25]:
            details = cog.persona_manager.get_persona_details(f)
            is_active = (f == cog.persona_manager.current_persona_name)
            label = f"{details['display_name']} {'(目前使用中)' if is_active else ''}"
            avatar_info = "| 附帶頭像" if details["avatar_exists"] else ""
            desc = f"檔名: {f} {avatar_info}"

            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=f,
                    description=desc[:100],
                    default=is_active
                )
            )
        super().__init__(placeholder="👉 點擊此處下拉選單選擇要切換的 AI 角色...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        selected_file = self.values[0]
        filepath = os.path.join(PERSONA_DIR, selected_file)
        if not os.path.exists(filepath):
            await interaction.response.send_message(f"❌ 找不到該設定檔：`{selected_file}`！", ephemeral=False)
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                new_content = f.read()

            self.cog.persona_manager.save_active_persona(selected_file, new_content)
            self.cog.dialog_service.clear_all_histories()
            self.cog._clear_all_hot_states()

            avatar_updated, nick_updated = await self.cog.persona_manager.update_bot_identity(
                interaction, selected_file, self.cog.bot.user
            )

            details = self.cog.persona_manager.get_persona_details(selected_file)
            json_status = f"`{details['json_filename']}`" if details["has_json"] else "預設 fallback"
            avatar_status = "✅ 頭像已同步更換" if avatar_updated else "ℹ️ 無新頭像檔或受 Discord 1小時變更頻率限制"
            nick_status = f"✅ 伺服器暱稱已變更為「{self.cog.persona_manager.persona_display_name}」" if nick_updated else "ℹ️ 暱稱未變更"

            embed = discord.Embed(
                title=f"✅ 已成功切換 AI 角色為：{self.cog.persona_manager.persona_display_name}",
                color=discord.Color.green(),
                description=f"📄 **人設檔案**：`{selected_file}`\n"
                            f"⚙️ **角色設定檔**：{json_status}\n"
                            f"🖼️ **頭像更新**：{avatar_status}\n"
                            f"🏷️ **暱稱更新**：{nick_status}\n\n"
                            f"**人設內容預覽：**\n```{new_content[:300]}...```"
            )
            embed.set_footer(text="對話歷史已自動重置，重啟機器人後亦會自動保留此設定！")
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logger.error(f"Error switching persona via UI menu to {selected_file}: {e}")
            await interaction.response.send_message(f"❌ 切換設定檔時發生錯誤：{e}", ephemeral=False)


class PersonaSelectView(discord.ui.View):
    def __init__(self, cog: "AIRoleplayCog", files: List[str]):
        super().__init__(timeout=120)
        self.add_item(PersonaSelectMenu(cog, files))


# --- Main AIRoleplayCog (Thin Controller) ---


@dataclass
class HotWindowState:
    """Tracks per-channel Mode A continuation window and per-user pass/ignore state."""

    expires_at: float
    active_users: Dict[int, int] = field(default_factory=dict)
    active_user_names: Dict[int, str] = field(default_factory=dict)
    bystander_passes: Dict[int, int] = field(default_factory=dict)
    ignored_bystanders: Set[int] = field(default_factory=set)


class AIRoleplayCog(commands.Cog):
    """Refactored Discord Controller coordinating Persona, Context, LLM Dialog, and Message Delivery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.persona_manager = PersonaManager()
        self.context_builder = ContextBuilder()
        self.delivery_service = MessageDeliveryService()
        self.dialog_service = GeminiDialogService(api_key=config.GEMINI_API_KEY)
        self.memory_service = MemoryService(ai_client=self.dialog_service.ai_client, data_dir="data")
        self.dialog_service.memory_service = self.memory_service
        self.proactive_judge = ProactiveJudge(ai_client=self.dialog_service.ai_client)
        # Peer-bot decay interaction state: {channel_id: {"count": int, "last_ts": float}}
        self._channel_bot_streaks: Dict[int, Dict[str, float]] = {}
        self._peer_replying_channels: Set[int] = set()
        # Mode A hot continuation window & per-(channel_id, user_id) debounce state
        self._hot_windows: Dict[int, HotWindowState] = {}
        self._debounce_buffers: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        self._debounce_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

    async def cog_load(self):
        """Initialize SQLite vector memory store and start background idle extraction worker."""
        await self.memory_service.initialize()

    async def cog_unload(self):
        """Gracefully stop pending proactive debounces, idle memory worker, and SQLite connection."""
        self._clear_all_hot_states()
        await self.memory_service.close()

    # Backwards-compatible properties
    @property
    def current_persona_name(self) -> str:
        return self.persona_manager.current_persona_name

    @property
    def persona_display_name(self) -> str:
        return self.persona_manager.persona_display_name

    @property
    def persona(self) -> str:
        return self.persona_manager.persona

    @property
    def error_messages(self):
        return self.persona_manager.error_messages

    @property
    def channel_histories(self):
        return self.dialog_service.channel_histories

    @property
    def ai_client(self):
        return self.dialog_service.ai_client

    def get_msg(self, key: str, **kwargs) -> str:
        return self.persona_manager.get_msg(key, **kwargs)

    def get_active_persona_aliases(self) -> List[str]:
        """Return list of wake-word aliases corresponding to the currently active persona."""
        return self.persona_manager.get_active_persona_aliases()

    @commands.Cog.listener()
    async def on_ready(self):
        """Automatically sync bot server nicknames across all joined guilds to match active persona on startup."""
        await self.persona_manager.sync_bot_identity_all_guilds(self.bot.guilds)

    def _get_effective_peer_bot_ids(self) -> Set[int]:
        """Return configured peer bot IDs excluding this bot's own user ID."""
        if self.bot.user:
            return config.PEER_BOT_IDS - {self.bot.user.id}
        return set(config.PEER_BOT_IDS)

    def _reset_channel_streak(self, channel_id: Optional[int]) -> None:
        """Reset consecutive bot-to-bot interaction counter for a channel."""
        if channel_id is not None:
            self._channel_bot_streaks.pop(channel_id, None)

    def _record_bot_streak(self, channel_id: int, now: Optional[float] = None) -> int:
        """Increment or restart the consecutive bot turn counter for a channel."""
        current_ts = time.monotonic() if now is None else now
        entry = self._channel_bot_streaks.get(channel_id)
        if entry and (current_ts - entry["last_ts"]) < config.PEER_BOT_STREAK_TIMEOUT:
            streak = int(entry["count"]) + 1
        else:
            streak = 1
        self._channel_bot_streaks[channel_id] = {"count": streak, "last_ts": current_ts}
        return streak

    @staticmethod
    def _get_peer_reply_probability(streak: int) -> float:
        """Return decayed reply probability for a 1-based consecutive bot streak count."""
        probs = config.PEER_BOT_REPLY_PROBS
        if streak <= 0 or streak > len(probs):
            return 0.0
        return probs[streak - 1]

    def _get_hot_window(self, channel_id: int, now: Optional[float] = None) -> Optional[HotWindowState]:
        """Return active hot window for `channel_id`, pruning if expired or empty."""
        state = self._hot_windows.get(channel_id)
        if not state:
            return None
        current_ts = time.monotonic() if now is None else now
        if current_ts >= state.expires_at or not state.active_users:
            self._hot_windows.pop(channel_id, None)
            return None
        return state

    def _register_hot_participant(
        self,
        channel_id: Optional[int],
        user_id: int,
        display_name: str = "",
        now: Optional[float] = None,
    ) -> Optional[HotWindowState]:
        """Add or refresh `user_id` in the channel hot window and extend window TTL."""
        if channel_id is None or not config.PROACTIVE_HOT_WINDOW_ENABLED:
            return None
        current_ts = time.monotonic() if now is None else now
        state = self._get_hot_window(channel_id, now=current_ts)
        if state is None:
            state = HotWindowState(expires_at=current_ts + config.PROACTIVE_HOT_WINDOW_SECONDS)
            self._hot_windows[channel_id] = state
        else:
            state.expires_at = current_ts + config.PROACTIVE_HOT_WINDOW_SECONDS

        state.ignored_bystanders.discard(user_id)
        state.bystander_passes.pop(user_id, None)

        # Re-insert to track most recent interaction order for LRU eviction at capacity
        state.active_users.pop(user_id, None)
        state.active_users[user_id] = 0
        if display_name:
            state.active_user_names[user_id] = display_name

        max_users = max(1, config.PROACTIVE_HOT_MAX_ACTIVE_USERS)
        while len(state.active_users) > max_users:
            evicted_uid = next(iter(state.active_users))
            state.active_users.pop(evicted_uid, None)
            state.active_user_names.pop(evicted_uid, None)

        return state

    def _record_user_pass(self, channel_id: int, user_id: int, now: Optional[float] = None) -> None:
        """Increment consecutive pass counter for an active user or bystander, evicting/ignoring at limits."""
        state = self._get_hot_window(channel_id, now=now)
        if not state:
            return

        if user_id in state.active_users:
            state.active_users[user_id] += 1
            if state.active_users[user_id] >= config.PROACTIVE_HOT_MAX_CONSECUTIVE_PASS:
                state.active_users.pop(user_id, None)
                state.active_user_names.pop(user_id, None)
                if not state.active_users:
                    self._hot_windows.pop(channel_id, None)
        else:
            passes = state.bystander_passes.get(user_id, 0) + 1
            state.bystander_passes[user_id] = passes
            if passes >= config.PROACTIVE_BYSTANDER_MAX_PASS:
                state.ignored_bystanders.add(user_id)

    def _cancel_user_debounce(self, channel_id: Optional[int], user_id: int) -> None:
        """Cancel any pending debounce task and discard buffered messages for `(channel_id, user_id)`."""
        if channel_id is None:
            return
        key = (channel_id, user_id)
        task = self._debounce_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        self._debounce_buffers.pop(key, None)

    def _clear_channel_hot_state(self, channel_id: Optional[int]) -> None:
        """Clear hot window and pending debounces for a single channel."""
        if channel_id is None:
            return
        self._hot_windows.pop(channel_id, None)
        keys = [k for k in self._debounce_tasks if k[0] == channel_id]
        for key in keys:
            self._cancel_user_debounce(key[0], key[1])

    def _clear_all_hot_states(self) -> None:
        """Cancel all pending debounces and clear all hot windows."""
        self._hot_windows.clear()
        for key in list(self._debounce_tasks.keys()):
            self._cancel_user_debounce(key[0], key[1])
        self._debounce_buffers.clear()

    async def _respond_to_message(
        self,
        message: discord.Message,
        ref_context: str,
        prompt_content: str,
        persona_id: str,
        image_bytes: Optional[bytes],
        image_mime: str,
        extra_image_parts: Optional[List],
        query_text: Optional[str] = None,
        is_peer_bot: bool = False,
    ) -> None:
        """Generate an AI reply and deliver it to the channel with typing feedback."""
        async with KeepTyping(message.channel):
            full_user_text = f"{ref_context}[{message.author.display_name}]: {prompt_content or '（這張圖片怎麼樣？）'}"
            effective_query = prompt_content if query_text is None else query_text

            reply_text = await self.dialog_service.generate_response(
                channel_id=message.channel.id,
                user_text=full_user_text,
                persona_manager=self.persona_manager,
                context_builder=self.context_builder,
                user_id=message.author.id,
                channel=message.channel,
                current_message_id=message.id,
                image_bytes=image_bytes,
                image_mime=image_mime,
                extra_image_parts=extra_image_parts,
                bot_user=self.bot.user,
                persona_id=persona_id,
                query_text=effective_query,
                skip_vip_check=is_peer_bot,
                is_peer_bot=is_peer_bot,
            )

            await self.delivery_service.send_split_message(
                target_message=message,
                channel=message.channel,
                text=reply_text,
            )

        is_error_reply = isinstance(reply_text, str) and "`[Detail:" in reply_text
        if (
            not is_peer_bot
            and not isinstance(message.channel, discord.DMChannel)
            and not is_error_reply
        ):
            self._register_hot_participant(
                channel_id=message.channel.id,
                user_id=message.author.id,
                display_name=message.author.display_name,
            )

    def _enqueue_proactive_debounce(
        self,
        message: discord.Message,
        text: str,
        image_bytes: Optional[bytes],
        image_mime: str,
        persona_id: str,
    ) -> None:
        """Buffer rapid messages per `(channel_id, user_id)` and schedule lightweight Mode A judge."""
        key = (message.channel.id, message.author.id)
        existing_task = self._debounce_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        self._debounce_buffers.setdefault(key, []).append(
            {
                "message": message,
                "text": text,
                "image_bytes": image_bytes,
                "image_mime": image_mime,
            }
        )
        self._debounce_tasks[key] = asyncio.create_task(
            self._debounced_proactive_judge(key[0], key[1], persona_id)
        )

    async def _debounced_proactive_judge(self, channel_id: int, user_id: int, persona_id: str) -> None:
        """Wait out per-user debounce window, then invoke ProactiveJudge and dispatch decision."""
        key = (channel_id, user_id)
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(config.PROACTIVE_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        finally:
            if self._debounce_tasks.get(key) is current_task:
                self._debounce_tasks.pop(key, None)

        items = self._debounce_buffers.pop(key, [])
        if not items:
            return

        window = self._get_hot_window(channel_id)
        if not window or user_id in window.ignored_bystanders:
            return
        if not vip_manager.has_remaining_quota(user_id):
            return

        last_item = items[-1]
        last_msg: discord.Message = last_item["message"]
        combined_text = "\n".join(item["text"] for item in items if item["text"]).strip()
        last_img_bytes = None
        last_img_mime = "image/png"
        for item in reversed(items):
            if item["image_bytes"]:
                last_img_bytes = item["image_bytes"]
                last_img_mime = item["image_mime"]
                break

        if not combined_text and last_img_bytes is None:
            return

        bot_display_name = self.persona_manager.persona_display_name
        ambient_context, _ = await self.context_builder.fetch_recent_channel_context(
            channel=last_msg.channel,
            current_message_id=last_msg.id,
            fetch_images=False,
            persona_display_name=bot_display_name,
            bot_user=self.bot.user,
            limit=10,
            max_age_minutes=config.PROACTIVE_CONTEXT_MAX_AGE_MINUTES,
        )

        is_active = user_id in window.active_users
        participant_names = [
            window.active_user_names.get(uid, str(uid)) for uid in window.active_users
        ]
        decision = await self.proactive_judge.judge(
            persona_name=bot_display_name,
            persona_summary=self.persona_manager.persona,
            ambient_context=ambient_context,
            speaker_name=last_msg.author.display_name,
            latest_message=combined_text,
            is_active_participant=is_active,
            active_participant_names=participant_names,
            image_bytes=last_img_bytes,
            image_mime=last_img_mime,
        )

        # Ensure window did not expire or clear while waiting on the judge LLM
        if not self._get_hot_window(channel_id):
            return

        if decision.decision == "reply":
            if not vip_manager.has_remaining_quota(user_id):
                return
            await self._respond_to_message(
                message=last_msg,
                ref_context="",
                prompt_content=combined_text,
                persona_id=persona_id,
                image_bytes=last_img_bytes,
                image_mime=last_img_mime,
                extra_image_parts=None,
                query_text=combined_text,
                is_peer_bot=False,
            )
        elif decision.decision == "react" and decision.emoji:
            try:
                await last_msg.add_reaction(decision.emoji)
            except Exception as e:
                logger.warning(f"Failed to add reaction {decision.emoji!r} in channel {channel_id}: {e}")
        else:
            self._record_user_pass(channel_id, user_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle incoming messages for memory buffering, peer-bot decay, Mode A hot windows, and direct replies."""
        is_self_bot = bool(self.bot.user and message.author.id == self.bot.user.id)
        peer_bot_ids = self._get_effective_peer_bot_ids()
        is_peer_bot = message.author.bot and (message.author.id in peer_bot_ids)

        if is_self_bot:
            if peer_bot_ids:
                self._record_bot_streak(message.channel.id)
            return

        if message.author.bot and not is_peer_bot:
            return

        streak = 0
        if is_peer_bot:
            streak = self._record_bot_streak(message.channel.id)
        else:
            self._reset_channel_streak(message.channel.id)

        # Extract image bytes if attachments are present
        image_bytes = None
        image_mime = "image/png"

        if message.attachments:
            att = message.attachments[0]
            if att.content_type and att.content_type.startswith("image/"):
                try:
                    image_bytes = await att.read()
                    image_mime = att.content_type
                except Exception as e:
                    logger.warning(f"Could not read attachment image: {e}")

        # Push human and configured peer-bot messages (with canonical @persona_display_name) to memory buffer
        persona_id = normalize_persona_id(self.persona_manager.current_persona_name)
        guild_id = message.guild.id if message.guild else None
        scope_key = normalize_scope_key(guild_id, message.author.id, platform="discord")
        norm_uid = normalize_user_id(message.author.id, platform="discord")
        bot_id = self.bot.user.id if self.bot.user else None
        bot_display_name = self.persona_manager.persona_display_name
        bot_overrides = build_bot_name_overrides(self.bot.user, bot_display_name)
        memory_text = resolve_discord_mentions(message, strip_user_id=None, user_name_overrides=bot_overrides)

        await self.memory_service.add_message(
            channel_id=str(message.channel.id),
            scope_key=scope_key,
            persona_id=persona_id,
            user_id=norm_uid,
            username=message.author.display_name,
            content=memory_text,
            image_bytes=image_bytes,
            image_mime=image_mime,
            platform="discord",
        )

        is_mentioned = self.bot.user in message.mentions if self.bot.user else False
        is_reply, ref_context, extra_image_parts = await self.context_builder.extract_reply_context(
            message,
            self.bot.user,
            persona_display_name=bot_display_name,
        )

        # Strip @Bot itself solely for wake-word detection and clean vector retrieval query_text
        stripped_content = resolve_discord_mentions(message, strip_user_id=bot_id)
        active_aliases = self.persona_manager.get_active_persona_aliases()
        has_wake_word = any(alias.lower() in stripped_content.lower() for alias in active_aliases if alias)
        is_dm = isinstance(message.channel, discord.DMChannel)

        is_explicit_trigger = is_mentioned or is_reply or has_wake_word or (not is_peer_bot and is_dm)

        if is_peer_bot:
            if not is_explicit_trigger:
                return
            if message.channel.id in self._peer_replying_channels:
                logger.debug(f"[PeerBot] Channel {message.channel.id} already replying; skipping duplicate trigger.")
                return

            prob = self._get_peer_reply_probability(streak)
            roll = random.random()
            if prob <= 0.0 or roll >= prob:
                logger.info(
                    f"[PeerBot] Skipped reply in channel {message.channel.id} "
                    f"(streak={streak}, prob={prob:.2f}, roll={roll:.2f})."
                )
                return

            self._peer_replying_channels.add(message.channel.id)
            try:
                delay_min = min(config.PEER_BOT_DELAY_MIN, config.PEER_BOT_DELAY_MAX)
                delay_max = max(config.PEER_BOT_DELAY_MIN, config.PEER_BOT_DELAY_MAX)
                delay = random.uniform(delay_min, delay_max)
                logger.info(
                    f"[PeerBot] Triggered reply in channel {message.channel.id} "
                    f"(streak={streak}, prob={prob:.2f}, roll={roll:.2f}, delay={delay:.2f}s)."
                )
                await asyncio.sleep(delay)
                await self._respond_to_message(
                    message=message,
                    ref_context=ref_context,
                    prompt_content=memory_text,
                    persona_id=persona_id,
                    image_bytes=image_bytes,
                    image_mime=image_mime,
                    extra_image_parts=extra_image_parts,
                    query_text=stripped_content,
                    is_peer_bot=True,
                )
            finally:
                self._peer_replying_channels.discard(message.channel.id)
            return

        if is_explicit_trigger:
            self._cancel_user_debounce(message.channel.id, message.author.id)
            await self._respond_to_message(
                message=message,
                ref_context=ref_context,
                prompt_content=memory_text,
                persona_id=persona_id,
                image_bytes=image_bytes,
                image_mime=image_mime,
                extra_image_parts=extra_image_parts,
                query_text=stripped_content,
                is_peer_bot=False,
            )
            return

        # Mode A: Dynamic multi-participant hot continuation window (no explicit mention needed)
        if not config.PROACTIVE_HOT_WINDOW_ENABLED:
            return
        window = self._get_hot_window(message.channel.id)
        if not window:
            return
        if message.reference is not None or message.author.id in window.ignored_bystanders:
            return
        if not vip_manager.has_remaining_quota(message.author.id):
            return

        self._enqueue_proactive_debounce(
            message=message,
            text=memory_text,
            image_bytes=image_bytes,
            image_mime=image_mime,
            persona_id=persona_id,
        )

    # --- Autocomplete for /chg_persona ---

    async def persona_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete choices listing all persona_*.txt files in data/personas/."""
        choices = []
        for file in self.persona_manager.get_available_persona_files():
            display_name = file.replace("persona_", "").replace(".txt", "")
            if current.lower() in display_name.lower() or current.lower() in file.lower():
                choices.append(app_commands.Choice(name=f"{display_name} ({file})", value=file))
        return choices[:25]

    # --- Slash Commands ---

    @app_commands.command(name="chat", description="與目前的 AI 角色進行對話")
    @app_commands.describe(prompt="你想對 AI 角色說的話", image="選擇性上傳圖片給 AI 看")
    async def chat(self, interaction: discord.Interaction, prompt: str, image: Optional[discord.Attachment] = None):
        await interaction.response.defer()
        self._reset_channel_streak(interaction.channel_id)
        self._cancel_user_debounce(interaction.channel_id, interaction.user.id)

        image_bytes = None
        image_mime = "image/png"
        if image and image.content_type and image.content_type.startswith("image/"):
            image_bytes = await image.read()
            image_mime = image.content_type

        persona_id = normalize_persona_id(self.persona_manager.current_persona_name)
        scope_key = normalize_scope_key(interaction.guild_id, interaction.user.id, platform="discord")
        norm_uid = normalize_user_id(interaction.user.id, platform="discord")

        await self.memory_service.add_message(
            channel_id=str(interaction.channel_id),
            scope_key=scope_key,
            persona_id=persona_id,
            user_id=norm_uid,
            username=interaction.user.display_name,
            content=prompt,
            image_bytes=image_bytes,
            image_mime=image_mime,
            platform="discord",
        )

        reply_text = await self.dialog_service.generate_response(
            channel_id=interaction.channel_id,
            user_text=f"[{interaction.user.display_name}]: {prompt}",
            persona_manager=self.persona_manager,
            context_builder=self.context_builder,
            user_id=interaction.user.id,
            channel=interaction.channel,
            image_bytes=image_bytes,
            image_mime=image_mime,
            bot_user=self.bot.user,
            persona_id=persona_id,
            query_text=prompt,
        )

        clean_text = self.delivery_service.clean_residual_tags(reply_text)
        formatted_message = f"💬 **{interaction.user.display_name}**: {prompt}\n\n{clean_text}"

        chunk_size = 1900
        chunks = [formatted_message[i:i + chunk_size] for i in range(0, len(formatted_message), chunk_size)]

        for idx, chunk in enumerate(chunks):
            if idx == 0:
                await interaction.followup.send(chunk)
            else:
                await interaction.channel.send(chunk)

        is_error_reply = isinstance(reply_text, str) and "`[Detail:" in reply_text
        if (
            interaction.channel
            and not isinstance(interaction.channel, discord.DMChannel)
            and not is_error_reply
        ):
            self._register_hot_participant(
                channel_id=interaction.channel_id,
                user_id=interaction.user.id,
                display_name=interaction.user.display_name,
            )

    @app_commands.command(name="chg_persona", description="快速切換 AI 角色的性格、頭像、暱稱與設定 JSON")
    @app_commands.describe(filename="選擇要切換的 persona_xxx.txt 檔案 (若留空則彈出互動下拉選單)")
    @app_commands.autocomplete(filename=persona_autocomplete)
    @require_bot_admin()
    async def chg_persona(self, interaction: discord.Interaction, filename: Optional[str] = None):
        files = self.persona_manager.get_available_persona_files()

        if not filename:
            if not files:
                await interaction.response.send_message("❌ 目前 `data/personas/` 目錄中沒有任何 `persona_*.txt` 設定檔！", ephemeral=False)
                return

            embed = discord.Embed(
                title="🎭 AI 角色設定檔切換選單",
                description=f"💬 **操作者**: {interaction.user.mention}\n"
                            f"🌟 **目前使用中**：`{self.persona_manager.current_persona_name}` ({self.persona_manager.persona_display_name})\n\n"
                            f"請使用下方**下拉選單**選擇要切換的 AI 角色設定檔：",
                color=discord.Color.blue()
            )
            for f in sorted(files):
                details = self.persona_manager.get_persona_details(f)
                is_active = (f == self.persona_manager.current_persona_name)
                prefix = "👉 [使用中] " if is_active else "📄 "
                json_info = f"附帶專屬設定檔 `{details['json_filename']}`" if details["has_json"] else "使用預設設定 JSON"

                embed.add_field(
                    name=f"{prefix}{details['display_name']} (`{f}`)",
                    value=f"提示: {json_info}",
                    inline=False
                )

            view = PersonaSelectView(self, sorted(files))
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            return

        # Direct filename execution
        filepath = os.path.join(PERSONA_DIR, filename)
        if not os.path.exists(filepath):
            await interaction.response.send_message(f"❌ 找不到該設定檔：`{filename}`！請確認檔案是否存在於 `data/personas/` 目錄中。", ephemeral=False)
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                new_content = f.read()

            self.persona_manager.save_active_persona(filename, new_content)
            self.dialog_service.clear_all_histories()
            self._clear_all_hot_states()

            avatar_updated, nick_updated = await self.persona_manager.update_bot_identity(
                interaction, filename, self.bot.user
            )

            details = self.persona_manager.get_persona_details(filename)
            json_status = f"`{details['json_filename']}`" if details["has_json"] else "預設 fallback"
            avatar_status = "✅ 頭像已同步更換" if avatar_updated else "ℹ️ 無專屬圖片或受到 Discord 變更限制"
            nick_status = f"✅ 伺服器暱稱已變更為「{self.persona_manager.persona_display_name}」" if nick_updated else "ℹ️ 暱稱未變更"

            await interaction.response.send_message(
                f"💬 **{interaction.user.display_name}** 指令切換角色：\n"
                f"✅ **切換成功！**\n"
                f"👤 角色名稱：`{self.persona_manager.persona_display_name}`\n"
                f"📄 人設檔案：`{filename}`\n"
                f"⚙️ 角色設定檔：{json_status}\n"
                f"🖼️ 頭像更新：{avatar_status}\n"
                f"🏷️ 暱稱更新：{nick_status}\n\n"
                f"**人設內容預覽：**\n```{new_content[:300]}...```",
                ephemeral=False
            )
        except Exception as e:
            logger.error(f"Error switching persona to {filename}: {e}")
            await interaction.response.send_message(f"❌ 切換設定檔時發生錯誤：{e}", ephemeral=False)

    @app_commands.command(name="list_personas", description="列出目前可用的所有角色設定檔與對應的設定 JSON")
    async def list_personas(self, interaction: discord.Interaction):
        files = self.persona_manager.get_available_persona_files()
        if not files:
            await interaction.response.send_message("ℹ️ 目前沒有找到任何 `persona_*.txt` 設定檔！", ephemeral=False)
            return

        embed = discord.Embed(title="🎭 可用的 AI 角色與設定 JSON 清單", color=discord.Color.purple())
        for f in sorted(files):
            details = self.persona_manager.get_persona_details(f)
            is_active = (f == self.persona_manager.current_persona_name)
            prefix = "🌟 [目前使用中] " if is_active else "📄 "
            json_name = details["json_filename"] if details["has_json"] else "預設"

            embed.add_field(
                name=f"{prefix}{details['char_name']} (`{f}`)",
                value=f"描述：_{details['summary']}_\n設定 JSON：`{json_name}` | 頭像：{details['avatar_status']}",
                inline=False
            )

        embed.set_footer(text="提示：使用 /chg_persona 指令開啟互動選單進行快速切換！")
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="reset_chat", description="清除目前頻道的 AI 對話歷史紀錄")
    async def reset_chat(self, interaction: discord.Interaction):
        existed = self.dialog_service.reset_channel_history(interaction.channel_id)
        self._clear_channel_hot_state(interaction.channel_id)
        if existed:
            await interaction.response.send_message(self.get_msg("reset_chat_success"), ephemeral=False)
        else:
            await interaction.response.send_message(self.get_msg("reset_chat_empty"), ephemeral=False)

    @app_commands.command(name="view_memory", description="檢視目前 AI 角色對群組、您的專屬印象與 Top 5 向量事件記憶")
    async def view_memory(self, interaction: discord.Interaction):
        """View persona-isolated dual-layer memory context and top recalled vector facts."""
        persona_id = normalize_persona_id(self.persona_manager.current_persona_name)
        scope_key = normalize_scope_key(interaction.guild_id, interaction.user.id, platform="discord")
        norm_uid = normalize_user_id(interaction.user.id, platform="discord")

        snapshot = await self.memory_service.get_memory_snapshot(
            persona_id=persona_id,
            scope_key=scope_key,
            user_id=norm_uid,
            top_facts_limit=5,
        )
        culture = snapshot["guild_culture"]
        user_profile = snapshot["user_profile"]
        top_facts = snapshot["top_facts"]

        embed = discord.Embed(
            title=f"🧠 {self.persona_manager.persona_display_name} 的記憶檔案",
            color=discord.Color.purple()
        )

        # Layer 1: Group Culture
        if culture:
            vibe = culture.group_vibe or "尚未觀察出明確氛圍"
            jokes = culture.inside_jokes or []
            style = culture.speech_style or "尚在了解群體說話風格"
            jokes_str = "\n".join([f"• {j}" for j in jokes]) if jokes else "尚無特定內部梗"

            embed.add_field(
                name="🌐 第一層：群體文化記憶 (Group Culture)",
                value=f"**群組氛圍**：{vibe}\n**常用說話風格**：{style}\n**內部梗與迷因**：\n{jokes_str}"[:1024],
                inline=False
            )
        else:
            embed.add_field(
                name="🌐 第一層：群體文化記憶 (Group Culture)",
                value="ℹ️ 目前此角色尚未在此空間積累群體記憶，多聊聊天我就會更融入大家囉！",
                inline=False
            )

        # Layer 2: User Profile
        if user_profile:
            style = user_profile.speaking_style or "尚在了解對話風格..."
            traits = user_profile.traits_and_interests or "尚在觀察個人喜好..."

            embed.add_field(
                name=f"👤 第二層：對 {interaction.user.display_name} 的專屬印象",
                value=f"**講話風格**：{style}\n**喜好與特徵**：{traits}"[:1024],
                inline=False
            )
        else:
            embed.add_field(
                name=f"👤 第二層：對 {interaction.user.display_name} 的專屬印象",
                value="ℹ️ 目前此角色對您的說話印象積累中，多在頻道裡聊聊天我就會記住您囉！",
                inline=False
            )

        # Layer 3: Top 5 Vector Facts
        if top_facts:
            import time
            now = time.time()
            fact_lines = []
            for f in top_facts:
                age_tag = ContextManager.format_relative_time(max(0.0, now - f.updated_at))
                fact_lines.append(
                    f"• `[ID:{f.id}]` {age_tag} ({f.category}｜★{f.importance}｜喚起{f.recall_count}次) {f.fact_text}"
                )
            embed.add_field(
                name="🗂️ 第三層：長期事件與向量記憶 (Top 5)",
                value="\n".join(fact_lines)[:1024],
                inline=False
            )
        else:
            embed.add_field(
                name="🗂️ 第三層：長期事件與向量記憶 (Top 5)",
                value="ℹ️ 目前尚未累積可供檢索的具體事件記憶片段。",
                inline=False
            )

        embed.set_footer(
            text=f"查詢者: {interaction.user.display_name} • 角色空間: {persona_id} • 可用 /forget_memory 刪除記憶"
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="forget_memory", description="刪除目前 AI 角色下的指定事件記憶、個人記憶或群組記憶")
    @app_commands.describe(
        fact_id="依 /view_memory 顯示的記憶 ID 刪除單筆記憶",
        keyword="依關鍵字刪除包含該詞彙的事實記憶",
        clear_my_memory="清除您在此角色下的所有個人印象與記憶事實",
        reset_guild_memory="[Bot 管理員專用] 重置本空間在此角色下的群體文化與所有記憶"
    )
    async def forget_memory(
        self,
        interaction: discord.Interaction,
        fact_id: Optional[int] = None,
        keyword: Optional[str] = None,
        clear_my_memory: bool = False,
        reset_guild_memory: bool = False,
    ):
        """Delete vector memory facts or reset user/scope memory under the active persona."""
        if fact_id is None and not keyword and not clear_my_memory and not reset_guild_memory:
            await interaction.response.send_message(
                "ℹ️ 請至少指定一項操作參數：`fact_id`、`keyword`、`clear_my_memory` 或 `reset_guild_memory`。",
                ephemeral=True,
            )
            return

        persona_id = normalize_persona_id(self.persona_manager.current_persona_name)
        scope_key = normalize_scope_key(interaction.guild_id, interaction.user.id, platform="discord")
        norm_uid = normalize_user_id(interaction.user.id, platform="discord")
        is_admin = vip_manager.is_bot_admin(interaction.user.id)

        if reset_guild_memory:
            if not is_admin:
                await interaction.response.send_message(
                    "⛔ **權限被拒**！只有指定的 Bot 管理員可以重置群組記憶。",
                    ephemeral=True,
                )
                return
            res = await self.memory_service.clear_scope_memory(persona_id=persona_id, scope_key=scope_key)
            await interaction.response.send_message(
                f"🧹 已重置 **{self.persona_manager.persona_display_name}** 在本空間的記憶："
                f"刪除群組文化 {res['cultures_deleted']} 筆、事件記憶 {res['facts_deleted']} 筆。",
                ephemeral=True,
            )
            return

        messages = []
        if clear_my_memory:
            res = await self.memory_service.clear_user_memory(persona_id=persona_id, user_id=norm_uid)
            messages.append(
                f"已清除您在 **{self.persona_manager.persona_display_name}** 下的個人印象 {res['profiles_deleted']} 筆、"
                f"事件記憶 {res['facts_deleted']} 筆。"
            )

        if fact_id is not None or keyword:
            target_uid = None if is_admin else norm_uid
            deleted_count = await self.memory_service.forget_facts(
                persona_id=persona_id,
                scope_key=scope_key,
                user_id=target_uid,
                fact_id=fact_id,
                keyword=keyword,
            )
            messages.append(f"已刪除符合條件的事件記憶共 **{deleted_count}** 筆。")

        await interaction.response.send_message("🧹 " + "\n".join(messages), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle permission errors for bot admin slash commands."""
        err_msg = str(error)
        if "NOT_BOT_ADMIN" in err_msg or isinstance(error, app_commands.MissingPermissions):
            msg = "⛔ **權限被拒**！此管理指令僅限指定的 Bot 管理員 (User ID) 執行，伺服器管理員身分無效。"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            logger.error(f"App command error in AIRoleplayCog: {error}", exc_info=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AIRoleplayCog(bot))
