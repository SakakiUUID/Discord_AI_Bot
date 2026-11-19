# ⏳ 聊天室時間感知 (Temporal Awareness) 與 1 分鐘極速自主插話系統規格指南

> **專案**：`dc_auto_bot`  
> **目標模組**：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)  
> **核心升級**：
> 1. **時間感知與沉寂斷層**：在歷史訊息與後台 Log 中全面標註「相對時間差」（如 `[剛才]`、`[5分鐘前]`、`[2小時前]`）與「沉寂分割線」（`--- ⏳ 沉寂 X 小時 ---`），讓 AI 與開發者一眼看懂時間脈絡。
> 2. **冷卻時間縮短至 1 分鐘**：由原本的 15 分鐘（900 秒）縮短為 **60 秒**，大幅提高互動靈活性。
> 3. **AI 時間感知決策賦權**：讓 Gemini 根據時間標籤自主判斷「當前最新話題」，避免回覆數小時前的過期冷場訊息。  
> **文件用途**：供後續對話或開發者直接依循此規格修改 Python 原始碼。

---

## 📌 1. 架構與運作原理

### 1.1. 時間差計算與標籤規範
在 `fetch_recent_channel_context` 讀取每則訊息時，以當前時間 `now_utc` 比對 `msg.created_at`：
* **< 60 秒** ➔ 標記為 `[剛才]`
* **1 ~ 59 分鐘** ➔ 標記為 `[X分鐘前]`
* **1 ~ 24 小時** ➔ 標記為 `[X小時前]`
* **> 24 小時** ➔ 標記為 `[X天前]`

### 1.2. 沉寂斷層分割線 (Silence Gap Divider)
若相鄰兩則訊息的時間間隔 **≥ 20 分鐘**，系統自動在中間插入一行沉寂分割線：
```text
[2小時前] [使用者 A]: 莫寧你覺得這個要怎麼解？
--- ⏳ (此處頻道沉寂了 2 小時) ---
[3分鐘前] [使用者 B]: 剛才打遊戲遇到怪人
[剛才]    [使用者 C]: 哈哈笑死
```

### 1.3. 1 分鐘極速冷卻與 AI 智能決策
* 冷卻時間縮短為 **60 秒**（防止同一分鐘內連續洗版，但允許快速跟進話題）。
* AI 提示詞明確要求：**「嚴禁去補回答已被沉寂斷層隔開的幾小時前舊問題，必須聚焦於『[剛才]』或最新正在討論的內容」**。

---

## 🛠️ 2. 具體代碼修改 (Diff)

### 檔案：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)

#### 2.1. 縮短冷卻時間為 60 秒
```diff
 class AIRoleplayCog(commands.Cog):
     def __init__(self, bot: commands.Bot):
         self.bot = bot
         self.spontaneous_cooldowns: Dict[int, float] = {}
         self.spontaneous_enabled: bool = True
-        self.spontaneous_cooldown_seconds: int = 900  # 15 分鐘
+        self.spontaneous_cooldown_seconds: int = 60   # 縮短至 1 分鐘
         self.spontaneous_probability: float = 0.08    # 8% 機率
```

---

