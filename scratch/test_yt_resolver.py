import asyncio
import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from cogs.youtube import YouTubeCog

async def run_tests():
    cog = YouTubeCog(None)
    test_urls = [
        "https://www.youtube.com/@WutheringWaves.ZH.official",
        "@MrBeast",
        "https://www.youtube.com/channel/UCVZqteUtWfSnwQ0ShjHixVw"
    ]

    print("=" * 60)
    print("🧪 測試 YouTube 頻道 Handle / 網址解析器")
    print("=" * 60)

    for url in test_urls:
        print(f"\n測試網址: {url}")
        cid, name = await cog.resolve_yt_channel(url)
        print(f"👉 解析結果: Channel ID = {cid}, Name = {name}")

if __name__ == "__main__":
    asyncio.run(run_tests())
