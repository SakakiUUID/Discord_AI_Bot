# 🎭 AI 角色自主對話偵測與主動插話/表情反應系統規格書 (Proactive Chime-In System)

> **專案**：`dc_auto_bot`  
> **模組**：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)  
> **目標**：讓 AI 角色不僅在被 `@Bot` 或 `回覆` 時才被動說話，更能像真實群友一樣「在適當時機主動吐槽、接梗或默默按 Emoji 表情反應」，並透過「四層防洗版漏斗」確保零擾民、低 Token 開銷。  
> **文件用途**：供後續對話或開發者直接依循此規格進行功能開發與測試。

---

## 📌 1. 功能需求與核心目標

1. **自主性 (Proactivity)**：機器人監聽一般聊天對話，當偵測到符合話題特徵或特定情境時，自主發起互動。
2. **多模態互動層級**：
   * **層級 1：Emoji 默默反應 (Reaction)**：不發文字訊息，僅在用戶很有梗的訊息底下按一個表情（如 `:cmonBruh:`、`✨`、`😂`），**擬真感極高且 0 洗版**。
   * **層級 2：文字簡短吐槽/接梗 (Text Chime-In)**：15~35 字以內的短句接話，展現角色性格。
3. **極致防洗版與成本控制 (Anti-Spam & Token Safety)**：
   * **長冷卻保護**：單一頻道預設 **15 分鐘冷卻**（可配置）。
   * **本地零成本初篩**：90% 以上普通對話在本地 0ms 直接放行，不浪費 Gemini API 請求。
   * **AI 二元決策拒絕權 (`[PASS]`)**：若話題不合適，AI 可自主放棄插話。

---

## 🏛️ 2. 四層漏斗過濾架構 (4-Tier Gating Pipeline)

```mermaid
flowchart TD
    A[用戶發送普通訊息 (非 @Bot / 非回覆)] --> L0[閘門 0: 基礎安全與白名單<br>過濾 Bot / 指令 / 黑名單頻道]
    
    L0 -->|通過| L1[閘門 1: 冷卻時間檢查 Cooldown<br>頻道需距離上次發言 > 15 分鐘]
    L0 -->|不通過| Drop[放行 / 0 Token 開銷]
    
    L1 -->|冷卻時間已過| L2[閘門 2: 本地特徵與機率骰 0 Token<br>命中角色名 / 內部梗 / 情緒詞 / 8% 隨機骰]
    L1 -->|冷卻中| Drop
    
    L2 -->|命中特徵或隨機骰| L3[閘門 3: Gemini 3.7 Flash 決策生成]
    L2 -->|平淡對話/未中| Drop
    
    L3 -->|輸出 [PASS]| Drop
    L3 -->|輸出 [REACTION: xxx]| React[✨ 執行 Emoji 按反應]
    L3 -->|輸出 角色台詞| Chat[💬 發送簡短文字吐槽/接梗]
    
    React --> UpdateCD[更新頻道冷卻計時器]
    Chat --> UpdateCD
```

---

## 🔍 3. 四層閘門詳細規範

### 🔹 閘門 0：基礎安全與範圍過濾 (0ms)
* 排除發送者為 Bot（包含本機器人自己，**100% 杜絕 Bot 與 Bot 無限循環對話**）。
* 排除以 `!` 或 `/` 開頭之指令訊息。
* 排除設定為靜音黑名單的頻道。

### 🔹 閘門 1：冷卻時間控制 (Channel Cooldown)
* 預設每頻道冷卻時間：**15 分鐘 (`IDLE_COOLDOWN = 900` 秒)**。
* 機器人主動發言（文字或按反應）後，該頻道進入冷卻狀態，時間內不再主動插嘴。

### 🔹 閘門 2：本地特徵啟發式比對 (Heuristic Matcher - 0 Token)
訊息滿足以下**任一條件**，方可進入下一階段：
1. **隱性提及角色**：包含當前角色名稱或代稱（如「莫寧」、「愛莉」、「達妮婭」、「愛彌斯」、「梨諾」、「教授」、「機器人」）。
2. **群組內部梗命中**：包含 `guild_cultures.json` 內已記錄之 `inside_jokes` 關鍵字。
3. **強烈情緒與吐槽熱詞**：包含 `["笑死", "大保底", "破防", "救命", "太強了吧", "？？？", "真的假"]`。
4. **隨機靈性骰子**：即使無特定詞彙，普通訊息有 **8% 機率 (`PROBABILITY = 0.08`)** 通過。

