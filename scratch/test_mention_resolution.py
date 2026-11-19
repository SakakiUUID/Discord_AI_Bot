import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord
import config
from utils.context_builder import ContextBuilder, resolve_discord_mentions
from cogs.ai_roleplay import AIRoleplayCog


def _make_user(user_id: int, display_name: str, is_bot: bool = False):
    user = MagicMock()
    user.id = user_id
    user.display_name = display_name
    user.name = display_name
    user.bot = is_bot
    return user


def _make_message(
    msg_id: int,
    channel,
    author,
    content: str,
    mentions=None,
    role_mentions=None,
    channel_mentions=None,
    guild=None,
    reference=None,
):
    msg = MagicMock()
    msg.id = msg_id
    msg.channel = channel
    msg.guild = guild
    msg.author = author
    msg.content = content
    msg.mentions = mentions or []
    msg.role_mentions = role_mentions or []
    msg.channel_mentions = channel_mentions or []
    msg.attachments = []
    msg.reference = reference
    msg.created_at = datetime.now(timezone.utc)
    return msg


def _build_cog(bot_id: int = 1111, bot_name: str = "小愛", persona_display_name: str = None):
    bot = MagicMock()
    bot.user = _make_user(bot_id, bot_name, is_bot=True)
    bot.guilds = []
    canonical_name = persona_display_name or bot_name

    with patch("cogs.ai_roleplay.PersonaManager") as mock_pm_cls, \
         patch("cogs.ai_roleplay.GeminiDialogService") as mock_ds_cls, \
         patch("cogs.ai_roleplay.MemoryService") as mock_ms_cls, \
         patch("cogs.ai_roleplay.MessageDeliveryService") as mock_deliv_cls:
        pm = mock_pm_cls.return_value
        pm.current_persona_name = "aemeath"
        pm.persona_display_name = canonical_name
        pm.get_active_persona_aliases.return_value = [canonical_name]

        ds = mock_ds_cls.return_value
        ds.ai_client = MagicMock()
        ds.generate_response = AsyncMock(return_value="好喔")

        ms = mock_ms_cls.return_value
        ms.add_message = AsyncMock()

        deliv = mock_deliv_cls.return_value
        deliv.send_split_message = AsyncMock()

        return AIRoleplayCog(bot)


async def test_resolve_mentions_users_roles_channels_and_fallbacks():
    xiaoming = _make_user(2001, "小明")
    dahua = _make_user(2002, "大華")
    bot_user = _make_user(1111, "小愛", is_bot=True)
    role = SimpleNamespace(id=3001, name="管理員")
    ch = SimpleNamespace(id=4001, name="閒聊區")

    guild = MagicMock()
    guild.get_member = lambda uid: dahua if uid == 2002 else None

    msg = _make_message(
        10,
        ch,
        _make_user(2003, "小華"),
        "<@!1111> 你覺得 <@2001> 跟 <@2002> 在 <#4001> 找 <@&3001> 和 <@9999> 說得對嗎？",
        mentions=[bot_user, xiaoming],  # 2002 intentionally omitted to test guild fallback
        role_mentions=[role],
        channel_mentions=[ch],
        guild=guild,
    )

    # Without stripping bot: keep everyone + role + channel, preserve unknown ID
    full = resolve_discord_mentions(msg, strip_user_id=None)
    assert full == "@小愛 你覺得 @小明 跟 @大華 在 #閒聊區 找 @管理員 和 <@9999> 說得對嗎？"

    # Stripping bot user: removes @Bot and collapses extra spaces cleanly
    stripped = resolve_discord_mentions(msg, strip_user_id=1111)
    assert stripped == "你覺得 @小明 跟 @大華 在 #閒聊區 找 @管理員 和 <@9999> 說得對嗎？"

    # Pure @Bot becomes empty when stripped, or canonical override when preserved
    pure_bot_msg = _make_message(11, ch, xiaoming, "<@1111>", mentions=[bot_user])
    assert resolve_discord_mentions(pure_bot_msg, strip_user_id=1111) == ""
    assert resolve_discord_mentions(pure_bot_msg, strip_user_id=None) == "@小愛"
    assert resolve_discord_mentions(pure_bot_msg, strip_user_id=None, user_name_overrides={1111: "莫寧"}) == "@莫寧"


