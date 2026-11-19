# 🤖 Discord AI Auto Bot (Gemini 3.8 Flash + 向量長期記憶 + YouTube 即時通知)

一個基於 Python `discord.py`、Google `google-genai` SDK 與 `FastAPI` 打造的多功能 Discord 機器人（目前採用 **Gemini 3.8 Flash** 模型）。

本機器人具備 **AI 角色扮演對話（多角色切換 / 喚醒詞與免 `@` 主動接話視窗 / 雙 Bot 衰減互動 / Mention 名稱解析 / 多模態圖片辨識 / Google Search 聯網搜尋 / SQLite + NumPy 輕量向量混合記憶 / 動態聊天室上下文）**、**YouTube 影片即時推送通知（WebSub Webhook + RSS 雙軌機制）**，以及 **獨立 Bot 管理層與 VIP 限額白名單系統**。

---

## 🌟 核心功能特色

### 🎭 1. Gemini AI 角色扮演對話系統
* **多種自然互動觸發方式**：支援 **`@Bot` 提及**、**直接回覆 (Reply) Bot 訊息**、**喚醒關鍵字（依目前啟用角色的名字或暱稱）**、**私訊 (DM)**，以及 **`/chat` 斜線指令**。
* **Mode A 免 `@` 熱聊延續視窗 (`ProactiveJudge`)**：
  * 當使用者明確觸發 AI 回覆後，該頻道會開啟熱聊延續視窗（預設 **180 秒**，最多追蹤 **8 位**活躍參與者），經過 **2.5 秒**防抖整併連發訊息後，由輕量 JSON 裁判依脈絡判斷 **`reply`（自然接話）**、**`react`（加上單一標準 Unicode Emoji）** 或 **`pass`（安靜不插嘴）**。
  * **多話題隔離與自動退場**：其他群友自然插話加入後會自動納入追蹤；活躍者累積 **5 次** `pass` 即移出名單，未參與該話題的旁觀者累積 **2 次** `pass` 則於該輪暫時忽略；當活躍名單清空或逾時即關閉視窗（未具剩餘對話額度者亦不會觸發裁判）。
* **雙機器人自然衰減互動 (`PEER_BOT_IDS`)**：可設定同伴機器人 ID，在頻道內與同伴 Bot 互動時雙方發言皆納入記憶，並依連續對話回合套用機率衰減（預設 `100% -> 50% -> 30% -> 10% -> 0%`，逾 **600 秒**重置）與 **2.0 ~ 4.5 秒**隨機延遲避免無限互聊；未列入白名單的第三方系統機器人則維持忽略。
* **多角色動態切換**：支援自訂多組 `persona_*.txt` 人設 Prompt 與對應的 `persona_*_setting.json` 設定檔，可透過 `/chg_persona` 下拉選單一鍵切換並持久化保存（同時清除舊對話歷史與熱聊視窗狀態）。
* **頭像與暱稱同步變更**：啟動時與切換角色時，機器人會自動將各 Discord 伺服器暱稱與全域頭像（支援 `PNG`、`JPG`、`WEBP`）同步更新為該角色。
* **Discord `@Mention` 名稱解析與成員快取**：透過 `resolve_discord_mentions()` 將訊息與歷史紀錄中的 `<@user_id>`、`<@&role_id>`、`<#channel_id>` 轉換為可讀的 `@顯示名稱` 與 `#頻道名稱`，同時將 `@Bot` 本身對齊至目前啟用的人設名稱（`persona_display_name`），並搭配 `intents.members = True` 快取伺服器成員當下暱稱。
* **🧠 混合式長期記憶與輕量向量檢索 (`data/memory.db`)**：
  * **SQLite WAL + NumPy 混合架構**：捨棄容易覆寫遺失的舊式 JSON，整合 **L1 短期頻道對話**、**L2 常駐記憶（群體文化 / 內部梗動態汰換 + 個人印象）** 與 **L3 向量事件記憶 (`memory_facts`)**。
  * **依 Persona 隔離 + 空間安全分區**：不同角色各自擁有獨立的人物印象與事件回憶；私訊改以個別使用者 ID 隔離，群組具體事件記憶不跨伺服器外洩，而個人常駐印象則可在同角色下跨群延續。
  * **雙軌背景萃取與寫入去重**：人類與同伴 Bot 訊息即時寫入 `pending_messages` 緩衝，累積滿 **30 則** 或 **閒置 15 分鐘（且 ≥ 8 則）** 後自動背景萃取，透過 Gemini Embedding 計算相似度並於寫入前自動去重合併。
