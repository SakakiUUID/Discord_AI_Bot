import os
import re
import json
import time
import asyncio
import logging
from collections import deque
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from google import genai
from google.genai import types

import config
from core.context_manager import ContextManager
from core.memory import (
    MemoryStore,
    EmbeddingService,
    VectorIndex,
    GuildCulture,
    UserProfile,
    MemoryFact,
    ScoredFact,
    PendingMessage,
    normalize_persona_id,
    get_culture_persona_key,
    normalize_scope_key,
    normalize_user_id,
)

logger = logging.getLogger("core.memory_service")

DATA_DIR = "data"
GUILD_CULTURES_FILE = os.path.join(DATA_DIR, "guild_cultures.json")
USER_PROFILES_FILE = os.path.join(DATA_DIR, "user_profiles.json")

BUFFER_THRESHOLD = 30           # Track 1: Immediate extraction threshold
MIN_IDLE_MESSAGES = 8           # Track 2: Minimum messages needed for idle trigger
IDLE_TIMEOUT_SECONDS = 900      # Track 2: 15 minutes idle timeout
OVERLAP_COUNT = 5               # 5-message sliding overlap window across extractions
MAX_IMAGES_PER_CHANNEL = 3      # In-memory recent image cache cap per channel

VALID_CATEGORIES = {"preference", "event", "relationship", "goal", "general"}


