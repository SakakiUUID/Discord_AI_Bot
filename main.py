import os
import sys
import logging
import asyncio
import discord
from discord.ext import commands
from fastapi import FastAPI
import uvicorn

import config
from cogs.youtube import router as youtube_router
from utils.vip_manager import vip_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("bot.main")

# Initialize Discord Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class AutoBot(commands.Bot):
    async def setup_hook(self):
        """Load extension cogs and sync application slash commands."""
        logger.info("Loading extension cogs...")
        
        # Load AI Roleplay Cog
        try:
            await self.load_extension("cogs.ai_roleplay")
            logger.info("Loaded cog: cogs.ai_roleplay")
        except Exception as e:
            logger.error(f"Failed to load cogs.ai_roleplay: {e}", exc_info=True)

        # Load YouTube Cog
        try:
            await self.load_extension("cogs.youtube")
            logger.info("Loaded cog: cogs.youtube")
        except Exception as e:
            logger.error(f"Failed to load cogs.youtube: {e}", exc_info=True)

        # Load Admin Cog
        try:
            await self.load_extension("cogs.admin")
            logger.info("Loaded cog: cogs.admin")
        except Exception as e:
            logger.error(f"Failed to load cogs.admin: {e}", exc_info=True)

        # Global sync
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} Slash Commands globally.")
        except Exception as e:
            logger.error(f"Failed to sync slash commands globally: {e}")

bot = AutoBot(command_prefix="!", intents=intents)

# Initialize FastAPI App
app = FastAPI(
    title="Discord Auto Bot Webhook API",
    description="FastAPI Web Server for YouTube WebSub Webhooks and Health Checks",
    version="1.0.0"
)

# Include YouTube Router in FastAPI
app.include_router(youtube_router)

@app.get("/")
async def health_check():
    return {
        "status": "online",
        "bot_user": str(bot.user) if bot.user else "connecting...",
        "guilds_count": len(bot.guilds) if bot.is_ready() else 0
    }

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id if bot.user else 'Unknown'})")
    logger.info(f"Connected to {len(bot.guilds)} guilds.")
    
    # Clear stale guild command copies to eliminate duplicates in Discord UI
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info(f"Cleared stale guild command copies for: {guild.name}")
        except Exception as e:
            logger.warning(f"Could not clear guild commands for {guild.id}: {e}")

    logger.info("Bot is ready and operational! Type '/' in Discord to see all available slash commands!")

@bot.command(name="sync")
async def sync_cmd(ctx: commands.Context):
    """Admin command to force instant slash command sync for the current server."""
    if not vip_manager.is_bot_admin(ctx.author.id):
        await ctx.send("⛔ **權限被拒**！此指令僅限指定 Bot 管理員 (User ID) 執行。")
        return

    async with ctx.typing():
        try:
            bot.tree.clear_commands(guild=ctx.guild)
            await bot.tree.sync(guild=ctx.guild)
            synced = await bot.tree.sync()
            await ctx.send(f"✅ 已成功清除重複舊指令並同步 {len(synced)} 個全域斜線指令！請在 Discord 按 Ctrl + R 重新載入。")
        except Exception as e:
            await ctx.send(f"❌ 同步斜線指令失敗：{e}")

async def main():
    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "your_discord_bot_token_here":
        logger.error("DISCORD_TOKEN is not set in .env! Please configure your token before running.")
        sys.exit(1)

    # Create Uvicorn Web Server Config for FastAPI
    web_config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        log_level="info"
    )
    web_server = uvicorn.Server(web_config)

    # Run FastAPI web server and Discord Bot concurrently in asyncio event loop
    logger.info(f"Starting FastAPI Web Server on port {config.WEBHOOK_PORT}...")
    logger.info("Starting Discord Bot connection...")

    await asyncio.gather(
        web_server.serve(),
        bot.start(config.DISCORD_TOKEN)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot gracefully shutting down...")
