import asyncio
import sys
import types as py_types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord
import config
from core.dialog_service import DialogService
from utils.context_builder import ContextBuilder
from utils.gemini_dialog_service import GeminiDialogService
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
    reference=None,
):
    msg = MagicMock()
    msg.id = msg_id
    msg.channel = channel
    msg.guild = MagicMock(id=999001)
    msg.author = author
    msg.content = content
    msg.mentions = mentions or []
    msg.attachments = []
    msg.reference = reference
    msg.created_at = datetime.now(timezone.utc)
    return msg


def _build_cog(bot_id: int = 1111, bot_name: str = "小愛"):
    bot = MagicMock()
    bot.user = _make_user(bot_id, bot_name, is_bot=True)
    bot.guilds = []

    with patch("cogs.ai_roleplay.PersonaManager") as mock_pm_cls, \
         patch("cogs.ai_roleplay.GeminiDialogService") as mock_ds_cls, \
         patch("cogs.ai_roleplay.MemoryService") as mock_ms_cls, \
         patch("cogs.ai_roleplay.MessageDeliveryService") as mock_deliv_cls, \
         patch("cogs.ai_roleplay.ContextBuilder") as mock_cb_cls:
        pm = mock_pm_cls.return_value
        pm.current_persona_name = "aemeath"
        pm.get_active_persona_aliases.return_value = [bot_name, "愛彌斯"]

        ds = mock_ds_cls.return_value
        ds.ai_client = MagicMock()
        ds.generate_response = AsyncMock(return_value="嗨呀，我聽到了！")

        ms = mock_ms_cls.return_value
        ms.add_message = AsyncMock()

        deliv = mock_deliv_cls.return_value
        deliv.send_split_message = AsyncMock()

        cb = mock_cb_cls.return_value
        cb.extract_reply_context = AsyncMock(return_value=(False, "", []))

        cog = AIRoleplayCog(bot)
        return cog


async def test_config_parsing_and_self_exclusion():
    assert config._parse_int_set("123, abc, 456 , ,789") == {123, 456, 789}
    assert config._parse_float_list("1.0, bad, 0.4, 0.0", [0.9]) == [1.0, 0.4, 0.0]
    assert config._parse_float_list("bad, ", [1.0, 0.5]) == [1.0, 0.5]

    cog = _build_cog(bot_id=1111)
    with patch.object(config, "PEER_BOT_IDS", {1111, 2222}):
        assert cog._get_effective_peer_bot_ids() == {2222}


async def test_decay_probability_curve_and_stop():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    with patch.object(config, "PEER_BOT_IDS", {2222}), \
         patch.object(config, "PEER_BOT_REPLY_PROBS", [1.0, 0.5, 0.3, 0.1]), \
         patch.object(config, "PEER_BOT_DELAY_MIN", 0.0), \
         patch.object(config, "PEER_BOT_DELAY_MAX", 0.0):
        assert [cog._get_peer_reply_probability(i) for i in range(1, 7)] == [1.0, 0.5, 0.3, 0.1, 0.0, 0.0]

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 55501
        channel.typing = MagicMock(return_value=AsyncMock())
        peer = _make_user(2222, "大肥魚", is_bot=True)

        # Force roll=0.0 so streaks 1..4 always reply, streak 5 must stop (prob=0.0)
        with patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
             patch("cogs.ai_roleplay.random.random", return_value=0.0):
            for idx in range(1, 6):
                msg = _make_message(idx, channel, peer, "小愛你覺得呢？", mentions=[cog.bot.user])
                await cog.on_message(msg)

        assert cog.dialog_service.generate_response.await_count == 4
        assert cog.memory_service.add_message.await_count == 5
        # Verify peer flags were passed on generated replies
        last_kwargs = cog.dialog_service.generate_response.call_args.kwargs
        assert last_kwargs["skip_vip_check"] is True
        assert last_kwargs["is_peer_bot"] is True