def _strip_json_fences(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip() or "{}"


class MemoryService:
    """
    Facade coordinating SQLite persistence (MemoryStore), Gemini Embeddings (EmbeddingService),
    partitioned NumPy retrieval (VectorIndex), and dual-track background memory extraction.
    """

    def __init__(
        self,
        ai_client: Optional[genai.Client] = None,
        data_dir: str = DATA_DIR,
        db_path: Optional[str] = None,
        embedding_model: str = config.GEMINI_EMBEDDING_MODEL,
        embedding_dim: int = config.MEMORY_EMBEDDING_DIM,
        max_cached_partitions: int = 64,
    ):
        self.data_dir = data_dir
        resolved_db_path = db_path or (
            config.MEMORY_DB_PATH if data_dir == DATA_DIR else os.path.join(data_dir, "memory.db")
        )
        self.ai_client = ai_client
        self.store = MemoryStore(
            db_path=resolved_db_path,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
        )
        self.embedding_service = EmbeddingService(
            ai_client=ai_client,
            model=embedding_model,
            dim=embedding_dim,
        )
        self.vector_index = VectorIndex(
            expected_dim=embedding_dim,
            max_partitions=max_cached_partitions,
        )

        self.channel_image_cache: Dict[str, deque] = {}
        self.extracting_channels: set[str] = set()
        self.idle_worker_task: Optional[asyncio.Task] = None
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def initialize(self):
        """Initialize SQLite store, migrate legacy JSON if present, and start idle worker."""
        async with self._init_lock:
            if self._initialized:
                return
            await self.store.initialize()
            if self.store.embedding_mismatch and self.ai_client:
                await self.reembed_incompatible_facts()
            self._initialized = True
        self.start_idle_worker()

    async def close(self):
        """Stop background idle worker, clear vector cache, and close SQLite connection."""
        if self.idle_worker_task and not self.idle_worker_task.done():
            self.idle_worker_task.cancel()
            try:
                await self.idle_worker_task
            except asyncio.CancelledError:
                pass
            self.idle_worker_task = None

        self.vector_index.clear_all()
        self.channel_image_cache.clear()
        await self.store.close()
        self._initialized = False

    async def _ensure_initialized(self):
        if not self._initialized:
            await self.initialize()

    async def reembed_incompatible_facts(self) -> int:
        """Rebuild embeddings for facts whose dimension/model differs from active config."""
        if not self.ai_client:
            return 0
        facts = await self.store.get_incompatible_facts(force_all=self.store.embedding_mismatch)
        if not facts:
            await self.store.update_embedding_meta()
            return 0

        texts = [f.fact_text for f in facts]
        vectors = await self.embedding_service.embed_documents(texts)
        if vectors is None:
            logger.warning("Re-embedding aborted because EmbeddingService failed; incompatible facts remain isolated.")
            return 0

        updated = 0
        for fact, vec in zip(facts, vectors):
            if fact.id is not None and vec is not None:
                await self.store.update_fact_embedding(fact.id, vec, int(vec.size))
                updated += 1

        await self.store.update_embedding_meta()
        self.vector_index.clear_all()
        logger.info(f"🔄 [MemoryService] Re-embedded {updated} memory facts for model {self.embedding_service.model}.")
        return updated

    def start_idle_worker(self):
        """Start the background idle check worker loop if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                if self.idle_worker_task is None or self.idle_worker_task.done():
                    self.idle_worker_task = asyncio.create_task(self._idle_check_worker())
        except RuntimeError:
            pass

    async def _idle_check_worker(self):
        """Background loop checking SQLite pending_messages for idle channels to trigger extraction."""
        logger.info("🌙 [MemoryService] Idle check worker loop started.")
        try:
            while True:
                await asyncio.sleep(60)
                if not self.store.is_connected:
                    continue
                now = time.time()
                stats = await self.store.get_pending_channel_stats()
                for channel_id, count, last_ts in stats:
                    if channel_id in self.extracting_channels:
                        continue
                    if count >= MIN_IDLE_MESSAGES and (now - last_ts) >= IDLE_TIMEOUT_SECONDS:
                        logger.info(
                            f"🌙 [MemoryService] Channel {channel_id} idle for >=15m with {count} pending msgs. "
                            "Triggering extraction."
                        )
                        asyncio.create_task(self.extract_memories(channel_id))
        except asyncio.CancelledError:
            logger.info("MemoryService idle check worker stopped.")
        except Exception as e:
            logger.error(f"Error in MemoryService idle worker: {e}", exc_info=True)

    async def add_message(
        self,
        channel_id: Any,
        scope_key: str,
        persona_id: str,
        user_id: Any,
        username: str,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None,
        trigger_background: bool = True,
        platform: str = "discord",
    ):
        """Persist non-bot text message into pending_messages and keep up to 3 recent images in RAM."""
        await self._ensure_initialized()

        clean_content = (content or "").strip()
        if not clean_content and not image_bytes:
            return

        cid = str(channel_id)
        pid = normalize_persona_id(persona_id)
        uid = normalize_user_id(user_id, platform=platform)

        has_image = 0
        if image_bytes and image_mime:
            has_image = 1
            if cid not in self.channel_image_cache:
                self.channel_image_cache[cid] = deque(maxlen=MAX_IMAGES_PER_CHANNEL)
            self.channel_image_cache[cid].append((image_bytes, image_mime))

        await self.store.append_pending_message(
            PendingMessage(
                channel_id=cid,
                scope_key=scope_key,
                persona_id=pid,
                user_id=uid,
                username=username or uid,
                content=clean_content,
                has_image=has_image,
                created_at=time.time(),
            )
        )

        if trigger_background and cid not in self.extracting_channels:
            pending = await self.store.get_pending_messages(cid)
            if len(pending) >= BUFFER_THRESHOLD:
                asyncio.create_task(self.extract_memories(cid))

    async def add_message_to_buffer(
        self,
        channel_id: Any,
        guild_id: Optional[Any],
        user_id: Any,
        username: str,
        content: str,
        image_bytes: Optional[bytes] = None,
        image_mime: Optional[str] = None,
        persona_id: str = "default",
        platform: str = "discord",
    ):
        """Backwards-compatible helper normalizing scope_key and delegating to add_message."""
        scope_key = normalize_scope_key(guild_id, user_id, platform=platform)
        await self.add_message(
            channel_id=channel_id,
            scope_key=scope_key,
            persona_id=persona_id,
            user_id=user_id,
            username=username,
            content=content,
            image_bytes=image_bytes,
            image_mime=image_mime,
            platform=platform,
        )

    async def _ensure_partition_loaded(self, persona_id: str, scope_key: str):
        if not self.vector_index.has_partition(persona_id, scope_key):
            facts = await self.store.get_facts_by_partition(persona_id, scope_key)
            self.vector_index.load_partition(persona_id, scope_key, facts)

    @staticmethod
    def _build_extraction_prompt(
        existing_culture_dict: Dict[str, Any],
        existing_profiles_dict: Dict[str, Dict[str, Any]],
        transcript: str,
    ) -> str:
        return f"""
你是一個專門分析社群對話、個人特徵與長期事件記憶的多模態 AI 記憶萃取引擎。
請分析以下來自通訊頻道最近的對話紀錄（包含附帶的圖片/梗圖），並產出：
1. 【群體文化與內部梗】(`guild_culture`)
2. 【活躍使用者的常駐印象】(`user_updates`)
3. 【具體可檢索的原子事實/事件記憶】(`new_facts`)

【現有的群體文化記憶】
{json.dumps(existing_culture_dict, ensure_ascii=False, indent=2)}

【現有的發言者個人印象記憶 (請在此基礎上進行增量更新/融合)】
{json.dumps(existing_profiles_dict, ensure_ascii=False, indent=2)}

【最近的對話紀錄 (包含附帶圖片)】
{transcript}

【內部梗 (Inside Jokes) 動態審核與淘汰標準】：
1. 審核原則（動態全生命週期維護）：
   - 【保留】：群友仍經常提起、或具備長期高度共鳴的核心梗。
   - 【淘汰移除】：若現有清單中的某個舊梗已時過境遷、最近完全無人提起、或重要性下降，請直接將其從清單中移除淘汰！
   - 【收錄新梗】：必須是群友「反覆提及、集體吐槽、自嘲、具備強烈迷因感或共同經歷笑點」的專屬話題 (例: 大保底歪莫寧、加班地獄)。
2. 嚴格禁止收錄：日常瑣事 (如吃便當、洗澡、睡過頭、天氣好)、正常技術/課業問答、普通打招呼。
3. 數量收斂：最終 "inside_jokes" 請回傳動態維護後的最新精華清單（維持在 3 ~ 6 個最具代表性的核心梗）。若對話平淡無新梗且無須淘汰，請保留現有核心梗。

【原子事實記憶 (new_facts) 萃取規則】：
1. 僅萃取具體、未來可能會被提起的長期事實、明確偏好、人物關係、重要目標或顯著事件。
2. 每筆 `fact_text` 須為簡短獨立完整的句子，開頭標註主角名稱（例：`[阿明] 養了一隻叫豆漿的橘貓`、`[群組事件] 大家約好週六晚上打瓦羅蘭`）。
3. 禁止收錄無意義的寒暄、一次性問候、或沒有具體資訊量的閒聊；若本批沒有值得長期記住的新事實，請回傳空陣列 `[]`。
4. `category` 僅限：`"preference"` / `"event"` / `"relationship"` / `"goal"` / `"general"`。
5. `importance` 為 1～5 的整數（1=輕微瑣事、3=一般值得記住的喜好/事件、5=核心長期設定或重大事件）。
6. `user_id` 請填寫該事實對應的發言者 ID（需與紀錄中的 ID 一致），若屬群組共同事件可填 `null`。

請回傳嚴格的 JSON 格式（不要包含額外的 Markdown 說明文字）：
{{
  "guild_culture": {{
    "group_vibe": "融合現有記憶與最新對話，一句話總結群組氛圍",
    "inside_jokes": ["動態審核後的內部梗精華清單 (3~6個)"],
    "speech_style": "群體說話風格摘要"
  }},
  "user_updates": {{
    "user_id_xxx": {{
      "username": "使用者名稱",
      "speaking_style": "綜合評估該用戶的說話習慣與語氣特徵",
      "traits_and_interests": "【增量融合】保留長期核心特質並融合新興趣與話題 (約 40~90 字)"
    }}
  }},
  "new_facts": [
    {{
      "user_id": "user_id_xxx 或 null",
      "fact_text": "[使用者名稱] 具體可檢索的事實描述",
      "category": "preference",
      "importance": 3
    }}
  ]
}}
"""

    async def extract_memories(self, channel_id: Any):
        """
        Background worker summarizing guild culture, user profiles, and atomic vector facts.
        Persists atomically and trims pending_messages only after successful commit.
        """
        await self._ensure_initialized()
        if not self.ai_client:
            return

        cid = str(channel_id)
        if cid in self.extracting_channels:
            return

        self.extracting_channels.add(cid)
        try:
            pending = await self.store.get_pending_messages(cid)
            if not pending:
                return

            max_processed_id = pending[-1].id or 0
            scope_key = pending[-1].scope_key
            persona_id = normalize_persona_id(pending[-1].persona_id)
            culture_persona_key = get_culture_persona_key(persona_id)

            # Build transcript (text from SQLite, up to 3 latest images from RAM cache)
            cached_images = list(self.channel_image_cache.get(cid, ()))
            image_parts: List[types.Part] = []
            for img_bytes, img_mime in cached_images[-MAX_IMAGES_PER_CHANNEL:]:
                image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=img_mime))

            transcript_lines = []
            for msg in pending:
                img_tag = "(附圖) " if msg.has_image else ""
                transcript_lines.append(f"[{msg.username} (ID: {msg.user_id})]: {img_tag}{msg.content}")
            if image_parts:
                transcript_lines.append(f"（本批附加了最近 {len(image_parts)} 張對話圖片供視覺分析）")
            transcript = "\n".join(transcript_lines)

            existing_culture = await self.store.get_guild_culture(culture_persona_key, scope_key)
            existing_culture_dict = (
                {
                    "group_vibe": existing_culture.group_vibe,
                    "inside_jokes": existing_culture.inside_jokes,
                    "speech_style": existing_culture.speech_style,
                }
                if existing_culture
                else {}
            )

            active_user_ids = sorted({m.user_id for m in pending})
            existing_profiles = await self.store.get_user_profiles(persona_id, active_user_ids)
            existing_profiles_dict = {
                uid: {
                    "username": prof.username,
                    "speaking_style": prof.speaking_style,
                    "traits_and_interests": prof.traits_and_interests,
                }
                for uid, prof in existing_profiles.items()
            }

            prompt = self._build_extraction_prompt(existing_culture_dict, existing_profiles_dict, transcript)
            contents: List[types.Part] = list(image_parts)
            contents.append(types.Part.from_text(text=prompt))

            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model=config.GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )

            raw_json = _strip_json_fences(response.text or "{}")
            result_data = json.loads(raw_json)
            if not isinstance(result_data, dict):
                logger.warning("Gemini extraction did not return a JSON object; aborting trim to preserve pending msgs.")
                return

            now = time.time()

            # 1. Prepare GuildCulture update
            guild_culture_obj: Optional[GuildCulture] = None
            raw_culture = result_data.get("guild_culture")
            if isinstance(raw_culture, dict):
                managed_jokes = raw_culture.get("inside_jokes", [])
                if existing_culture is None:
                    cleaned_jokes = (
                        [str(j).strip() for j in managed_jokes if str(j).strip()][:6]
                        if isinstance(managed_jokes, list)
                        else []
                    )
                    guild_culture_obj = GuildCulture(
                        persona_id=culture_persona_key,
                        scope_key=scope_key,
                        group_vibe=str(raw_culture.get("group_vibe", "")),
                        inside_jokes=cleaned_jokes,
                        speech_style=str(raw_culture.get("speech_style", "")),
                        updated_at=now,
                    )
                else:
                    if isinstance(managed_jokes, list) and managed_jokes:
                        cleaned_jokes = [str(j).strip() for j in managed_jokes if str(j).strip()][:6]
                    else:
                        cleaned_jokes = existing_culture.inside_jokes
                    guild_culture_obj = GuildCulture(
                        persona_id=culture_persona_key,
                        scope_key=scope_key,
                        group_vibe=str(raw_culture.get("group_vibe", existing_culture.group_vibe)),
                        inside_jokes=cleaned_jokes,
                        speech_style=str(raw_culture.get("speech_style", existing_culture.speech_style)),
                        updated_at=now,
                    )

            # 2. Prepare UserProfile updates
            profile_updates: List[UserProfile] = []
            raw_users = result_data.get("user_updates")
            if isinstance(raw_users, dict):
                for raw_uid, udata in raw_users.items():
                    if not isinstance(udata, dict):
                        continue
                    uid = normalize_user_id(raw_uid)
                    prev = existing_profiles.get(uid)
                    profile_updates.append(
                        UserProfile(
                            persona_id=persona_id,
                            user_id=uid,
                            username=str(udata.get("username", prev.username if prev else "")),
                            speaking_style=str(udata.get("speaking_style", prev.speaking_style if prev else "")),
                            traits_and_interests=str(
                                udata.get("traits_and_interests", prev.traits_and_interests if prev else "")
                            ),
                            updated_at=now,
                        )
                    )

            # 3. Prepare & embed new_facts with write-time semantic deduplication (>= 0.88)
            facts_to_insert: List[MemoryFact] = []
            facts_to_update: List[MemoryFact] = []
            raw_facts = result_data.get("new_facts")
            parsed_facts: List[Dict[str, Any]] = []

            if isinstance(raw_facts, list):
                for item in raw_facts:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("fact_text", "")).strip()
                    if not text:
                        continue
                    raw_fuid = item.get("user_id")
                    fuid = normalize_user_id(raw_fuid) if raw_fuid not in (None, "", "null", "None") else None
                    cat = str(item.get("category", "general")).strip().lower()
                    if cat not in VALID_CATEGORIES:
                        cat = "general"
                    try:
                        imp = max(1, min(5, int(item.get("importance", 3))))
                    except (TypeError, ValueError):
                        imp = 3
                    parsed_facts.append({
                        "user_id": fuid,
                        "fact_text": text,
                        "category": cat,
                        "importance": imp,
                    })

            if parsed_facts:
                embeddings = await self.embedding_service.embed_documents([f["fact_text"] for f in parsed_facts])
                if embeddings is None:
                    logger.warning(
                        f"⚠️ [MemoryService] Embedding failed for channel {cid}; "
                        "aborting batch commit to preserve pending_messages for retry."
                    )
                    return

                await self._ensure_partition_loaded(persona_id, scope_key)

                for fdict, vec in zip(parsed_facts, embeddings):
                    if vec is None:
                        continue
                    fuid = fdict["user_id"]
                    match = self.vector_index.find_most_similar(persona_id, scope_key, vec, user_id=fuid)

                    # Also check against new inserts queued in this same batch
                    batch_match: Optional[Tuple[MemoryFact, float]] = None
                    for pending_fact in facts_to_insert:
                        if pending_fact.user_id == fuid and pending_fact.embedding is not None:
                            sim = float(np.dot(pending_fact.embedding, vec))
                            if batch_match is None or sim > batch_match[1]:
                                batch_match = (pending_fact, sim)

                    best_match = match
                    if batch_match and (best_match is None or batch_match[1] > best_match[1]):
                        best_match = batch_match

                    if best_match and best_match[1] >= config.MEMORY_DEDUP_THRESHOLD:
                        existing_fact = best_match[0]
                        existing_fact.fact_text = fdict["fact_text"]
                        existing_fact.category = fdict["category"]
                        existing_fact.importance = max(existing_fact.importance, fdict["importance"])
                        existing_fact.embedding = vec
                        existing_fact.embedding_dim = int(vec.size)
                        existing_fact.updated_at = now
                        if existing_fact.id is not None and existing_fact not in facts_to_update:
                            facts_to_update.append(existing_fact)
                        logger.info(
                            f"🔁 [Memory Dedup] Merged fact (sim={best_match[1]:.3f}): '{fdict['fact_text']}'"
                        )
                    else:
                        facts_to_insert.append(
                            MemoryFact(
                                persona_id=persona_id,
                                scope_key=scope_key,
                                user_id=fuid,
                                fact_text=fdict["fact_text"],
                                category=fdict["category"],
                                importance=fdict["importance"],
                                embedding=vec,
                                embedding_dim=int(vec.size),
                                created_at=now,
                                updated_at=now,
                            )
                        )

            # 4. Atomic SQLite commit + pending trim + partition cap enforcement
            pruned_ids = await self.store.commit_extraction_batch(
                channel_id=cid,
                max_processed_id=max_processed_id,
                keep_overlap=OVERLAP_COUNT,
                guild_culture=guild_culture_obj,
                user_profiles=profile_updates,
                facts_to_insert=facts_to_insert,
                facts_to_update=facts_to_update,
                partition_persona_id=persona_id,
                partition_scope_key=scope_key,
                max_partition_facts=config.MEMORY_PARTITION_MAX_FACTS,
            )

            # 5. Write-through update VectorIndex cache
            if pruned_ids:
                self.vector_index.remove_facts(persona_id, scope_key, pruned_ids)
            for fact in facts_to_update:
                self.vector_index.upsert_fact(fact)
            for fact in facts_to_insert:
                self.vector_index.upsert_fact(fact)

            # Clear cached images that were just processed
            if cid in self.channel_image_cache:
                self.channel_image_cache[cid].clear()

            logger.info(
                f"🧠 [MemoryService] Extraction committed for channel={cid}, persona={persona_id}, scope={scope_key}: "
                f"+{len(facts_to_insert)} new facts, ~{len(facts_to_update)} updated, -{len(pruned_ids)} pruned."
            )

        except Exception as e:
            logger.error(f"Error extracting memories in background for channel {cid}: {e}", exc_info=True)
        finally:
            self.extracting_channels.discard(cid)

    async def retrieve_context(
        self,
        persona_id: str,
        scope_key: str,
        user_id: Any,
        query_text: str = "",
        top_k: int = config.MEMORY_TOP_K,
    ) -> str:
        """
        Retrieve GuildCulture + UserProfile + Top-K semantically recalled MemoryFacts
        for the specified (persona_id, scope_key, user_id). Degrades gracefully on embedding failure.
        """
        await self._ensure_initialized()

        pid = normalize_persona_id(persona_id)
        uid = normalize_user_id(user_id)
        culture_pid = get_culture_persona_key(pid)

        culture = await self.store.get_guild_culture(culture_pid, scope_key)
        profile = await self.store.get_user_profile(pid, uid)

        scored_facts: List[ScoredFact] = []
        clean_query = (query_text or "").strip()
        if clean_query:
            t0 = time.perf_counter()
            query_vec = await self.embedding_service.embed_query(clean_query)
            if query_vec is not None:
                await self._ensure_partition_loaded(pid, scope_key)
                scored_facts = self.vector_index.search(
                    persona_id=pid,
                    scope_key=scope_key,
                    query_vec=query_vec,
                    top_k=top_k,
                    min_sim=config.MEMORY_MIN_SIMILARITY,
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                max_sim = max((s.cosine_sim for s in scored_facts), default=0.0)
                stats = self.vector_index.get_stats()
                logger.info(
                    f"🔍 [Vector Recall] persona={pid} scope={scope_key} hits={len(scored_facts)} "
                    f"max_sim={max_sim:.3f} elapsed={elapsed_ms:.1f}ms "
                    f"cache=({stats['partitions']} partitions, {stats['vectors']} vectors)"
                )

                if scored_facts:
                    now = time.time()
                    hit_ids = [s.fact.id for s in scored_facts if s.fact.id is not None]
                    if hit_ids:
                        await self.store.mark_facts_recalled(hit_ids, now)
                        self.vector_index.mark_recalled(pid, scope_key, hit_ids, now)

        context_parts: List[str] = []

        if culture:
            jokes_text = "、".join(culture.inside_jokes) if culture.inside_jokes else ""
            culture_str = (
                f"【群體文化與氛圍 (Guild Culture)】\n"
                f"- 群組氛圍：{culture.group_vibe}\n"
                f"- 說話習慣：{culture.speech_style}"
            )
            if jokes_text:
                culture_str += (
                    f"\n- 群友熟知的專屬內部梗：{jokes_text}\n"
                    f"  (注意：僅在話題高度相關時自然融入，嚴禁生硬硬塞！)"
                )
            context_parts.append(culture_str)

        if profile:
            display = profile.username or uid
            user_str = (
                f"【關於當前發言者 ({display}) 的專屬印象】\n"
                f"- 說話風格：{profile.speaking_style}\n"
                f"- 喜好與特徵：{profile.traits_and_interests}"
            )
            context_parts.append(user_str)

        if scored_facts:
            now = time.time()
            fact_lines = []
            for s in scored_facts:
                age_tag = ContextManager.format_relative_time(max(0.0, now - s.fact.updated_at))
                fact_lines.append(f"- {age_tag} {s.fact.fact_text}")
            context_parts.append(
                "【相關歷史記憶片段 (僅在自然相關時參考使用)】\n" + "\n".join(fact_lines)
            )

        if not context_parts:
            return ""

        return "【雙層記憶與向量長期回憶】\n" + "\n\n".join(context_parts) + "\n---"

    async def get_combined_context(
        self,
        guild_id: Optional[Any],
        user_id: Any,
        persona_id: str = "default",
        query_text: str = "",
        platform: str = "discord",
    ) -> str:
        """Backwards-compatible context lookup wrapper delegating to retrieve_context."""
        scope_key = normalize_scope_key(guild_id, user_id, platform=platform)
        return await self.retrieve_context(
            persona_id=persona_id,
            scope_key=scope_key,
            user_id=user_id,
            query_text=query_text,
        )

    # --- Inspection & Forget Commands Support ---

    async def get_memory_snapshot(
        self,
        persona_id: str,
        scope_key: str,
        user_id: Any,
        top_facts_limit: int = 5,
    ) -> Dict[str, Any]:
        """Return GuildCulture, UserProfile, and top recalled/recent facts for /view_memory."""
        await self._ensure_initialized()
        pid = normalize_persona_id(persona_id)
        uid = normalize_user_id(user_id)
        culture_pid = get_culture_persona_key(pid)

        culture = await self.store.get_guild_culture(culture_pid, scope_key)
        profile = await self.store.get_user_profile(pid, uid)
        top_facts = await self.store.get_top_facts(
            persona_id=pid,
            scope_key=scope_key,
            user_id=uid,
            limit=top_facts_limit,
        )
        return {
            "persona_id": pid,
            "scope_key": scope_key,
            "user_id": uid,
            "guild_culture": culture,
            "user_profile": profile,
            "top_facts": top_facts,
        }

    async def forget_facts(
        self,
        persona_id: str,
        scope_key: Optional[str] = None,
        user_id: Optional[str] = None,
        fact_id: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> int:
        """Delete matching memory facts from SQLite and synchronize VectorIndex cache."""
        await self._ensure_initialized()
        pid = normalize_persona_id(persona_id)
        norm_uid = normalize_user_id(user_id) if user_id is not None else None
        deleted = await self.store.delete_facts_by_id_or_keyword(
            persona_id=pid,
            scope_key=scope_key,
            user_id=norm_uid,
            fact_id=fact_id,
            keyword=keyword,
        )
        by_scope: Dict[str, List[int]] = {}
        for skey, fid in deleted:
            by_scope.setdefault(skey, []).append(fid)
        for skey, fids in by_scope.items():
            self.vector_index.remove_facts(pid, skey, fids)
        return len(deleted)

    async def clear_user_memory(
        self,
        persona_id: str,
        user_id: Any,
        scope_key: Optional[str] = None,
    ) -> Dict[str, int]:
        """Clear a user's profile and personal facts under the specified persona."""
        await self._ensure_initialized()
        pid = normalize_persona_id(persona_id)
        norm_uid = normalize_user_id(user_id)
        res = await self.store.clear_user_memory(persona_id=pid, user_id=norm_uid, scope_key=scope_key)
        by_scope: Dict[str, List[int]] = {}
        for skey, fid in res.get("deleted_targets", []):
            by_scope.setdefault(skey, []).append(fid)
        for skey, fids in by_scope.items():
            self.vector_index.remove_facts(pid, skey, fids)
        return {
            "profiles_deleted": int(res.get("profiles_deleted", 0)),
            "facts_deleted": int(res.get("facts_deleted", 0)),
        }

    async def clear_scope_memory(self, persona_id: str, scope_key: str) -> Dict[str, int]:
        """Admin reset: clear guild culture and all facts for (persona_id, scope_key)."""
        await self._ensure_initialized()
        pid = normalize_persona_id(persona_id)
        res = await self.store.clear_scope_memory(persona_id=pid, scope_key=scope_key)
        self.vector_index.clear_partition(pid, scope_key)
        return res
