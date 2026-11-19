import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord
import config
from core.proactive_judge import ProactiveDecision, ProactiveJudge, is_valid_unicode_emoji, parse_decision_json
from utils.context_builder import ContextBuilder
from utils.vip_manager import VIPManager
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
    created_at=None,
):
    msg = MagicMock()
    msg.id = msg_id
    msg.channel = channel
    msg.guild = MagicMock(id=999001)
    msg.author = author
    msg.content = content
    msg.mentions = mentions or []
    msg.role_mentions = []
    msg.channel_mentions = []
    msg.attachments = []
    msg.reference = reference
    msg.created_at = created_at or datetime.now(timezone.utc)
    msg.add_reaction = AsyncMock()
    return msg


def _build_cog(bot_id: int = 1111, bot_name: str = "小愛"):
    bot = MagicMock()
    bot.user = _make_user(bot_id, bot_name, is_bot=True)
    bot.guilds = []

    with patch("cogs.ai_roleplay.PersonaManager") as mock_pm_cls, \
         patch("cogs.ai_roleplay.GeminiDialogService") as mock_ds_cls, \
         patch("cogs.ai_roleplay.MemoryService") as mock_ms_cls, \
         patch("cogs.ai_roleplay.MessageDeliveryService") as mock_deliv_cls:
        pm = mock_pm_cls.return_value
        pm.current_persona_name = "aemeath"
        pm.persona_display_name = bot_name
        pm.persona = "你是小愛，一個愛聊天的群友。"
        pm.get_active_persona_aliases.return_value = [bot_name]

        ds = mock_ds_cls.return_value
        ds.ai_client = MagicMock()
        ds.generate_response = AsyncMock(return_value="嗨呀，這個我懂！")

        ms = mock_ms_cls.return_value
        ms.add_message = AsyncMock()

        deliv = mock_deliv_cls.return_value
        deliv.send_split_message = AsyncMock()

        cog = AIRoleplayCog(bot)
        cog.context_builder = MagicMock()
        cog.context_builder.extract_reply_context = AsyncMock(return_value=(False, "", []))
        cog.context_builder.fetch_recent_channel_context = AsyncMock(return_value=("【聊天室近況記錄】\n[小明]: 安安\n---\n", []))
        return cog


async def test_judge_json_parsing_and_emoji_validation():
    assert is_valid_unicode_emoji("👍")
    assert is_valid_unicode_emoji("❤️")
    assert is_valid_unicode_emoji("👨‍👩‍👧")
    assert not is_valid_unicode_emoji("<:custom:12345>")
    assert not is_valid_unicode_emoji("haha")
    assert not is_valid_unicode_emoji("")

    d1 = parse_decision_json('{"decision": "reply", "emoji": "", "reason": "following up"}')
    assert d1.decision == "reply"

    d2 = parse_decision_json('{"decision": "react", "emoji": "😂", "reason": "funny"}')
    assert d2.decision == "react" and d2.emoji == "😂"

    # Invalid custom emoji degrades safely to pass
    d3 = parse_decision_json('{"decision": "react", "emoji": "<:pepe:123>", "reason": "bad"}')
    assert d3.decision == "pass"

    # Broken JSON / leftover bracket degrades safely to pass
    d4 = parse_decision_json('[')
    assert d4.decision == "pass"

    # Verify ProactiveJudge uses JSON mime type and temperature 0.0
    fake_client = MagicMock()
    captured = {}

    def fake_gen(model, contents, config):
        captured["config"] = config
        captured["text"] = contents[0].parts[0].text
        resp = MagicMock()
        resp.text = json.dumps({"decision": "reply", "emoji": "", "reason": "topic match"})
        return resp

    fake_client.models.generate_content = fake_gen
    judge = ProactiveJudge(ai_client=fake_client)
    res = await judge.judge(
        persona_name="小愛",
        persona_summary="活潑可愛",
        ambient_context="1m前 [小明]: @小愛 今晚打遊戲嗎",
        speaker_name="小華",
        latest_message="我也想跟！缺輔嗎",
        is_active_participant=False,
        active_participant_names=["小明"],
    )
    assert res.decision == "reply"
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].temperature == 0.0
    assert "小明" in captured["text"]
    assert "尚未加入" in captured["text"]


