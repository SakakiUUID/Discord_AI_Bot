import re
import logging
from typing import Optional, List
import discord

logger = logging.getLogger("adapters.discord.delivery")

DISCORD_MAX_LENGTH = 1900


class DiscordDeliveryService:
    """Handles Discord message formatting and safe 1,900-char splitting."""

    @staticmethod
    def clean_residual_tags(text: str) -> str:
        """Clean any [MEME: xxx] or [EMOTION: xxx] residual tags before sending."""
        clean = re.sub(r"\[(?:MEME|EMOTION):\s*[^\]]+\]", "", text, flags=re.IGNORECASE).strip()
        return clean

    async def send_split_message(
        self,
        target_message: Optional[discord.Message],
        channel: discord.abc.Messageable,
        text: str,
    ) -> List[discord.Message]:
        """Send message safely chunked into <= 1,900 chars."""
        clean_text = self.clean_residual_tags(text)
        if not clean_text:
            return []

        chunks = [clean_text[i:i + DISCORD_MAX_LENGTH] for i in range(0, len(clean_text), DISCORD_MAX_LENGTH)]
        sent_messages = []

        for idx, chunk in enumerate(chunks):
            try:
                if target_message and idx == 0:
                    try:
                        msg = await target_message.reply(chunk, mention_author=False)
                    except discord.HTTPException:
                        msg = await channel.send(chunk)
                else:
                    msg = await channel.send(chunk)
                sent_messages.append(msg)
            except Exception as e:
                logger.error(f"Failed to send split message chunk {idx + 1}/{len(chunks)}: {e}")

        return sent_messages
