import os
import logging
from typing import List, Optional, Tuple
import discord

from core.persona_service import PersonaService, PERSONA_DIR, ACTIVE_PERSONA_FILE, DEFAULT_PERSONA_FILENAME, DEFAULT_PERSONA_CONTENT, DEFAULT_SETTING

logger = logging.getLogger("bot.persona_manager")


class PersonaManager(PersonaService):
    """Manages AI persona prompts, settings, avatar assets, and Discord guild nickname synchronization."""

    def __init__(self, personas_dir: str = PERSONA_DIR):
        super().__init__(personas_dir=personas_dir)

    async def update_bot_identity(self, interaction: discord.Interaction, filename: str, bot_user: Optional[discord.ClientUser]) -> Tuple[bool, bool]:
        """Update bot's server nickname using persona display name and global avatar if persona_xxx.png exists."""
        avatar_updated = False
        nick_updated = False
        target_name = self.persona_display_name

        if interaction.guild and interaction.guild.me:
            try:
                await interaction.guild.me.edit(nick=target_name)
                nick_updated = True
                logger.info(f"Updated bot nickname in guild {interaction.guild.name} to: {target_name}")
            except Exception as e:
                logger.warning(f"Could not update server nickname: {e}")

        img_path = None
        for ext in [".png", ".jpg", ".jpeg", ".webp"]:
            candidate = os.path.join(self.personas_dir, filename.replace(".txt", ext))
            if os.path.exists(candidate):
                img_path = candidate
                break

        if img_path and bot_user:
            try:
                with open(img_path, "rb") as f:
                    img_bytes = f.read()
                await bot_user.edit(avatar=img_bytes)
                avatar_updated = True
                logger.info(f"Successfully updated bot global avatar from: {img_path}")
            except discord.HTTPException as e:
                logger.warning(f"Discord avatar update rate-limited or error: {e}")
            except Exception as e:
                logger.error(f"Error updating avatar: {e}")

        return avatar_updated, nick_updated

    async def sync_bot_identity_all_guilds(self, guilds: List[discord.Guild]):
        """Update server nickname across all connected guilds to match persona display name."""
        for guild in guilds:
            if guild.me:
                try:
                    await guild.me.edit(nick=self.persona_display_name)
                    logger.info(f"Synced bot nickname in guild '{guild.name}' to: '{self.persona_display_name}'")
                except Exception as e:
                    logger.warning(f"Could not sync nickname in guild {guild.name}: {e}")
