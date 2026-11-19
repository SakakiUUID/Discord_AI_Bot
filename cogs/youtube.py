import os
import re
import logging
import asyncio
from typing import Optional, List, Dict
import aiohttp
import aiosqlite
import feedparser
from defusedxml import ElementTree as ET

import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import APIRouter, Request, Response, HTTPException

import config

logger = logging.getLogger("bot.youtube")

DB_PATH = "data/subscriptions.db"

# FastAPI Router for WebSub Webhook
router = APIRouter(prefix="/youtube", tags=["YouTube Webhook"])

class YouTubeCog(commands.Cog):
    """Cog for YouTube channel subscriptions, WebSub Webhook receiver, and RSS fallback polling."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None
        
        # Link this cog instance to router for Webhook callbacks
        router.youtube_cog = self

    async def cog_load(self):
        """Initialize SQLite database and start background RSS fallback loop."""
        os.makedirs("data", exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                discord_channel_id TEXT NOT NULL,
                yt_channel_id TEXT NOT NULL,
                yt_channel_name TEXT NOT NULL,
                last_video_id TEXT DEFAULT ''
            )
        """)
        await self.db.commit()
        logger.info("YouTube database initialized.")

        # Start RSS polling fallback loop
        self.rss_fallback_loop.start()

    async def cog_unload(self):
        """Cleanup database connection and tasks."""
        self.rss_fallback_loop.cancel()
        if self.db:
            await self.db.close()

    # --- Utility Functions ---

    async def resolve_yt_channel(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Resolve a YouTube URL or handle into (yt_channel_id, yt_channel_name)."""
        url = url.strip()

        # 1. 直接命中 UC... 格式 Channel ID 網址或標準化 Handle 網址
        direct_match = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})", url)
        if direct_match:
            url = f"https://www.youtube.com/channel/{direct_match.group(1)}"
        elif url.startswith("@"):
            url = f"https://www.youtube.com/{url}"
        elif not url.startswith("http"):
            url = f"https://www.youtube.com/@{url}"

        # 2. 發起請求並透過多重現代 YouTube 特徵提取 Channel ID 與名稱
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        return None, None
                    
                    html = await resp.text()
                    channel_id = None

                    # 特徵 A: canonical 或 og:url 連結
                    match = re.search(r'<(?:link|meta)[^>]+(?:href|content)=["\']https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})["\']', html)
                    if match:
                        channel_id = match.group(1)

                    # 特徵 B: itemprop="identifier" 或 itemprop="channelId"
                    if not channel_id:
                        match = re.search(r'itemprop=["\'](?:identifier|channelId)["\']\s+content=["\'](UC[a-zA-Z0-9_-]{22})["\']', html)
                        if match:
                            channel_id = match.group(1)

                    # 特徵 C: JSON 鍵值 (externalId, browseId, channelId)
                    if not channel_id:
                        match = re.search(r'["\'](?:externalId|browseId|channelId)["\']\s*:\s*["\'](UC[a-zA-Z0-9_-]{22})["\']', html)
                        if match:
                            channel_id = match.group(1)

                    # 特徵 D: 內嵌 /channel/UC... 連結
                    if not channel_id:
                        match = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', html)
                        if match:
                            channel_id = match.group(1)

                    # 提取頻道名稱 (優先使用 og:title)
                    title = None
                    og_title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', html)
                    if og_title_match:
                        title = og_title_match.group(1).strip()
                    elif title_match := re.search(r'<title>(.*?)</title>', html):
                        title = title_match.group(1).replace(" - YouTube", "").strip()

                    if not title:
                        title = f"Channel {channel_id}" if channel_id else "YouTube Channel"

                    return channel_id, title
            except Exception as e:
                logger.error(f"Error resolving YouTube channel URL '{url}': {e}")

        return None, None

    async def fetch_channel_name_from_rss(self, yt_channel_id: str) -> str:
        """Fetch YouTube channel title from its open RSS feed."""
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_channel_id}"
        feed = await asyncio.to_thread(feedparser.parse, feed_url)
        if feed and hasattr(feed, "feed") and hasattr(feed.feed, "title"):
            return feed.feed.title
        return f"Channel {yt_channel_id}"

    async def register_websub(self, yt_channel_id: str, mode: str = "subscribe") -> bool:
        """Register WebSub subscription with Google PubSubHubbub Hub if WEBHOOK_BASE_URL is configured."""
        if not config.WEBHOOK_BASE_URL:
            logger.info("WEBHOOK_BASE_URL is not set. WebSub registration skipped (RSS polling will be used).")
            return False

        callback_url = f"{config.WEBHOOK_BASE_URL}/youtube/webhook"
        topic_url = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={yt_channel_id}"
        hub_url = "https://pubsubhubbub.appspot.com/subscribe"

        payload = {
            "hub.callback": callback_url,
            "hub.mode": mode,
            "hub.topic": topic_url,
            "hub.lease_seconds": "864000",  # 10 days
            "hub.secret": config.WEBHOOK_SECRET,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(hub_url, data=payload) as resp:
                    if resp.status in (202, 204):
                        logger.info(f"Successfully requested WebSub {mode} for {yt_channel_id}")
                        return True
                    else:
                        logger.warning(f"WebSub registration returned status {resp.status} for {yt_channel_id}")
            except Exception as e:
                logger.error(f"Failed to register WebSub for {yt_channel_id}: {e}")
        return False

    async def send_video_notification(self, yt_channel_id: str, video_id: str, video_title: str, channel_name: str):
        """Send new video notification to all Discord channels subscribed to this YouTube channel."""
        if not self.db:
            return

        async with self.db.execute(
            "SELECT discord_channel_id, last_video_id FROM subscriptions WHERE yt_channel_id = ?",
            (yt_channel_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        for discord_channel_id_str, last_video_id in rows:
            if last_video_id == video_id:
                continue  # Skip if already posted

            channel_id = int(discord_channel_id_str)
            discord_channel = self.bot.get_channel(channel_id)
            if not discord_channel:
                try:
                    discord_channel = await self.bot.fetch_channel(channel_id)
                except Exception:
                    logger.warning(f"Could not fetch Discord channel {channel_id}")
                    continue

            if discord_channel:
                embed = discord.Embed(
                    title=f"📢 {channel_name} 上架了新影片！",
                    description=f"**[{video_title}](https://www.youtube.com/watch?v={video_id})**",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg")
                embed.add_field(name="影片連結", value=f"https://www.youtube.com/watch?v={video_id}")
                embed.set_footer(text="YouTube 即時通知")

                try:
                    await discord_channel.send(content=f"https://www.youtube.com/watch?v={video_id}", embed=embed)
                except Exception as e:
                    logger.error(f"Failed to send notification to channel {channel_id}: {e}")

        # Update last_video_id in DB
        await self.db.execute(
            "UPDATE subscriptions SET last_video_id = ? WHERE yt_channel_id = ?",
            (video_id, yt_channel_id)
        )
        await self.db.commit()

    # --- Background RSS Polling Loop ---

    @tasks.loop(minutes=3.0)
    async def rss_fallback_loop(self):
        """Background task checking RSS feeds for new videos (fallback when WebSub is inactive)."""
        if not self.db:
            return

        async with self.db.execute("SELECT DISTINCT yt_channel_id, yt_channel_name FROM subscriptions") as cursor:
            subscribed_channels = await cursor.fetchall()

        for yt_channel_id, channel_name in subscribed_channels:
            try:
                feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_channel_id}"
                feed = await asyncio.to_thread(feedparser.parse, feed_url)

                if feed and feed.entries:
                    latest_entry = feed.entries[0]
                    video_id = getattr(latest_entry, "yt_videoid", None)
                    video_title = getattr(latest_entry, "title", "新影片")

                    if not video_id and hasattr(latest_entry, "link"):
                        match = re.search(r"v=([a-zA-Z0-9_-]+)", latest_entry.link)
                        if match:
                            video_id = match.group(1)

                    if video_id:
                        await self.send_video_notification(yt_channel_id, video_id, video_title, channel_name)
            except Exception as e:
                logger.error(f"Error in RSS polling loop for channel {yt_channel_id}: {e}")

    @rss_fallback_loop.before_loop
    async def before_rss_loop(self):
        await self.bot.wait_until_ready()

    # --- Slash Commands ---

    @app_commands.command(name="sub_yt", description="訂閱特定的 YouTube 頻道新影片通知")
    @app_commands.describe(url="輸入 YouTube 頻道網址或 Handle (例: https://www.youtube.com/@MrBeast)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def sub_yt(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer()

        yt_channel_id, yt_channel_name = await self.resolve_yt_channel(url)
        if not yt_channel_id:
            await interaction.followup.send("❌ 無法辨識該 YouTube 頻道網址，請確認網址格式是否正確！")
            return

        guild_id = str(interaction.guild_id)
        discord_channel_id = str(interaction.channel_id)

        # Check existing subscription
        async with self.db.execute(
            "SELECT id FROM subscriptions WHERE discord_channel_id = ? AND yt_channel_id = ?",
            (discord_channel_id, yt_channel_id)
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            await interaction.followup.send(f"ℹ️ 本頻道已經訂閱過 **{yt_channel_name}** 囉！")
            return

        # Insert into DB
        await self.db.execute(
            "INSERT INTO subscriptions (guild_id, discord_channel_id, yt_channel_id, yt_channel_name) VALUES (?, ?, ?, ?)",
            (guild_id, discord_channel_id, yt_channel_id, yt_channel_name)
        )
        await self.db.commit()

        # Try WebSub registration
        await self.register_websub(yt_channel_id)

        await interaction.followup.send(
            f"✅ **訂閱成功！**\n已成功綁定 YouTube 頻道：**{yt_channel_name}** (`{yt_channel_id}`)\n有新影片時將在此頻道發布通知！"
        )

    @app_commands.command(name="unsub_yt", description="取消訂閱本頻道的特定 YouTube 頻道通知")
    @app_commands.describe(yt_channel_name_or_id="輸入頻道名稱或 Channel ID")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unsub_yt(self, interaction: discord.Interaction, yt_channel_name_or_id: str):
        await interaction.response.defer()

        discord_channel_id = str(interaction.channel_id)
        target = yt_channel_name_or_id.strip()

        async with self.db.execute(
            "DELETE FROM subscriptions WHERE discord_channel_id = ? AND (yt_channel_id = ? OR yt_channel_name LIKE ?)",
            (discord_channel_id, target, f"%{target}%")
        ) as cursor:
            deleted_count = cursor.rowcount

        await self.db.commit()

        if deleted_count > 0:
            await interaction.followup.send(f"🗑️ 已成功移除 {deleted_count} 個符合條件的 YouTube 頻道訂閱。")
        else:
            await interaction.followup.send("❌ 找不到符合條件的訂閱紀錄，請確認名稱是否正確！")

    @app_commands.command(name="list_yt", description="列出本 Discord 頻道關注的所有 YouTube 頻道")
    async def list_yt(self, interaction: discord.Interaction):
        discord_channel_id = str(interaction.channel_id)

        async with self.db.execute(
            "SELECT yt_channel_name, yt_channel_id FROM subscriptions WHERE discord_channel_id = ?",
            (discord_channel_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message("ℹ️ 本 Discord 頻道目前尚未訂閱任何 YouTube 頻道！使用 `/sub_yt` 來新增吧。")
            return

        embed = discord.Embed(title="📺 本頻道訂閱的 YouTube 清單", color=discord.Color.red())
        for idx, (name, channel_id) in enumerate(rows, 1):
            embed.add_field(
                name=f"{idx}. {name}",
                value=f"ID: `{channel_id}`\n[開啟頻道頁面](https://www.youtube.com/channel/{channel_id})",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    cog = YouTubeCog(bot)
    await bot.add_cog(cog)

# --- FastAPI Webhook Endpoints ---

@router.get("/webhook")
async def websub_verify(request: Request):
    """Handle WebSub challenge verification from Google."""
    params = request.query_params
    topic = params.get("hub.topic")
    challenge = params.get("hub.challenge")
    mode = params.get("hub.mode")

    if challenge and mode == "subscribe":
        logger.info(f"WebSub verification challenge accepted for topic: {topic}")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=400, detail="Invalid verification request")

@router.post("/webhook")
async def websub_notification(request: Request):
    """Handle Atom XML push notifications from Google WebSub."""
    body = await request.body()
    try:
        root = ET.fromstring(body)
        # Namespace handling for Atom XML
        ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        
        entry = root.find("atom:entry", ns)
        if entry is not None:
            video_id_el = entry.find("yt:videoId", ns)
            channel_id_el = entry.find("yt:channelId", ns)
            title_el = entry.find("atom:title", ns)
            author_el = entry.find("atom:author/atom:name", ns)

            video_id = video_id_el.text if video_id_el is not None else None
            yt_channel_id = channel_id_el.text if channel_id_el is not None else None
            title = title_el.text if title_el is not None else "新影片"
            author_name = author_el.text if author_el is not None else "YouTube Channel"

            if video_id and yt_channel_id:
                youtube_cog: Optional[YouTubeCog] = getattr(router, "youtube_cog", None)
                if youtube_cog:
                    # Trigger notification asynchronously
                    asyncio.create_task(
                        youtube_cog.send_video_notification(yt_channel_id, video_id, title, author_name)
                    )
                return Response(content="Notification received", status_code=200)

    except Exception as e:
        logger.error(f"Error parsing WebSub XML payload: {e}")

    return Response(content="OK", status_code=200)
