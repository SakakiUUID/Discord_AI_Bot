# 🤖 雙 AI 同伴互動與機率遞減對話系統計畫書 (Peer-Bot Decay Interaction)

> **專案**：`dc_auto_bot`  
> **定位**：功能分析與實施規劃文件（尚未修改程式碼）  
> **目標**：讓兩隻部署於同伺服器的 AI Bot 能在**全頻道不設限**的情況下自然互動（互相附和、吐槽或鬥嘴）、**彼此寫入與召回長期記憶**，並嚴格採用 **`100% ➔ 50% ➔ 30% ➔ 10% ➔ 0%` 漸進遞減機率閘門**防止無限洗版迴圈。

---

## 📌 1. 需求與核心規格確認

1. **雙向互動定位**：不限定鬥嘴，涵蓋互相附和、吐槽、接話與延續話題。
2. **觸發與漸進遞減序列（以真人最後發言為起點）**：
   * **第 0 步（人類起頭）**：真人發言觸發 Bot A（透過 `@BotA`、Reply、喚醒詞或 `/chat`），Bot A 回覆中提到 Bot B 的名字/喚醒詞或 `@BotB`。
   * **第 1 次同伴接話（Bot B 回覆 Bot A）**：機率 **`100%`**
   * **第 2 次同伴接話（Bot A 再回 Bot B）**：機率 **`50%`**
   * **第 3 次同伴接話（Bot B 再回 Bot A）**：機率 **`30%`**
   * **第 4 次同伴接話（Bot A 再回 Bot B）**：機率 **`10%`**
   * **第 5 次及之後**：機率 **`0%`**（強制停火，直到有**真人**在該頻道再次發言才重置序列）。
3. **頻道範圍**：**不限制頻道**，伺服器內任一文字頻道皆可觸發。
4. **記憶整合**：**記住對方記憶**——同伴 Bot 的發言照常進入 `MemoryService`（`pending_messages` ➔ `user_profiles` + `memory_facts`），讓兩隻 AI 隨時間累積對彼此的印象與共同回憶。

---

## 🏗️ 2. 現況程式碼阻礙與關鍵機制設計

