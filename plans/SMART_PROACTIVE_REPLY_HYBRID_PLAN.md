# 🔥 免提及熱聊延續視窗系統計畫書（模式 A：動態多參與者版）

> **專案**：`dc_auto_bot`  
> **定位**：架構分析與設計規劃文件（尚未修改程式碼）  
> **範圍決策**：專注於 **「純模式 A：熱聊延續視窗」**（模式 B 暫緩）；針對群聊多人加入與雙話題並行，升級為**「動態多參與者 (`active_users`) + 個人獨立 5 次 `pass` 移除 + 雙條件關窗」**機制。  
> **設計目標**：讓話題發起者與後續加入的第 2、第 3 位群友都能免 `@Bot` 順暢續聊；各自獨立退場，直到**所有參與者皆被移除**或**超過時間門檻沒有任何對話**才關閉視窗，同時維持無關路人極低 API 消耗。

---

## 📌 1. 核心狀態機與規則

每個頻道維護一份熱聊視窗狀態 `HotWindowState`：
* `expires_at: float`：頻道熱聊總到期時間（每次 Bot 於該頻道成功回覆即刷新為 `now + 180s`）。
* `active_users: Dict[int, int]`：目前活躍參與該話題的使用者與**各自獨立的連續 `pass` 次數**（`{user_id: consecutive_passes}`）。
* `bystander_passes: Dict[int, int]` & `ignored_bystanders: Set[int]`：同頻道其他無關路人的「暫時忽略表」，避免另一條話題的路人重複消耗裁判 API。

### 參與者加入與退場規則
| 事件 | 對象 | 系統行為 |
| :--- | :--- | :--- |
| **顯性喚醒 / `/chat` / Bot 成功回覆** | 任一真人使用者 | 1. 若該用戶原在 `ignored_bystanders`，立即解除。<br>2. 將該用戶加入（或保留）在 `active_users[uid] = 0`（**不會覆蓋掉其他既有參與者**，可設上限如最多 8 人）。<br>3. 刷新頻道 `expires_at = now + 180s`。 |
| **已在 `active_users` 的成員免 `@` 發言** | 第 1、2、3…位參與者 | 經 2.5s Debounce 進入 JSON 裁判：<br>• **`reply`**：Bot 回覆，將**該成員** `active_users[uid]` 歸零並刷新 `expires_at = now + 180s`。<br>• **`react`**：按 Emoji，不改變 `pass` 計數、不延長 `expires_at`。<br>• **`pass`**：**僅將該成員自己** `active_users[uid] += 1`；**滿 5 次時只將此成員移出 `active_users`**，其餘成員不受影響。 |
| **尚未加入的群友免 `@` 發言** | 想加入話題的新人 / 或另條話題路人 | 先檢查是否為 Reply 其他真人（若是則直接略過）；不在 `ignored_bystanders` 時才進入裁判：<br>• **`reply`**：判定他成功加入話題 ➔ **直接晉升納入 `active_users[uid] = 0`**，並刷新 `expires_at`！<br>• **`pass`**：完全不影響 `active_users` 任何人的 `pass` 次數；僅累計該路人自己的 `bystander_passes[uid] += 1`，連續 **2 次** `pass` 即列入本輪 `ignored_bystanders`（不再為另一條話題花 API，直到他主動 `@Bot` 或視窗重開）。 |
| **視窗徹底關閉條件（滿足其一即關閉）** | 整個頻道視窗 | 1. **`active_users` 全部被移除（變成空字典 `{}`）**。<br>2. **`now >= expires_at`（到達 180 秒時間門檻，期間無任何 Bot 成功對話續期）**。 |

> **補充**：私訊（DM）本身即屬直接對話（`is_dm = True`），維持全時免 `@` 對話，不走模式 A 裁判。

---

## 🔄 2. 訊息分流與多參與者演進流程

