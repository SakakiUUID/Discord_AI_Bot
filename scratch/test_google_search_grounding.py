import os
import sys
import asyncio
import logging

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from core.dialog_service import DialogService
from google.genai import types


def test_search_configuration_and_tools():
    print("=================================================================")
    print("🧪 測試 Google Search Grounding 聯網搜尋設定與 Tool 配置")
    print("=================================================================\n")

    # 1. 驗證預設開關
    print(f"[1] 測試 config.ENABLE_GOOGLE_SEARCH 預設值:")
    print(f"    config.ENABLE_GOOGLE_SEARCH = {config.ENABLE_GOOGLE_SEARCH}")
    assert isinstance(config.ENABLE_GOOGLE_SEARCH, bool), "ENABLE_GOOGLE_SEARCH 必須為布林值"
    print("    ✅ 預設開關型別檢驗 PASS\n")

    # 2. 測試 DialogService._get_genai_config 工具注入
    dialog_service = DialogService()
    test_persona = "你是愛彌斯，元氣滿滿的少女。"
    test_memory = "【群組記憶】漂泊者是好朋友。"

    print("[2] 測試 enable_search=True 時工具生成:")
    config.ENABLE_GOOGLE_SEARCH = True
    gen_config = dialog_service._get_genai_config(
        persona=test_persona,
        memory_context=test_memory,
        enable_search=True
    )
    assert gen_config.tools is not None, "enable_search=True 時 tools 不應為 None"
    assert len(gen_config.tools) == 1, "tools 陣列長度應為 1"
    assert gen_config.tools[0].google_search is not None, "tools[0] 應包含 google_search"
    assert "【聯網搜尋與時效指示】" in gen_config.system_instruction, "System instruction 應包含搜尋指示"
    print(f"    Tools 配置: {gen_config.tools}")
    print("    ✅ Google Search 工具注入驗證 PASS\n")

    # 3. 測試 enable_search=False 時不注入工具
    print("[3] 測試 enable_search=False 時禁用工具:")
    gen_config_disabled = dialog_service._get_genai_config(
        persona=test_persona,
        memory_context=test_memory,
        enable_search=False
    )
    assert gen_config_disabled.tools is None, "enable_search=False 時 tools 應為 None"
    assert "【聯網搜尋與時效指示】" not in gen_config_disabled.system_instruction, "System instruction 不應包含搜尋指示"
    print("    ✅ 禁用搜尋工具驗證 PASS\n")

    # 4. 測試 全域 config.ENABLE_GOOGLE_SEARCH=False 時不注入工具
    print("[4] 測試 全域 config.ENABLE_GOOGLE_SEARCH=False 行為:")
    config.ENABLE_GOOGLE_SEARCH = False
    gen_config_global_off = dialog_service._get_genai_config(
        persona=test_persona,
        memory_context=test_memory,
        enable_search=True
    )
    assert gen_config_global_off.tools is None, "全域關閉時 tools 應為 None"
    config.ENABLE_GOOGLE_SEARCH = True  # 還原
    print("    ✅ 全域開關控制驗證 PASS\n")


async def test_live_search_if_api_key_available():
    print("[5] 測試 Gemini API 聯網搜尋調用 (若有 GEMINI_API_KEY):")
    if not config.GEMINI_API_KEY:
        print("    ℹ️ 未偵測到 GEMINI_API_KEY，跳過實際 API 查詢測試。")
        return

    from google import genai
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    dialog_service = DialogService(ai_client=client)

    persona = "你是愛彌斯，說話熱情活潑，稱呼對方為漂泊者。"
    query = "請問 2026 年當前有什麼即時新聞或近期焦點時事？請簡短用你的角色口吻回覆 2 句話。"
    print(f"    發送測試查詢: '{query}'")

    clean_text, emotion, meme_kw = await dialog_service.generate_response(
        channel_id="test_search_channel",
        user_text=query,
        persona=persona,
        enable_search=True
    )

    print(f"    AI 回覆內容:\n    {clean_text}")
    print(f"    提取情緒: {emotion}, 提取迷因: {meme_kw}")
    assert clean_text and len(clean_text) > 5, "回覆內容不應為空"
    print("    ✅ 實際 API 聯網搜尋與人設整合 PASS\n")


async def main():
    test_search_configuration_and_tools()
    await test_live_search_if_api_key_available()
    print("=================================================================")
    print("🎉 Google Search Grounding 聯網搜尋單元測試 100% 通過！")
    print("=================================================================")


if __name__ == "__main__":
    asyncio.run(main())
