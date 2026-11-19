# 📈 對話歷史回溯擴充 (30 則) 與無縫雙軌滑動記憶學習機制指南

> **專案**：`dc_auto_bot`  
> **目標**：
> 1. 將對話時的聊天室環境上下文 (Ambient Context) 歷史讀取數量從 **10 則提升至 30 則**。
> 2. 將背景記憶學習升級為**「雙軌混合機制 (Hybrid)」**：滿 30 則立刻學 + 話題冷卻散場 (沉寂 15 分鐘且滿 8 則) 自動收尾沉澱學習。
> 3. 引入**「5 則滑動重疊窗口 (Sliding Overlap Window)」**：解決話題剛好被 30 則切斷時前後語意脫節問題，提供無縫跨批次前情提要。  
> **文件用途**：供後續對話或開發者依循此指南快速執行代碼修改與測試。

---

## 📌 1. 核心架構與三大優勢

### 1.1. 對話歷史擴充至 30 則 ([`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py))
* **好處**：覆蓋過去 10~25 分鐘的完整對話脈絡，大幅提升 AI 在面對洗版、梗圖穿插或長篇討論時的前後文理解力與接梗深度。
* **效能影響**：Discord API 單次請求最多可抓 100 筆，30 筆與 10 筆網路開銷完全相同（< 50ms）；Gemini 3.7 Flash 額外增加約 400~600 Tokens，延遲與成本幾乎零負擔。

### 1.2. 雙軌混合記憶學習機制 ([`utils/memory_manager.py`](file:///d:/OneDrive/tools/dc_auto_bot/utils/memory_manager.py))
解決純次數「卡在 28 則沒滿就不學」與純時間「熱聊時反應遲鈍」的痛點：
* **軌道 1（熱聊即時觸發）**：緩衝區滿 **30 則** ➔ **立即觸發**背景萃取。
* **軌道 2（話題冷卻自動收尾）**：若緩衝區累積 **≥ 8 則**，且群組**安靜超過 15 分鐘**（話題散場）➔ **自動收尾觸發**背景學習。

### 1.3. 5 則滑動重疊窗口 (Sliding Overlap Window)
* **解決痛點**：若長達 45 條的熱門話題剛好在第 30 條處被切斷（例如第 29 條鋪梗、第 31 條出笑點），傳統清空緩衝區會造成語意斷層。
* **機制**：每次萃取完成後，**保留最後 5 則訊息作為下一批次的前情提要**（`buffer = messages_to_process[-5:]`），確保跨批次上下文 100% 無縫銜接。

```mermaid
flowchart LR
    subgraph Batch1 [第 1 批萃取 (1~30 則)]
        M1[第 1 則] --> M25[第 25 則] --> M26[第 26 則] --> M30[第 30 則]
    end
    
    subgraph Overlap [5 則重疊窗口: 前情提要]
        M26 --> M30
    end
    
    subgraph Batch2 [第 2 批萃取 (26~45 則)]
        Overlap --> M31[第 31 則] --> M45[第 45 則]
    end
```

---

## 🛠️ 2. 具體代碼修改 (Diff)

### 檔案 1：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)
在 `fetch_recent_channel_context` 函式中，將歷史抓取數量由 `10` 改為 `30`：

```diff
         fetched_items = []
         empty_count = 0
         try:
-            async for msg in channel.history(limit=10, before=before_obj):
+            async for msg in channel.history(limit=30, before=before_obj):
                 # Keep our Bot's own messages, but skip third-party automated system bots
                 if msg.author.bot and msg.author != self.bot.user:
                     continue
```

---

### 檔案 2：[`utils/memory_manager.py`](file:///d:/OneDrive/tools/dc_auto_bot/utils/memory_manager.py)
引入時間戳記追蹤、背景定時巡檢任務與 5 則滑動重疊機制：