* **聊天室動態上下文 (`ContextBuilder`)**：觸發回覆時即時抓取頻道近 **30 則**聊天紀錄（附帶相對時間標籤與 `>= 20 分鐘` 沉寂分隔線），與向量檢索透過 `asyncio.gather` 平行執行。
* **多模態視覺辨識**：支援直接上傳圖片、回覆包含圖片的舊訊息，並依對話看圖意圖（`IMAGE_INTENT_KEYWORDS`）選擇性讀取頻道歷史附圖（上限 3 張）。
* **Google Search 聯網搜尋 (`ENABLE_GOOGLE_SEARCH`)**：預設啟用即時聯網檢索最新時事、日期與遊戲資訊，並轉化為角色第一人稱口吻回覆。
* **持續輸入中 (`KeepTyping`) 與長文自動切塊**：生成期間每 4.5 秒發送 `typing()` 心跳；Gemini 輸出上限為 2048 Tokens，超過 Discord 2000 字限制時自動拆分多則發送。

---

### 📺 2. YouTube 影片即時通知系統
* **WebSub (PubSubHubbub) + RSS 雙軌機制**：
  * **主推**：FastAPI 提供 Port 8080 Webhook 接收 YouTube 官方即時推送。
  * **備援**：背景自動每 3 分鐘輪詢 RSS Feed，確保通知零漏發。
* **頻道綁定與多頻道分發**：不同 Discord 文字頻道可訂閱不同的 YouTube 頻道，通知精準發送至設定的頻道。
* **SQLite 持久化儲存**：訂閱紀錄寫入 `data/subscriptions.db`，重啟或遷移不丟失。

---

### 👑 3. 獨立 Bot 管理層與 VIP 白名單限制
* **User ID 嚴格權限防護**：Bot 管理權限獨立於 Discord 伺服器管理員身分，僅限 `admin_user_ids` 白名單中的 Discord User ID 執行管理操作。
* **`/admin_panel` 互動式控制面板**：為 Bot 管理員提供專屬私人介面（`ephemeral=True`），透過按鈕與彈窗管理白名單及每日對話上限。
* **動態可配置對話上限**：非 VIP 成員預設每日對話上限為 10 次（每日重置），額度用盡時以角色語氣提示；VIP 成員享有無限次數對話。

---

## 📋 斜線指令清單 (Slash Commands)

### 🎭 AI 角色對話與記憶相關
| 斜線指令 | 權限限制 | 功能說明 |
| :--- | :--- | :--- |
| `/chat <prompt> [image]` | 全員 | 與目前的 AI 角色進行對話（支援選擇性上傳圖片，並納入記憶學習） |
| `/chg_persona [filename]` | Bot Admin | 切換 AI 角色設定檔（留空則彈出互動下拉選單） |
| `/list_personas` | 全員 | 列出目前可用的所有角色設定檔、描述與設定 JSON |
| `/view_memory` | 全員 | 檢視目前角色對本群組文化、您的個人印象，以及 Top 5 向量事件記憶 |
| `/forget_memory` | 全員 / Bot Admin | 依 `fact_id` 或 `keyword` 刪除事件記憶、清除個人記憶 (`clear_my_memory`)，或重置本空間記憶 (`reset_guild_memory`，限 Bot Admin) |
| `/reset_chat` | 全員 | 清除目前頻道的 AI 短期對話歷史紀錄 |

### 📺 YouTube 通知相關
| 斜線指令 | 權限限制 | 功能說明 |
| :--- | :--- | :--- |
| `/sub_yt <url>` | 伺服器管理頻道權限 (`manage_channels`) | 訂閱指定的 YouTube 頻道，將通知發送至目前頻道 |
| `/unsub_yt <yt_channel_name_or_id>` | 伺服器管理頻道權限 (`manage_channels`) | 移除目前 Discord 頻道的指定 YouTube 訂閱 |
| `/list_yt` | 全員 | 查看目前頻道訂閱的 YouTube 頻道清單 |

### 🎛️ 超級管理員與 VIP 相關
| 斜線指令 | 權限限制 | 功能說明 |
| :--- | :--- | :--- |
| `/admin_panel` | Bot Admin | 開啟 Bot 專屬私人互動控制面板 |
| `/vip_list` | 全員 | 查看 Bot 管理員名單、VIP 白名單與每日對話上限設定 |

> 💡 **提示**：Bot 管理員亦可在 Discord 頻道中輸入文字指令 `!sync` 來強制清除舊副本並重新同步全域斜線指令。

