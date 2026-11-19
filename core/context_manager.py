import re
import logging
from typing import List, Optional, Tuple, Dict, Any
import datetime

logger = logging.getLogger("core.context_manager")

IMAGE_INTENT_KEYWORDS = (
    "圖", "照片", "照", "梗圖", "迷因", "meme", "截圖",
    "上面", "剛才", "剛剛", "剛發", "這張", "看這", "看那",
    "畫", "image", "pic", "photo", "screenshot", "draw", "look", "see"
)


class ContextManager:
    """Manages relative temporal awareness tags, silence gap dividers, and intent parsing for conversations."""

    @staticmethod
    def has_image_intent(text: str) -> bool:
        """Check if user's text contains keywords indicating intent to inspect/reference an image."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in IMAGE_INTENT_KEYWORDS)

    @staticmethod
    def format_relative_time(diff_seconds: float) -> str:
        """Convert seconds difference into human/AI friendly relative time tag."""
        if diff_seconds < 60:
            return "[剛才]"
        elif diff_seconds < 3600:
            return f"[{int(diff_seconds // 60)}分鐘前]"
        elif diff_seconds < 86400:
            hours = diff_seconds / 3600
            return f"[{int(hours) if hours.is_integer() else f'{hours:.1f}'}小時前]"
        else:
            days = int(diff_seconds // 86400)
            return f"[{days}天前]"

    @staticmethod
    def format_gap_duration(gap_seconds: float) -> str:
        """Format silence gap duration between consecutive messages."""
        if gap_seconds < 3600:
            return f"{int(gap_seconds // 60)} 分鐘"
        elif gap_seconds < 86400:
            return f"{int(gap_seconds // 3600)} 小時"
        else:
            return f"{int(gap_seconds // 86400)} 天"

    def build_ambient_context_string(self, message_items: List[Dict[str, Any]]) -> str:
        """
        Build formatted ambient context string with temporal tags and silence gap dividers from chronological message items.
        Expected item keys: 'author', 'text', 'created_at', 'has_image' (optional)
        """
        if not message_items:
            return ""

        now = datetime.datetime.now(datetime.timezone.utc)
        recent_lines = []
        last_msg_time = None

        for item in message_items:
            created_at = item.get("created_at")
            if created_at:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.timezone.utc)

                # Check silence gap
                if last_msg_time is not None:
                    gap = (created_at - last_msg_time).total_seconds()
                    if gap >= 1200:  # 20 minutes
                        recent_lines.append(f"--- ⏳ (此處頻道沉寂了 {self.format_gap_duration(gap)}) ---")
                last_msg_time = created_at

                diff_seconds = max(0.0, (now - created_at).total_seconds())
                time_tag = f"{self.format_relative_time(diff_seconds)} "
            else:
                time_tag = ""

            author = item.get("author", "群友")
            text = item.get("text", "")
            img_tag = "(附圖) " if item.get("has_image") else ""

            if text:
                recent_lines.append(f"{time_tag}[{author}]: {img_tag}{text}")
            elif img_tag:
                recent_lines.append(f"{time_tag}[{author}]: {img_tag}（發送了圖片）")

        if not recent_lines:
            return ""

        context_str = "\n".join(recent_lines)
        return f"【聊天室近況記錄】\n{context_str}\n---\n"
