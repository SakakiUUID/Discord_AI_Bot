import time
from dataclasses import dataclass, field
from typing import Optional, List, Any
import numpy as np

import config


def normalize_persona_id(persona_name_or_id: Optional[str]) -> str:
    """Normalize persona filename or identifier (e.g. 'persona_aemeath.txt' -> 'aemeath')."""
    if not persona_name_or_id:
        return "default"
    raw = str(persona_name_or_id).strip().lower()
    if raw.startswith("persona_"):
        raw = raw[len("persona_"):]
    if raw.endswith(".txt"):
        raw = raw[:-len(".txt")]
    return raw or "default"


def get_culture_persona_key(persona_id: str, isolate_by_persona: Optional[bool] = None) -> str:
    """Return persona partition key for guild_cultures depending on isolation toggle."""
    isolate = config.MEMORY_ISOLATE_GUILD_BY_PERSONA if isolate_by_persona is None else isolate_by_persona
    return normalize_persona_id(persona_id) if isolate else "_shared"


def normalize_user_id(user_id: Any, platform: str = "discord") -> str:
    """Normalize platform user ID into namespaced form (e.g. 'discord:123456')."""
    raw = str(user_id).strip()
    if not raw:
        return f"{platform}:unknown"
    if ":" in raw:
        return raw
    return f"{platform}:{raw}"


def normalize_scope_key(
    guild_id: Optional[Any],
    user_id: Optional[Any] = None,
    platform: str = "discord"
) -> str:
    """
    Normalize guild/DM scope key (e.g. 'discord_guild:123' or 'discord_dm:456').
    Ensures DM conversations are strictly isolated per user rather than sharing a global 'DM' key.
    """
    if guild_id is not None:
        raw_gid = str(guild_id).strip()
        if raw_gid and raw_gid.upper() != "DM":
            if raw_gid.startswith(("discord_guild:", "discord_dm:", "line_group:", "line_user:")):
                return raw_gid
            group_prefix = "line_group" if platform == "line" else f"{platform}_guild"
            return f"{group_prefix}:{raw_gid}"

    raw_uid = str(user_id).strip() if user_id is not None else "unknown"
    if ":" in raw_uid:
        raw_uid = raw_uid.split(":", 1)[1]
    dm_prefix = "line_user" if platform == "line" else f"{platform}_dm"
    return f"{dm_prefix}:{raw_uid or 'unknown'}"


@dataclass
class GuildCulture:
    """Persistent group culture memory scoped by (persona_id, scope_key)."""
    persona_id: str
    scope_key: str
    group_vibe: str = ""
    inside_jokes: List[str] = field(default_factory=list)
    speech_style: str = ""
    updated_at: float = field(default_factory=time.time)


@dataclass
class UserProfile:
    """Persistent user impression profile scoped by (persona_id, user_id)."""
    persona_id: str
    user_id: str
    username: str = ""
    speaking_style: str = ""
    traits_and_interests: str = ""
    updated_at: float = field(default_factory=time.time)


@dataclass
class MemoryFact:
    """Atomic L3 episodic/semantic fact with L2-normalized float32 embedding."""
    persona_id: str
    scope_key: str
    fact_text: str
    user_id: Optional[str] = None
    category: str = "general"
    importance: int = 3
    embedding: Optional[np.ndarray] = None
    embedding_dim: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_recalled_at: Optional[float] = None
    recall_count: int = 0
    id: Optional[int] = None


@dataclass
class ScoredFact:
    """Vector recall candidate scored by cosine similarity, recency decay, and importance."""
    fact: MemoryFact
    cosine_sim: float
    recency_decay: float
    final_score: float


@dataclass
class PendingMessage:
    """Restart-safe buffered chat message persisted in SQLite."""
    channel_id: str
    scope_key: str
    persona_id: str
    user_id: str
    username: str
    content: str
    has_image: int = 0
    created_at: float = field(default_factory=time.time)
    id: Optional[int] = None