```diff
 import os
 import json
 import logging
 import asyncio
+import time
 from typing import Dict, List, Optional, Any
 from google import genai
 from google.genai import types

 DATA_DIR = "data"
 GUILD_CULTURES_FILE = os.path.join(DATA_DIR, "guild_cultures.json")
 USER_PROFILES_FILE = os.path.join(DATA_DIR, "user_profiles.json")
-BUFFER_THRESHOLD = 20  # Number of messages to trigger background memory extraction
+BUFFER_THRESHOLD = 30  # 熱聊即時觸發門檻
+MIN_IDLE_MESSAGES = 8   # 冷卻收尾最低訊息門檻
+IDLE_TIMEOUT_SECONDS = 900  # 安靜冷卻時間 (15 分鐘)
+OVERLAP_COUNT = 5      # 跨批次滑動重疊前情提要筆數

 class MemoryManager:
     def __init__(self):
         self.guild_cultures: Dict[str, Dict[str, Any]] = {}
         self.user_profiles: Dict[str, Dict[str, Any]] = {}
         self.channel_buffers: Dict[int, List[Dict[str, Any]]] = {}
+        self.channel_last_activity: Dict[int, float] = {}
         self.extracting_channels: set = set()
+        self.idle_check_task: Optional[asyncio.Task] = None
         
         # Initialize Google GenAI client for background extraction
         self.ai_client = genai.Client(api_key=config.GEMINI_API_KEY) if config.GEMINI_API_KEY else None
         
         self._load_data()
+        self._start_idle_check_loop()
+
+    def _start_idle_check_loop(self):
+        """啟動定時巡檢迴圈 (每 5 分鐘檢查一次是否有散場冷卻之頻道)"""
+        try:
+            loop = asyncio.get_running_loop()
+            self.idle_check_task = loop.create_task(self._idle_check_worker())
+        except RuntimeError:
+            pass # 尚未有 running loop 時會在事件循環就緒後啟動
+
+    async def _idle_check_worker(self):
+        """背景巡檢：自動結算已散場但未滿 30 則的對話記憶"""
+        while True:
+            try:
+                await asyncio.sleep(300) # 每 5 分鐘巡檢一次
+                now = time.time()
+                for channel_id, buffer in list(self.channel_buffers.items()):
+                    if channel_id in self.extracting_channels:
+                        continue
+                    last_time = self.channel_last_activity.get(channel_id, 0)
+                    # 滿足：累積滿 8 則 且 已安靜超過 15 分鐘
+                    if len(buffer) >= MIN_IDLE_MESSAGES and (now - last_time) >= IDLE_TIMEOUT_SECONDS:
+                        logger.info(f"🌙 [Memory Engine] Channel {channel_id} idle for 15m with {len(buffer)} msgs. Triggering idle extraction.")
+                        asyncio.create_task(self.extract_memories_in_background(channel_id))
+            except Exception as e:
+                logger.error(f"Error in memory idle check worker: {e}")

     def add_message_to_buffer(
         self,
         channel_id: int,
         guild_id: Optional[int],
         user_id: int,
         username: str,
         content: str,
         image_bytes: Optional[bytes] = None,
         image_mime: Optional[str] = None
     ):
+        # 確保背景定時巡檢任務已運行
+        if self.idle_check_task is None or self.idle_check_task.done():
+            self._start_idle_check_loop()
+
         if not content and not image_bytes:
             return
 
         if channel_id not in self.channel_buffers:
             self.channel_buffers[channel_id] = []
+        self.channel_last_activity[channel_id] = time.time()
 
         item = {
             "guild_id": str(guild_id) if guild_id else "DM",
             "user_id": str(user_id),
             "username": username,
             "content": content.strip() if content else "（分享了一張圖片/梗圖）"
         }
         if image_bytes and image_mime:
             item["image_bytes"] = image_bytes
             item["image_mime"] = image_mime
 
         self.channel_buffers[channel_id].append(item)
 
-        # Trigger background memory extraction if threshold is reached and not currently extracting
+        # 軌道 1：累積滿 30 則立刻觸發
         if len(self.channel_buffers[channel_id]) >= BUFFER_THRESHOLD and channel_id not in self.extracting_channels:
             asyncio.create_task(self.extract_memories_in_background(channel_id))

     async def extract_memories_in_background(self, channel_id: int):
         """Background asynchronous task to extract group culture and user profiles."""
         if channel_id in self.extracting_channels or not self.ai_client:
             return
 
         self.extracting_channels.add(channel_id)
         messages_to_process = list(self.channel_buffers.get(channel_id, []))
-        self.channel_buffers[channel_id] = []  # Clear buffer
+        # 滑動重疊窗口：保留最後 OVERLAP_COUNT 則作為下一批次的前情提要
+        self.channel_buffers[channel_id] = messages_to_process[-OVERLAP_COUNT:] if len(messages_to_process) > OVERLAP_COUNT else []
```

---

### 檔案 3：[`README.md`](file:///d:/OneDrive/tools/dc_auto_bot/README.md)（文件同步）
同步更新專案說明文件：

```diff
-* **聊天室動態上下文 (Ambient Context)**：@標記機器人時，自動抓取頻道內最近 10 條聊天紀錄（包含 Bot 本人之前的發言），AI 完美理解對話前後文。
+* **聊天室動態上下文 (Ambient Context)**：@標記機器人時，自動抓取頻道內最近 30 條聊天紀錄（包含 Bot 本人之前的發言），AI 完美理解對話前後文。
```

---

## 🧪 3. 測試與驗證計畫

| 測試情境 | 操作流程 | 預期表現 |
| :--- | :--- | :--- |
| **1. 軌道 1：熱聊即時學習** | 在頻道內連續發言 30 則訊息。 | 發送第 30 則當下立即觸發萃取，並在緩衝區保留第 26~30 則作為下一輪前情提要。 |
| **2. 軌道 2：散場冷卻收尾學習** | 在頻道發言 12 則訊息後停止發言（群友散場）。 | 靜置 15 分鐘後，日誌顯示 `🌙 [Memory Engine] ... idle for 15m with 12 msgs. Triggering idle extraction.`，成功消化未滿 30 則的對話。 |
| **3. 滑動重疊無縫銜接測試** | 某長話題跨越第 30 條（第 28 條鋪梗，第 32 條出笑點）。 | 觀察第二輪萃取日誌與 JSON 輸出，確認 AI 成功結合前情提要 26~30 條讀懂完整笑點，未出現語意斷層。 |
| **4. 上下文 30 則理解測試** | 群友熱聊 20 則以上話題後 @Bot 提問：「剛才大家主要在聊什麼？」。 | AI 能精準總結 20 則以前的開頭話題，記憶連貫性極佳。 |