#### 2.2. 升級 `fetch_recent_channel_context` (時間差計算與沉寂分割線)
```diff
+    @staticmethod
+    def _format_relative_time(diff_seconds: float) -> str:
+        """Convert seconds difference into human/AI friendly relative time tag."""
+        if diff_seconds < 60:
+            return "[剛才]"
+        elif diff_seconds < 3600:
+            return f"[{int(diff_seconds // 60)}分鐘前]"
+        elif diff_seconds < 86400:
+            hours = diff_seconds / 3600
+            return f"[{int(hours) if hours.is_integer() else f'{hours:.1f}'}小時前]"
+        else:
+            days = int(diff_seconds // 86400)
+            return f"[{days}天前]"
+
+    @staticmethod
+    def _format_gap_duration(gap_seconds: float) -> str:
+        """Format silence gap between consecutive messages."""
+        if gap_seconds < 3600:
+            return f"{int(gap_seconds // 60)} 分鐘"
+        elif gap_seconds < 86400:
+            return f"{int(gap_seconds // 3600)} 小時"
+        else:
+            return f"{int(gap_seconds // 86400)} 天"

     async def fetch_recent_channel_context(
         self,
         channel: discord.abc.Messageable,
         current_message_id: Optional[int] = None,
         fetch_images: bool = False
     ) -> tuple[str, list[types.Part]]:
         """Fetch recent messages from the channel for ambient context."""
         if not hasattr(channel, "history"):
             return "", []
 
         fetched_items = []
         empty_count = 0
+        now_utc = discord.utils.utcnow()
         try:
             before_obj = None
             if current_message_id:
                 before_obj = discord.Object(id=current_message_id)
 
             async for msg in channel.history(limit=30, before=before_obj):
                 if msg.author.bot and msg.author != self.bot.user:
                     continue
 
                 clean = re.sub(r'<@!?\d+>', '', msg.content).strip() if msg.content else ""
                 has_img_att = bool(msg.attachments and any(a.content_type and a.content_type.startswith("image/") for a in msg.attachments))
 
                 img_bytes = None
                 img_mime = "image/png"
                 if fetch_images and has_img_att:
                     for att in msg.attachments:
                         if att.content_type and att.content_type.startswith("image/"):
                             if not att.size or att.size <= 8 * 1024 * 1024:
                                 try:
                                     img_bytes = await att.read()
                                     img_mime = att.content_type
                                     break
                                 except Exception as e:
                                     logger.warning(f"Could not read historical image attachment: {e}")
 
                 if not clean and not has_img_att and not img_bytes:
                     empty_count += 1
                     continue
 
                 author_name = f"{self.persona_display_name} (你)" if (self.bot.user and msg.author.id == self.bot.user.id) else msg.author.display_name
+                diff_seconds = max(0.0, (now_utc - msg.created_at).total_seconds())
+                time_tag = self._format_relative_time(diff_seconds)
+
                 fetched_items.append({
                     "author": author_name,
                     "text": clean,
                     "image_bytes": img_bytes,
                     "image_mime": img_mime,
                     "has_img_att": has_img_att,
+                    "created_at": msg.created_at,
+                    "time_tag": time_tag
                 })
         except Exception as e:
             logger.warning(f"Failed to fetch channel history for context: {e}")
 
         if not fetched_items:
             return "", []
 
         # Chronological order: reverse from newest-first to oldest-first
         fetched_items.reverse()
 
         ambient_image_parts = []
         recent_messages = []
         image_counter = 1
+        last_msg_time = None
 
         for item in fetched_items:
+            # 檢查相鄰兩則訊息的時間差，若超過 20 分鐘 (1200秒) 則插入沉寂斷層線
+            if last_msg_time is not None:
+                gap = (item["created_at"] - last_msg_time).total_seconds()
+                if gap >= 1200:
+                    recent_messages.append(f"--- ⏳ (此處頻道沉寂了 {self._format_gap_duration(gap)}) ---")
+            last_msg_time = item["created_at"]
+
             img_tag = ""
             if fetch_images and item["image_bytes"] and item["image_mime"]:
                 ambient_image_parts.append(
                     types.Part.from_bytes(data=item["image_bytes"], mime_type=item["image_mime"])
                 )
                 img_tag = f"(附圖 {image_counter}) "
                 image_counter += 1
             elif item["has_img_att"]:
                 img_tag = "(附圖) "
 
+            time_prefix = f"{item['time_tag']} "
             if item["text"]:
-                recent_messages.append(f"[{item['author']}]: {img_tag}{item['text']}")
+                recent_messages.append(f"{time_prefix}[{item['author']}]: {img_tag}{item['text']}")
             elif img_tag:
-                recent_messages.append(f"[{item['author']}]: {img_tag}（發送了圖片）")
+                recent_messages.append(f"{time_prefix}[{item['author']}]: {img_tag}（發送了圖片）")
 
         if not recent_messages:
             return "", []
 
         context_str = "\n".join(recent_messages)
-        logger.info(f"Fetched {len(recent_messages)} recent channel messages ({len(ambient_image_parts)} images) for AI context:\n{context_str}")
+        logger.info(f"🕒 Fetched {len(recent_messages)} messages for AI context with temporal tags:\n{context_str}")
         return f"【聊天室近況記錄】\n{context_str}\n---\n", ambient_image_parts
```

---

#### 2.3. 更新 `generate_spontaneous_response` (注入時間感知決策 Prompt)
```diff
         spontaneous_instruction = (
             f"{self.persona}\n\n"
             f"{memory_context}\n\n"
             f"【目前對話情境】\n"
             f"你正在 Discord 聊天室旁聽大家的對話。\n"
-            f"【自主插話與表情反應決策規則】：\n"
-            f"請根據最近的對話脈絡，判斷你是否適合「像朋友一樣自然地插嘴一句」或「給最後一則訊息按表情反應」。\n"
+            f"【時間感知與插話決策規則】：\n"
+            f"每則訊息皆有時間差標籤（如 [剛才]、[5分鐘前]、[2小時前] 以及 --- ⏳ 沉寂斷層 ---）。\n"
+            f"1. 請務必聚焦於標記為「[剛才]」或最新正在進行的話題，嚴禁去補回答已被沉寂斷層隔開的數小時前舊話題！\n"
+            f"2. 若當前話題熱烈、剛好聊到笑點、提到你的專屬話題或正在問候你，請像朋友一樣簡短吐槽/接梗 (15~35 字以內) 或按表情反應。\n"
+            f"3. 若當前話題嚴肅、技術排錯、不適合打擾、或你無話可說 ➔ 請嚴格只輸出單一標籤：[PASS]\n"
+            f"4. 若只想默默按表情 ➔ 請輸出：[REACTION: 表情名稱或Unicode] (例: [REACTION: 😂] 或 [REACTION: cmonBruh])\n"
         )
```

---

## 🧪 3. 測試與驗證計畫

| 測試項目 | 操作情境 | 預期表現 |
| :--- | :--- | :--- |
| **1. 跨時段歷史隔離** | 使用者 A 在 2 小時前發言提及 Bot 名字（未 @）。2 小時後使用者 B 發言：「大家早安」。 | Log 清楚印出 `[2小時前]` 與 `--- ⏳ 沉寂 2 小時 ---`；Bot **絕不會**回答使用者 A 的舊問題，若要插話只會針對使用者 B 的早安進行問候。 |
| **2. 1 分鐘極速冷卻** | Bot 自主插話後，群友在 1 分鐘內繼續熱聊。 | 60 秒內 Bot 保持靜默防洗版；60 秒過後若出現新笑點，Bot 能再次自發互動。 |
| **3. Log 時間差可讀性** | 在終端機查看 Bot 的輸出日誌。 | 每條抓取的訊息前均清晰附帶 `[剛才]`、`[3分鐘前]`、`[1小時前]` 等時間標籤，排錯極為直覺。 |
