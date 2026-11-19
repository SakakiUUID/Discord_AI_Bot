# 🗑️ 移除 AI 自動迷因發圖 (Meme Sticker Delivery Engine) 功能計畫書

> **文件定位**：功能移除與程式碼重構計畫 (Feature Decommissioning Plan)  
> **目標對象**：[`core/sticker_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/sticker_service.py)、[`core/dialog_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/dialog_service.py)、[`core/agent_engine.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/agent_engine.py)、[`core/models/outgoing.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/models/outgoing.py)、[`utils/gemini_dialog_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/utils/gemini_dialog_service.py)、[`cogs/ai_roleplay.py`](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py)、[`adapters/discord/delivery.py`](file:///d:/OneDrive/Project/dc_auto_bot/adapters/discord/delivery.py)、[`config.py`](file:///d:/OneDrive/Project/dc_auto_bot/config.py)  
> **核心目標**：乾淨移除依賴 `STICKER_TRIGGER_PROBABILITY`、`[MEME: ...]` 標籤注入與 `data/meme_stickers_index.json` 的「自動拿圖片/迷因貼圖發送」機制，消除不必要的 Prompt 干擾、磁碟索引載入與多餘欄位，同時 **100% 保留** 使用者傳圖視覺辨識（Multimodal Vision）、雙層記憶學習、Google Search Grounding 與人設對話功能。

---

## 📌 1. 功能盤點與移除邊界釐清

在動手前，需明確區分專案中三種與「圖片／貼圖」相關的機制，避免誤刪有用功能：

| 機制名稱 | 實際用途 | 處置建議 |
| :--- | :--- | :--- |
| **1. 動態迷因梗圖發送 (`[MEME: ...]`)** | 對話時以 `40%` 機率注入 Prompt 要求 AI 輸出 `[MEME: 動作]`，再從 `data/meme_stickers_index.json` + `image/` 挑圖發送 Discord 附件。 | **🔴 本次主要移除目標** |
| **2. 讀取群友圖片 (Multimodal Vision)** | 讓 AI 看懂群友在頻道、`@Bot`、回覆訊息或 `/chat` 中上傳的圖片，並納入記憶學習（`IncomingMessage.image_bytes`、`ContextBuilder`）。 | **🟢 完整保留**（與自動發圖無關） |
| **3. 傳統情感貼圖 (`[EMOTION: ...]`)** | AI 依人設輸出 `[EMOTION: happy]`，從 `assets/emotions/{emotion}.png` 取圖。目前 `assets/emotions/` 僅有 `.gitkeep`、無實體 PNG。 | **🟡 預設保留**（若確認連同舊標籤一併退場，見第 4 節可選清理） |

---

## 🔍 2. 移除範圍與影響評估 (Impact & Scope Analysis)

### 2.1. 必刪 / 必改的運行期程式碼（核心範圍）

1. **刪除檔案**：
   - [`core/sticker_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/sticker_service.py)：整個 `StickerService` 類別、索引載入、`extract_meme_tag`、`match_sticker`、`get_prompt_instruction` 與全域單例 `sticker_service`。