async def test_lost_roll_still_buffers_memory():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    with patch.object(config, "PEER_BOT_IDS", {2222}), \
         patch.object(config, "PEER_BOT_REPLY_PROBS", [1.0, 0.5, 0.3, 0.1]):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 55502
        peer = _make_user(2222, "大肥魚", is_bot=True)

        # Advance streak to 1 via own message first, then peer arrives at streak 2 (prob=0.5)
        self_msg = _make_message(1, channel, cog.bot.user, "第一句")
        await cog.on_message(self_msg)
        assert cog._channel_bot_streaks[55502]["count"] == 1

        with patch("cogs.ai_roleplay.random.random", return_value=0.99):
            peer_msg = _make_message(2, channel, peer, "小愛再聊聊嘛", mentions=[cog.bot.user])
            await cog.on_message(peer_msg)

        assert cog._channel_bot_streaks[55502]["count"] == 2
        assert cog.memory_service.add_message.await_count == 1
        assert cog.dialog_service.generate_response.await_count == 0


async def test_timeout_human_and_slash_chat_reset():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    with patch.object(config, "PEER_BOT_IDS", {2222}), \
         patch.object(config, "PEER_BOT_STREAK_TIMEOUT", 600.0):
        assert cog._record_bot_streak(10, now=100.0) == 1
        assert cog._record_bot_streak(10, now=200.0) == 2
        # Timeout >= 600s resets streak back to 1
        assert cog._record_bot_streak(10, now=850.0) == 1

        # Human message resets channel streak
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 10
        human = _make_user(7777, "測試玩家", is_bot=False)
        human_msg = _make_message(3, channel, human, "安安大家")
        await cog.on_message(human_msg)
        assert 10 not in cog._channel_bot_streaks

        # /chat resets channel streak
        cog._record_bot_streak(10, now=900.0)
        assert cog._channel_bot_streaks[10]["count"] == 1
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        interaction.channel_id = 10
        interaction.guild_id = 999001
        interaction.user = human
        interaction.channel = channel
        await cog.chat.callback(cog, interaction, prompt="哈囉")
        assert 10 not in cog._channel_bot_streaks


async def test_unknown_bot_completely_ignored():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    with patch.object(config, "PEER_BOT_IDS", {2222}):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 55503
        stranger_bot = _make_user(9999, "SystemBot", is_bot=True)
        msg = _make_message(1, channel, stranger_bot, "小愛", mentions=[cog.bot.user])
        await cog.on_message(msg)

        assert 55503 not in cog._channel_bot_streaks
        assert cog.memory_service.add_message.await_count == 0
        assert cog.dialog_service.generate_response.await_count == 0


async def test_concurrent_chunk_lock():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    with patch.object(config, "PEER_BOT_IDS", {2222}), \
         patch.object(config, "PEER_BOT_REPLY_PROBS", [1.0, 0.5, 0.3, 0.1]), \
         patch.object(config, "PEER_BOT_DELAY_MIN", 0.05), \
         patch.object(config, "PEER_BOT_DELAY_MAX", 0.05), \
         patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch("cogs.ai_roleplay.random.random", return_value=0.0):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 55504
        peer = _make_user(2222, "大肥魚", is_bot=True)
        msg1 = _make_message(1, channel, peer, "第一段 小愛", mentions=[cog.bot.user])
        msg2 = _make_message(2, channel, peer, "第二段 小愛", mentions=[cog.bot.user])

        await asyncio.gather(cog.on_message(msg1), cog.on_message(msg2))
        assert cog.dialog_service.generate_response.await_count == 1
        assert 55504 not in cog._peer_replying_channels


