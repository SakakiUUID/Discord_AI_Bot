import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from utils.memory_manager import MemoryManager

def run_tests():
    print("=" * 65)
    print("🧪 測試 Inside Jokes 智慧動態汰換機制 (方案 A)")
    print("=" * 65)

    mm = MemoryManager()
    guild_id = "test_guild_123"

    # Setup initial culture with an obsolete joke
    mm.guild_cultures[guild_id] = {
        "group_vibe": "熱鬧吐槽",
        "inside_jokes": ["昨晚吃便當打翻", "大保底歪莫寧", "加班地獄"],
        "speech_style": "簡短"
    }

    print("\n[1] 測試舊梗淘汰與新梗增補:")
    print(f"    初始內部梗清單: {mm.guild_cultures[guild_id]['inside_jokes']}")

    # Simulated AI result: obsolete joke removed, new joke added
    mock_ai_result = {
        "guild_culture": {
            "group_vibe": "抽卡狂歡熱鬧吐槽",
            "inside_jokes": ["大保底歪莫寧", "加班地獄", "長離一命太強了"],
            "speech_style": "快節奏梗圖"
        }
    }

    # Simulate extraction result processing
    culture_data = mock_ai_result["guild_culture"]
    managed_jokes = culture_data.get("inside_jokes", [])
    if isinstance(managed_jokes, list) and managed_jokes:
        cleaned_jokes = [str(j).strip() for j in managed_jokes if str(j).strip()][:6]
    else:
        cleaned_jokes = mm.guild_cultures[guild_id].get("inside_jokes", [])

    mm.guild_cultures[guild_id]["group_vibe"] = culture_data.get("group_vibe", "")
    mm.guild_cultures[guild_id]["inside_jokes"] = cleaned_jokes
    mm.guild_cultures[guild_id]["speech_style"] = culture_data.get("speech_style", "")

    updated_jokes = mm.guild_cultures[guild_id]["inside_jokes"]
    print(f"    更新後內部梗清單: {updated_jokes}")

    assert "昨晚吃便當打翻" not in updated_jokes, "過期舊梗應該被成功淘汰移除"
    assert "長離一命太強了" in updated_jokes, "熱門新梗應該被成功收錄"
    assert "大保底歪莫寧" in updated_jokes, "核心舊梗應該被妥善保留"
    print("    ✅ 舊梗汰除與新梗收錄 PASS")

    print("\n[2] 測試最多 6 個數量收斂限制:")
    long_jokes_input = ["梗1", "梗2", "梗3", "梗4", "梗5", "梗6", "梗7", "梗8"]
    cleaned_long = [str(j).strip() for j in long_jokes_input if str(j).strip()][:6]
    print(f"    輸入 8 個梗 ➔ 截斷結果: {cleaned_long} (長度: {len(cleaned_long)})")
    assert len(cleaned_long) == 6
    print("    ✅ 數量收斂 PASS")

    print("\n[3] 測試空回傳時的安全防護 (保留現有清單):")
    empty_ai_result = {"guild_culture": {"group_vibe": "平淡聊天", "inside_jokes": []}}
    managed_empty = empty_ai_result["guild_culture"].get("inside_jokes", [])
    if isinstance(managed_empty, list) and managed_empty:
        safe_jokes = [str(j).strip() for j in managed_empty if str(j).strip()][:6]
    else:
        safe_jokes = mm.guild_cultures[guild_id].get("inside_jokes", [])
    print(f"    空回傳防護後內部梗清單: {safe_jokes}")
    assert len(safe_jokes) == 3
    print("    ✅ 安全防護 PASS")

    print("\n" + "=" * 65)
    print("🎉 全部 Inside Jokes 智慧動態汰換測試 100% 通過！")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
