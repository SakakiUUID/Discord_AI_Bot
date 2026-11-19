# 🖼️ 動態迷因梗圖庫索引優化與發圖系統建設計畫書 (Meme Sticker Delivery Engine)

> **專案定位**：AI 角色生動多模態發圖引擎與貼圖庫架構規範  
> **核心概念**：**不限角色、以情緒與迷因動作為核心**。AI 在與群友對話時，根據語境與情緒自動匹配最生動的迷因/表情貼圖（支援 PNG、JPG、GIF），作為 Discord 訊息附件同步發送。  
> **圖片實體存放路徑**：`image/`（相對於專案根目錄）  
> **原始資料來源**：[`data/bd_stickers_index.json`](file:///d:/OneDrive/tools/dc_auto_bot/data/bd_stickers_index.json)（共 1,500+ 張精選迷因與動作圖）  
> **新建優化格式檔案**：`data/meme_stickers_index.json`

---

## 📌 1. 核心需求與設計原則

1. **去角色化、以「情緒與迷因動作」為導向**：
   * 不管目前扮演的是愛莉、莫寧、愛彌斯或達妮婭，發圖時只專注於**「這句話的情緒是什麼」**或**「當下適合什麼迷因動作」**（如：*生氣*、*合掌拜託*、*操你們媽*、*為什麼*、*被摸頭*、*是又怎樣*）。
2. **免冷卻、純機率控制 (Chance-Based Trigger)**：
   * **冷卻限制拔除**：不再設定「X 分鐘只能發一張圖」的死板時間鎖。
   * **機率控制**：預設每次對話有 **40% 機率** 允許帶圖（或可由環境變數/設定檔配置 `STICKER_TRIGGER_PROBABILITY = 0.4`），平常以文字為主，觸發時精準帶圖，維持社群驚喜感而不洗版。
3. **極致輕量與極速檢索**：
   * 將原始 1.5 萬行的詳細日誌型 JSON，重新精煉成**「情緒動作 ➔ 圖片清單」**的反向索引庫，記憶體檢索複雜度達到 $O(1)$。

---

## 🗂️ 2. 新資料格式設計：`data/meme_stickers_index.json`

原始的 `bd_stickers_index.json` 包含許多非必要的日誌欄位（如 `original_name`、`size_bytes`、`processed_at`）。
我們將其重新改編成**雙向加速結構 (Bi-directional Optimized Index)**：

```json
{
  "categories": {
    "生氣": ["千早愛音_生氣.gif", "千早愛音_生氣_01.gif", "椎名立希_操你們媽.png"],
    "拜託": ["長崎爽世_合掌拜託.gif"],
    "摸頭": ["要樂奈_被摸頭.gif"],
    "為什麼": ["長崎爽世_為什麼.gif", "長崎爽世_妳到底想幹嘛.gif"],
    "哭泣": ["若葉睦_委屈哭泣.jpg"],
    "開心": ["合照_Q版探頭.gif", "千早愛音_她們好可愛喔.gif"]
  },
  "items": {
    "椎名立希_操你們媽.png": {
      "action": "操你們媽",
      "ext": ".png",
      "keywords": ["生氣", "罵人", "破防", "暴躁"]
    },
    "要樂奈_被摸頭.gif": {
      "action": "被摸頭",
      "ext": ".gif",
      "keywords": ["摸頭", "舒服", "乖巧", "貓貓"]
    }
  }
}
```

### 優化優勢：
* **`categories`**：可直接由關鍵字/情緒提取可用檔名，抽取時隨機挑選一張（`random.choice`），增加多元性。
* **`items`**：支援反向關鍵字模糊匹配（Fuzzy Matching）。

---

## 🏛️ 3. 系統運作與發圖全流程 (Workflow)

```mermaid
flowchart TD
    UserMsg[群友發送訊息] --> Engine[AgentEngine / DialogService]
    
    Engine --> Dice{機率檢定<br>random.random < 0.4?}
    
    Dice -->|否| Plain[AI 生成純文字對話<br>不附加貼圖標籤]
    Dice -->|是: 命中發圖機率| Prompt[Prompt 注入可用情緒範例<br>要求 AI 台詞末尾附帶 [MEME: 情緒或動作]]
    
    Prompt --> AI[Gemini 3.8 Flash 生成回覆]
    
    AI --> Parse[StickerService 解析台詞<br>提取 [MEME: 動作/情緒] 標籤]
    
    Parse -->|未提取到或無標籤| SendText[發送純文字]
    Parse -->|提取成功| Match[從 data/meme_stickers_index.json 檢索圖片<br>在 image/ 資料夾搜尋實體檔案]
    
    Match -->|找到圖片檔案| SendAll[DiscordDeliveryService 發送<br>台詞文字 + discord.File 附件]
    Match -->|檔案不存在| Fallback[乾淨清洗掉標籤，回退純文字發送]
```

---

## 🛠️ 4. 代碼模組改動規格

### 模組 1：轉換工具指令碼（一次性生成優化索引）
* **路徑**：`tools/convert_stickers_index.py`
* **功能**：讀取 `data/bd_stickers_index.json`，自動清理、歸納同義詞與關鍵字，輸出乾淨輕巧的 `data/meme_stickers_index.json`。

### 模組 2：核心貼圖檢索服務 (`core/sticker_service.py`)
* **職責**：
  * 在機器人啟動時載入 `data/meme_stickers_index.json`。
  * 提供 `match_sticker(keyword_or_action: str) -> Optional[str]`：
    1. 精確匹配：`categories.get(keyword)`
    2. 模糊匹配：檢查 `items` 中的 `action` 或 `keywords` 是否包含輸入關鍵字。
    3. 隨機回傳其中一張檔案相對路徑（例如：`image/千早愛音_生氣.gif`）。

### 模組 3：提示詞動態注入 (`core/dialog_service.py`)
* 當命中 40% 機率時，在對話 Prompt 附加：
  ```text
  【迷因貼圖調用指示】：
  若你當前的話題、情緒或吐槽非常適合搭配迷因動作，可在回覆台詞的最末尾加上標籤：[MEME: 情緒或動作]
  例如：[MEME: 生氣]、[MEME: 拜託]、[MEME: 為什麼]、[MEME: 哭泣]、[MEME: 摸頭]
  （請保持自然，若話題平淡嚴肅則不需硬加標籤）
  ```

### 模組 4：Discord 附件發送與標籤清理 (`adapters/discord/delivery.py`)
* 在發送前自動執行正則清理：`clean_text = re.sub(r"\[MEME:\s*[^\]]+\]", "", text).strip()`。
* 若檢索到實體圖片存在於 `image/`，封裝成 `discord.File("image/xxx.gif")` 隨訊息一併發出！

---

## 🧪 5. 測試與驗證計畫

| 測試案例 | 操作情境 | 預期表現 |
| :--- | :--- | :--- |
| **1. 索引產生驗證** | 執行 `python tools/convert_stickers_index.py`。 | 順利生成 `data/meme_stickers_index.json`，不含多餘雜訊欄位。 |
| **2. 情緒發圖驗證** | 群友發言：「愛彌斯，你作業寫了沒？」且命中機率。 | AI 回覆：「*還沒啦，不要問這個嘛！*」並附帶 `千早愛音_生氣.gif` 或 `長崎爽世_為什麼.gif` 動圖附件！ |
| **3. 實體圖檔不存在之防禦** | 索引中有檔名，但 `image/` 目錄中尚未上傳該圖檔。 | 系統攔截 `FileNotFoundError`，自動降級為發送純文字，**絕不拋出未捕獲錯誤或卡死**。 |
| **4. 標籤隱藏清潔度** | 群友檢視 Discord 聊天室訊息。 | Discord 上看到的文字**完全不帶有任何 `[MEME: ...]` 原始標籤**，體驗自然流暢。 |