async def test_context_builder_preserves_peer_bots():
    cb = ContextBuilder()
    self_bot = _make_user(1111, "小愛", is_bot=True)
    peer_bot = _make_user(2222, "大肥魚", is_bot=True)
    third_party_bot = _make_user(9999, "MEE6", is_bot=True)
    human = _make_user(7777, "路人甲", is_bot=False)

    fake_channel = MagicMock()
    msgs = [
        _make_message(4, fake_channel, third_party_bot, "等級提升了！"),
        _make_message(3, fake_channel, peer_bot, "小愛今天吃什麼？"),
        _make_message(2, fake_channel, self_bot, "我剛喝完奶茶！"),
        _make_message(1, fake_channel, human, "你們在聊什麼？"),
    ]

    async def fake_history(limit=30, before=None):
        for m in msgs:
            yield m

    fake_channel.history = fake_history

    with patch.object(config, "PEER_BOT_IDS", {2222}):
        ambient_text, _ = await cb.fetch_recent_channel_context(
            channel=fake_channel,
            current_message_id=5,
            persona_display_name="小愛",
            bot_user=self_bot,
        )

    assert "路人甲" in ambient_text
    assert "大肥魚" in ambient_text
    assert "小愛" in ambient_text
    assert "MEE6" not in ambient_text
    assert "等級提升了" not in ambient_text


async def test_dialog_service_vip_bypass_and_transient_hint():
    fake_client = MagicMock()
    captured_configs = []

    def fake_generate_content(model, contents, config):
        captured_configs.append(config)
        resp = MagicMock()
        resp.text = "嗯嗯，我知道了。"
        resp.candidates = []
        return resp

    fake_client.models.generate_content = fake_generate_content
    ds = DialogService(ai_client=fake_client)

    with patch("core.dialog_service.vip_manager.check_and_increment_usage", return_value=(False, 99, 10)) as mock_vip:
        # Normal user is blocked
        blocked_reply = await ds.generate_response(
            channel_id="ch-1",
            user_text="[人類]: 哈囉",
            persona="你是小愛",
            user_vip_id=7777,
        )
        assert "今日對話額度已用完" in blocked_reply
        assert mock_vip.call_count == 1

        # Peer bot bypasses VIP check and gets transient hint
        peer_reply = await ds.generate_response(
            channel_id="ch-2",
            user_text="[大肥魚]: 小愛早安",
            persona="你是小愛",
            user_vip_id=2222,
            skip_vip_check=True,
            is_peer_bot=True,
        )
        assert peer_reply == "嗯嗯，我知道了。"
        assert mock_vip.call_count == 1  # Not called again!
        assert len(captured_configs) == 1
        assert "同伴互動情境提示" in captured_configs[0].system_instruction
        # Persistent channel history does not contain the hint
        assert "同伴互動情境提示" not in str(ds.channel_histories["ch-2"])

    # GeminiDialogService forwards skip_vip_check & is_peer_bot cleanly
    gds = GeminiDialogService(api_key=None)
    gds.ai_client = fake_client
    pm = MagicMock(persona="你是小愛", current_persona_name="aemeath", get_msg=lambda k, **kw: k)
    cb = MagicMock()
    cb.fetch_recent_channel_context = AsyncMock(return_value=("", []))
    with patch("core.dialog_service.vip_manager.check_and_increment_usage", return_value=(False, 99, 10)) as mock_vip2:
        gds_reply = await gds.generate_response(
            channel_id=12345,
            user_text="[大肥魚]: 嗨",
            persona_manager=pm,
            context_builder=cb,
            user_id=2222,
            skip_vip_check=True,
            is_peer_bot=True,
        )
        assert gds_reply == "嗯嗯，我知道了。"
        assert mock_vip2.call_count == 0


async def main():
    tests = [
        test_config_parsing_and_self_exclusion,
        test_decay_probability_curve_and_stop,
        test_lost_roll_still_buffers_memory,
        test_timeout_human_and_slash_chat_reset,
        test_unknown_bot_completely_ignored,
        test_concurrent_chunk_lock,
        test_context_builder_preserves_peer_bots,
        test_dialog_service_vip_bypass_and_transient_hint,
    ]
    for fn in tests:
        await fn()
        print(f"[PASS] {fn.__name__}")
    print(f"\nAll {len(tests)} peer-bot verification tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
