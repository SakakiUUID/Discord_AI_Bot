import re
import logging
from typing import Dict, List, Optional, Set, Tuple
import discord
from google.genai import types

import config
from core.context_manager import ContextManager, IMAGE_INTENT_KEYWORDS

logger = logging.getLogger("bot.context_builder")


def _build_id_name_map(items, name_attr: str) -> Dict[int, str]:
    """Build an {id: name} lookup map from Discord mention objects safely."""
    result: Dict[int, str] = {}
    for item in items or []:
        item_id = getattr(item, "id", None)
        item_name = getattr(item, name_attr, None)
        if isinstance(item_id, int) and isinstance(item_name, str) and item_name:
            result[item_id] = item_name
    return result


def build_bot_name_overrides(
    bot_user: Optional[discord.ClientUser],
    persona_display_name: Optional[str],
) -> Optional[Dict[int, str]]:
    """Build a {bot_id: persona_display_name} override map when both ID and display name are valid."""
    bot_id = getattr(bot_user, "id", None) if bot_user is not None else None
    if isinstance(bot_id, int) and isinstance(persona_display_name, str) and persona_display_name:
        return {bot_id: persona_display_name}
    return None


def resolve_discord_mentions(
    message: discord.Message,
    strip_user_id: Optional[int] = None,
    user_name_overrides: Optional[Dict[int, str]] = None,
) -> str:
    """
    Resolve Discord <@user_id>, <@&role_id>, and <#channel_id> tags into human-readable names.
    Optionally strips mentions targeting `strip_user_id` (e.g. the bot itself for search queries)
    or overrides specific user IDs with canonical names via `user_name_overrides`.
    """
    text = getattr(message, "content", None) or ""
    if not text:
        return ""

    if strip_user_id is not None:
        text = re.sub(rf"<@!?{strip_user_id}>", "", text)

    overrides = {
        uid: name
        for uid, name in (user_name_overrides or {}).items()
        if isinstance(uid, int) and isinstance(name, str) and name
    }
    user_map = _build_id_name_map(getattr(message, "mentions", None), "display_name")
    guild = getattr(message, "guild", None)

    def _replace_user(match: re.Match) -> str:
        uid = int(match.group(1))
        if strip_user_id is not None and uid == strip_user_id:
            return ""
        name = overrides.get(uid) or user_map.get(uid)
        if not name and guild is not None and hasattr(guild, "get_member"):
            member = guild.get_member(uid)
            member_name = getattr(member, "display_name", None) if member else None
            if isinstance(member_name, str) and member_name:
                name = member_name
        return f"@{name}" if name else match.group(0)

    text = re.sub(r"<@!?(\d+)>", _replace_user, text)

    role_map = _build_id_name_map(getattr(message, "role_mentions", None), "name")
    text = re.sub(
        r"<@&(\d+)>",
        lambda m: f"@{role_map[int(m.group(1))]}" if int(m.group(1)) in role_map else m.group(0),
        text,
    )

    ch_map = _build_id_name_map(getattr(message, "channel_mentions", None), "name")
    text = re.sub(
        r"<#(\d+)>",
        lambda m: f"#{ch_map[int(m.group(1))]}" if int(m.group(1)) in ch_map else m.group(0),
        text,
    )

    # Collapse redundant spaces left behind when stripping @Bot at start/middle
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class ContextBuilder(ContextManager):
    """Builds Discord ambient chat context, relative time tags, silence gap dividers, and multimodal image parts."""

    async def fetch_recent_channel_context(
        self,
        channel: discord.abc.Messageable,
        current_message_id: Optional[int] = None,
        fetch_images: bool = False,
        persona_display_name: str = "AI",
        bot_user: Optional[discord.ClientUser] = None,
        allowed_bot_ids: Optional[Set[int]] = None,
        limit: int = 30,
        max_age_minutes: Optional[int] = None,
    ) -> Tuple[str, List[types.Part]]:
        """Fetch recent messages in the Discord channel with relative time tags, silence gaps, and multimodal image parts."""
        if not hasattr(channel, "history"):
            return "", []

        fetched_items = []
        empty_count = 0
        now_utc = discord.utils.utcnow()
        max_age_seconds = max_age_minutes * 60.0 if max_age_minutes is not None else None
        effective_allowed_bots = config.PEER_BOT_IDS if allowed_bot_ids is None else allowed_bot_ids
        bot_overrides = build_bot_name_overrides(bot_user, persona_display_name)
        try:
            before_obj = discord.Object(id=current_message_id) if current_message_id else None
            # Fetch up to `limit` recent messages in the channel before current message
            async for msg in channel.history(limit=limit, before=before_obj):
                diff_seconds = max(0.0, (now_utc - msg.created_at).total_seconds())
                if max_age_seconds is not None and diff_seconds > max_age_seconds:
                    continue

                # Keep Bot's own messages and configured peer bots, skip third-party automated system bots
                is_self_bot = bot_user is not None and (
                    msg.author == bot_user or getattr(msg.author, "id", None) == getattr(bot_user, "id", None)
                )
                is_allowed_peer = getattr(msg.author, "id", None) in effective_allowed_bots
                if msg.author.bot and not is_self_bot and not is_allowed_peer:
                    continue

                clean = resolve_discord_mentions(msg, strip_user_id=None, user_name_overrides=bot_overrides)

                # Check for image attachments in this historical message
                img_bytes = None
                img_mime = None
                has_img_att = False
                if msg.attachments:
                    for att in msg.attachments:
                        if att.content_type and att.content_type.startswith("image/"):
                            has_img_att = True
                            if fetch_images and (not att.size or att.size <= 8 * 1024 * 1024):
                                try:
                                    img_bytes = await att.read()
                                    img_mime = att.content_type
                                    break
                                except Exception as e:
                                    logger.warning(f"Could not read historical image attachment: {e}")

                if not clean and not has_img_att and not img_bytes:
                    empty_count += 1
                    continue

                author_name = f"{persona_display_name} (你)" if (bot_user and msg.author.id == bot_user.id) else msg.author.display_name
                time_tag = self.format_relative_time(diff_seconds)

                fetched_items.append({
                    "author": author_name,
                    "text": clean,
                    "image_bytes": img_bytes,
                    "image_mime": img_mime,
                    "has_img_att": has_img_att,
                    "created_at": msg.created_at,
                    "time_tag": time_tag
                })

            if empty_count > 0:
                logger.warning(
                    f"⚠️ 偵測到 {empty_count} 則歷史訊息內容為空白！"
                    "請確認 Discord Developer Portal -> Bot 頁面中的「MESSAGE CONTENT INTENT」開關是否有開啟 (ON)！"
                )
        except Exception as e:
            logger.warning(f"Failed to fetch channel history for context: {e}")

        if not fetched_items:
            logger.info("No ambient channel context fetched.")
            return "", []

        # Chronological order: reverse from newest-first to oldest-first
        fetched_items.reverse()

        if fetch_images:
            total_images = sum(1 for item in fetched_items if item["image_bytes"])
            if total_images > 3:
                images_to_skip = total_images - 3
                for item in fetched_items:
                    if item["image_bytes"]:
                        if images_to_skip > 0:
                            item["image_bytes"] = None
                            item["image_mime"] = None
                            images_to_skip -= 1

        ambient_image_parts = []
        recent_messages = []
        image_counter = 1
        last_msg_time = None

        for item in fetched_items:
            # 檢查相鄰兩則訊息的時間差，若超過 20 分鐘 (1200秒) 則插入沉寂斷層線
            if last_msg_time is not None:
                gap = (item["created_at"] - last_msg_time).total_seconds()
                if gap >= 1200:
                    recent_messages.append(f"--- ⏳ (此處頻道沉寂了 {self.format_gap_duration(gap)}) ---")
            last_msg_time = item["created_at"]

            img_tag = ""
            if fetch_images and item["image_bytes"] and item["image_mime"]:
                ambient_image_parts.append(
                    types.Part.from_bytes(data=item["image_bytes"], mime_type=item["image_mime"])
                )
                img_tag = f"(附圖 {image_counter}) "
                image_counter += 1
            elif item["has_img_att"]:
                img_tag = "(附圖) "

            time_prefix = f"{item['time_tag']} "
            if item["text"]:
                recent_messages.append(f"{time_prefix}[{item['author']}]: {img_tag}{item['text']}")
            elif img_tag:
                recent_messages.append(f"{time_prefix}[{item['author']}]: {img_tag}（發送了圖片）")

        if not recent_messages:
            return "", []

        context_str = "\n".join(recent_messages)
        logger.info(f"🕒 Fetched {len(recent_messages)} messages for AI context with temporal tags:\n{context_str}")
        return f"【聊天室近況記錄】\n{context_str}\n---\n", ambient_image_parts

    async def extract_reply_context(
        self,
        message: discord.Message,
        bot_user: Optional[discord.ClientUser],
        persona_display_name: str = "AI",
    ) -> Tuple[bool, str, List[types.Part]]:
        """Extract reply target text and optional images from Discord message reference."""
        is_reply = False
        ref_context = ""
        extra_image_parts = []
        bot_overrides = build_bot_name_overrides(bot_user, persona_display_name)

        if message.reference:
            ref_msg = message.reference.resolved
            if not isinstance(ref_msg, discord.Message) and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    ref_msg = None

            if isinstance(ref_msg, discord.Message):
                if bot_user and ref_msg.author == bot_user:
                    is_reply = True
                ref_clean = resolve_discord_mentions(ref_msg, strip_user_id=None, user_name_overrides=bot_overrides)
                ref_author = ref_msg.author.display_name

                # Check if replied message contains image
                has_ref_image = False
                if ref_msg.attachments:
                    for att in ref_msg.attachments:
                        if att.content_type and att.content_type.startswith("image/"):
                            if not att.size or att.size <= 8 * 1024 * 1024:
                                try:
                                    ref_img_bytes = await att.read()
                                    extra_image_parts.append(
                                        types.Part.from_bytes(data=ref_img_bytes, mime_type=att.content_type)
                                    )
                                    has_ref_image = True
                                    break
                                except Exception as e:
                                    logger.warning(f"Could not read reply target image: {e}")

                ref_img_tag = "(附帶圖片) " if has_ref_image else ""
                if ref_clean or has_ref_image:
                    ref_context = f"【你正回覆的目標訊息 ({ref_author})】: {ref_img_tag}{ref_clean or '（發送了圖片）'}\n"

        return is_reply, ref_context, extra_image_parts

    async def fetch_channel_context_and_images(
        self,
        channel: discord.abc.Messageable,
        current_message_id: Optional[int] = None,
        user_text: str = "",
        bot_user: Optional[discord.ClientUser] = None
    ) -> str:
        """Alias for fetch_recent_channel_context returning ambient context string."""
        fetch_images = self.has_image_intent(user_text)
        context_str, _ = await self.fetch_recent_channel_context(
            channel=channel,
            current_message_id=current_message_id,
            fetch_images=fetch_images,
            bot_user=bot_user
        )
        return context_str

