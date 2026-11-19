import sys
import os
import datetime

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from utils.context_builder import ContextBuilder

def run_tests():
    print("=" * 60)
    print("🧪 測試時間感知 (Temporal Awareness) 標籤與沉寂斷層系統")
    print("=" * 60)

    # Test 1: Relative time format
    print("\n[1] 測試相對時間標籤轉換 (format_relative_time):")
    t_just_now = ContextBuilder.format_relative_time(25.0)
    t_mins = ContextBuilder.format_relative_time(300.0) # 5m
    t_hours = ContextBuilder.format_relative_time(7200.0) # 2h
    t_days = ContextBuilder.format_relative_time(172800.0) # 2d

    print(f"    25 秒前: {t_just_now} (預期 [剛才])")
    print(f"    300 秒前: {t_mins} (預期 [5分鐘前])")
    print(f"    7200 秒前: {t_hours} (預期 [2小時前])")
    print(f"    172800 秒前: {t_days} (預期 [2天前])")

    assert t_just_now == "[剛才]"
    assert t_mins == "[5分鐘前]"
    assert t_hours == "[2小時前]"
    assert t_days == "[2天前]"
    print("    ✅ 相對時間標籤 PASS")

    # Test 2: Gap duration format
    print("\n[2] 測試沉寂間隔時間轉換 (format_gap_duration):")
    g_mins = ContextBuilder.format_gap_duration(1500.0) # 25m
    g_hours = ContextBuilder.format_gap_duration(10800.0) # 3h
    g_days = ContextBuilder.format_gap_duration(259200.0) # 3d

    print(f"    1500 秒間隔: {g_mins} (預期 25 分鐘)")
    print(f"    10800 秒間隔: {g_hours} (預期 3 小時)")
    print(f"    259200 秒間隔: {g_days} (預期 3 天)")

    assert g_mins == "25 分鐘"
    assert g_hours == "3 小時"
    assert g_days == "3 天"
    print("    ✅ 沉寂間隔轉換 PASS")

    # Test 3: Silence gap insertion simulation
    print("\n[3] 測試沉寂斷層分割線插入邏輯 (Gap >= 20m):")
    now = datetime.datetime.now(datetime.timezone.utc)
    mock_items = [
        {"author": "UserA", "text": "2小時前問莫寧問題", "time_tag": "[2小時前]", "created_at": now - datetime.timedelta(hours=2), "image_bytes": None, "has_img_att": False},
        {"author": "UserB", "text": "剛才上線跟大家打招呼", "time_tag": "[剛才]", "created_at": now - datetime.timedelta(seconds=10), "image_bytes": None, "has_img_att": False}
    ]

    recent_messages = []
    last_msg_time = None
    for item in mock_items:
        if last_msg_time is not None:
            gap = (item["created_at"] - last_msg_time).total_seconds()
            if gap >= 1200:
                recent_messages.append(f"--- ⏳ (此處頻道沉寂了 {ContextBuilder.format_gap_duration(gap)}) ---")
        last_msg_time = item["created_at"]
        recent_messages.append(f"{item['time_tag']} [{item['author']}]: {item['text']}")

    context_str = "\n".join(recent_messages)
    print(f"    組合後的上下文預覽:\n{context_str}")

    assert "--- ⏳ (此處頻道沉寂了 1 小時)" in context_str or "--- ⏳ (此處頻道沉寂了 2 小時)" in context_str
    assert "[2小時前] [UserA]: 2小時前問莫寧問題" in context_str
    assert "[剛才] [UserB]: 剛才上線跟大家打招呼" in context_str
    print("    ✅ 沉寂斷層分割線 PASS")

    print("\n" + "=" * 60)
    print("🎉 全部單元測試 100% 通過！")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