async def test_vip_has_remaining_quota_read_only(tmp_path=None):
    vm = VIPManager.__new__(VIPManager)
    vm.config_path = ""
    vm.usage_path = ""
    vm.config = {"enabled": True, "daily_limit_non_vip": 2, "admin_user_ids": [], "vip_user_ids": [999]}
    today = datetime.now().date().isoformat()
    vm.usage_data = {"1001": {"date": today, "count": 2}, "1002": {"date": today, "count": 1}}

    assert vm.has_remaining_quota(999) is True  # VIP
    assert vm.has_remaining_quota(1001) is False  # Exhausted
    assert vm.has_remaining_quota(1002) is True  # 1 < 2
    assert vm.usage_data["1002"]["count"] == 1  # Strictly read-only!
    assert vm.has_remaining_quota(1003) is True  # Fresh user


async def test_context_builder_limit_and_max_age_minutes():
    cb = ContextBuilder()
    now = datetime.now(timezone.utc)
    self_bot = _make_user(1111, "小愛", is_bot=True)
    human = _make_user(2001, "小明")
    fake_channel = MagicMock()

    recent = _make_message(2, fake_channel, human, "五分鐘前說的話", created_at=now - timedelta(minutes=5))
    stale = _make_message(1, fake_channel, human, "二十分鐘前說的話", created_at=now - timedelta(minutes=20))

    async def fake_history(limit=30, before=None):
        assert limit == 10
        for m in (recent, stale):
            yield m

    fake_channel.history = fake_history
    with patch.object(config, "PEER_BOT_IDS", set()):
        ctx, _ = await cb.fetch_recent_channel_context(
            channel=fake_channel,
            current_message_id=3,
            persona_display_name="小愛",
            bot_user=self_bot,
            limit=10,
            max_age_minutes=15,
        )
    assert "五分鐘前說的話" in ctx
    assert "二十分鐘前說的話" not in ctx


async def test_multi_participant_join_and_individual_eviction():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 7001

    ming = _make_user(2001, "小明")
    hua = _make_user(2002, "小華")
    mei = _make_user(2003, "阿美")

    with patch.object(config, "PROACTIVE_HOT_WINDOW_ENABLED", True), \
         patch.object(config, "PROACTIVE_DEBOUNCE_SECONDS", 0.02), \
         patch.object(config, "PROACTIVE_HOT_MAX_CONSECUTIVE_PASS", 5), \
         patch.object(config, "PROACTIVE_HOT_WINDOW_SECONDS", 180.0), \
         patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch("cogs.ai_roleplay.vip_manager.has_remaining_quota", return_value=True):

        # 1. 小明 explicitly @Bot opens the window
        await cog.on_message(_make_message(1, channel, ming, "<@1111> 開局囉", mentions=[cog.bot.user]))
        win = cog._get_hot_window(7001)
        assert win is not None and 2001 in win.active_users

        # 2. 小華 & 阿美 chime in without @, judge returns "reply" -> both promoted to active_users
        cog.proactive_judge.judge = AsyncMock(return_value=ProactiveDecision(decision="reply", reason="joining"))
        await cog.on_message(_make_message(2, channel, hua, "我也要聊這件事"))
        await asyncio.sleep(0.05)
        await cog.on_message(_make_message(3, channel, mei, "算我一個！"))
        await asyncio.sleep(0.05)

        win = cog._get_hot_window(7001)
        assert set(win.active_users.keys()) == {2001, 2002, 2003}

        # 3. 小明 gets 5 consecutive passes -> ONLY 小明 is evicted; 小華 & 阿美 stay!
        cog.proactive_judge.judge = AsyncMock(return_value=ProactiveDecision(decision="pass", reason="off-topic"))
        for idx in range(10, 15):
            await cog.on_message(_make_message(idx, channel, ming, f"自言自語 {idx}"))
            await asyncio.sleep(0.05)

        win = cog._get_hot_window(7001)
        assert win is not None
        assert 2001 not in win.active_users
        assert set(win.active_users.keys()) == {2002, 2003}

        # 4. When remaining participants also reach 5 passes, window closes immediately
        for idx in range(20, 25):
            await cog.on_message(_make_message(idx, channel, hua, f"掰掰 {idx}"))
            await asyncio.sleep(0.05)
        for idx in range(30, 35):
            await cog.on_message(_make_message(idx, channel, mei, f"先走了 {idx}"))
            await asyncio.sleep(0.05)

        assert cog._get_hot_window(7001) is None