2. **修改 [`config.py`](file:///d:/OneDrive/Project/dc_auto_bot/config.py)**：
   - 移除 `STICKER_TRIGGER_PROBABILITY` 設定讀取（第 27–28 行）。
3. **修改 [`core/dialog_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/dialog_service.py)**：
   - 移除 `extract_meme_tag()` 靜態方法（第 43–52 行）。
   - `_get_genai_config()`：移除 `allow_meme` 參數與 `【迷因貼圖調用指示】` System Instruction 注入區塊（第 65、72–78 行）。
   - `generate_response()`：
     - 移除 `allow_meme` 參數與 `random.random() < config.STICKER_TRIGGER_PROBABILITY` 機率檢定（第 108、151–155、164 行）。
     - 移除 `extract_meme_tag()` 呼叫（第 195 行）。
     - 將回傳簽章由 3-tuple `Tuple[str, Optional[str], Optional[str]]` 簡化回 2-tuple `Tuple[str, Optional[str]]`（`clean_text, emotion`），同步調整所有早期 `return` 路徑（第 111–123、142、184、197、202 行）。
4. **修改 [`utils/gemini_dialog_service.py`](file:///d:/OneDrive/Project/dc_auto_bot/utils/gemini_dialog_service.py)**：
   - 將 `generate_response()` 的回傳型別標註由 `Tuple[str, Optional[str], Optional[str]]` 改回 `Tuple[str, Optional[str]]`（第 35 行）。
5. **修改 [`core/models/outgoing.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/models/outgoing.py)**：
   - 移除 `meme_sticker_path: Optional[str] = None` 欄位（第 13 行）。
6. **修改 [`core/agent_engine.py`](file:///d:/OneDrive/Project/dc_auto_bot/core/agent_engine.py)**：
   - 移除 `StickerService` 匯入、`__init__` 的 `meme_index_path` / `image_dir` 參數與 `self.sticker_service` 實例化（第 15、29–30、33、45 行）。
   - `process_message()` 改為接收 `(reply_text, emotion)` 2-tuple，移除 `meme_sticker_path` 檢索、磁碟檢查與 log（第 111、125–135 行），並在建立 `OutgoingResponse` 時移除 `meme_sticker_path`（第 140 行）。
7. **修改 [`cogs/ai_roleplay.py`](file:///d:/OneDrive/Project/dc_auto_bot/cogs/ai_roleplay.py)**：
   - `on_message`（第 212–237 行）與 `/chat`（第 269–292 行）：
     - 將 `generate_response(...)` 解包改為 `reply_text, emotion = ...`。
     - 移除 `if meme_keyword:` 區塊內對 `core.sticker_service.sticker_service` 與 `delivery_service.get_sticker_file_from_path(...)` 的呼叫。
     - 更新 `/chat` 第 292 行標籤清理呼叫名稱（若 `clean_residual_meme_tag` 改名為 `clean_residual_tags`）。
8. **修改 [`adapters/discord/delivery.py`](file:///d:/OneDrive/Project/dc_auto_bot/adapters/discord/delivery.py)**：
   - 移除僅服務於迷因貼圖路徑載入的 `get_sticker_file_from_path()`（第 32–40 行）。
   - 將 `clean_residual_meme_tag()`（第 43–47 行）與 `send_split_message()`（第 57 行）中的命名更名為 `clean_residual_tags()`（正則仍可安全相容清理殘留標籤，或僅保留 `\[EMOTION:\s*[^\]]+\]`）。
9. **修改 [`.dockerignore`](file:///d:/OneDrive/Project/dc_auto_bot/.dockerignore)**：
   - 移除第 6 行的 `image/`（不再需要迷因圖庫目錄）。

---

## 🛠️ 3. 建議執行步驟 (Execution Steps)

### Step 1：解除 Core 層與 Config 耦合
1. 自 `config.py` 刪除 `STICKER_TRIGGER_PROBABILITY`。
2. 調整 `core/dialog_service.py`：移除 `allow_meme`、迷因 Prompt 注入、`extract_meme_tag`，將 `generate_response()` 恢復回傳 `(clean_text, emotion)`。
3. 調整 `core/models/outgoing.py` 與 `core/agent_engine.py`：移除 `meme_sticker_path`、`StickerService` 依賴與貼圖檢索流程。
4. 刪除 `core/sticker_service.py`。

### Step 2：更新 Adapter、Utils Wrapper 與 Discord Cog
1. 更新 `utils/gemini_dialog_service.py` 的回傳型別標註為 `Tuple[str, Optional[str]]`。
2. 更新 `adapters/discord/delivery.py`：移除 `get_sticker_file_from_path()`，並將 `clean_residual_meme_tag` 重新命名為 `clean_residual_tags`。
3. 更新 `cogs/ai_roleplay.py` 的 `on_message` 與 `/chat`：改接 2-tuple、刪除 `if meme_keyword:` 分支、改用 `clean_residual_tags`。

### Step 3：清理環境設定與文件
1. 移除 `.dockerignore` 的 `image/`（以及本機 `.env` 中的 `STICKER_TRIGGER_PROBABILITY`，若有設定）。
2. 若選擇一併清理靜態資源（見第 4 節），刪除對應資料檔與離線腳本。
3. 依照專案 skill [`record-file-change`](file:///d:/OneDrive/Project/dc_auto_bot/.agents/skills/record-file-change/SKILL.md) 規範，於完成修改後更新 [`.change_log.md`](file:///d:/OneDrive/Project/dc_auto_bot/.change_log.md)。

---

## 📦 4. 可選清理項目 (Optional Cleanup — 待確認)

以下檔案在移除上述運行期程式碼後**不會再被 Bot 載入或執行**，可視需求決定是否一併刪除（可釋放約 1.7 MB 並保持倉庫整潔）：

1. **貼圖索引與 Manifest 資料檔（共約 1.67 MB）**：
   - `data/meme_stickers_index.json` (~637 KB)
   - `data/bd_stickers_index.json` (~559 KB)
   - `data/bd_stickers_manifest.json` (~559 KB)
2. **離線貼圖辨識與索引建置工具 (`tools/`)**：
   - `tools/build_meme_index_from_folder.py`
   - `tools/convert_stickers_index.py`
   - `tools/process_bd_stickers.py`
   - `tools/reclassify_umiri_taki.py`
3. **歷史計畫文件**：
   - [`plans/MEME_STICKER_DELIVERY_ENGINE_PLAN.md`](file:///d:/OneDrive/Project/dc_auto_bot/plans/MEME_STICKER_DELIVERY_ENGINE_PLAN.md)（建議保留或於檔頭加註 `[已棄用 / Decommissioned]`）。
4. **若連舊的 `[EMOTION: ...]` 表情貼圖機制也不想保留**（目前 `assets/emotions/` 內無實際圖檔）：
   - 可再額外移除 `DialogService.extract_emotion_tag()`、`AgentEngine.get_sticker_path()`、`DiscordDeliveryService.get_emotion_sticker()`，以及 `data/personas/persona_*.txt` 中的 `[EMOTION: ...]` 指令敘述。

---

## 🧪 5. 移除後驗證計畫 (Verification Checklist)

| 驗證項目 | 檢查 / 測試方式 | 預期結果 |
| :--- | :--- | :--- |
| **1. 殘留引用檢查** | 搜尋全專案是否還有 `sticker_service`、`MEME`、`meme_keyword`、`STICKER_TRIGGER_PROBABILITY`。 | 運行期程式碼 (`core/`, `adapters/`, `cogs/`, `utils/`, `config.py`, `main.py`) 中 **零殘留**。 |
| **2. 語法與啟動檢查** | 執行 `python -m py_compile main.py core/*.py adapters/discord/*.py cogs/*.py utils/*.py config.py` 並啟動 Bot。 | 無 `ImportError` 或啟動警告（不再出現 `StickerService` / `meme_stickers_index.json` 載入 log）。 |
| **3. 一般 @Bot / 回覆對話** | 在 Discord `@Bot` 或觸發喚醒詞進行多輪對話（含觸發 Google Search 與一般閒聊）。 | 正常純文字回覆，不會出現 `[MEME: ...]` 字樣，也不會夾帶貼圖附件。 |
| **4. `/chat` 斜線指令（含傳圖）** | 執行 `/chat` 並測試純文字與附加圖片詢問。 | AI 能正常看圖並以人設文字回覆，不報 `ValueError: not enough values to unpack`。 |
| **5. 記憶學習驗證** | 群組對話累積後觸發背景記憶摘要。 | `MemoryService` 正常記錄與更新群體文化及個人印象，完全不受影響。 |
