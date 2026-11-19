import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from utils.persona_manager import PersonaManager

def run_tests():
    print("=" * 65)
    print("🧪 測試角色喚醒詞 / 名字關鍵字直接對話 (Name-Wake Word Dialogue Test)")
    print("=" * 65)

    pm = PersonaManager()

    # Test 1: Aliases per persona
    print("\n[1] 測試五大角色專屬喚醒詞別名列表:")
    
    # Aemeath
    pm.current_persona_name = "persona_aemeath.txt"
    aemeath_aliases = pm.get_active_persona_aliases()
    print(f"    愛彌斯別名: {aemeath_aliases}")
    assert "愛彌斯" in aemeath_aliases and "aemeath" in aemeath_aliases and "小愛" in aemeath_aliases

    # Mornye
    pm.current_persona_name = "persona_mornye.txt"
    mornye_aliases = pm.get_active_persona_aliases()
    print(f"    莫寧別名: {mornye_aliases}")
    assert "莫寧" in mornye_aliases and "mornye" in mornye_aliases and "莫寧教授" in mornye_aliases

    # Airi (Default)
    pm.current_persona_name = "persona_default.txt"
    airi_aliases = pm.get_active_persona_aliases()
    print(f"    愛莉別名: {airi_aliases}")
    assert "愛莉" in airi_aliases and "airi" in airi_aliases and "愛莉希雅" in airi_aliases

    # Daniya
    pm.current_persona_name = "persona_daniya.txt"
    daniya_aliases = pm.get_active_persona_aliases()
    print(f"    達妮婭別名: {daniya_aliases}")
    assert "達妮婭" in daniya_aliases and "daniya" in daniya_aliases

    # Liino
    pm.current_persona_name = "persona_liino.txt"
    liino_aliases = pm.get_active_persona_aliases()
    print(f"    梨諾別名: {liino_aliases}")
    assert "梨諾" in liino_aliases and "liino" in liino_aliases
    print("    ✅ 五大角色喚醒詞別名 PASS")

    # Test 2: Matching logic
    print("\n[2] 測試各情境喚醒詞命中比對:")
    
    # Active: Aemeath
    pm.current_persona_name = "persona_aemeath.txt"
    active_aliases = pm.get_active_persona_aliases()
    
    msg_1 = "今天天氣真好你覺得呢 愛彌斯"
    hit_1 = any(a.lower() in msg_1.lower() for a in active_aliases)
    print(f"    '{msg_1}' ➔ 命中: {hit_1} (預期 True)")
    assert hit_1 is True

    msg_2 = "小愛 你在幹嘛？"
    hit_2 = any(a.lower() in msg_2.lower() for a in active_aliases)
    print(f"    '{msg_2}' ➔ 命中: {hit_2} (預期 True)")
    assert hit_2 is True

    msg_3 = "今天晚餐吃咖哩飯"
    hit_3 = any(a.lower() in msg_3.lower() for a in active_aliases)
    print(f"    '{msg_3}' ➔ 命中: {hit_3} (預期 False)")
    assert hit_3 is False

    # Test 3: Inactive persona isolation
    print("\n[3] 測試非活躍角色隔離 (當前為愛彌斯，群友叫莫寧):")
    msg_4 = "莫寧教授 請問這個怎麼算？"
    hit_4 = any(a.lower() in msg_4.lower() for a in active_aliases)
    print(f"    '{msg_4}' ➔ 命中: {hit_4} (預期 False)")
    assert hit_4 is False
    print("    ✅ 非活躍角色隔離 PASS")

    print("\n" + "=" * 65)
    print("🎉 全部喚醒詞單元測試 100% 通過！")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
