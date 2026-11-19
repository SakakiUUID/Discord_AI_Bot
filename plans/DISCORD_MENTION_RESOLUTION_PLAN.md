# 🏷️ Discord Mention (`@使用者`) 解析與保留修復計畫書

> **專案**：`dc_auto_bot`  
> **定位**：Bug 根因分析與修復規劃文件（尚未修改程式碼）  
> **目標**：停止將所有 `<@user_id>` 標籤無差別刪除為空白；改為**精準解析並保留使用者之間（含同伴 Bot）的 `@顯示名稱`**，讓運行 Log、AI 即時上下文、被回覆目標與長期記憶都能正確辨識「誰 tag 了誰」。

---

## 📌 1. 現況問題根因盤點

目前系統在以下 **4 個關鍵路徑** 對 Discord Mention 處理失當：

| 位置 | 現行程式碼行為 | 實際造成的問題 |
| :--- | :--- | :--- |
| **[utils/context_builder.py#L45](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L45)**<br>(`fetch_recent_channel_context`) | `re.sub(r'<@!?\d+>', '', msg.content)` | 近 30 則脈絡與終端 Log 內**所有 `@某人` 全部消失**；若訊息只有 `@某人` 更會變成空白並誤觸 `empty_count` Intent 警告。 |
| **[utils/context_builder.py#L164](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L164)**<br>(`extract_reply_context`) | `re.sub(r'<@!?\d+>', '', ref_msg.content)` | 被 Reply 的目標訊息內一樣看不到 `@誰`。 |
| **[cogs/ai_roleplay.py#L310](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L310)**<br>(`on_message`) | `re.sub(r'<@!?\d+>', '', message.content)` | 使用者對 Bot 問「`@Bot 你覺得 @小明 說得對嗎？`」，AI 實際只收到「`你覺得  說得對嗎？`」，完全不知道指涉對象。 |
| **[cogs/ai_roleplay.py#L298](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L298)**<br>(`memory_service.add_message`) | 直接傳入未轉換的 `message.content` | 長期記憶資料庫收到的是原始數字字串 `<@123456789>`，記憶萃取引擎不知道是哪一位群友。 |

---

## 🛠️ 2. 目標解析策略設計

### 2.1. 為何不直接使用 `re.sub(r'<@!?\d+>', '', ...)` 或單靠 `msg.clean_content`？
* **`re.sub` 全刪**：無法區分「`@Bot自己`」與「`@其他群友`」。
* **直接用 `discord.Message.clean_content` 的限制**：`clean_content` 會把 `@Bot自己` 也一併保留成 `@Bot顯示名稱`，在「當前發言去除呼叫前綴」與「向量查詢字串 (`query_text`)」上缺乏彈性。
* **最佳做法**：新增一個純本地、零額外 API 請求的共用解析函式（例如 `resolve_discord_mentions(message, strip_bot_user=None)`），直接利用 `discord.Message` 已快取的 `mentions`、`role_mentions`、`channel_mentions` 與 `guild.get_member` 進行精準替換。

### 2.2. 各情境下的精準輸出規則

| 訊息情境 | `@Bot 自己` (`<@bot_id>`) | `@其他成員 / 同伴 Bot` (`<@user_id>`) | `#頻道` / `@身分組` |
| :--- | :--- | :--- | :--- |
| **1. 當下對 Bot 的發言本文** (`clean_content` / `query_text`) | **移除**（避免對向量檢索造成雜訊，且 `is_mentioned` 已表達對話對象） | **保留並轉為 `@顯示名稱`**（例如 `@小明`） | **保留並轉為 `#頻道名` / `@身分組名`** |
| **2. 聊天室近況與 Log** (`ambient_context`) | **轉為 `@<bot_display_name>`**（保留誰曾 `@Bot` 或點名 `Bot` 的完整語意） | **保留並轉為 `@顯示名稱`** | **保留並轉為 `#頻道名` / `@身分組名`** |
| **3. 被回覆的目標訊息** (`ref_context`) | **轉為 `@<bot_display_name>`** | **保留並轉為 `@顯示名稱`** | **保留並轉為 `#頻道名` / `@身分組名`** |
| **4. 長期記憶學習緩衝** (`memory_service.add_message`) | **轉為 `@<bot_display_name>`** | **保留並轉為 `@顯示名稱`** | **保留並轉為 `#頻道名` / `@身分組名`** |

> **補充說明**：若 `mentions` 列表偶爾在跨伺服器轉貼或極舊快取中找不到某個 `user_id`，則試著用 `message.guild.get_member(uid)` 查詢；若仍找不到，保留原標籤或降級顯示 `@使用者` 而不靜默刪成空白。

---

## 💻 3. 解析函式設計與實作細節

### 3.1. 共用工具函式設計（建議置於 [utils/context_builder.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py)）

```python
def resolve_discord_mentions(
    message: discord.Message,
    strip_user_id: Optional[int] = None,
) -> str:
    """
    Resolve Discord <@user_id>, <@&role_id>, and <#channel_id> tags into human-readable names.
    Optionally strips mentions targeting `strip_user_id` (e.g. the bot itself in direct prompts).
    """
    text = message.content or ""
    if not text:
        return ""

    # 1. Strip target bot mention if requested
    if strip_user_id is not None:
        text = re.sub(rf"<@!?{strip_user_id}>", "", text)

    # 2. Build lookup map from message.mentions (+ guild cache fallback)
    user_map: Dict[int, str] = {}
    for u in getattr(message, "mentions", []) or []:
        user_map[u.id] = u.display_name

    def _replace_user(match: re.Match) -> str:
        uid = int(match.group(1))
        if strip_user_id is not None and uid == strip_user_id:
            return ""
        name = user_map.get(uid)
        if not name and message.guild:
            member = message.guild.get_member(uid)
            if member:
                name = member.display_name
        return f"@{name}" if name else match.group(0)

    text = re.sub(r"<@!?(\d+)>", _replace_user, text)

    # 3. Resolve Role mentions (<@&id>) and Channel mentions (<#id>)
    role_map = {r.id: r.name for r in (getattr(message, "role_mentions", []) or [])}
    text = re.sub(
        r"<@&(\d+)>",
        lambda m: f"@{role_map[int(m.group(1))]}" if int(m.group(1)) in role_map else m.group(0),
        text,
    )

    ch_map = {c.id: c.name for c in (getattr(message, "channel_mentions", []) or [])}
    text = re.sub(
        r"<#(\d+)>",
        lambda m: f"#{ch_map[int(m.group(1))]}" if int(m.group(1)) in ch_map else m.group(0),
        text,
    )

    # Collapse redundant spaces left behind when stripping @Bot at start/middle
    return re.sub(r"[ \t]{2,}", " ", text).strip()
```

### 3.2. 各呼叫端對應修改點

1. **[utils/context_builder.py#L45](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L45)**（`fetch_recent_channel_context`）
   * 將原本 `clean = re.sub(r'<@!?\d+>', '', msg.content).strip()...`
   * 改為 `clean = resolve_discord_mentions(msg, strip_user_id=None)`。
   * **效果**：Log 與 `【聊天室近況記錄】` 會顯示 `[小華]: @小明 明天要打副本嗎`，純 `@小明` 的訊息也不再被當成空白跳過或誤報 Intent 警告。

2. **[utils/context_builder.py#L164](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L164)**（`extract_reply_context`）
   * 將原本 `ref_clean = re.sub(r'<@!?\d+>', '', ref_msg.content).strip()...`
   * 改為 `ref_clean = resolve_discord_mentions(ref_msg, strip_user_id=None)`。

3. **[cogs/ai_roleplay.py#L292-L315](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L292-L315)**（`on_message`）
   * **給記憶緩衝區**：先產生 `memory_text = resolve_discord_mentions(message, strip_user_id=None)`，傳給 `memory_service.add_message(..., content=memory_text)`，確保長期記憶與 `new_facts` 能知道成員互 tag 的關係。
   * **給觸發判斷與當句對話**：產生 `bot_id = self.bot.user.id if self.bot.user else None` 後，`clean_content = resolve_discord_mentions(message, strip_user_id=bot_id)`。
   * **效果**：
     * 群友發 `@Bot 你看 @小明 剛剛說什麼` ➔ `is_mentioned=True`，`clean_content` 為 `你看 @小明 剛剛說什麼`，`query_text` 不受 `@Bot` 干擾且完整保留 `@小明`。
     * 原本發純 `@Bot` 沒打字 ➔ `clean_content` 仍為 `""`，照常進 fallback 提示（或依附圖處理），不影響既有邏輯。

---

## ⚠️ 4. 潛在影響與注意事項

1. **`has_wake_word` 連鎖互動檢查**：
   * 由於 `clean_content` 改為保留其他人的 `@顯示名稱`，若群友沒有 `@Bot`，只是發了一句 `@梨諾別理他`（真正使用 Discord mention 標記了叫做「梨諾」的真人成員，而非 Bot），因顯示名稱包含關鍵字仍可能命中 `has_wake_word`——這是文字關鍵字比對的固有特性，與原先行為一致。
2. **同伴 Bot (`PEER_BOT_IDS`) 互動更自然**：
   * 若 Bot A 在回覆中使用了其他群友或 Bot B 的顯示名稱，或人類寫 `@BotA 你跟 @BotB 聊聊`，Bot A 的輸入與雙邊近況 Log 都會明確顯示 `@BotB名稱`，大幅提升對話對象辨識度。
3. **無須額外 Discord Intent 或 API 請求**：
   * `message.mentions`、`role_mentions`、`channel_mentions` 由 Discord Gateway 事件與 `channel.history()` 直接隨訊息物件提供，不增加延遲或 Rate Limit 風險。

---

## 🧪 5. 驗證與測試清單

| 測試項目 | 操作範例 | 預期結果 |
| :--- | :--- | :--- |
| **1. 群友互 tag 與 Log 顯示** | 小華在頻道發 `@小明 你吃飯了嗎`，接著 `@Bot 剛才小華問了誰？` | `🕒 Fetched ...` Log 顯示 `[小華]: @小明 你吃飯了嗎`；AI 準確回答「小華剛才問了小明」。 |
| **2. 當句含 `@Bot` 與 `@其他人`** | `@Bot 你覺得 @小明 說的對嗎` | AI 收到的 `full_user_text` 保留 `@小明`，且成功去掉開頭 `@Bot`。 |
| **3. 純 tag 不誤判空訊息** | 頻道近期有人發一則純 `@小明`（無其他文字），無圖片 | ContextBuilder 不再將其視為空白跳過，也不再因純 tag 觸發 `empty_count` Intent 警告。 |
| **4. Reply 目標含 tag** | 回覆一則內容為「`@大華 記得交作業`」的舊訊息並 `@Bot 解釋一下` | `ref_context` 正確包含 `@大華 記得交作業`。 |
| **5. 長期記憶保留人名** | 累積多則包含 `@小明` 的對話後觸發萃取 | `pending_messages` 與萃取出來的記憶不再出現 `<@123456789>` 數字 ID，而是可讀的 `@小明`。 |
