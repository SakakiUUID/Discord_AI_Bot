# 🗑️ 移除 AI 角色自動回覆 (自主插話 Spontaneous Chime-In) 功能計畫書

> **文件定位**：功能移除與程式碼精簡重構計畫 (Feature Decommissioning Plan)  
> **目標對象**：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)、[`utils/spontaneous_engine.py`](file:///d:/OneDrive/tools/dc_auto_bot/utils/spontaneous_engine.py)  
> **核心目標**：乾淨俐落地移除「自動回覆 / 自主插話與表情反應 (Spontaneous Chime-In)」功能，讓 Bot 回歸純粹的「被動交互模式（僅在 @Bot、回覆 Bot 或使用 `/chat` 指令時回應）」，同時 100% 保留記憶學習、人設切換與主對話功能。

---

## 📌 1. 移除範圍與影響評估 (Impact & Scope Analysis)

### 1.1. 需移除的檔案與模組
1. **刪除檔案**：[`utils/spontaneous_engine.py`](file:///d:/OneDrive/tools/dc_auto_bot/utils/spontaneous_engine.py)（整個自主插話引擎）。
2. **修改檔案**：[`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)
   * 移除 `SpontaneousEngine` 匯入與實例化。
   * 移除 `on_message` 尾部的非主動觸發分支 (`elif self.spontaneous_engine...`)。
   * 移除 `/spontaneous_toggle` 管理員斜線指令。
   * 移除 `spontaneous_*` 屬性轉發器。

### 1.2. 保留且不受影響的核心功能（零風險 🟢）
* ✅ **@Bot 標記與回覆對話**：正常運作。
* ✅ **`/chat` 與 `/chg_persona` 斜線指令**：正常運作。
* ✅ **`/bot_info` 狀態回報**：正常運作。
* ✅ **雙層記憶系統 (`MemoryManager`)**：`on_message` 依然會將每一則聊天記錄寫入緩衝區進行背景學習，完全不受影響。
* ✅ **時間感知與歷史上下文 (`ContextBuilder`)**：常規對話依然享有帶時間標籤的上下文理解。
* ✅ **YouTube 推播與 VIP 管理**：完全不受影響。

---

## 🛠️ 2. 具體代碼移除規範 (Exact Modification Steps)

### 步驟 1：修改 [`cogs/ai_roleplay.py`](file:///d:/OneDrive/tools/dc_auto_bot/cogs/ai_roleplay.py)

#### 1.1. 移除 Import
```diff
 from utils.vip_manager import vip_manager
 from utils.persona_manager import PersonaManager
 from utils.context_builder import ContextBuilder
 from utils.message_delivery import MessageDeliveryService
 from utils.gemini_dialog_service import GeminiDialogService
-from utils.spontaneous_engine import SpontaneousEngine
```

#### 1.2. 移除 `__init__` 中的引擎實例化與屬性轉發
```diff
     def __init__(self, bot: commands.Bot):
         self.bot = bot
         self.persona_manager = PersonaManager()
         self.context_builder = ContextBuilder()
         self.delivery_service = MessageDeliveryService()
         self.dialog_service = GeminiDialogService()
-        self.spontaneous_engine = SpontaneousEngine()
```
*(同時刪除 `spontaneous_cooldowns`、`spontaneous_enabled`、`spontaneous_cooldown_seconds`、`spontaneous_probability` 等 getter/setter 屬性)*

#### 1.3. 移除 `on_message` 中的自主插話分支
```diff
                 await self.delivery_service.send_split_message(
                     target_message=message,
                     channel=message.channel,
                     text=reply_text,
                     sticker_file=sticker_file
                 )
-
-        # 2. Proactive Chime-In (Non-mentioned / Non-reply / Non-DM)
-        elif self.spontaneous_engine.spontaneous_enabled and not isinstance(message.channel, discord.DMChannel):
-            if self.spontaneous_engine.should_attempt_spontaneous(message, self.persona_manager.persona_display_name):
-                asyncio.create_task(
-                    self.spontaneous_engine.handle_spontaneous_chime_in(
-                        trigger_message=message,
-                        context_builder=self.context_builder,
-                        persona_manager=self.persona_manager,
-                        delivery_service=self.delivery_service,
-                        ai_client=self.dialog_service.ai_client,
-                        bot=self.bot
-                    )
-                )
```

#### 1.4. 移除 `/spontaneous_toggle` 斜線指令
直接刪除 `spontaneous_toggle` 函式（第 484 ~ 503 行）。

---

### 步驟 2：刪除 [`utils/spontaneous_engine.py`](file:///d:/OneDrive/tools/dc_auto_bot/utils/spontaneous_engine.py)
直接將該檔案從檔案系統中移除。

---

## 🧪 3. 測試與驗證計畫 (Verification Checklist)

| 驗證項目 | 測試操作 | 預期表現 |
| :--- | :--- | :--- |
| **1. 普通閒聊靜默驗證** | 群友在文字頻道中隨意發言（提到「笑死」、「大保底」、「莫寧」等關鍵字，但未 @Bot）。 | Bot **100% 保持完全安靜**，絕不主動發送文字或點擊表情反應。 |
| **2. 主動 @Bot 對話驗證** | 群友發言 `@Bot 你好` 或回覆 Bot 的舊訊息。 | Bot **正常秒回**，帶有思考打字狀態與表情貼圖。 |
| **3. 斜線指令完整性驗證** | 執行 `/chat`、`/chg_persona`、`/bot_info`。 | 所有指令正常回應，且 `/spontaneous_toggle` 已自 Discord 指令選單中完全消失。 |
| **4. 記憶學習不中斷驗證** | 群友連續發言滿 30 則。 | 後台順利觸發 `🧠 [Memory Engine] Background extraction complete...`，背景學習正常運行。 |
