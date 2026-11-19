import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from core.sticker_service import StickerService
from core.dialog_service import DialogService
from adapters.discord.delivery import DiscordDeliveryService

def run_tests():
    print("=" * 65)
    print("🧪 測試動態迷因梗圖庫索引與發圖系統 (Meme Sticker Delivery Test)")
    print("=" * 65)

    # 1. Test StickerService Index Loading & Matching
    print("\n[1] 測試 StickerService 索引載入與多層檢索:")
    ss = StickerService(index_path="data/meme_stickers_index.json", image_dir="image")
    assert len(ss.items) > 0, "貼圖索引不應為空"
    assert len(ss.categories) > 0, "分類關鍵字庫不應為空"
    print(f"    成功載入 {len(ss.items)} 張貼圖，共 {len(ss.categories)} 個分類關鍵字")

    # Match test: 生氣
    match_angry = ss.match_sticker("生氣")
    print(f"    檢索 '生氣' ➔ 匹配路徑: {match_angry}")
    assert match_angry is not None and match_angry.startswith("image/"), "應能匹配到生氣相關貼圖"

    # Match test: 拜託
    match_please = ss.match_sticker("拜託")
    print(f"    檢索 '拜託' ➔ 匹配路徑: {match_please}")
    assert match_please is not None and match_please.startswith("image/")

    # Match test: 摸頭
    match_pat = ss.match_sticker("摸頭")
    print(f"    檢索 '摸頭' ➔ 匹配路徑: {match_pat}")
    assert match_pat is not None and match_pat.startswith("image/")

    # Match test: 未知關鍵字
    match_none = ss.match_sticker("量子力學超弦理論隨機字串")
    print(f"    檢索無關字串 ➔ 結果: {match_none} (預期 None)")
    assert match_none is None
    print("    ✅ StickerService 匹配檢索 PASS")

    # 2. Test Tag Extraction in DialogService
    print("\n[2] 測試 [MEME: xxx] 標籤解析與分離:")
    raw_response = "還沒寫完啦，不要一直催我嘛！ [MEME: 拜託] [EMOTION: happy]"
    clean_text, emotion = DialogService.extract_emotion_tag(raw_response)
    clean_text, meme_tag = DialogService.extract_meme_tag(clean_text)

    print(f"    原始回應: {raw_response}")
    print(f"    純文字台詞: {clean_text}")
    print(f"    提取情緒標籤: {emotion} (預期 happy)")
    print(f"    提取迷因標籤: {meme_tag} (預期 拜託)")

    assert clean_text == "還沒寫完啦，不要一直催我嘛！"
    assert emotion == "happy"
    assert meme_tag == "拜託"
    print("    ✅ 標籤分離 PASS")

    # 3. Test Residual Tag Cleaning in Delivery Service
    print("\n[3] 測試 Discord 發送前標籤殘留清潔防禦:")
    dirty_text = "這是一段測試訊息 [MEME: 生氣] 祝大家順利！"
    cleaned = DiscordDeliveryService.clean_residual_meme_tag(dirty_text)
    print(f"    過濾前: {dirty_text}")
    print(f"    過濾後: {cleaned}")
    assert "[MEME:" not in cleaned
    assert cleaned == "這是一段測試訊息  祝大家順利！"
    print("    ✅ 標籤殘留清潔防禦 PASS")

    print("\n" + "=" * 65)
    print("🎉 全部動態迷因貼圖庫單元測試 100% 通過！")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