### 2.1. 目前擋住雙 Bot 互動的四個位置
1. **[cogs/ai_roleplay.py#L181-L182](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py#L181-L182)**：`if message.author.bot: return` 直接攔截所有非真人訊息（包含同伴 Bot）。
2. **[utils/context_builder.py#L34-L36](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L34-L36)**：`if msg.author.bot and (bot_user and msg.author != bot_user): continue` 會把同伴 Bot 的發言從近 30 則脈絡中過濾掉。
3. **[adapters/discord/delivery.py#L36-L38](file:///d:/OneDrive/Project/dc_auto_bot/adapters/discord/delivery.py#L36-L38)**：發送回覆時固定使用 `target_message.reply(chunk)`，一旦開放互回，雙方每次回覆都會自動形成 `is_reply = True`，必須靠遞減機率與深度上限收斂。
4. **[core/dialog_service.py#L87-L91](file:///d:/OneDrive/Project/dc_auto_bot/core/dialog_service.py#L87-L91)**：非 VIP 用戶預設每日上限 10 次；同伴 Bot 的 User ID 必須豁免計算，否則講滿 10 句即無法繼續。

---

### 2.2. 跨進程同步的「連續 Bot 回合狀態機」設計

由於兩隻 Bot 是兩個獨立運行的進程（不共享記憶體），每隻 Bot 必須能在本地精準推算同一條機率序列 `[1.0, 0.5, 0.3, 0.1, 0.0]`。最穩健的做法是以「**同頻道自上一次真人發言以來，連續出現的 Bot 訊息數 (`streak`)**」為狀態指標：

| 事件順序 | 頻道內事件 | 兩邊各自記錄的 `streak` | 接手方觸發時看到的 `streak` | 套用機率 |
| :--- | :--- | :---: | :---: | :---: |
| **人類起頭** | 真人發言（呼叫 A） | 歸零 `0` | — | 真人觸發（正常 100%） |
| **Bot A 首輪回覆** | Bot A 回覆真人（內容提到 B） | `0 -> 1` | Bot B 收到時 `streak == 1` | **`100%`**（第 1 次 Bot 互回） |
| **Bot B 回 Bot A** | Bot B Reply Bot A | `1 -> 2` | Bot A 收到時 `streak == 2` | **`50%`**（第 2 次 Bot 互回） |
| **Bot A 回 Bot B** | Bot A Reply Bot B | `2 -> 3` | Bot B 收到時 `streak == 3` | **`30%`**（第 3 次 Bot 互回） |
| **Bot B 回 Bot A** | Bot B Reply Bot A | `3 -> 4` | Bot A 收到時 `streak == 4` | **`10%`**（第 4 次 Bot 互回） |
| **Bot A 回 Bot B** | Bot A Reply Bot B | `4 -> 5` | Bot B 收到時 `streak >= 5` | **`0%`**（強制停火） |

#### 關鍵邊界情況處理
1. **如何取得準確的 `streak`？（即時追蹤 + 啟動冷卻歸零）**
   * 每隻 Bot 在記憶體維護 `channel_peer_streaks[channel_id] = {"count": int, "last_ts": float}`。
   * **真人訊息**：任何 `not message.author.bot` 的訊息皆將該頻道 `count` 歸零 `0`。
   * **自身或同伴 Bot 訊息**：`author.id == self.bot.user.id` 或 `author.id in config.PEER_BOT_IDS` 時，`count += 1` 並更新 `last_ts`。
   * **逾期自動歸零 (`PEER_STREAK_RESET_SECONDS = 600`)**：若距離上一則同頻道 Bot 互動已超過 10 分鐘，視同舊話題已結束，自動將 `count` 重置為 `0` 再累計（同時可自然處理 `/chat` 斜線指令未觸發真人 `on_message` 的情況——另於 `/chat` 執行時主動將本地 `count` 歸零）。
2. **長訊息切塊 (`> 1900 字`) 防雙重觸發**：
   * 若 Bot A 單次回覆超過 1900 字拆成 2 則發送，第 2 則可能再次命中關鍵字或推高 `streak`。
   * **防護**：每隻 Bot 維護 `peer_replying_channels: set[str]`，處理中的頻道不重複排入第 2 則同伴觸發；且對同伴對話加上短暫防抖延遲（例如 `2.0～4.5 秒`），可同時防止多塊連發造成的雙重計數。
3. **只有「自己被叫到或被 Reply」才進入機率擲骰**：
   * 就算在機率區間內，仍須滿足 `is_mentioned or is_reply or has_wake_word` 才進入機率判斷，確保兩人話題不相關時不會無端插話。
   * 一旦 `Bot A` 透過 `target_message.reply(...)` 回覆 `Bot B`（`streak == 2, 3, 4`），`Bot B` 端的 `is_reply` 就會自然為 `True`，無需每句刻意呼喊名字即可自然接龍。
4. **若骰輸了（未通過 50% / 30% / 10% 或達 0%）**：
   * **依然完成 `memory_service.add_message(...)`**（記住對方最後這句說了什麼），僅跳過回覆生成流程，保持安靜。

---

## 🔄 3. 完整執行流程圖

```mermaid
flowchart TD
    M[頻道收到 Discord 訊息] --> S{發言者身分檢查}
    S -->|自己 self.bot.user| SELF[本地 streak += 1<br>不觸發回覆]
    S -->|其他陌生 Bot / Webhook| DROP[直接忽略 return]
    S -->|真人使用者| H_RST[重置該頻道 streak = 0<br>寫入 memory_service 緩衝]
    S -->|同伴 Bot in PEER_BOT_IDS| P_INC[若逾時先歸零，再 streak += 1<br>寫入 memory_service 緩衝 記住對方]

    H_RST --> TRIG_H{觸發條件?<br>@ / Reply / 關鍵字 / DM}
    TRIG_H -->|否| END1[安靜結束]
    TRIG_H -->|是| GEN_H[正常回覆真人<br>扣正常 VIP 額度]

    P_INC --> TRIG_P{是否點名/回覆我?<br>@ / Reply / 關鍵字}
    TRIG_P -->|否| END2[安靜記下但不插話]
    TRIG_P -->|是| LOCK{該頻道正在<br>回覆同伴嗎?}
    LOCK -->|是| END2
    LOCK -->|否| PROB[依當前 streak 查機率表<br>1->100%, 2->50%, 3->30%, 4->10%, >=5->0%]
    PROB -->|未命中 / 0%| END2
    PROB -->|命中| WAIT[防抖等待 2.0~4.5 秒<br>啟動 KeepTyping]
    WAIT --> GEN_P[豁免 VIP 額度<br>帶入同伴歷史與記憶產生自然接話]
    GEN_P --> SEND[Reply 回覆同伴訊息]
```

---

## 🛠️ 4. 設定與程式碼修改規劃

### 4.1. 新增環境變數設定（[config.py](file:///d:/OneDrive/Project/dc_auto_bot/config.py) & [.env.example](file:///d:/OneDrive/Project/dc_auto_bot/.env.example)）
```env
# 允許互動與記住記憶的同伴 Bot Discord User ID（多個以逗號分隔，留空代表停用）
PEER_BOT_IDS=
# 依連續 Bot 回合數設定的機率遞減表（streak=1, 2, 3, 4...，超過清單長度視為 0.0）
PEER_BOT_REPLY_PROBS=1.0,0.5,0.3,0.1
# 同伴 Bot 連續互動逾時自動歸零秒數
PEER_BOT_STREAK_TIMEOUT=600
# 收到同伴 Bot 訊息後的擬人化思考延遲秒數區間
PEER_BOT_DELAY_MIN=2.0
PEER_BOT_DELAY_MAX=4.5
```

### 4.2. 各模組調整重點
1. **[config.py](file:///d:/OneDrive/Project/dc_auto_bot/config.py)**：解析 `PEER_BOT_IDS: Set[int]`、`PEER_BOT_REPLY_PROBS: List[float]`（預設 `[1.0, 0.5, 0.3, 0.1]`）、逾時與延遲區間參數。
2. **[utils/context_builder.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py#L34-L36)**：
   * `fetch_recent_channel_context` 新增可選參數 `allowed_bot_ids: Optional[Set[int]] = None`（預設讀取 `config.PEER_BOT_IDS`）。
   * 跳過邏輯調整為：只跳過「既不是自己，也不在 `allowed_bot_ids` 內的 Bot」，讓近 30 則上下文完整保留兩隻 AI 彼此的對話脈絡。
3. **[cogs/ai_roleplay.py](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py)**：
   * 新增 `_channel_bot_streaks: Dict[int, Dict[str, float]]` 與 `_peer_replying_channels: set[int]` 狀態追蹤輔助方法（含逾時歸零、機率查詢）。
   * 調整 `on_message` 入口守衛：
     * 若 `message.author.id == self.bot.user.id`：更新本地 `streak += 1` 後 `return`。
     * 若 `message.author.bot` 且不在 `config.PEER_BOT_IDS`：直接 `return`。
     * 若非 Bot：重置 `streak = 0`；若為同伴 Bot：累加 `streak += 1`。
   * **共同進入 `memory_service.add_message(...)`**：確認同伴 Bot 的發言也持久化寫入目前角色（`persona_id`）的 `pending_messages`，以對方的 Discord 顯示名稱（角色暱稱）與 Bot User ID 累積印象與事實記憶。
   * 同伴觸發時：檢查 `streak` 對應機率、加上 `peer_replying_channels` 防重複鎖、執行 `asyncio.sleep(random.uniform(...))` 並顯示 `KeepTyping`。
   * `/chat` 指令執行時：同步將該頻道本地 `streak` 歸零。
4. **[core/dialog_service.py](file:///d:/OneDrive/Project/dc_auto_bot/core/dialog_service.py) / [utils/gemini_dialog_service.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/gemini_dialog_service.py)**：
   * 支援傳入 `skip_vip_check: bool = False`（或當 `is_peer_bot=True` 時豁免每日 VIP 額度扣減，避免對話 10 次後撞上限）。
   * 當 `is_peer_bot=True` 時，可於當輪 `user_text` 或暫時指示加上簡短風格提示（例如：「*提示：對方是同群的另一位 AI 夥伴，請以自然、簡短口語附和、接梗或輕度吐槽回應，避免過度正式寒暄。*」）。

---

## ⚠️ 5. 雙 Bot 併行運行重要檢查清單 (Ops Guardrails)

在實際啟動兩隻 Bot 前，請務必確認以下部署隔離設定（避免兩隻互搶檔案與資料庫）：
1. **彼此填入對方的 User ID**：
   * Bot A 的 `.env` 填 `PEER_BOT_IDS=<Bot_B_ID>`
   * Bot B 的 `.env` 填 `PEER_BOT_IDS=<Bot_A_ID>`
   * *(切勿把自己的 ID 寫進自己的 `PEER_BOT_IDS`。)*
2. **各自獨立的持久化目錄**：
   * 兩隻必須掛載**不同的 `./data` 資料夾**（或各自設定獨立 `MEMORY_DB_PATH`、`personas` 目錄與 `vip_config.json`），避免同時寫入同一份 SQLite 導致鎖死，或共用同一份 `active_persona.txt`。
3. **各自獨立的 Webhook Port**：若同時啟用 FastAPI Web Server，需設定不同的 `WEBHOOK_PORT`（例如 `8080` 與 `8081`）。
4. **確保兩邊都已開啟 `MESSAGE CONTENT INTENT`**，否則無法讀取對方發出的純文字內容進行喚醒詞匹配與記憶學習。

---

## 🧪 6. 測試與驗證矩陣

| 測試情境 | 操作步驟 | 預期行為 |
| :--- | :--- | :--- |
| **1. 第 1 次接話 (100%)** | 真人 `@BotA 你覺得小愛今天怎麼樣？` ➔ Bot A 回覆中提到「小愛」 | Bot B 偵測 `streak == 1`，**100%** 在 2～4.5 秒後回覆 Bot A。 |
| **2. 機率漸進收斂** | 重複觸發多次雙 Bot 互聊並統計多輪長度 | `streak=2` 約 50% 繼續；`streak=3` 約 30%；`streak=4` 約 10%；`streak>=5` **100% 停止（0%）**，絕不無限迴圈。 |
| **3. 真人插話重置** | 雙 Bot 停火於 `0%` 後，真人在同頻道隨意發言或再起頭 | `streak` 立刻歸零；下一次由真人引出同伴對話時重新從 **100%** 開始。 |
| **4. 全頻道通用與上下文** | 在不同頻道（非特定專屬頻道）觸發雙 Bot 互聊 | Bot A 與 Bot B 的近 30 則上下文皆能看見對方發言，對話連貫不失憶。 |
| **5. 記憶對方學習驗證** | 兩隻互動累積達學習門檻後執行 `/view_memory` | 雙方各自 `data/memory.db` 內能看到對彼此 `user_id` 萃取出的風格印象與事件事實。 |
| **6. 陌生 Bot 隔離驗證** | 頻道內由第三方通知 Bot / Webhook 提及關鍵字 | 兩隻 AI **完全忽略**，不進記憶也不回覆。 |
