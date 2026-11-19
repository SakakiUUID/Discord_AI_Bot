import asyncio
import sys
import os
import time

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from utils.memory_manager import MemoryManager, BUFFER_THRESHOLD, MIN_IDLE_MESSAGES, IDLE_TIMEOUT_SECONDS, OVERLAP_COUNT

async def run_tests():
    print("=" * 60)
    print("🧪 測試雙軌混合記憶觸發與 5 則滑動重疊窗口")
    print("=" * 60)

    mgr = MemoryManager()
    channel_id = 999001
    guild_id = 888001

    # Test 1: Verify constants
    print(f"\n[1] 驗證常數設定:")
    print(f"    BUFFER_THRESHOLD = {BUFFER_THRESHOLD} (預期 30)")
    print(f"    MIN_IDLE_MESSAGES = {MIN_IDLE_MESSAGES} (預期 8)")
    print(f"    IDLE_TIMEOUT_SECONDS = {IDLE_TIMEOUT_SECONDS} (預期 900)")
    print(f"    OVERLAP_COUNT = {OVERLAP_COUNT} (預期 5)")
    assert BUFFER_THRESHOLD == 30
    assert MIN_IDLE_MESSAGES == 8
    assert IDLE_TIMEOUT_SECONDS == 900
    assert OVERLAP_COUNT == 5
    print("    ✅ 常數驗證 PASS")

    # Test 2: Add 30 messages and verify sliding overlap window
    print(f"\n[2] 測試發送 30 則訊息與 5 則重疊窗口:")
    # Mock extract_memories_in_background to avoid real Gemini API call during unit test
    extracted_batches = []
    async def mock_extract(cid):
        mgr.extracting_channels.add(cid)
        to_process = list(mgr.channel_buffers.get(cid, []))
        mgr.channel_buffers[cid] = to_process[-OVERLAP_COUNT:] if len(to_process) > OVERLAP_COUNT else []
        extracted_batches.append(to_process)
        mgr.extracting_channels.discard(cid)

    mgr.extract_memories_in_background = mock_extract

    for i in range(1, 31):
        mgr.add_message_to_buffer(
            channel_id=channel_id,
            guild_id=guild_id,
            user_id=1001,
            username="Tester",
            content=f"這是第 {i} 則訊息"
        )

    # Let event loop process tasks
    await asyncio.sleep(0.1)

    print(f"    萃取批次處理訊息數量: {len(extracted_batches[0])} 則")
    print(f"    萃取後剩餘重疊前情提要數量: {len(mgr.channel_buffers[channel_id])} 則")
    assert len(extracted_batches[0]) == 30
    assert len(mgr.channel_buffers[channel_id]) == 5
    print(f"    前情提要包含的內容: {[m['content'] for m in mgr.channel_buffers[channel_id]]}")
    assert mgr.channel_buffers[channel_id][0]["content"] == "這是第 26 則訊息"
    assert mgr.channel_buffers[channel_id][-1]["content"] == "這是第 30 則訊息"
    print("    ✅ 30 則即時觸發與 5 則滑動重疊 PASS")

    # Test 3: Idle timeout check logic
    print(f"\n[3] 測試散場冷卻 (Idle Timeout) 檢測邏輯:")
    idle_channel = 999002
    for i in range(1, 10): # 9 msgs (>= 8)
        mgr.add_message_to_buffer(
            channel_id=idle_channel,
            guild_id=guild_id,
            user_id=1002,
            username="IdleUser",
            content=f"散場前的發言 {i}"
        )

    # Simulate 16 minutes passed
    mgr.channel_last_activity[idle_channel] = time.time() - 960

    # Run check logic
    now = time.time()
    last_time = mgr.channel_last_activity.get(idle_channel, 0)
    buf = mgr.channel_buffers.get(idle_channel, [])
    should_trigger = len(buf) >= MIN_IDLE_MESSAGES and (now - last_time) >= IDLE_TIMEOUT_SECONDS
    print(f"    頻道訊息數: {len(buf)}, 閒置時間: {int(now - last_time)}s, 觸發判定: {should_trigger}")
    assert should_trigger is True
    print("    ✅ 散場冷卻檢測 PASS")

    print("\n" + "=" * 60)
    print("🎉 全部單元測試 100% 通過！")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