---

## 📂 專案目錄結構

```text
dc_auto_bot/
├── main.py                         # Bot & FastAPI 啟動入口 (併行運作與指令同步)
├── config.py                       # 環境變數與向量記憶/搜尋設定載入器
├── requirements.txt                # Python 依賴套件清單
├── Dockerfile                      # Docker 容器化部署設定 (python:3.11-slim)
├── docker-compose.yml              # Docker Compose 服務與 ./data 掛載設定
├── setup_gce.sh                    # GCP GCE 主機 Docker 環境初始化腳本
├── LICENSE                         # MIT 授權條款
├── README.md                       # 專案說明文件
│
├── core/                           # 核心業務邏輯層
│   ├── agent_engine.py             # 統一 Agent 編排核心
│   ├── context_manager.py          # 相對時間標籤、沉寂分隔線與看圖意圖判斷
│   ├── dialog_service.py           # Gemini 多輪歷史、Prompt 組裝與 Google Search Grounding
│   ├── memory_service.py           # 混合記憶門面 (雙軌萃取、並行向量召回、去重)
│   ├── persona_service.py          # 人設載入、設定 JSON 解析與喚醒詞對應
│   ├── proactive_judge.py          # Mode A 免 @ 主動接話輕量 JSON 裁判 (reply / react / pass)
│   ├── memory/
│   │   ├── models.py               # 記憶資料模型與 scope_key / persona_id 正規化工具
│   │   ├── store.py                # aiosqlite WAL 持久層與舊版 JSON 自動遷移
│   │   ├── embeddings.py           # Gemini Embedding 封裝與失敗安全降級
│   │   └── vector_index.py         # 依 (persona_id, scope_key) 分區的 NumPy L2 向量快取
│   └── models/
│       ├── incoming.py             # 標準輸入訊息模型
│       └── outgoing.py             # 標準輸出訊息模型
│
├── adapters/                       # 通道轉接器與訊息發送層
│   ├── base.py                     # 轉接器抽象基類
│   └── discord/
│       └── delivery.py             # Discord 長訊息切塊發送與殘留標籤清理
│
├── utils/                          # Discord Cog 介接與權限輔助模組
│   ├── context_builder.py          # Discord 近 30 則歷史脈絡、Mention 名稱解析與回覆圖片擷取
│   ├── gemini_dialog_service.py    # 平行執行環境上下文抓取與向量記憶檢索
│   ├── memory_manager.py           # MemoryService 相容子類別
│   ├── message_delivery.py         # DiscordDeliveryService 相容包裝
│   ├── persona_manager.py          # Bot 暱稱與全域頭像同步
│   └── vip_manager.py              # 獨立 Bot Admin 與 VIP User ID 白名單管理
│
├── cogs/                           # Discord Slash Commands 與事件監聽模組
│   ├── ai_roleplay.py              # AI 角色對話、Mode A 熱聊視窗、Peer-Bot 衰減與記憶指令
│   ├── admin.py                    # /admin_panel 與 /vip_list 控制面板
│   └── youtube.py                  # YouTube WebSub & RSS 推送通知 Cog
│
├── plans/                          # 功能規劃、重構分析與設計文件
│
└── data/                           # 持久化資料目錄 (執行期生成或本機保留，由 .gitignore 排除)
    ├── memory.db                   # SQLite 混合記憶與向量事實資料庫 (啟動後自動建立)
    ├── subscriptions.db            # YouTube 訂閱 SQLite 資料庫
    ├── vip_config.json             # VIP 與 Bot 管理員 User ID 白名單設定檔 (由 .gitignore 排除)
    ├── vip_usage.json              # 非 VIP 每日對話次數紀錄檔 (由 .gitignore 排除)
    └── personas/
        ├── active_persona.txt      # 當前生效的角色指標 (本機運行期產生)
        ├── persona_default.txt     # 預設原創角色 Prompt (愛莉 Airi)
        ├── persona_default_setting.json # 預設原創角色設定 JSON
        └── (可自行新增 persona_xxx.* 自訂角色與頭像，預設由 .gitignore 排除)
```

> *(註：若升級自舊版且 `data/` 內仍有 `guild_cultures.json` 或 `user_profiles.json`，首次啟動新系統時會自動遷移匯入 `data/memory.db`，並將原檔備份為 `.bak`。)*

---

## 🛠️ 環境設定與安裝步驟

