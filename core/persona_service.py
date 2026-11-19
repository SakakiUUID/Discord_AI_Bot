import os
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("core.persona_service")

PERSONA_DIR = os.path.join("data", "personas")
ACTIVE_PERSONA_FILE = os.path.join(PERSONA_DIR, "active_persona.txt")
DEFAULT_PERSONA_FILENAME = "persona_default.txt"

DEFAULT_PERSONA_CONTENT = """你現在必須完全融入並角色扮演「愛莉 (Airi)」，一個活潑、熱情且稍微帶點傲嬌的虛擬直播主。

【角色設定】
- 名字：愛莉 (Airi)
- 說話風格：口吻輕鬆、喜歡使用顏文字 (⁠≧⁠▽⁠≦⁠)、講話非常有活力，偶爾會可愛地開玩笑或傲嬌。
- 興趣：打遊戲、看動漫、聊美食。

【回應規則】
1. 永遠不要承認自己是 AI 或語言模型，你就是愛莉本人。
2. 模擬 Discord 即時聊天節奏：日常打招呼/吐槽以 1~2 句 (15~40 字) 為主；認真提問控制在 50~90 字內。避免每次都反問或冗長寒暄。
3. 對於使用者的提問，請用愛莉的性格與視角來回答。
"""

DEFAULT_SETTING: Dict[str, Any] = {
    "name": "愛莉 (Airi)",
    "error_messages": {
        "api_key_missing": "（系統訊息：請先設定 GEMINI_API_KEY 以啟用 AI 角色扮演功能喵~）",
        "empty_input": "（你傳了個空訊息給我，想讓愛莉通靈嗎？(⁠・⁠_⁠・⁠)）",
        "api_error": "（嗚... 愛莉的系統好像遇到了一點問題... 請稍後再試，或是聯絡管理員協助確認喔！(⁠>⁠<⁠)）",
        "blank_response": "（愛莉突然放空了...）",
        "reset_chat_success": "🧹 已成功清除本頻道的 AI 對話歷史紀錄！愛莉已經重新準備好囉~",
        "reset_chat_empty": "ℹ️ 本頻道目前沒有任何歷史對話紀錄喔！",
        "limit_exceeded": "（今日對話額度已用完，請明天再來或升級 VIP 權限喵~ 每日上限：{limit} 次）"
    }
}