```mermaid
flowchart TD
    M[收到 Discord 訊息] --> G0{發言者身分檢查}
    G0 -->|self.bot.user / 陌生 Bot / Webhook| DROP0[依現有規則處理/略過]
    G0 -->|同伴 Bot in PEER_BOT_IDS| PEER[僅走現有 Peer-Bot 顯性機率遞減<br>不開窗、不進入模式 A 裁判]
    G0 -->|真人使用者| MEM[一律寫入 memory_service 緩衝<br>並重置 peer streak]

    MEM --> EXP{是否顯性觸發?<br>@ / Reply Bot / 喚醒詞 / DM}
    EXP -->|是| CANCEL[取消該人進行中 Debounce]
    CANCEL --> DIRECT[進入主對話生成回覆]
    DIRECT --> ADD_U[納入 active_users 與歸零 pass<br>刷新頻道 expires_at +180s]

    EXP -->|否| WIN{頻道熱窗開啟中?<br>未逾時且 active_users 非空}
    WIN -->|否| SILENT[安靜放行 0 API 開銷]
    WIN -->|是| REF_OTHER{Reply 其他真人<br>或在 ignored_bystanders?}
    REF_OTHER -->|是| SILENT
    REF_OTHER -->|否| QUOTA{VIP 預檢有剩餘額度?}
    QUOTA -->|否| SILENT
    QUOTA -->|是| DEB[依 channel_id + user_id<br>進入 2.5s Debounce 合併]

    DEB --> JUDGE[輕量 JSON 裁判<br>只看 15 分鐘內近 10 則訊息]
    JUDGE -->|react| REACT_ACT[按單一 Unicode Emoji<br>不扣額度、不動 pass]
    JUDGE -->|reply| PROMOTE[回覆並將該人納入/歸零 active_users<br>刷新頻道 expires_at +180s]
    JUDGE -->|pass| PASS_CHK{是否在 active_users 中?}

    PASS_CHK -->|否: 旁觀路人| BYS[bystander_passes += 1<br>達 2 次列入 ignored_bystanders]
    PASS_CHK -->|是: 話題參與者| ACT_P[該員 passes += 1<br>達 5 次則從 active_users 移除]
    ACT_P --> EMPTY_CHK{active_users 是否已全空?}
    EMPTY_CHK -->|是| CLOSE_W[立刻關閉該頻道熱聊視窗]
    EMPTY_CHK -->|否| KEEP_W[視窗保留給其他仍在聊的參與者]
```

---

## 🛡️ 3. 針對舊版痛點與多人聊天的關鍵防護

1. **Structured JSON 裁判（消除 `[` 殘字 Bug）**：
   * 裁判固定 `response_mime_type="application/json"`、`temperature=0.0`、`max_output_tokens=128`（不開 Google Search、不跑向量記憶），回傳 `{"decision": "reply"|"react"|"pass", "emoji": "...", "reason": "..."}`。解析失敗一律降級 `pass`。
2. **精準標記當前發言者與最近 15 分鐘上下文 (`max_age_minutes = 15`)**：
   * 裁判只抓近 10 則且在 15 分鐘內的訊息，同時在 Prompt 清楚標出：**「目前在對話中的參與者名單」**與**「剛發送新訊息的這一位成員名稱與是否為既有參與者」**，讓模型精準分辨他是接續跟 AI 的話題、還是跟其他群友開了新話題。
3. **按 `(channel_id, user_id)` 獨立 Debounce（預設 `2.5s`）**：
   * 小明連打 3 行斷句時只合併他自己的訊息做 1 次裁判；同時小華打字不會重置或混進小明的緩衝，兩人互不干擾。
4. **雙 Bot (`PEER_BOT_IDS`) 天然互斥**：
   * 只有當下剛回覆過人類的那一隻 Bot 會在自己記憶體對該頻道開啟 `HotWindowState`，另一隻 Bot 的熱窗是關閉的，不搶話；同伴 Bot 的訊息永遠只走現有顯性機率遞減。
5. **VIP 額度靜默預檢**：
   * 在 [utils/vip_manager.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/vip_manager.py) 新增唯讀 `has_remaining_quota(user_id)`；無剩餘額度時靜默略過，且僅於 `reply` 時扣 1 次額度。

---

## 📁 4. 設定參數與模組修改規劃

### 4.1. 新增環境變數（[config.py](file:///d:/OneDrive/Project/dc_auto_bot/config.py) & [.env.example](file:///d:/OneDrive/Project/dc_auto_bot/.env.example)）
```env
# --- 模式 A：動態多參與者熱聊視窗設定 ---
PROACTIVE_HOT_WINDOW_ENABLED=true
# 頻道無任何成功互動後的熱窗過期時間（秒）
PROACTIVE_HOT_WINDOW_SECONDS=180
# 既有話題參與者各自允許連續被判 pass 的次數（達上限僅移除該人）
PROACTIVE_HOT_MAX_CONSECUTIVE_PASS=5
# 尚未加入話題的路人連續被判 pass 幾次後，本輪暫時忽略（省雙話題 API）
PROACTIVE_BYSTANDER_MAX_PASS=2
# 單一頻道熱窗最多同時追蹤的參與者數量
PROACTIVE_HOT_MAX_ACTIVE_USERS=8
# 同使用者防連發合併秒數（秒）
PROACTIVE_DEBOUNCE_SECONDS=2.5
# 裁判讀取近期上下文的最大訊息年齡（分鐘）
PROACTIVE_CONTEXT_MAX_AGE_MINUTES=15
```