async def test_context_builder_preserves_mentions_and_tag_only_messages():
    cb = ContextBuilder()
    self_bot = _make_user(1111, "舊暱稱", is_bot=True)
    xiaoming = _make_user(2001, "小明")
    xiaohua = _make_user(2003, "小華")
    fake_channel = MagicMock()

    msgs = [
        _make_message(3, fake_channel, xiaohua, "<@2001>", mentions=[xiaoming]),
        _make_message(2, fake_channel, xiaohua, "<@2001> 你吃飯了嗎 <@1111>", mentions=[xiaoming, self_bot]),
    ]

    async def fake_history(limit=30, before=None):
        for m in msgs:
            yield m

    fake_channel.history = fake_history

    with patch("utils.context_builder.logger.warning") as mock_warn, \
         patch.object(config, "PEER_BOT_IDS", set()):
        ambient_text, _ = await cb.fetch_recent_channel_context(
            channel=fake_channel,
            current_message_id=4,
            persona_display_name="小愛",
            bot_user=self_bot,
        )

    assert "@小明 你吃飯了嗎 @小愛" in ambient_text
    assert "@舊暱稱" not in ambient_text
    assert "@小明" in ambient_text
    # Tag-only message "<@2001>" must not trigger empty_count warning
    mock_warn.assert_not_called()


async def test_reply_context_resolves_mentions():
    cb = ContextBuilder()
    bot_user = _make_user(1111, "舊暱稱", is_bot=True)
    dahua = _make_user(2002, "大華")
    author = _make_user(2003, "小華")
    fake_channel = MagicMock()

    ref_msg = MagicMock(spec=discord.Message)
    ref_msg.author = dahua
    ref_msg.content = "<@2002> 幫我問 <@1111> 作業的事"
    ref_msg.mentions = [dahua, bot_user]
    ref_msg.role_mentions = []
    ref_msg.channel_mentions = []
    ref_msg.guild = None
    ref_msg.attachments = []

    ref = MagicMock()
    ref.resolved = ref_msg
    ref.message_id = 50

    msg = _make_message(51, fake_channel, author, "<@1111> 解釋一下", mentions=[bot_user], reference=ref)
    is_reply, ref_context, _ = await cb.extract_reply_context(msg, bot_user, persona_display_name="莫寧")

    assert is_reply is False
    assert "@大華 幫我問 @莫寧 作業的事" in ref_context


async def test_on_message_memory_and_prompt_mentions():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    xiaoming = _make_user(2001, "小明")
    xiaohua = _make_user(2003, "小華")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 8801

    msg = _make_message(
        100,
        channel,
        xiaohua,
        "<@1111> 你覺得 <@2001> 說得對嗎",
        mentions=[cog.bot.user, xiaoming],
    )

    with patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch.object(config, "PEER_BOT_IDS", set()):
        await cog.on_message(msg)

    # 1. Memory & prompt both keep @小愛 and @小明 (no <@id>)
    mem_call = cog.memory_service.add_message.call_args.kwargs
    assert mem_call["content"] == "@小愛 你覺得 @小明 說得對嗎"

    ds_call = cog.dialog_service.generate_response.call_args.kwargs
    assert ds_call["user_text"] == "[小華]: @小愛 你覺得 @小明 說得對嗎"
    # 2. Vector query_text strips @小愛 while keeping @小明
    assert ds_call["query_text"] == "你覺得 @小明 說得對嗎"


async def test_bare_self_mention_and_stale_nickname_override():
    cog = _build_cog(bot_id=1111, bot_name="舊暱稱", persona_display_name="莫寧")
    xiaohua = _make_user(2003, "小華")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 8802

    msg = _make_message(
        101,
        channel,
        xiaohua,
        "<@1111>",
        mentions=[cog.bot.user],
    )

    with patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch.object(config, "PEER_BOT_IDS", set()):
        await cog.on_message(msg)

    mem_call = cog.memory_service.add_message.call_args.kwargs
    assert mem_call["content"] == "@莫寧"

    ds_call = cog.dialog_service.generate_response.call_args.kwargs
    # Must NOT fall back to "（這張圖片怎麼樣？）" or embed "<@1111>"
    assert ds_call["user_text"] == "[小華]: @莫寧"
    assert ds_call["query_text"] == ""


async def main():
    tests = [
        test_resolve_mentions_users_roles_channels_and_fallbacks,
        test_context_builder_preserves_mentions_and_tag_only_messages,
        test_reply_context_resolves_mentions,
        test_on_message_memory_and_prompt_mentions,
        test_bare_self_mention_and_stale_nickname_override,
    ]
    for fn in tests:
        await fn()
        print(f"[PASS] {fn.__name__}")
    print(f"\nAll {len(tests)} mention-resolution verification tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
