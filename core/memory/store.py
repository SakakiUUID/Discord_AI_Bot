import os
import json
import time
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
import aiosqlite
import numpy as np

import config
from core.memory.models import (
    GuildCulture,
    UserProfile,
    MemoryFact,
    PendingMessage,
    normalize_persona_id,
    get_culture_persona_key,
    normalize_scope_key,
    normalize_user_id,
)

logger = logging.getLogger("core.memory.store")

SCHEMA_VERSION = "1"


class MemoryStore:
    """Async SQLite (WAL) persistence layer for dual-layer profiles, vector facts, and pending buffers."""

    def __init__(
        self,
        db_path: str = config.MEMORY_DB_PATH,
        embedding_model: str = config.GEMINI_EMBEDDING_MODEL,
        embedding_dim: int = config.MEMORY_EMBEDDING_DIM
    ):
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()
        self.embedding_mismatch: bool = False

    @property
    def is_connected(self) -> bool:
        return self.db is not None

    async def initialize(self):
        """Open SQLite WAL connection, create tables/indexes, check embedding metadata, and migrate legacy JSON."""
        if self.db is not None:
            return

        db_dir = os.path.dirname(self.db_path) or "."
        os.makedirs(db_dir, exist_ok=True)

        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA synchronous=NORMAL;")
        await self.db.execute("PRAGMA foreign_keys=ON;")

        await self._create_tables()
        await self._check_schema_meta()
        await self._migrate_legacy_json(db_dir)
        logger.info(f"🗄️ [MemoryStore] Initialized SQLite WAL store at {self.db_path}")

    async def close(self):
        """Close SQLite database connection safely."""
        if self.db is not None:
            await self.db.close()
            self.db = None
            logger.info("🗄️ [MemoryStore] Closed SQLite connection.")

    async def _create_tables(self):
        assert self.db is not None
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guild_cultures (
                persona_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                group_vibe TEXT DEFAULT '',
                inside_jokes_json TEXT DEFAULT '[]',
                speech_style TEXT DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (persona_id, scope_key)
            );

            CREATE TABLE IF NOT EXISTS user_profiles (
                persona_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT DEFAULT '',
                speaking_style TEXT DEFAULT '',
                traits_and_interests TEXT DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (persona_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                user_id TEXT,
                fact_text TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                importance INTEGER DEFAULT 3,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_recalled_at REAL,
                recall_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_facts_partition
                ON memory_facts (persona_id, scope_key, updated_at DESC);

            CREATE TABLE IF NOT EXISTS pending_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                persona_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                has_image INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pending_channel
                ON pending_messages (channel_id, id);
            """
        )
        await self.db.commit()

    async def _get_meta(self, key: str) -> Optional[str]:
        assert self.db is not None
        async with self.db.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def _set_meta(self, key: str, value: str):
        assert self.db is not None
        await self.db.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )

    async def _check_schema_meta(self):
        assert self.db is not None
        stored_model = await self._get_meta("embedding_model")
        stored_dim = await self._get_meta("embedding_dim")
        stored_version = await self._get_meta("schema_version")

        if stored_version is None:
            await self._set_meta("schema_version", SCHEMA_VERSION)

        if stored_model is None or stored_dim is None:
            await self._set_meta("embedding_model", self.embedding_model)
            await self._set_meta("embedding_dim", str(self.embedding_dim))
            await self.db.commit()
            return

        if stored_model != self.embedding_model or stored_dim != str(self.embedding_dim):
            self.embedding_mismatch = True
            logger.warning(
                f"⚠️ [MemoryStore] Embedding config changed! "
                f"Stored=({stored_model}, dim={stored_dim}) vs Current=({self.embedding_model}, dim={self.embedding_dim}). "
                f"Incompatible vectors will be isolated until re-embedded."
            )

    async def update_embedding_meta(self):
        """Record active embedding model and dimension after re-embedding or alignment."""
        assert self.db is not None
        async with self._write_lock:
            await self._set_meta("embedding_model", self.embedding_model)
            await self._set_meta("embedding_dim", str(self.embedding_dim))
            await self.db.commit()
            self.embedding_mismatch = False

    async def _migrate_legacy_json(self, data_dir: str):
        """One-time migration from legacy guild_cultures.json / user_profiles.json into SQLite."""
        assert self.db is not None
        if await self._get_meta("json_migrated_at") is not None:
            return

        cultures_path = os.path.join(data_dir, "guild_cultures.json")
        profiles_path = os.path.join(data_dir, "user_profiles.json")
        if not os.path.exists(cultures_path) and not os.path.exists(profiles_path):
            return

        active_persona_file = os.path.join(data_dir, "personas", "active_persona.txt")
        target_persona = "default"
        if os.path.exists(active_persona_file):
            try:
                with open(active_persona_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        target_persona = normalize_persona_id(content)
            except Exception as e:
                logger.warning(f"Could not read active_persona.txt during migration: {e}")

        now = time.time()
        migrated_cultures = 0
        migrated_profiles = 0

        async with self._write_lock:
            await self.db.execute("BEGIN")
            try:
                if os.path.exists(cultures_path):
                    with open(cultures_path, "r", encoding="utf-8") as f:
                        old_cultures = json.load(f)
                    if isinstance(old_cultures, dict):
                        culture_persona = get_culture_persona_key(target_persona)
                        for raw_gid, cdata in old_cultures.items():
                            if not isinstance(cdata, dict):
                                continue
                            scope_key = "discord_dm:legacy" if str(raw_gid).upper() == "DM" else normalize_scope_key(raw_gid)
                            jokes = cdata.get("inside_jokes", [])
                            if not isinstance(jokes, list):
                                jokes = []
                            await self.db.execute(
                                """
                                INSERT OR IGNORE INTO guild_cultures
                                (persona_id, scope_key, group_vibe, inside_jokes_json, speech_style, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    culture_persona,
                                    scope_key,
                                    str(cdata.get("group_vibe", "")),
                                    json.dumps(jokes[:6], ensure_ascii=False),
                                    str(cdata.get("speech_style", "")),
                                    now
                                )
                            )
                            migrated_cultures += 1

                if os.path.exists(profiles_path):
                    with open(profiles_path, "r", encoding="utf-8") as f:
                        old_profiles = json.load(f)
                    if isinstance(old_profiles, dict):
                        for raw_uid, udata in old_profiles.items():
                            if not isinstance(udata, dict):
                                continue
                            norm_uid = normalize_user_id(raw_uid, platform="discord")
                            await self.db.execute(
                                """
                                INSERT OR IGNORE INTO user_profiles
                                (persona_id, user_id, username, speaking_style, traits_and_interests, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    target_persona,
                                    norm_uid,
                                    str(udata.get("username", "")),
                                    str(udata.get("speaking_style", "")),
                                    str(udata.get("traits_and_interests", "")),
                                    now
                                )
                            )
                            migrated_profiles += 1

                await self._set_meta("json_migrated_at", str(now))
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

        for path in (cultures_path, profiles_path):
            if os.path.exists(path):
                bak_path = f"{path}.bak"
                if os.path.exists(bak_path):
                    bak_path = f"{path}.{int(now)}.bak"
                os.replace(path, bak_path)

        logger.info(
            f"📦 [MemoryStore] Migrated legacy JSON to SQLite (persona='{target_persona}'): "
            f"{migrated_cultures} guild cultures, {migrated_profiles} user profiles."
        )

    # --- Guild Cultures & User Profiles ---

    async def get_guild_culture(self, persona_id: str, scope_key: str) -> Optional[GuildCulture]:
        assert self.db is not None
        async with self.db.execute(
            "SELECT * FROM guild_cultures WHERE persona_id = ? AND scope_key = ?",
            (persona_id, scope_key)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                jokes = json.loads(row["inside_jokes_json"] or "[]")
            except Exception:
                jokes = []
            return GuildCulture(
                persona_id=row["persona_id"],
                scope_key=row["scope_key"],
                group_vibe=row["group_vibe"] or "",
                inside_jokes=jokes if isinstance(jokes, list) else [],
                speech_style=row["speech_style"] or "",
                updated_at=row["updated_at"],
            )

    async def get_user_profile(self, persona_id: str, user_id: str) -> Optional[UserProfile]:
        assert self.db is not None
        async with self.db.execute(
            "SELECT * FROM user_profiles WHERE persona_id = ? AND user_id = ?",
            (persona_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return UserProfile(
                persona_id=row["persona_id"],
                user_id=row["user_id"],
                username=row["username"] or "",
                speaking_style=row["speaking_style"] or "",
                traits_and_interests=row["traits_and_interests"] or "",
                updated_at=row["updated_at"],
            )

    async def get_user_profiles(self, persona_id: str, user_ids: List[str]) -> Dict[str, UserProfile]:
        assert self.db is not None
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        sql = f"SELECT * FROM user_profiles WHERE persona_id = ? AND user_id IN ({placeholders})"
        params: List[Any] = [persona_id, *user_ids]
        profiles: Dict[str, UserProfile] = {}
        async with self.db.execute(sql, params) as cursor:
            async for row in cursor:
                profiles[row["user_id"]] = UserProfile(
                    persona_id=row["persona_id"],
                    user_id=row["user_id"],
                    username=row["username"] or "",
                    speaking_style=row["speaking_style"] or "",
                    traits_and_interests=row["traits_and_interests"] or "",
                    updated_at=row["updated_at"],
                )
        return profiles

    # --- Memory Facts ---

    @staticmethod
    def _row_to_fact(row: aiosqlite.Row) -> MemoryFact:
        blob = row["embedding"]
        vec = np.frombuffer(blob, dtype=np.float32).copy() if blob else None
        return MemoryFact(
            id=row["id"],
            persona_id=row["persona_id"],
            scope_key=row["scope_key"],
            user_id=row["user_id"],
            fact_text=row["fact_text"],
            category=row["category"] or "general",
            importance=int(row["importance"] or 3),
            embedding=vec,
            embedding_dim=int(row["embedding_dim"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_recalled_at=row["last_recalled_at"],
            recall_count=int(row["recall_count"] or 0),
        )

    async def get_facts_by_partition(
        self,
        persona_id: str,
        scope_key: str,
        expected_dim: Optional[int] = None
    ) -> List[MemoryFact]:
        assert self.db is not None
        dim_filter = expected_dim if expected_dim is not None else self.embedding_dim
        async with self.db.execute(
            """
            SELECT * FROM memory_facts
            WHERE persona_id = ? AND scope_key = ? AND embedding_dim = ?
            ORDER BY updated_at DESC
            """,
            (persona_id, scope_key, dim_filter)
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_fact(r) for r in rows]

    async def get_incompatible_facts(self, expected_dim: Optional[int] = None, force_all: bool = False) -> List[MemoryFact]:
        assert self.db is not None
        dim = expected_dim if expected_dim is not None else self.embedding_dim
        if force_all:
            sql = "SELECT * FROM memory_facts ORDER BY id ASC"
            params: Tuple[Any, ...] = ()
        else:
            sql = "SELECT * FROM memory_facts WHERE embedding_dim != ? ORDER BY id ASC"
            params = (dim,)
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_fact(r) for r in rows]

    async def update_fact_embedding(self, fact_id: int, embedding: np.ndarray, embedding_dim: int):
        assert self.db is not None
        blob = np.asarray(embedding, dtype=np.float32).tobytes()
        async with self._write_lock:
            await self.db.execute(
                "UPDATE memory_facts SET embedding = ?, embedding_dim = ? WHERE id = ?",
                (blob, embedding_dim, fact_id)
            )
            await self.db.commit()

    async def get_top_facts(
        self,
        persona_id: str,
        scope_key: str,
        user_id: Optional[str] = None,
        limit: int = 5
    ) -> List[MemoryFact]:
        """Fetch top recalled / recent facts for display in /view_memory."""
        assert self.db is not None
        if user_id:
            sql = """
                SELECT * FROM memory_facts
                WHERE persona_id = ? AND scope_key = ? AND (user_id = ? OR user_id IS NULL)
                ORDER BY recall_count DESC, updated_at DESC
                LIMIT ?
            """
            params: Tuple[Any, ...] = (persona_id, scope_key, user_id, limit)
        else:
            sql = """
                SELECT * FROM memory_facts
                WHERE persona_id = ? AND scope_key = ?
                ORDER BY recall_count DESC, updated_at DESC
                LIMIT ?
            """
            params = (persona_id, scope_key, limit)

        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_fact(r) for r in rows]

    async def mark_facts_recalled(self, fact_ids: List[int], recalled_at: float):
        assert self.db is not None
        if not fact_ids:
            return
        placeholders = ",".join("?" for _ in fact_ids)
        async with self._write_lock:
            await self.db.execute(
                f"UPDATE memory_facts SET last_recalled_at = ?, recall_count = recall_count + 1 WHERE id IN ({placeholders})",
                [recalled_at, *fact_ids]
            )
            await self.db.commit()

    async def delete_facts_by_id_or_keyword(
        self,
        persona_id: str,
        scope_key: Optional[str] = None,
        user_id: Optional[str] = None,
        fact_id: Optional[int] = None,
        keyword: Optional[str] = None
    ) -> List[Tuple[str, int]]:
        """Delete matching memory facts and return list of (scope_key, deleted_fact_id)."""
        assert self.db is not None
        clauses = ["persona_id = ?"]
        params: List[Any] = [persona_id]

        if scope_key is not None:
            clauses.append("scope_key = ?")
            params.append(scope_key)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if fact_id is not None:
            clauses.append("id = ?")
            params.append(fact_id)
        if keyword:
            clauses.append("fact_text LIKE ?")
            params.append(f"%{keyword.strip()}%")

        where_sql = " AND ".join(clauses)
        async with self._write_lock:
            async with self.db.execute(
                f"SELECT id, scope_key FROM memory_facts WHERE {where_sql}",
                params
            ) as cursor:
                rows = await cursor.fetchall()
                targets = [(row["scope_key"], int(row["id"])) for row in rows]

            if not targets:
                return []

            ids = [t[1] for t in targets]
            placeholders = ",".join("?" for _ in ids)
            await self.db.execute(f"DELETE FROM memory_facts WHERE id IN ({placeholders})", ids)
            await self.db.commit()
            return targets

    async def clear_user_memory(
        self,
        persona_id: str,
        user_id: str,
        scope_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete user's profile and facts under a given persona."""
        assert self.db is not None
        async with self._write_lock:
            await self.db.execute("BEGIN")
            try:
                cur_prof = await self.db.execute(
                    "DELETE FROM user_profiles WHERE persona_id = ? AND user_id = ?",
                    (persona_id, user_id)
                )
                if scope_key:
                    async with self.db.execute(
                        "SELECT id, scope_key FROM memory_facts WHERE persona_id = ? AND user_id = ? AND scope_key = ?",
                        (persona_id, user_id, scope_key)
                    ) as c:
                        rows = await c.fetchall()
                else:
                    async with self.db.execute(
                        "SELECT id, scope_key FROM memory_facts WHERE persona_id = ? AND user_id = ?",
                        (persona_id, user_id)
                    ) as c:
                        rows = await c.fetchall()

                deleted_targets = [(r["scope_key"], int(r["id"])) for r in rows]
                if deleted_targets:
                    ids = [t[1] for t in deleted_targets]
                    placeholders = ",".join("?" for _ in ids)
                    await self.db.execute(f"DELETE FROM memory_facts WHERE id IN ({placeholders})", ids)

                await self.db.commit()
                return {
                    "profiles_deleted": cur_prof.rowcount or 0,
                    "facts_deleted": len(deleted_targets),
                    "deleted_targets": deleted_targets,
                }
            except Exception:
                await self.db.rollback()
                raise

    async def clear_scope_memory(self, persona_id: str, scope_key: str) -> Dict[str, int]:
        """Admin reset: delete guild culture and all facts for (persona_id, scope_key)."""
        assert self.db is not None
        culture_persona = get_culture_persona_key(persona_id)
        async with self._write_lock:
            await self.db.execute("BEGIN")
            try:
                cur_cult = await self.db.execute(
                    "DELETE FROM guild_cultures WHERE persona_id = ? AND scope_key = ?",
                    (culture_persona, scope_key)
                )
                cur_facts = await self.db.execute(
                    "DELETE FROM memory_facts WHERE persona_id = ? AND scope_key = ?",
                    (persona_id, scope_key)
                )
                await self.db.commit()
                return {
                    "cultures_deleted": cur_cult.rowcount or 0,
                    "facts_deleted": cur_facts.rowcount or 0,
                }
            except Exception:
                await self.db.rollback()
                raise

    # --- Pending Messages Buffer ---

    async def append_pending_message(self, msg: PendingMessage) -> int:
        assert self.db is not None
        async with self._write_lock:
            cursor = await self.db.execute(
                """
                INSERT INTO pending_messages
                (channel_id, scope_key, persona_id, user_id, username, content, has_image, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.channel_id,
                    msg.scope_key,
                    msg.persona_id,
                    msg.user_id,
                    msg.username,
                    msg.content,
                    msg.has_image,
                    msg.created_at,
                )
            )
            await self.db.commit()
            msg.id = cursor.lastrowid
            return int(cursor.lastrowid)

    async def get_pending_messages(self, channel_id: str) -> List[PendingMessage]:
        assert self.db is not None
        async with self.db.execute(
            "SELECT * FROM pending_messages WHERE channel_id = ? ORDER BY id ASC",
            (channel_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                PendingMessage(
                    id=int(r["id"]),
                    channel_id=r["channel_id"],
                    scope_key=r["scope_key"],
                    persona_id=r["persona_id"] or "default",
                    user_id=r["user_id"],
                    username=r["username"],
                    content=r["content"],
                    has_image=int(r["has_image"] or 0),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    async def get_pending_channel_stats(self) -> List[Tuple[str, int, float]]:
        """Return (channel_id, message_count, latest_created_at) across all buffered channels."""
        assert self.db is not None
        async with self.db.execute(
            """
            SELECT channel_id, COUNT(*) AS cnt, MAX(created_at) AS last_ts
            FROM pending_messages
            GROUP BY channel_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [(r["channel_id"], int(r["cnt"]), float(r["last_ts"])) for r in rows]

    # --- Atomic Extraction Batch Commit ---

    async def commit_extraction_batch(
        self,
        channel_id: str,
        max_processed_id: int,
        keep_overlap: int,
        guild_culture: Optional[GuildCulture],
        user_profiles: List[UserProfile],
        facts_to_insert: List[MemoryFact],
        facts_to_update: List[MemoryFact],
        partition_persona_id: str,
        partition_scope_key: str,
        max_partition_facts: int = config.MEMORY_PARTITION_MAX_FACTS,
    ) -> List[int]:
        """
        Atomically commit extracted culture, user profiles, new/updated facts, cap pruning,
        and sliding-window cleanup of pending_messages. Returns list of pruned fact IDs.
        """
        assert self.db is not None
        pruned_ids: List[int] = []

        async with self._write_lock:
            await self.db.execute("BEGIN")
            try:
                if guild_culture is not None:
                    await self.db.execute(
                        """
                        INSERT INTO guild_cultures
                            (persona_id, scope_key, group_vibe, inside_jokes_json, speech_style, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(persona_id, scope_key) DO UPDATE SET
                            group_vibe = excluded.group_vibe,
                            inside_jokes_json = excluded.inside_jokes_json,
                            speech_style = excluded.speech_style,
                            updated_at = excluded.updated_at
                        """,
                        (
                            guild_culture.persona_id,
                            guild_culture.scope_key,
                            guild_culture.group_vibe,
                            json.dumps(guild_culture.inside_jokes[:6], ensure_ascii=False),
                            guild_culture.speech_style,
                            guild_culture.updated_at,
                        )
                    )

                for profile in user_profiles:
                    await self.db.execute(
                        """
                        INSERT INTO user_profiles
                            (persona_id, user_id, username, speaking_style, traits_and_interests, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(persona_id, user_id) DO UPDATE SET
                            username = excluded.username,
                            speaking_style = excluded.speaking_style,
                            traits_and_interests = excluded.traits_and_interests,
                            updated_at = excluded.updated_at
                        """,
                        (
                            profile.persona_id,
                            profile.user_id,
                            profile.username,
                            profile.speaking_style,
                            profile.traits_and_interests,
                            profile.updated_at,
                        )
                    )

                for fact in facts_to_update:
                    if fact.id is None or fact.embedding is None:
                        continue
                    blob = np.asarray(fact.embedding, dtype=np.float32).tobytes()
                    await self.db.execute(
                        """
                        UPDATE memory_facts
                        SET fact_text = ?, category = ?, importance = ?,
                            embedding = ?, embedding_dim = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            fact.fact_text,
                            fact.category,
                            fact.importance,
                            blob,
                            fact.embedding_dim,
                            fact.updated_at,
                            fact.id,
                        )
                    )

                for fact in facts_to_insert:
                    if fact.embedding is None:
                        continue
                    blob = np.asarray(fact.embedding, dtype=np.float32).tobytes()
                    cursor = await self.db.execute(
                        """
                        INSERT INTO memory_facts
                            (persona_id, scope_key, user_id, fact_text, category, importance,
                             embedding, embedding_dim, created_at, updated_at, last_recalled_at, recall_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fact.persona_id,
                            fact.scope_key,
                            fact.user_id,
                            fact.fact_text,
                            fact.category,
                            fact.importance,
                            blob,
                            fact.embedding_dim,
                            fact.created_at,
                            fact.updated_at,
                            fact.last_recalled_at,
                            fact.recall_count,
                        )
                    )
                    fact.id = int(cursor.lastrowid)

                # Enforce soft partition cap: prune lowest-importance / least-recently-recalled old facts
                if max_partition_facts > 0:
                    async with self.db.execute(
                        "SELECT COUNT(*) AS cnt FROM memory_facts WHERE persona_id = ? AND scope_key = ?",
                        (partition_persona_id, partition_scope_key)
                    ) as c:
                        count_row = await c.fetchone()
                        total = int(count_row["cnt"]) if count_row else 0

                    excess = total - max_partition_facts
                    if excess > 0:
                        async with self.db.execute(
                            """
                            SELECT id FROM memory_facts
                            WHERE persona_id = ? AND scope_key = ?
                            ORDER BY
                                CASE WHEN importance <= 2 THEN 0 ELSE 1 END ASC,
                                importance ASC,
                                COALESCE(last_recalled_at, created_at) ASC,
                                updated_at ASC
                            LIMIT ?
                            """,
                            (partition_persona_id, partition_scope_key, excess)
                        ) as c:
                            prune_rows = await c.fetchall()
                            pruned_ids = [int(r["id"]) for r in prune_rows]

                        if pruned_ids:
                            placeholders = ",".join("?" for _ in pruned_ids)
                            await self.db.execute(
                                f"DELETE FROM memory_facts WHERE id IN ({placeholders})",
                                pruned_ids
                            )

                # Trim processed pending messages while preserving last `keep_overlap` messages <= max_processed_id
                # and keeping any brand-new messages with id > max_processed_id untouched.
                if max_processed_id > 0:
                    if keep_overlap > 0:
                        await self.db.execute(
                            """
                            DELETE FROM pending_messages
                            WHERE channel_id = ?
                              AND id <= ?
                              AND id NOT IN (
                                  SELECT id FROM pending_messages
                                  WHERE channel_id = ? AND id <= ?
                                  ORDER BY id DESC
                                  LIMIT ?
                              )
                            """,
                            (channel_id, max_processed_id, channel_id, max_processed_id, keep_overlap)
                        )
                    else:
                        await self.db.execute(
                            "DELETE FROM pending_messages WHERE channel_id = ? AND id <= ?",
                            (channel_id, max_processed_id)
                        )

                await self.db.commit()
                return pruned_ids
            except Exception:
                await self.db.rollback()
                raise