### 🔹 閘門 3：AI 決策與生成規範 (Gemini 3.7 Flash)
將最近 15 則上下文傳給 Gemini，並在 System Prompt 注入決策指示：
```text
【自主插話決策規則】：
你正在 Discord 聊天室旁聽。請根據最近的對話脈絡，判斷你是否適合「像朋友一樣自然地插嘴一句」或「給最後一則訊息按表情反應」。
1. 若當前話題嚴肅、不適合打擾、或你無話可說 ➔ 請嚴格輸出：[PASS]
2. 若只想默默按表情表達情緒 ➔ 請輸出：[REACTION: 表情名稱或Unicode] (例如 [REACTION: 😂] 或 [REACTION: cmonBruh])
3. 若適合開口接梗/吐槽 ➔ 請以當前角色性格輸出 1 句話 (15~35 字以內)，可附帶 [EMOTION: xxx]。
```

---

## 🛠️ 4. 具體代碼實現規格 (`cogs/ai_roleplay.py`)

### 4.1. 新增冷卻與開關屬性
```python
class AIRoleplayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 主動插話冷卻追蹤 (channel_id -> last_spontaneous_time)
        self.spontaneous_cooldowns: Dict[int, float] = {}
        self.spontaneous_enabled: bool = True
        self.spontaneous_cooldown_seconds: int = 900  # 15 分鐘
        self.spontaneous_probability: float = 0.08    # 8% 機率
```

### 4.2. 本地特徵比對函式
```python
def should_attempt_spontaneous(self, message: discord.Message) -> bool:
    """本地 0 Token 判定是否值得嘗試主動插話"""
    now = time.time()
    last_time = self.spontaneous_cooldowns.get(message.channel.id, 0)
    if now - last_time < self.spontaneous_cooldown_seconds:
        return False

    content = message.content.lower()

    # 1. 角色名稱比對
    role_keywords = ["莫寧", "愛莉", "達妮婭", "愛彌斯", "梨諾", "教授", "機器人", "bot"]
    if any(k in content for k in role_keywords):
        return True

    # 2. 群組內部梗比對
    guild_id = str(message.guild.id) if message.guild else "DM"
    culture = memory_manager.guild_cultures.get(guild_id, {})
    for joke in culture.get("inside_jokes", []):
        if joke.lower() in content:
            return True

    # 3. 強烈情緒熱詞
    sentiment_words = ["笑死", "大保底", "破防", "救命", "太強了吧", "？？？", "真的假"]
    if any(w in content for w in sentiment_words):
        return True

    # 4. 隨機骰子 (8%)
    return random.random() < self.spontaneous_probability
```

### 4.3. 在 `on_message` 中整合主動執行邏輯
```python
# 若非 @Bot 且非回覆本 Bot，且符合主動插話條件
if not is_mentioned and not is_reply and self.spontaneous_enabled and self.should_attempt_spontaneous(message):
    asyncio.create_task(self.handle_spontaneous_chime_in(message))
```

### 4.4. 執行與處理 AI 決策結果
```python
async def handle_spontaneous_chime_in(self, trigger_message: discord.Message):
    """處理主動插嘴生成與發送"""
    channel = trigger_message.channel
    self.spontaneous_cooldowns[channel.id] = time.time() # 進入冷卻

    # 取得最近 15 則上下文
    ambient_context, _ = await self.fetch_recent_channel_context(channel, fetch_images=False)
    
    prompt = f"{ambient_context}\n[系統指示：請評估是否進行自主插話，若不適合請回傳 [PASS]]"
    
    # 呼叫 Gemini 進行判定生成
    reply_text, emotion = await self.generate_spontaneous_response(channel.id, prompt)
    
    if not reply_text or "[PASS]" in reply_text:
        return # 放行，不動作

    # 處理表情反應 [REACTION: xxx]
    if reaction_match := re.search(r"\[REACTION:\s*([a-zA-Z0-9_]+)\]", reply_text):
        emoji_name = reaction_match.group(1)
        emoji_obj = discord.utils.get(self.bot.emojis, name=emoji_name) or emoji_name
        try:
            await trigger_message.add_reaction(emoji_obj)
        except Exception:
            pass
        return

    # 發送簡短文字吐槽/接梗
    await channel.send(reply_text)
```

---

## 🧪 5. 測試與驗證計畫

| 測試情境 | 測試方法 | 預期表現 |
| :--- | :--- | :--- |
| **1. 提及角色名主動插話** | 群友對話聊到「莫寧教授今天在嗎？」（不 @Bot）。 | 機器人自主識別並以莫寧身份接話回應。 |
| **2. 命中群組內部梗** | 群友提及已記錄的 `inside_jokes`（如「大保底」）。 | 機器人自然調侃接梗。 |
| **3. 默默點擊 Emoji 反應** | 群友發出搞笑言論，AI 判定適合發表情。 | 機器人不傳送文字訊息，但在該訊息下方點擊 Emoji 反應。 |
| **4. 15 分鐘冷卻防洗版** | 機器人插話後，群友在 5 分鐘內持續熱聊。 | 機器人安靜旁聽，絕不連續發言，直到 15 分鐘冷卻完畢。 |
| **5. 不適宜話題自主 [PASS]** | 群友進行嚴肅程式碼除錯討論。 | AI 判定輸出 `[PASS]`，背景自動放行，不打擾群友。 |