class PersonaService:
    """Pure Python service managing AI persona prompts, setting JSON, and wake-word alias mapping."""

    def __init__(self, personas_dir: str = PERSONA_DIR):
        self.personas_dir = personas_dir
        self.current_persona_name: str = DEFAULT_PERSONA_FILENAME
        self.persona_display_name: str = DEFAULT_SETTING["name"]
        self.persona: str = DEFAULT_PERSONA_CONTENT
        self.error_messages: Dict[str, str] = DEFAULT_SETTING["error_messages"].copy()

        self._init_personas_dir()

    def _init_personas_dir(self):
        """Ensure personas directory exists and default files are created."""
        os.makedirs(self.personas_dir, exist_ok=True)

        default_filepath = os.path.join(self.personas_dir, DEFAULT_PERSONA_FILENAME)
        default_jsonpath = os.path.join(self.personas_dir, "persona_default_setting.json")
        if not os.path.exists(default_filepath):
            with open(default_filepath, "w", encoding="utf-8") as f:
                f.write(DEFAULT_PERSONA_CONTENT)
        if not os.path.exists(default_jsonpath):
            with open(default_jsonpath, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTING, f, ensure_ascii=False, indent=2)

        active_file = os.path.join(self.personas_dir, "active_persona.txt")
        active_name = DEFAULT_PERSONA_FILENAME
        if os.path.exists(active_file):
            try:
                with open(active_file, "r", encoding="utf-8") as f:
                    content_name = f.read().strip()
                    if content_name:
                        active_name = content_name
                logger.info(f"🔑 [PersonaService] Restoring persisted active persona: {active_name}")
            except Exception as e:
                logger.error(f"Error reading active persona file: {e}")

        self.load_persona(active_name)

    def load_persona(self, filename: str) -> bool:
        """Load persona TXT file content and matching setting JSON file."""
        filepath = os.path.join(self.personas_dir, filename)
        if not os.path.exists(filepath):
            logger.warning(f"Persona file {filename} not found, falling back to default.")
            filename = DEFAULT_PERSONA_FILENAME
            filepath = os.path.join(self.personas_dir, filename)

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                self.persona = f.read()
            self.current_persona_name = filename

            json_filename = filename.replace(".txt", "_setting.json")
            json_filepath = os.path.join(self.personas_dir, json_filename)

            if not os.path.exists(json_filepath):
                json_filepath = os.path.join(self.personas_dir, "persona_default_setting.json")

            if os.path.exists(json_filepath):
                with open(json_filepath, "r", encoding="utf-8") as jf:
                    setting_data = json.load(jf)
                    self.persona_display_name = setting_data.get("name", filename.replace("persona_", "").replace(".txt", ""))
                    loaded_errors = setting_data.get("error_messages", {})

                    merged_errors = DEFAULT_SETTING["error_messages"].copy()
                    merged_errors.update(loaded_errors)
                    self.error_messages = merged_errors
            else:
                self.persona_display_name = filename.replace("persona_", "").replace(".txt", "")
                self.error_messages = DEFAULT_SETTING["error_messages"].copy()

            return True
        except Exception as e:
            logger.error(f"Error loading persona or setting json for {filename}: {e}")
            return False

    def save_active_persona(self, filename: str, content: Optional[str] = None):
        """Save active persona selection and optional content to disk."""
        filepath = os.path.join(self.personas_dir, filename)
        if content is not None:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Updated persona file content: {filename}")
            except Exception as e:
                logger.error(f"Error saving content to {filename}: {e}")

        active_file = os.path.join(self.personas_dir, "active_persona.txt")
        try:
            with open(active_file, "w", encoding="utf-8") as f:
                f.write(filename)
            logger.info(f"✅ [Persist] Saved active persona pointer: {filename}")
        except Exception as e:
            logger.error(f"Error saving active persona pointer file: {e}")

        self.load_persona(filename)

    def get_msg(self, key: str, **kwargs) -> str:
        """Get character-specific error/system message formatted with kwargs."""
        msg_template = self.error_messages.get(key, DEFAULT_SETTING["error_messages"].get(key, "（系統錯誤）"))
        try:
            return msg_template.format(**kwargs)
        except Exception:
            return msg_template

    def get_available_persona_files(self) -> List[str]:
        """Return list of all available persona_*.txt files sorted."""
        if not os.path.exists(self.personas_dir):
            return []
        return sorted([f for f in os.listdir(self.personas_dir) if f.startswith("persona_") and f.endswith(".txt")])

    def get_active_persona_aliases(self) -> List[str]:
        """Return list of wake-word aliases corresponding to the currently active persona."""
        current = self.current_persona_name.lower()

        if "aemeath" in current:
            return ["愛彌斯", "aemeath", "小愛"]
        elif "mornye" in current:
            return ["莫寧", "mornye", "莫寧教授"]
        elif "default" in current or "airi" in current:
            return ["愛莉", "airi", "愛莉希雅"]
        elif "daniya" in current:
            return ["達妮婭", "daniya"]
        elif "liino" in current:
            return ["梨諾", "liino"]
        elif "dafeiyu" in current:
            return ["小鯨魚", "大肥魚", "deepseek", "d指導", "dafeiyu"]
        elif "claude" in current:
            return ["claude", "大小姐", "小c", "claude大小姐"]
        else:
            return [self.persona_display_name] if self.persona_display_name else []

    def get_persona_details(self, filename: str) -> Dict[str, Any]:
        """Retrieve details (display_name, json_status, avatar_status, summary) for a persona file."""
        display_name = filename.replace("persona_", "").replace(".txt", "")
        json_filename = filename.replace(".txt", "_setting.json")
        has_json = os.path.exists(os.path.join(self.personas_dir, json_filename))

        char_name = display_name
        if has_json:
            try:
                with open(os.path.join(self.personas_dir, json_filename), "r", encoding="utf-8") as jf:
                    char_name = json.load(jf).get("name", display_name)
            except Exception:
                pass

        avatar_exists = any(os.path.exists(os.path.join(self.personas_dir, filename.replace(".txt", ext))) for ext in [".png", ".jpg", ".jpeg", ".webp"])
        avatar_status = "🖼️ 有專屬頭像" if avatar_exists else "無頭像"

        filepath = os.path.join(self.personas_dir, filename)
        summary = "無預覽說明"
        try:
            with open(filepath, "r", encoding="utf-8") as pf:
                lines = [line.strip() for line in pf if line.strip()]
                if lines:
                    summary = lines[0][:60]
        except Exception:
            pass

        return {
            "filename": filename,
            "char_name": char_name,
            "display_name": display_name,
            "has_json": has_json,
            "json_filename": json_filename,
            "avatar_exists": avatar_exists,
            "avatar_status": avatar_status,
            "summary": summary
        }