async def test_bystander_ignored_after_max_pass_and_react_no_mutation():
    cog = _build_cog(bot_id=1111, bot_name="小愛")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 7002

    ming = _make_user(2001, "小明")
    daxiong = _make_user(2004, "大雄")

    with patch.object(config, "PROACTIVE_HOT_WINDOW_ENABLED", True), \
         patch.object(config, "PROACTIVE_DEBOUNCE_SECONDS", 0.02), \
         patch.object(config, "PROACTIVE_BYSTANDER_MAX_PASS", 2), \
         patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch("cogs.ai_roleplay.vip_manager.has_remaining_quota", return_value=True):

        await cog.on_message(_make_message(1, channel, ming, "<@1111> 早安", mentions=[cog.bot.user]))
        win = cog._get_hot_window(7002)
        orig_exp = win.expires_at

        # Test react does NOT change pass count or extend expires_at
        cog.proactive_judge.judge = AsyncMock(return_value=ProactiveDecision(decision="react", emoji="😂", reason="funny"))
        m_react = _make_message(2, channel, ming, "哈哈哈超好笑")
        await cog.on_message(m_react)
        await asyncio.sleep(0.05)
        m_react.add_reaction.assert_awaited_once_with("😂")
        assert win.active_users[2001] == 0
        assert win.expires_at == orig_exp

        # Bystander 大雄 chats on another topic 3 times -> judge called only twice, then ignored
        cog.proactive_judge.judge = AsyncMock(return_value=ProactiveDecision(decision="pass", reason="other topic"))
        for idx in range(10, 13):
            await cog.on_message(_make_message(idx, channel, daxiong, f"老王今晚吃啥 {idx}"))
            await asyncio.sleep(0.05)

        assert cog.proactive_judge.judge.await_count == 2
        assert 2004 in win.ignored_bystanders
        assert win.active_users[2001] == 0  # Unchanged!


async def test_expiry_reply_to_human_quota_and_dual_bot_isolation():
    cog_a = _build_cog(bot_id=1111, bot_name="小愛")
    cog_b = _build_cog(bot_id=2222, bot_name="大肥魚")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 7003
    ming = _make_user(2001, "小明")

    with patch.object(config, "PROACTIVE_HOT_WINDOW_ENABLED", True), \
         patch.object(config, "PROACTIVE_DEBOUNCE_SECONDS", 0.02), \
         patch.object(config, "PEER_BOT_IDS", {1111, 2222}), \
         patch("cogs.ai_roleplay.KeepTyping", return_value=AsyncMock()), \
         patch("cogs.ai_roleplay.vip_manager.has_remaining_quota", return_value=True):

        # Calling Bot A opens window ONLY on Bot A, not Bot B
        msg_to_a = _make_message(1, channel, ming, "<@1111> 小愛你在嗎", mentions=[cog_a.bot.user])
        await cog_a.on_message(msg_to_a)
        await cog_b.on_message(msg_to_a)
        assert cog_a._get_hot_window(7003) is not None
        assert cog_b._get_hot_window(7003) is None

        # Human Reply to another human skips judge and does NOT increment pass
        cog_a.proactive_judge.judge = AsyncMock()
        ref = SimpleNamespace(message_id=999, resolved=None)
        await cog_a.on_message(_make_message(2, channel, ming, "我回別人一下", reference=ref))
        await asyncio.sleep(0.05)
        assert cog_a.proactive_judge.judge.await_count == 0
        assert cog_a._get_hot_window(7003).active_users[2001] == 0

        # Exhausted VIP quota skips judge silently
        with patch("cogs.ai_roleplay.vip_manager.has_remaining_quota", return_value=False):
            await cog_a.on_message(_make_message(3, channel, ming, "這句不該耗裁判 API"))
            await asyncio.sleep(0.05)
        assert cog_a.proactive_judge.judge.await_count == 0

        # Window expiry closes window cleanly
        win = cog_a._get_hot_window(7003)
        assert cog_a._get_hot_window(7003, now=win.expires_at + 1.0) is None
        assert 7003 not in cog_a._hot_windows


async def main():
    tests = [
        test_judge_json_parsing_and_emoji_validation,
        test_vip_has_remaining_quota_read_only,
        test_context_builder_limit_and_max_age_minutes,
        test_multi_participant_join_and_individual_eviction,
        test_bystander_ignored_after_max_pass_and_react_no_mutation,
        test_expiry_reply_to_human_quota_and_dual_bot_isolation,
    ]
    for fn in tests:
        await fn()
        print(f"[PASS] {fn.__name__}")
    print(f"\nAll {len(tests)} Mode A proactive reply tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
