from dataclasses import dataclass
from typing import Optional


@dataclass
class OutgoingResponse:
    """Standardized outgoing response data contract returned by Core Agent Engine."""
    reply_text: str                          # Clean AI generated response text
    reaction_emoji: Optional[str] = None     # Emoji reaction if requested
    target_message_id: Optional[str] = None  # Original message ID to reply to

