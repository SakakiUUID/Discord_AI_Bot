# 喚醒詞偵測前濾除網址與裸網域實作計畫 (Approach A)

> **建立日期**：2026-11-19  
> **對應問題**：群組訊息中只要包含 `claude.ai`（或 `https://claude.ai/...`、`chat.deepseek.com` 等含角色英文別名的網址），就會觸發 `has_wake_word` 讓 AI 誤插話回覆。  
> **規劃範圍**：僅實作 **作法 A（濾除 URL / 裸網域 / Email 後再進行喚醒詞比對）**；不動現有詞彙邊界或縮減既有角色別名清單。

---

## 1. 現況與問題根因

目前文字喚醒詞的偵測位於 `cogs/ai_roleplay.py` 與 `core/persona_service.py`：

1. **別名定義（`core/persona_service.py` `get_active_persona_aliases()`）**
   * `claude` 角色包含 `"claude"`、`dafeiyu` 角色包含 `"deepseek"`、`default` 包含 `"airi"` 等英文關鍵字。
2. **比對邏輯（`cogs/ai_roleplay.py` `on_message()`）**
   ```python
   stripped_content = resolve_discord_mentions(message, strip_user_id=bot_id)
   active_aliases = self.persona_manager.get_active_persona_aliases()
   has_wake_word = any(alias.lower() in stripped_content.lower() for alias in active_aliases if alias)
   ```
   * 直接對 `stripped_content` 做無條件子字串搜尋（`in`），導致任何包含 `claude.ai` 或 `https://claude.ai/...` 的訊息都命中 `"claude"` 並視為 `is_explicit_trigger = True`。

---

## 2. 設計原則

1. **只淨化「喚醒詞比對用文字」，絕不竄改訊息原文**：
   * 寫入長期記憶緩衝的 `memory_text`、傳入 LLM 的 `prompt_content`，以及向量檢索的 `query_text`（`stripped_content`）都**必須保留完整網址**。
   * 確保使用者在「邊喚醒 AI 邊貼網址（例如：`大小姐幫我看 https://claude.ai/...`）」時，AI 依然看得到完整連結。
2. **集中於純 Python 邏輯層，不綁定 Discord 物件**：
   * 將網址濾除與喚醒詞比對抽為 `PersonaService` 的靜態／實例方法，與 `get_active_persona_aliases()` 放在同一模組維持高內聚，也讓現有純單元測試（`scratch/test_wake_word.py`）可直接覆蓋驗證。
3. **兼顧中文環境下的裸網域貼上習慣**：
   * 除了標準 `https://...`、`http://...`、`www....`，也要處理群友直接打 `claude.ai`、`這個claude.ai好用嗎`（中文無空白黏連），同時避免誤傷小數（如 `3.8`）或中文句點標點。

---

## 3. 詳細實作設計

### 3.1 `core/persona_service.py`：新增網址濾除與喚醒詞判定輔助方法

在 `PersonaService`（或模組頂層編譯常數正則 + `PersonaService` 方法）新增兩個小而單一職責的函式：

#### A. 正則規則設計（依優先序套用並替換為空白 `" "`）
1. **標準網址（含協議頭或 `www.`）**：
   * Pattern：`r"(?i)(?:https?://|www\.)[^\s<>()\[\]\"']+"`
   * 涵蓋場景：
     * `https://claude.ai/chat/123?foo=bar`
     * Discord 關閉預覽的尖括號網址 `<https://claude.ai/new>`（在 `>` 停止，網址本體完全清掉）
     * Markdown 連結的 URL 部分 `[參考連結](https://claude.ai/new)`（在 `)` 停止，保留顯示文字 `[參考連結]`）
2. **Email 位址（防連帶誤傷）**：
   * Pattern：`r"(?i)[a-z0-9._%+-]+@(?:[a-z0-9-]+\.)+[a-z]{2,}"`
   * 防止 `claude@example.com` 或 `support@claude.ai` 誤觸發。
3. **裸網域／無協議頭網址（如 `claude.ai`、`claude.ai/chat`）**：
   * Pattern 概念：
     ```python
     _COMMON_TLDS = (
         "com|org|net|edu|gov|io|ai|app|dev|co|me|tv|gg|cc|xyz|info|biz|"
         "tech|cloud|site|online|club|shop|blog|wiki|tw|jp|kr|cn|uk|de|fr|"
         "us|ca|au|hk|sg|ly|to|sh|fm"
     )
     _BARE_DOMAIN_RE = re.compile(
         rf"(?<![a-zA-Z0-9._/@-])(?:[a-zA-Z0-9-]+\.)+(?:{_COMMON_TLDS})(?![a-zA-Z0-9])(?:/[^\s<>()\[\]\"']*)?",
         re.IGNORECASE,
     )
     ```
   * **邊界關鍵說明**：
     * 開頭不用 `\b`，改用 `(?<![a-zA-Z0-9._/@-])`：因為在 Python Unicode 正則中中文字屬於 `\w`，若用 `\b`，群友打 `這個claude.ai好用嗎` 會匹配不到開頭；改用 ASCII 負向先行/回溯斷言後，中文緊貼網域仍可精準移除。
     * 結尾使用 `(?![a-zA-Z0-9])` + TLD 白名單：可避免 `3.8`、`v1.0` 或打字多了一個句點 `claude.怎麼了` 被誤當成網域刪除。
     * 若含路徑如 `claude.ai/share/xyz`，後面可選 `(?:/[^\s<>()\[\]\"']*)?` 會一併清除整段路徑。

