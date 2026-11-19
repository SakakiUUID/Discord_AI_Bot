import logging
from adapters.discord.delivery import DiscordDeliveryService, DISCORD_MAX_LENGTH
from core.dialog_service import DialogService

logger = logging.getLogger("bot.message_delivery")


class MessageDeliveryService(DiscordDeliveryService):
    """Backwards-compatible wrapper for DiscordDeliveryService."""

    @staticmethod
    def clean_speech_text(text: str):
        return DialogService.clean_speech_text(text)
