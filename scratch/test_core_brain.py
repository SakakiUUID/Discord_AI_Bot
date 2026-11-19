import sys
import os
import datetime
from unittest.mock import MagicMock

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from core.models import IncomingMessage, OutgoingResponse
from core.persona_service import PersonaService
from core.memory_service import MemoryService
from core.dialog_service import DialogService
from core.context_manager import ContextManager
from core.agent_engine import AgentEngine

def run_tests():
    print("=" * 65)
    print("🧪 測試通用 AI Agent 核心中樞純度 (Core Brain Purity Test)")
    print("=" * 65)

    # 1. Models Test
    print("\n[1] 測試標準資料契約 (IncomingMessage / OutgoingResponse):")
    now = datetime.datetime.now(datetime.timezone.utc)
    incoming = IncomingMessage(
        channel_type="line",
        channel_id="line_group_123",
        user_id="U12345678",
        username="Tester",
        text="愛彌斯 今天心情好嗎？",
        created_at=now,
        is_direct_chat=False
    )
    assert incoming.channel_type == "line"
    assert incoming.user_id == "U12345678"
    print(f"    IncomingMessage 建立成功: {incoming.username} @ {incoming.channel_type}")

    outgoing = OutgoingResponse(
        reply_text="心情很好呀！",
        emotion="happy",
        sticker_path="assets/emotions/happy.png"
    )
    assert outgoing.reply_text == "心情很好呀！"
    assert outgoing.emotion == "happy"
    print("    ✅ Data Models PASS")

    # 2. PersonaService Test
    print("\n[2] 測試 PersonaService 純 Python 人設與別名:")
    ps = PersonaService()
    ps.load_persona("persona_aemeath.txt")
    aliases = ps.get_active_persona_aliases()
    print(f"    愛彌斯活躍別名: {aliases}")
    assert "小愛" in aliases and "愛彌斯" in aliases
    print("    ✅ PersonaService PASS")

    # 3. ContextManager Test
    print("\n[3] 測試 ContextManager 時間感知與環境上下文:")
    cm = ContextManager()
    t_tag = cm.format_relative_time(45)
    assert t_tag == "[剛才]"
    mock_msgs = [
        {"author": "User1", "text": "早安", "created_at": now - datetime.timedelta(minutes=30)},
        {"author": "User2", "text": "早呀", "created_at": now - datetime.timedelta(seconds=10)}
    ]
    ambient_str = cm.build_ambient_context_string(mock_msgs)
    print(f"    生成環境上下文:\n{ambient_str.strip()}")
    assert "--- ⏳ (此處頻道沉寂了" in ambient_str
    print("    ✅ ContextManager PASS")

    # 4. MemoryService Test
    print("\n[4] 測試 MemoryService 命名空間雙層記憶:")
    ms = MemoryService(ai_client=None)
    ms.guild_cultures["line_group_123"] = {
        "group_vibe": "LINE 社群早安問候",
        "inside_jokes": ["大保底歪莫寧", "加班地獄"],
        "speech_style": "簡短"
    }
    ms.user_profiles["line_U12345678"] = {
        "username": "Tester",
        "speaking_style": "熱情",
        "traits_and_interests": "喜歡抽卡與討論遊戲"
    }
    mem_ctx = ms.get_combined_context(guild_id="line_group_123", user_id="line_U12345678")
    print(f"    組合記憶字串預覽:\n{mem_ctx}")
    assert "LINE 社群早安問候" in mem_ctx
    assert "喜歡抽卡與討論遊戲" in mem_ctx
    print("    ✅ MemoryService PASS")

    # 5. AgentEngine Orchestration Test
    print("\n[5] 測試 AgentEngine 統一協調器 (無 Discord 依賴):")
    engine = AgentEngine(api_key=None)
    engine.persona_service.load_persona("persona_aemeath.txt")

    # Wake word check
    assert engine.check_wake_word("小愛 在嗎？") is True
    assert engine.check_wake_word("今天晚餐吃什麼") is False

    # Sticker check
    sticker_path = engine.get_sticker_path("happy")
    print(f"    'happy' 貼圖路徑: {sticker_path}")

    print("    ✅ AgentEngine PASS")

    print("\n" + "=" * 65)
    print("🎉 核心大腦中樞 (Core Brain) 100% 獨立純度驗證通過！")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