#### B. 新增方法介面
```python
@staticmethod
def strip_urls_for_wake_word(text: str) -> str:
    """Remove full URLs, emails, and bare domains before wake-word matching."""
    if not text:
        return ""
    cleaned = _FULL_URL_RE.sub(" ", text)
    cleaned = _EMAIL_RE.sub(" ", cleaned)
    cleaned = _BARE_DOMAIN_RE.sub(" ", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()

def has_wake_word(self, text: str, aliases: Optional[List[str]] = None) -> bool:
    """Check whether `text` contains any active persona wake-word alias after stripping URLs."""
    wake_text = self.strip_urls_for_wake_word(text).lower()
    if not wake_text:
        return False
    target_aliases = self.get_active_persona_aliases() if aliases is None else aliases
    return any(alias.lower() in wake_text for alias in target_aliases if alias)
```
* **相容性**：`PersonaManager` 繼承自 `PersonaService`，因此 `self.persona_manager.has_wake_word(stripped_content)` 可直接呼叫，無需在 `PersonaManager` 重複實作。

---

### 3.2 `cogs/ai_roleplay.py`：替換喚醒詞檢查呼叫點

在 `on_message()`（約第 556–560 行）將原本的直接子字串判斷改為呼叫 `has_wake_word()`：

```python
# Strip @Bot itself solely for wake-word detection and clean vector retrieval query_text
stripped_content = resolve_discord_mentions(message, strip_user_id=bot_id)
has_wake_word = self.persona_manager.has_wake_word(stripped_content)
is_dm = isinstance(message.channel, discord.DMChannel)
```

* `stripped_content` 本身不變，後續 `query_text=stripped_content` 與 `prompt_content=memory_text` 仍保有完整原始網址。
* Peer-Bot 互動流程同樣共用 `has_wake_word` 判斷，因此若同伴 Bot 在訊息裡附上 `claude.ai` 連結，也不會再誤判成在叫 Claude。

---

## 4. 行為預期對照與既有邊界說明

### 4.1 預期結果範例（以啟用 `persona_claude.txt` 為例）

| 使用者訊息內容 | 濾除網址後用於比對的字串 | 是否觸發 | 說明 |
| :--- | :--- | :---: | :--- |
| `https://claude.ai/chat/123` | `""` | ❌ 否 | 完整 URL 已移除 |
| `<https://claude.ai/new>` | `<>` | ❌ 否 | Discord 關閉預覽格式一樣不觸發 |
| `推薦用 claude.ai 寫文章` | `推薦用 寫文章` | ❌ 否 | 裸網域已移除 |
| `這個claude.ai好用嗎` | `這個 好用嗎` | ❌ 否 | 中文與裸網域相連仍能正確識別並移除 |
| `[這頁](https://claude.ai/chat)` | `[這頁]()` | ❌ 否 | Markdown URL 部分已移除 |
| `大小姐幫我看 https://claude.ai/new` | `大小姐幫我看` | ✅ 是 | 留有「大小姐」喚醒詞，且 AI 原文仍看得到網址 |
| `claude 幫我看這頁 https://example.com` | `claude 幫我看這頁` | ✅ 是 | 正常呼喚 + 貼其他網址不受影響 |

### 4.2 本方案（作法 A）的合理邊界
* 若訊息沒有網址、純粹文字寫到 `我今天用 Claude 寫程式`，或 Markdown 顯示文字本身寫 `[Claude 官網](https://claude.ai)`，因「作法 A」僅針對網址進行濾除，這類純文本上的字眼仍會命中 `"claude"`（符合預期；若日後需連這類純討論也一併抑制，可再評估調整英文單字喚醒詞或結合詞彙邊界與上下文判斷）。

---

## 5. 檔案影響清單

| 檔案 | 預計變更 |
| :--- | :--- |
| `core/persona_service.py` | 編譯 `_FULL_URL_RE` / `_EMAIL_RE` / `_BARE_DOMAIN_RE`，新增 `strip_urls_for_wake_word()` 與 `has_wake_word()` |
| `cogs/ai_roleplay.py` | `on_message()` 改呼叫 `self.persona_manager.has_wake_word(stripped_content)` |
| `scratch/test_wake_word.py` | 補齊 `persona_claude.txt` / `persona_dafeiyu.txt` 與各類 URL / 裸網域 / 混合喚醒測試案例 |

---

## 6. 驗證計畫

1. **擴充並執行單元測試 `scratch/test_wake_word.py`**：
   * 覆蓋原有五大角色別名與隔離測試，加上 `claude` 與 `dafeiyu`（`deepseek`）別名測試。
   * 測試上述第 4.1 節所有 URL、尖括號 `<...>`、Markdown 連結、裸網域（含中文黏連）、Email、與「喚醒詞 + URL 並存」案例。
   * 確認 `strip_urls_for_wake_word()` 不會誤刪 `3.8`、`claude.怎麼辦` 等非網域字串。
2. **整合檢查（Docker / 實際執行確認）**：
   * 在 Discord 頻道實際貼出 `https://claude.ai/...` 與 `claude.ai`，確認機器人不主動插話回覆，但仍正常寫入 `pending_messages` 與熱聊上下文（若正處於 Mode A 視窗則改由 `ProactiveJudge` 依脈絡裁決）。
   * 確認「`大小姐幫我看 https://claude.ai/...`」能正常觸發並讓 AI 回應原文中的網址資訊。