### 4.2. 各檔案調整規劃
1. **新增 [core/proactive_judge.py](file:///d:/OneDrive/Project/dc_auto_bot/core/proactive_judge.py)**：
   * 實作 `ProactiveDecision` 與 `ProactiveJudge`，針對「既有參與者續聊」與「新群友試圖加入話題」提供一致且精準的 JSON 判斷 Prompt，並驗證 `react` 僅限標準單一 Unicode Emoji。
2. **修改 [utils/context_builder.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/context_builder.py)**：
   * `fetch_recent_channel_context` 新增可選參數 `limit: int = 30` 與 `max_age_minutes: Optional[int] = None`（主對話維持預設 30 則不變，裁判端傳 `limit=10, max_age_minutes=15`）。
3. **修改 [utils/vip_manager.py](file:///d:/OneDrive/Project/dc_auto_bot/utils/vip_manager.py)**：
   * 新增唯讀方法 `has_remaining_quota(self, user_id: int) -> bool`。
4. **修改 [cogs/ai_roleplay.py](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py)**：
   * 定義輕量資料結構 `HotWindowState`（`expires_at`、`active_users: Dict[int, int]`、`bystander_passes: Dict[int, int]`、`ignored_bystanders: Set[int]`）與 `_hot_windows: Dict[int, HotWindowState]`。
   * 封裝 `_register_hot_participant(channel_id, user_id)`（新增/歸零該員 pass 並延長 `expires_at`，若超上限則淘汰最早未活躍者）、`_record_user_pass(channel_id, user_id)`（達 5 次移出並在 `active_users` 全空時刪窗）、以及定時/存取時清理過期視窗 (`now >= expires_at`)。
   * `_debounce_tasks` 與 `_debounce_buffers` 改以 `(channel_id, user_id)` 為鍵值，互不干擾。

---

## 🧪 5. 驗收與測試矩陣

| 測試場景 | 操作方式 | 預期行為 |
| :--- | :--- | :--- |
| **1. 多位群友自然加入同個話題** | 小明 `@Bot` 開局後，小華、阿美接連不 `@` 針對同一話題提問 | 裁判判定 `reply` 後，小華與阿美自動加入 `active_users`，三人同時享有免 `@` 續聊權且不互踢。 |
| **2. 個別滿 5 次 `pass` 獨立退場** | 小明連發 5 輪與 Bot 無關的話（全判 `pass`），期間小華仍在跟 Bot 聊 | **只有小明被移出 `active_users`**；小華仍留在熱窗內正常續聊，視窗不會提早關閉。 |
| **3. 全部移除即關閉視窗** | 當視窗內僅剩小華一人，小華也集滿 5 次 `pass` | `active_users` 歸零變空，頻道熱窗**立即關閉**，恢復 0 API 呼叫。 |
| **4. 雙話題無關路人不浪費 API** | 熱窗內大雄與老王在同頻道聊晚餐（連續 2 輪判 `pass`） | 不會消耗小明/小華的 5 次 `pass`；大雄滿 `2` 次 `bystander_passes` 後加入 `ignored_bystanders`，之後零 API 消耗（除非他主動 `@Bot`）。 |
| **5. 逾時無對話自動關窗** | 最後一次 Bot 回覆後超過 180 秒無人讓 Bot 接話 | `now >= expires_at`，熱窗自動失效關閉，下次須顯性喚醒才能重開。 |
| **6. 主動 Reply 他人不扣次數** | 小明用 Discord Reply 回覆了小華的訊息 | 直接略過裁判，不扣減小明那 5 次 `pass` 額度。 |
| **7. 額度滿與雙 Bot 安全** | 非 VIP 達上限；或雙 Bot 同頻道只呼叫 Bot A | 滿額靜默略過不跳警告；僅 Bot A 開啟該頻道視窗，Bot B 不搶話。 |
