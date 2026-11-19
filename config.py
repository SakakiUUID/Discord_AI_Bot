import os
from dotenv import load_dotenv

# Load environment variables from .env file if available
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dc_bot_secret_123")

# Multi-Channel Configuration (Comma-separated: discord,line)
ENABLED_CHANNELS = [c.strip().lower() for c in os.getenv("ENABLED_CHANNELS", "discord").split(",") if c.strip()]

# Google Search Grounding Configuration (Default: True)
ENABLE_GOOGLE_SEARCH = os.getenv("ENABLE_GOOGLE_SEARCH", "true").lower() in ("true", "1", "yes")

# LINE Bot Configuration (Optional for line channel)
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

# Vector Memory & Embedding Configuration
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
MEMORY_EMBEDDING_DIM = int(os.getenv("MEMORY_EMBEDDING_DIM", "768"))
MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "5"))
MEMORY_MIN_SIMILARITY = float(os.getenv("MEMORY_MIN_SIMILARITY", "0.45"))
MEMORY_DEDUP_THRESHOLD = float(os.getenv("MEMORY_DEDUP_THRESHOLD", "0.88"))
MEMORY_PARTITION_MAX_FACTS = int(os.getenv("MEMORY_PARTITION_MAX_FACTS", "500"))
MEMORY_ISOLATE_GUILD_BY_PERSONA = os.getenv("MEMORY_ISOLATE_GUILD_BY_PERSONA", "true").lower() in ("true", "1", "yes")
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", os.path.join("data", "memory.db"))


def _parse_int_set(raw: str) -> set[int]:
    return {int(token.strip()) for token in raw.split(",") if token.strip().isdigit()}


def _parse_float_list(raw: str, default: list[float]) -> list[float]:
    values = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values or default


# Peer-Bot Decay Interaction Configuration
PEER_BOT_IDS = _parse_int_set(os.getenv("PEER_BOT_IDS", ""))
PEER_BOT_REPLY_PROBS = _parse_float_list(
    os.getenv("PEER_BOT_REPLY_PROBS", "1.0,0.5,0.3,0.1"),
    [1.0, 0.5, 0.3, 0.1],
)
PEER_BOT_STREAK_TIMEOUT = float(os.getenv("PEER_BOT_STREAK_TIMEOUT", "600"))
PEER_BOT_DELAY_MIN = float(os.getenv("PEER_BOT_DELAY_MIN", "2.0"))
PEER_BOT_DELAY_MAX = float(os.getenv("PEER_BOT_DELAY_MAX", "4.5"))

# Mode A: Dynamic Multi-Participant Hot Continuation Window Configuration
PROACTIVE_HOT_WINDOW_ENABLED = os.getenv("PROACTIVE_HOT_WINDOW_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_HOT_WINDOW_SECONDS = float(os.getenv("PROACTIVE_HOT_WINDOW_SECONDS", "180"))
PROACTIVE_HOT_MAX_CONSECUTIVE_PASS = int(os.getenv("PROACTIVE_HOT_MAX_CONSECUTIVE_PASS", "5"))
PROACTIVE_BYSTANDER_MAX_PASS = int(os.getenv("PROACTIVE_BYSTANDER_MAX_PASS", "2"))
PROACTIVE_HOT_MAX_ACTIVE_USERS = int(os.getenv("PROACTIVE_HOT_MAX_ACTIVE_USERS", "8"))
PROACTIVE_DEBOUNCE_SECONDS = float(os.getenv("PROACTIVE_DEBOUNCE_SECONDS", "2.5"))
PROACTIVE_CONTEXT_MAX_AGE_MINUTES = int(os.getenv("PROACTIVE_CONTEXT_MAX_AGE_MINUTES", "15"))


