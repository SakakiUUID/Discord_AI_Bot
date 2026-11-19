# 🐛 主動插話與表情反應解析器修復指南 (Spontaneous Reaction Parser Fix)

> **專案**：`dc_auto_bot`  
> **目標模組**：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)  
> **問題描述**：在觸發 `Spontaneous Chime-In` 主動插話時，Discord 頻道只收到引號/中括號 `[`，未正確執行 Emoji 表情反應或文字台詞發送。  
> **文件用途**：供後續對話或開發者直接依循此修復指南修改 Python 原始碼。

---

## 📌 1. 根本原因深入剖析

1. **Emoji 正則表達式範圍過窄**：
   * **舊正則**：`r"\[REACTION:\s*([a-zA-Z0-9_]+)\]"`。
   * **問題**：此正則只匹配英文字母與數字，當 Gemini 輸出 Unicode Emoji（如 `[REACTION: 🤔]`、`[REACTION: ✨]`、`[REACTION: 😂]`）時**完全匹配失敗 (`None`)**。
2. **標籤穿透至文字發送邏輯**：
   * 由於 `reaction_match` 匹配失敗，程式碼誤將 `[REACTION: 🤔]` 當成普通文字台詞傳給 `channel.send()`，導致 Discord 頻道收到中括號標籤。
3. **缺少前綴台詞標籤清理**：
   * 當 AI 輸出包含角色前綴（如 `[莫寧]: 漂泊者...` 或被引號包裹 `["..."]`）時，未進行前綴與引號清洗。

---

## 🛠️ 2. 具體修復代碼規格 (`cogs/ai_roleplay.py`)

請在 [`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py) 的 `generate_spontaneous_response` 與 `handle_spontaneous_chime_in` 進行以下修正：

### 2.1. 修復 `generate_spontaneous_response` (清理標籤與前綴)
```diff
         try:
             response = await asyncio.to_thread(
                 self.ai_client.models.generate_content,
                 model=config.GEMINI_MODEL,
                 contents=prompt,
                 config=types.GenerateContentConfig(
                     system_instruction=spontaneous_instruction,
                     temperature=0.7,
                     max_output_tokens=200
                 )
             )
             raw_text = (response.text or "").strip()
-            if not raw_text or "[PASS]" in raw_text:
+            if not raw_text or re.search(r"\[PASS\]", raw_text, re.IGNORECASE):
                 return None, None
 
+            # 若為表情反應標籤，直接回傳讓 handler 處理
+            if re.search(r"\[REACTION:\s*[^\]]+\]", raw_text, re.IGNORECASE):
+                return raw_text, None
+
             # Parse emotion tag if present
             emotion = None
             emotion_match = re.search(r"\[EMOTION:\s*([a-zA-Z0-9_]+)\]", raw_text)
             if emotion_match:
                 emotion = emotion_match.group(1).lower()
                 clean_text = re.sub(r"\[EMOTION:\s*[a-zA-Z0-9_]+\]", "", raw_text).strip()
             else:
                 clean_text = raw_text
 
+            # 清理可能的角色名稱前綴 (例: "[莫寧]: " 或 "莫寧: ") 與包裹引號
+            clean_text = re.sub(r"^\[?[^:\]\n]+\]?:\s*", "", clean_text).strip()
+            clean_text = clean_text.strip("\"'「」")
+
+            # 安全防護：若清洗後只剩括號或無效文字則放棄
+            if not clean_text or clean_text.startswith("[") and clean_text.endswith("]"):
+                return None, None
+
             return clean_text, emotion
         except Exception as e:
             logger.error(f"Error in generate_spontaneous_response: {e}")
             return None, None
```

---

### 2.2. 修復 `handle_spontaneous_chime_in` (擴充 Emoji 解析並支援 Unicode)
```diff
         if not reply_text:
             return
 
-        # 1. 處理表情反應 [REACTION: xxx]
-        reaction_match = re.search(r"\[REACTION:\s*([a-zA-Z0-9_]+)\]", reply_text)
+        # 1. 處理表情反應 [REACTION: xxx] (支援 Unicode Emoji 與 Discord 自訂表情)
+        reaction_match = re.search(r"\[REACTION:\s*([^\]]+)\]", reply_text, re.IGNORECASE)
         if reaction_match:
-            emoji_name = reaction_match.group(1)
-            emoji_obj = discord.utils.get(self.bot.emojis, name=emoji_name) if self.bot else None
+            emoji_raw = reaction_match.group(1).strip().strip(":")
+            emoji_obj = discord.utils.get(self.bot.emojis, name=emoji_raw) if self.bot else None
+            target_reaction = emoji_obj if emoji_obj else emoji_raw
             try:
-                if emoji_obj:
-                    await trigger_message.add_reaction(emoji_obj)
-                else:
-                    await trigger_message.add_reaction(emoji_name)
-                logger.info(f"✨ [Spontaneous Reaction] Reacted '{emoji_name}' to message by {trigger_message.author.display_name}")
+                await trigger_message.add_reaction(target_reaction)
+                logger.info(f"✨ [Spontaneous Reaction] Reacted '{target_reaction}' to message by {trigger_message.author.display_name}")
             except Exception as e:
-                logger.warning(f"Could not add spontaneous reaction '{emoji_name}': {e}")
+                logger.warning(f"Could not add spontaneous reaction '{target_reaction}': {e}")
             return
 
         # 2. 處理文字插話發送
         clean_reply = reply_text.strip()
         if clean_reply:
             sticker_file = None
             if emotion:
                 emotion_path = os.path.join("assets", "emotions", f"{emotion}.png")
                 if os.path.exists(emotion_path):
                     sticker_file = discord.File(emotion_path, filename=f"{emotion}.png")
 
             logger.info(f"💬 [Spontaneous Chime-In] Bot chimed in channel {channel.id}: '{clean_reply[:50]}...'")
             if sticker_file:
                 await channel.send(clean_reply, file=sticker_file)
             else:
                 await channel.send(clean_reply)
```

---

## 🧪 3. 測試與驗證清單

1. **Unicode Emoji 測試**：
   * 模擬/觸發 AI 回傳 `[REACTION: 🤔]` 或 `[REACTION: 😂]`。
   * **預期結果**：訊息下方正確亮起 `🤔` 或 `😂` 表情符號，頻道中**絕不發送**多餘文字或 `[` 引號。
2. **自訂表情符號測試**：
   * 模擬/觸發 AI 回傳 `[REACTION: cmonBruh]` 或 `[REACTION: :cmonBruh:]`。
   * **預期結果**：正確查找伺服器 `<:cmonBruh:...>` 自訂表情並為訊息按反應。
3. **角色前綴清洗測試**：
   * 模擬 AI 輸出 `[莫寧]: 漂泊者，請注意休息。`。
   * **預期結果**：Discord 頻道收到乾淨的 `漂泊者，請注意休息。`，前綴 `[莫寧]:` 被自動剔除。
