# 🔍 Google 即時聯網搜尋擴充功能計畫書 (Google Search Grounding Extension)

> **專案定位**：AI 角色即時聯網資訊檢索能力擴充 (Web Search Grounding Engine)  
> **目標模組**：[`core/dialog_service.py`](file:///d:/OneDrive/tools/dc_auto_bot/core/dialog_service.py)、[`config.py`](file:///d:/OneDrive/tools/dc_auto_bot/config.py)  
> **核心目標**：為 Bot 接入官方 Google Search Grounding 工具。使 AI 角色在面對即時新聞、近期發布之遊戲攻略/爆料、賽事比分或最新時事問答時，能**自主決定何時聯網查證 Google**，並在查得最新正確資訊後**維持原汁原味的角色口吻與人設**自然回覆群友。  
> **文件用途**：供後續對話或開發者依循此規格進行功能實作與測試。

---

## 📌 1. 功能運作機制與特點

```mermaid
flowchart TD
    UserMsg[群友訊息<br>例: 鳴潮最新版本何時更新? / 今天的重大新聞是什麼?] --> Core[DialogService 接收請求]
    
    Core --> LLM[Gemini 3.8 / 3.7 Flash<br>掛載 tools: google_search]
    
    LLM --> Intent{AI 內部意圖判斷}
    
    Intent -->|日常問候 / 情感交流 / 既有知識| Direct[不聯網，直接以角色人設生成回覆]
    Intent -->|需要即時事實 / 最新時事 / 查證| Search[🌐 調用 Google Search 工具檢索網路]
    
    Search --> Grounding[Google 回傳即時網頁摘要與 Grounding 資訊]
    Grounding --> Personafy[🎭 消化吸收最新事實<br>轉換為角色說話風格 (愛彌斯/莫寧/愛莉...)]
    
    Direct --> Output[💬 發送最終回覆到頻道]
    Personafy --> Output
```

### 🌟 核心特性：
1. **動態自主觸發（Zero-Friction Autonomous Trigger）**：
   * 平常打招呼「早安」、「你好」或閒聊角色人設話題時，AI **完全不會盲目浪費搜尋**。
   * 只有在遇到時間敏感型問題（如「今天天氣」、「最新遊戲公告」、「即時新聞」）時，模型自動發起 Google Search 請求。
2. **角色人格不崩塌（Strict Persona Consistency）**：
   * 即使查閱了枯燥的網路新聞或技術報告，AI 依然會嚴格遵循 System Prompt 中的角色性格、口吻與口頭禪（例如愛彌斯的元氣俏皮「漂泊者！」、莫寧的嚴肅研究員風格），**絕不變成冷冰冰的維基百科**。
3. **靈活開關與環境變數控制**：
   * 支援在 `config.py` 與 `.env` 透過 `ENABLE_GOOGLE_SEARCH=true/false` 隨時全域開啟或關閉。

---

## 🛠️ 2. 代碼修改規格 (Exact Code Modifications)

### 檔案 1：[`config.py`](file:///d:/OneDrive/tools/dc_auto_bot/config.py)
新增聯網搜尋功能開關：

```diff
 # Meme Sticker Delivery Configuration (0.0 to 1.0, default 0.4 = 40% chance)
 STICKER_TRIGGER_PROBABILITY = float(os.getenv("STICKER_TRIGGER_PROBABILITY", "0.4"))
 
+# Google Search Grounding Configuration (Default: True)
+ENABLE_GOOGLE_SEARCH = os.getenv("ENABLE_GOOGLE_SEARCH", "true").lower() in ("true", "1", "yes")
```

---

### 檔案 2：[`core/dialog_service.py`](file:///d:/OneDrive/tools/dc_auto_bot/core/dialog_service.py)

#### 2.1. 調整 `_get_genai_config` 加入 Google 搜尋工具
利用 Google 官方 `google-genai` SDK 的 `types.Tool(google_search=types.GoogleSearch())`：

```diff
-    def _get_genai_config(self, persona: str, memory_context: str = "", allow_meme: bool = False) -> types.GenerateContentConfig:
+    def _get_genai_config(self, persona: str, memory_context: str = "", allow_meme: bool = False, enable_search: bool = True) -> types.GenerateContentConfig:
         """Assemble System Instruction combining character persona prompt, memory context, and optional meme instruction."""
         combined_instruction = persona
         if memory_context:
             combined_instruction += f"\n\n{memory_context}"
         if allow_meme:
             combined_instruction += (
                 "\n\n【迷因貼圖調用指示】：\n"
                 "若你當前的語境、吐槽或情緒非常適合搭配迷因動作，可在回覆台詞的最末尾加上標籤：[MEME: 情緒或動作]\n"
                 "例如：[MEME: 生氣]、[MEME: 拜託]、[MEME: 為什麼]、[MEME: 哭泣]、[MEME: 摸頭]、[MEME: 得意]、[MEME: 操你們媽]、[MEME: 比讚]、[MEME: 問號]\n"
                 "（請保持自然，若話題平淡嚴肅則不需硬加標籤）"
             )
 
+        # 當啟用聯網搜尋時，提醒 AI 在查得最新資訊後務必使用角色人設表達
+        tools = []
+        if enable_search and config.ENABLE_GOOGLE_SEARCH:
+            tools.append(types.Tool(google_search=types.GoogleSearch()))
+            combined_instruction += (
+                "\n\n【聯網搜尋與時效指示】：\n"
+                "你擁有即時聯網檢索最新資訊的能力。當對話涉及最新時事、近期活動日程、最新遊戲更新或即時事實時，請主動透過搜尋獲取精確事實。"
+                "檢索完畢後，請務必將資訊消化並轉化為你的角色第一人稱口吻來向對話者說明，切勿機械式照抄搜尋結果！"
+            )
+
         return types.GenerateContentConfig(
             system_instruction=combined_instruction,
+            tools=tools if tools else None,
             temperature=0.75,
             top_p=0.9,
             max_output_tokens=2048,
         )
```

#### 2.2. 在 `generate_response` 中傳遞 `enable_search`
```diff
         try:
             response = await asyncio.to_thread(
                 self.ai_client.models.generate_content,
                 model=config.GEMINI_MODEL,
                 contents=history,
-                config=self._get_genai_config(persona=persona, memory_context=memory_context, allow_meme=allow_meme)
+                config=self._get_genai_config(
+                    persona=persona,
+                    memory_context=memory_context,
+                    allow_meme=allow_meme,
+                    enable_search=True
+                )
             )
```

---

## 🧪 3. 測試與驗證計畫

| 測試案例 | 輸入訊息情境 | 預期表現 |
| :--- | :--- | :--- |
| **1. 實時新聞與時事查證** | 群友發問：「今天台灣有什麼重大時事新聞？」或「最近剛出的熱門遊戲有哪些？」 | AI 自動觸發 Google Search，搜尋今日最新新聞，並以角色口吻整理 2~3 點重點回覆。 |
| **2. 最新遊戲版本爆料/攻略** | 群友發問：「鳴潮 2.2 / 2.3 版本有公布什麼新角色嗎？」 | AI 聯網查詢庫洛官方社群與媒體最新公告，準確回答最新公布的角色資訊，杜絕舊模型知識幻覺。 |
| **3. 人設口吻維持檢驗** | 愛彌斯或莫寧回答搜尋結果時。 | 愛彌斯稱呼對方「漂泊者」並帶元氣風格；莫寧維持「黑石研究員」的嚴謹邏輯，**資訊準確且角色不崩塌**。 |
| **4. 一般閒聊不聯網檢驗** | 群友發問：「你今天吃了沒？」或「我想聽你講個笑話」。 | 系統內部評估無需搜尋，**0 額外搜尋延遲，秒回閒聊**。 |
| **5. 功能開關驗證** | 在 `.env` 設定 `ENABLE_GOOGLE_SEARCH=false`。 | 機器人完全關閉搜尋工具，回退至純離線模型推論模式。 |
