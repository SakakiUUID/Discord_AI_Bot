from dataclasses import dataclass, field
from typing import Optional, Any
import datetime


@dataclass
class IncomingMessage:
    """Standardized incoming message data contract across all communication channels."""
    channel_type: str                  # "discord" | "line" | "telegram" | "twitch"
    channel_id: str                    # Unique channel / room / group ID
    user_id: str                       # Globally unique user identifier (e.g. "dc_123456" or "line_Uabc123")
    username: str                      # User display name
    text: str                          # Raw message text
    created_at: datetime.datetime      # Message timestamp (UTC or timezone-aware)
    guild_id: Optional[str] = None     # Server / group ID (None for direct messages)
    is_direct_chat: bool = False       # True if @Bot, reply to Bot, wake-word matched, or DM
    reply_target_text: Optional[str] = None   # Text of message being replied to (if any)
    image_bytes: Optional[bytes] = None       # Attached image bytes
    image_mime: Optional[str] = None          # Image MIME type (e.g. "image/png")
    raw_event: Optional[Any] = None           # Native platform event object for advanced use
