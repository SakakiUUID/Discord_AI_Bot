from abc import ABC, abstractmethod
from typing import Optional
from core.models.incoming import IncomingMessage
from core.models.outgoing import OutgoingResponse


class BaseAdapter(ABC):
    """Abstract base class for all communication channel adapters (Discord, LINE, Telegram, Twitch, etc.)."""

    @abstractmethod
    async def start(self):
        """Start adapter connection or server."""
        pass

    @abstractmethod
    async def stop(self):
        """Stop adapter and cleanup resources."""
        pass

    @abstractmethod
    async def send_response(self, channel_id: str, response: OutgoingResponse, target_message: Optional[Any] = None):
        """Convert standard OutgoingResponse to platform-native message format and send."""
        pass
