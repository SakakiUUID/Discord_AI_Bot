import sys
import os
from unittest.mock import MagicMock

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from utils.persona_manager import PersonaManager
from utils.context_builder import ContextBuilder
from utils.message_delivery import MessageDeliveryService
from utils.gemini_dialog_service import GeminiDialogService
from cogs.ai_roleplay import AIRoleplayCog

def run_tests():
    print("=" * 65)
    print("🧪 測試 AI Roleplay 核心模組架構 (Passive Mode Test)")
    print("=" * 65)

    # 1. PersonaManager
    print("\n[1] 測試 PersonaManager 人設與設定管理:")
    pm = PersonaManager()
    print(f"    當前人設: {pm.current_persona_name} (名稱: {pm.persona_display_name})")
    files = pm.get_available_persona_files()
    print(f"    可用人設檔案數量: {len(files)} 個")
    assert len(files) > 0
    details = pm.get_persona_details(files[0])
    print(f"    首個人設詳情: {details['char_name']} (頭像: {details['avatar_status']})")
    print("    ✅ PersonaManager PASS")

    # 2. ContextBuilder
    print("\n[2] 測試 ContextBuilder 時間感知與圖片意圖:")
    cb = ContextBuilder()
    assert cb.format_relative_time(10) == "[剛才]"
    assert cb.format_relative_time(300) == "[5分鐘前]"
    assert cb.format_gap_duration(1800) == "30 分鐘"
    assert cb.has_image_intent("請看這張圖") is True
    assert cb.has_image_intent("今天天氣真好") is False
    print("    ✅ ContextBuilder PASS")

    # 3. MessageDeliveryService
    print("\n[3] 測試 MessageDeliveryService 表現層解析:")
    md = MessageDeliveryService()
    clean_text, emotion = md.extract_emotion_tag("太棒了！ [EMOTION: happy]")
    assert clean_text == "太棒了！"
    assert emotion == "happy"

    cleaned_speech = md.clean_speech_text("[莫寧]: 「漂泊者，請小心。」")
    assert cleaned_speech == "漂泊者，請小心。"
    print("    ✅ MessageDeliveryService PASS")

    # 4. GeminiDialogService
    print("\n[4] 測試 GeminiDialogService 歷史對話管理:")
    gds = GeminiDialogService(api_key=None)
    gds.channel_histories[100] = ["mock_content"]
    assert gds.reset_channel_history(100) is True
    assert gds.reset_channel_history(100) is False
    print("    ✅ GeminiDialogService PASS")

    # 5. AIRoleplayCog Controller
    print("\n[5] 測試 AIRoleplayCog 控制器整合 (純被動交互模式):")
    bot_mock = MagicMock()
    bot_mock.user.id = 12345
    cog = AIRoleplayCog(bot_mock)
    assert cog.persona_manager is not None
    assert cog.context_builder is not None
    assert cog.delivery_service is not None
    assert cog.dialog_service is not None
    assert cog.persona_display_name == pm.persona_display_name
    assert not hasattr(cog, "spontaneous_engine")
    print("    ✅ AIRoleplayCog Controller PASS")

    print("\n" + "=" * 65)
    print("🎉 全部單元測試 100% 通過！自主插話已安全移除！")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
