# 🏷️ 當句對話保留 `@Bot自己` 識別計畫書（做法 A：對話顯示保留、向量查詢分離）

> **專案**：`dc_auto_bot`  
> **定位**：設計與實施規劃文件（尚未修改程式碼）  
> **關聯前置計畫**：[DISCORD_MENTION_RESOLUTION_PLAN.md](file:///d:/OneDrive/Project/dc_auto_bot/plans/DISCORD_MENTION_RESOLUTION_PLAN.md)  
> **核心目標**：解決目前「歷史紀錄看得到 `@Bot`，但當下這句話卻把 `@Bot` 整段刪掉導致 AI 以為沒提到自己」的不一致問題。採用**做法 A**——在當句對話本文與長期記憶中**保留 `@角色名稱`**，同時將去除 `@Bot` 的乾淨文字單獨留給向量搜尋 (`query_text`) 與喚醒詞比對。

---

## 📌 1. 現況落差分析

在完成前次 Mention 解析改造後，各路徑對 `<@bot_id>` 的處理如下：

| 路徑 | 現行 `strip_user_id` 設定 | 實際表現 |
| :--- | :--- | :--- |
| 歷史近況 (`ambient_context`) | `None`（[utils/context_builder.py#L106](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L106)） | 保留為 `@<Bot伺服器暱稱>` |
| Reply 目標 (`ref_context`) | `None`（[utils/context_builder.py#L225](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L225)） | 保留為 `@<Bot伺服器暱稱>` |
| 記憶緩衝 (`memory_text`) | `None`（[cogs/ai_roleplay.py#L291](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L291)） | 保留為 `@<Bot伺服器暱稱>` |
| **當句對話 (`clean_content`)** | **`bot_id`**（[cogs/ai_roleplay.py#L311-L312](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L311-L312)） | **全句刪除 `@Bot自己`** |

這造成三個實際使用瑕疵：
1. **當句讀不到自己**：群友打「`你看得到我 tag 誰嗎？@Bot`」或「`我覺得 @Bot 比較好`」時，AI 實際只收到「`你看得到我 tag 誰嗎？`」與「`我覺得 比較好`」。
2. **純 `@Bot` 訊息誤落入圖片預設文字**：只打一個 `@Bot` 時，`clean_content` 變成 `""`，導致 [cogs/ai_roleplay.py#L226](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L226) 塞出不自然的預設字串 `（這張圖片怎麼樣？）`。
3. **純 `@Bot` 時向量查詢 fallback 漏洞**：[#L241](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L241) 使用 `query_text=clean_content or message.content`，當 `clean_content` 被清空時反而把原始數字標籤 `<@bot_id>` 拿去算 Gemini Embedding；此外，若伺服器暱稱尚未同步成目前人設名，`@Bot舊暱稱` 也可能造成角色認知落差。

---

## 🛠️ 2. 做法 A 設計方案（Prompt 本文保留 + 查詢字串分離）

### 2.1. 三份字串職責明確分離

| 用途變數 | 處理方式 | 目的 |
| :--- | :--- | :--- |
| **`prompt_content` / `memory_text`** | `strip_user_id=None`，且將 `bot_id` 覆蓋為 `@{persona_display_name}` | 讓 AI 在記憶與當句對話中都看得到完整句子與 `@自己角色名`，就算純 `@Bot` 也會收到 `[小明]: @梨諾` 而不會誤判成發圖片。 |
| **`stripped_content`** | `strip_user_id=bot_id` | 專供 `has_wake_word`（只偵測真實輸入的關鍵字）與向量召回 `query_text` 使用，避免句首呼叫詞干擾語意相似度；若僅打 `@Bot` 無實質內容則為 `""`，可自然跳過無意義的 Embedding API 呼叫。 |

### 2.2. 為什麼這在「AI 對 AI (`PEER_BOT_IDS`)」下也 100% 安全？
1. **第一道防護不變**：[cogs/ai_roleplay.py#L255-L262](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L255-L262) 針對 `is_self_bot` 無條件 `return`，Bot 就算在 Prompt 或自己回覆中看到 `@{persona_display_name}` 也絕不會自我觸發。
2. **解析為純文字、非真實 Ping**：`resolve_discord_mentions` 產出的是純文字 `@角色名`，不會在 Discord 送出新的 `<@ID>` 通知。
3. **Peer Bot 回合衰減不變**：`streak` 與 `100% -> 50% -> 30% -> 10% -> 0%` 完全依事件發話者計數，不受當句文字保留 `@自己` 影響。

---

## 💻 3. 建議修改範圍與實作細節

### 3.1. 擴充 [utils/context_builder.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py) 的 `resolve_discord_mentions`
新增可選參數 `user_name_overrides: Optional[Dict[int, str]] = None`：
* 在 `_replace_user` 解析 `uid` 時，優先查詢 `user_name_overrides.get(uid)`，其次才是 `message.mentions` 與 `guild.get_member`。
* 當處理 Bot 自身帳號（`bot_user.id`）且未要刪除時，直接將其覆蓋為當前角色的 `persona_display_name`：
  * `fetch_recent_channel_context`（[#L106](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L106)）：傳入 `user_name_overrides={bot_user.id: persona_display_name}`。
  * `extract_reply_context`（[#L204-L225](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L204-L225)）：也可傳入 `persona_display_name` 作相同覆蓋（可選預設 `"AI"` 或直接傳 `self.persona_manager.persona_display_name`），確保「歷史近況、Reply 目標、記憶、當句對話」四處對自己的稱呼 100% 統一。

### 3.2. 調整 [cogs/ai_roleplay.py](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py)
1. **`_respond_to_message`（[#L213-L250](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L213-L250)）**：
   * 新增可選參數 `query_text: Optional[str] = None`（或區分 `user_content` 與 `query_text`）。
   * 第 226 行：當有圖片且純 `@Bot` 沒打字或文字僅為稱呼時照常組成 `full_user_text = f"{ref_context}[{message.author.display_name}]: {prompt_content or '（這張圖片怎麼樣？）'}"`。
   * 第 241 行：傳給 `generate_response` 的 `query_text` 明確使用去除 `@Bot` 後的 `stripped_content`（允許為空字串 `""`，讓 [core/memory_service.py#L613-L614](file:///d:/OneDrive/Project/dc_auto_bot/core/memory_service.py#L613-L614) 在僅有 `@Bot` 無具體問題時直接略過 Embedding 請求）。
2. **`on_message`（[#L287-L368](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L287-L368)）**：
   * 建立 `bot_id = self.bot.user.id if self.bot.user else None` 與 `bot_overrides = {bot_id: self.persona_manager.persona_display_name} if bot_id else None`。
   * `memory_text = resolve_discord_mentions(message, strip_user_id=None, user_name_overrides=bot_overrides)` ➔ 同時供 `memory_service.add_message` 與當句 `prompt_content` 使用。
   * `stripped_content = resolve_discord_mentions(message, strip_user_id=bot_id)` ➔ 供 `has_wake_word` 與 `query_text` 使用。

---

## 🧪 4. 驗證測試案例

| 測試情境 | 使用者輸入範例 | 預期送給 Gemini 的對話內容 | 預期 `query_text` | 預期結果 |
| :--- | :--- | :--- | :--- | :--- |
| **1. 問有沒有看到 tag 自己** | `你看得到我 tag 誰嗎？@Bot` | `[小明]: 你看得到我 tag 誰嗎？@梨諾` | `你看得到我 tag 誰嗎？` | AI 明確看得到 `@梨諾` 與其他 `@群友`。 |
| **2. 句中同時提及自己與別人** | `我覺得 @Bot 跟 @小明 都很強` | `[小華]: 我覺得 @梨諾 跟 @小明 都很強` | `我覺得 跟 @小明 都很強` | 語句完全通順，不缺主詞。 |
| **3. 只打 `@Bot`（無附圖）** | `@Bot` | `[小明]: @梨諾` | `""`（略過向量查詢） | 不再誤塞「這張圖片怎麼樣？」且省 1 次 Embedding API。 |
| **4. 伺服器暱稱與人設名不同步** | Bot 暱稱仍為舊名，但人設已切為「莫寧」，群友 `@Bot` | 強制覆蓋解析為 `@莫寧` | `""` | AI 不會因為舊暱稱認不出自己在被呼叫。 |
| **5. AI 對 AI 互嘴循環檢查** | Peer Bot B 發送含 `@BotA` 訊息 | Bot A 收到 `@角色A` 並依機率回應 | 依規則去除 `@BotA` | 不會觸發 Bot A 自己回自己，`streak` 計數準確收斂。 |