### 1. 複製專案與安裝套件
```bash
git clone <your-repo-url>
cd dc_auto_bot

# 建立並啟用虛擬環境 (推薦)
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# 安裝所需套件
pip install -r requirements.txt
```

### 2. 設定 `.env` 環境變數檔
在專案根目錄建立 `.env` 檔案（完整選項請參考 [`.env.example`](.env.example)）：

```env
# Discord 機器人設定
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here

# Google Gemini API 與模型設定
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash
ENABLE_GOOGLE_SEARCH=true

# 向量記憶與 Embedding 設定
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
MEMORY_EMBEDDING_DIM=768
MEMORY_TOP_K=5
MEMORY_MIN_SIMILARITY=0.45
MEMORY_DEDUP_THRESHOLD=0.88
MEMORY_PARTITION_MAX_FACTS=500
MEMORY_ISOLATE_GUILD_BY_PERSONA=true
MEMORY_DB_PATH=data/memory.db

# 雙機器人衰減互動設定 (選填)
PEER_BOT_IDS=
PEER_BOT_REPLY_PROBS=1.0,0.5,0.3,0.1
PEER_BOT_STREAK_TIMEOUT=600
PEER_BOT_DELAY_MIN=2.0
PEER_BOT_DELAY_MAX=4.5

# Mode A 免 @ 主動熱聊延續視窗設定
PROACTIVE_HOT_WINDOW_ENABLED=true
PROACTIVE_HOT_WINDOW_SECONDS=180
PROACTIVE_HOT_MAX_CONSECUTIVE_PASS=5
PROACTIVE_BYSTANDER_MAX_PASS=2
PROACTIVE_HOT_MAX_ACTIVE_USERS=8
PROACTIVE_DEBOUNCE_SECONDS=2.5
PROACTIVE_CONTEXT_MAX_AGE_MINUTES=15

# Webhook 對外服務網址 (YouTube WebSub 接收用，留空則僅用 RSS 輪詢)
WEBHOOK_BASE_URL=
WEBHOOK_PORT=8080
WEBHOOK_SECRET=random_secret_string_for_websub
```

### 3. 開啟 Discord Developer Portal 權限 (重要 ⚠️)
1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)。
2. 點擊你的 Application ➡️ 左側選單選擇 **Bot**。
3. 往下滑找到 **Privileged Gateway Intents** 區塊。
4. 請將以下兩個開關切換為 **開啟 (ON / 藍色)** 並儲存：
   * **`MESSAGE CONTENT INTENT`**：讀取一般訊息內容進行關鍵字喚醒、記憶學習、上下文分析與免 `@` 熱聊判斷。
   * **`SERVER MEMBERS INTENT`**：快取伺服器成員資料，確保回溯頻道歷史與解析 `@Mention` 時能正確顯示成員當下伺服器暱稱。

---

## 🚀 啟動運行

### 本地直接啟動
```bash
python main.py
```
* 機器人啟動時會自動讀取 Bot Application Owner ID 作為第一位 Bot 超級管理員。
* 首次啟動或更新指令後，可在 Discord 輸入 `!sync`，並在 Discord 按 `Ctrl + R` 刷新介面。

### 使用 Docker / Docker Compose 部署（推薦）
```bash
# 使用 docker-compose 建置並背景啟動（自動掛載 ./data 保存 SQLite 與設定檔）
docker compose up -d --build
```
或使用原生 Docker 指令（請務必掛載 `-v ./data:/app/data` 以保留記憶與訂閱資料庫）：
```bash
docker build -t dc-auto-bot .
docker run -d --name dc_bot -p 8080:8080 --env-file .env -v ./data:/app/data dc-auto-bot
```

> 若要部署到 GCP Compute Engine (`e2-micro` / `e2-small`)，可參考 [`GCP_GCE_DEPLOY_TUTORIAL.md`](GCP_GCE_DEPLOY_TUTORIAL.md) 與 [`setup_gce.sh`](setup_gce.sh)。

---

## 📄 授權條款 (License)

本專案原始碼與文件採用 [MIT License](LICENSE) 授權。

> **授權範圍補充**：MIT License 僅適用於本專案原創程式碼與相關文件；使用者自行新增或任何第三方角色設定檔、文字描述與頭像圖片素材，其著作權與商標權均歸原權利人所有，不包含於本專案 MIT 授權範圍內。

---

## ⚠️ 免責聲明 (Disclaimer)

> **附註**：此程式由 AI 生成，使用前請自行評估與確認執行環境，對於任何因使用本程式造成的電腦損壞、資料遺失或衍生風險，概不負相關責任。
