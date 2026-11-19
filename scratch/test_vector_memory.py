import os
import sys
import json
import time
import types as pytypes
import asyncio
import tempfile
import shutil
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.dialog_service import DialogService
from core.agent_engine import AgentEngine
from core.models import IncomingMessage
from utils.gemini_dialog_service import GeminiDialogService
from utils.memory_manager import MemoryManager
import cogs.ai_roleplay as ai_roleplay_cog
from core.memory import (
    MemoryStore,
    EmbeddingService,
    VectorIndex,
    MemoryFact,
    l2_normalize,
    normalize_persona_id,
    normalize_scope_key,
    normalize_user_id,
)
from core.memory_service import MemoryService, OVERLAP_COUNT


def _unit_vec(seed: int, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return l2_normalize(v)


class _FakeEmbedObj:
    def __init__(self, values):
        self.values = values


class _FakeEmbedResponse:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeModels:
    def __init__(self, text_to_vec=None, fail_embed=False, extraction_payload=None, chat_replies=None):
        self.text_to_vec = text_to_vec or {}
        self.fail_embed = fail_embed
        self.extraction_payload = extraction_payload or {}
        self.chat_replies = list(chat_replies or ["沒問題，我記住囉！"])
        self.generate_calls = []

    def embed_content(self, model, contents, config=None):
        if self.fail_embed:
            raise RuntimeError("Simulated Embedding API outage")
        items = [contents] if isinstance(contents, str) else list(contents)
        embs = []
        for item in items:
            if item in self.text_to_vec:
                vec = self.text_to_vec[item]
            else:
                vec = _unit_vec(abs(hash(item)) % 10000, dim=8)
            embs.append(_FakeEmbedObj(vec.tolist()))
        return _FakeEmbedResponse(embs)

    def generate_content(self, model, contents, config=None):
        self.generate_calls.append({"model": model, "contents": contents, "config": config})
        if config and getattr(config, "response_mime_type", None) == "application/json":
            text = json.dumps(self.extraction_payload, ensure_ascii=False)
        else:
            text = self.chat_replies.pop(0) if self.chat_replies else "OK"
        return pytypes.SimpleNamespace(text=text, candidates=[])


class _FakeAIClient:
    def __init__(self, models):
        self.models = models


async def test_vector_index_math_and_lru():
    print("[1] Testing VectorIndex L2 normalization, hybrid scoring, and LRU eviction...")
    idx = VectorIndex(expected_dim=8, max_partitions=2)

    # Empty partition safe search
    q = np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    assert idx.search("aemeath", "discord_guild:1", q) == []

    now = time.time()
    v_exact = l2_normalize([10, 0, 0, 0, 0, 0, 0, 0])
    v_old = l2_normalize([0.99, 0.14, 0, 0, 0, 0, 0, 0])
    v_orth = l2_normalize([0, 1, 0, 0, 0, 0, 0, 0])

    f1 = MemoryFact(id=1, persona_id="aemeath", scope_key="discord_guild:1", user_id="discord:u1",
                    fact_text="[小明] 喜歡吃豚骨拉麵", importance=4, embedding=v_exact, embedding_dim=8,
                    created_at=now, updated_at=now)
    f2 = MemoryFact(id=2, persona_id="aemeath", scope_key="discord_guild:1", user_id="discord:u1",
                    fact_text="[小明] 去年去過東京", importance=2, embedding=v_old, embedding_dim=8,
                    created_at=now - 60 * 86400, updated_at=now - 60 * 86400)
    f3 = MemoryFact(id=3, persona_id="aemeath", scope_key="discord_guild:1", user_id="discord:u1",
                    fact_text="[小明] 不相關事件", importance=5, embedding=v_orth, embedding_dim=8,
                    created_at=now, updated_at=now)

    idx.load_partition("aemeath", "discord_guild:1", [f2, f1, f3])
    results = idx.search("aemeath", "discord_guild:1", q, top_k=5, min_sim=0.45, now=now)
    assert len(results) == 2, f"Expected 2 results above 0.45, got {len(results)}"
    assert results[0].fact.id == 1, "Recent exact match should rank #1"
    assert results[1].fact.id == 2

    # LRU eviction check (max_partitions=2)
    idx.load_partition("aemeath", "discord_guild:2", [])
    idx.load_partition("mornye", "discord_guild:3", [])
    assert not idx.has_partition("aemeath", "discord_guild:1"), "Oldest partition should be evicted"
    assert idx.has_partition("aemeath", "discord_guild:2")
    assert idx.has_partition("mornye", "discord_guild:3")
    print("    PASS")


async def test_dialog_service_history_and_reset():
    print("[2] Testing DialogService int/str reset and ambient_context decoupling...")
    fake_models = _FakeModels(chat_replies=["第一輪回覆", "第二輪回覆"])
    ds = DialogService(ai_client=_FakeAIClient(fake_models))

    r1 = await ds.generate_response(
        channel_id=123456789,
        user_text="[小明]: 你好",
        persona="你是愛莉",
        ambient_context="【聊天室近況記錄】\n[路人]: 昨天聊超多\n---\n",
    )
    assert r1 == "第一輪回覆"

    # Stored history must NOT contain ambient_context
    hist = ds.channel_histories["123456789"]
    assert len(hist) == 2
    stored_user_text = hist[0].parts[0].text
    assert "【聊天室近況記錄】" not in stored_user_text
    assert stored_user_text == "[小明]: 你好"

    # Transient request sent to Gemini MUST contain ambient_context
    sent_contents = fake_models.generate_calls[0]["contents"]
    assert "【聊天室近況記錄】" in sent_contents[0].parts[0].text

    # Reset works with int AND str
    assert ds.reset_channel_history(123456789) is True
    assert ds.reset_channel_history(123456789) is False
    print("    PASS")


async def test_dedup_persistence_isolation_and_fallback():
    print("[3] Testing SQLite persistence, restart recovery, dedup >=0.88, persona/DM isolation, and fallback...")
    tmp_dir = tempfile.mkdtemp(prefix="dc_mem_test_")
    try:
        # Prepare legacy JSON files to test one-time migration
        os.makedirs(os.path.join(tmp_dir, "personas"), exist_ok=True)
        with open(os.path.join(tmp_dir, "personas", "active_persona.txt"), "w", encoding="utf-8") as f:
            f.write("persona_aemeath.txt")
        with open(os.path.join(tmp_dir, "guild_cultures.json"), "w", encoding="utf-8") as f:
            json.dump({"999": {"group_vibe": "歡樂", "inside_jokes": ["大保底"], "speech_style": "簡短"}}, f)
        with open(os.path.join(tmp_dir, "user_profiles.json"), "w", encoding="utf-8") as f:
            json.dump({"1001": {"username": "阿明", "speaking_style": "愛吐槽", "traits_and_interests": "喜歡貓"}}, f)

        v_cat1 = l2_normalize([1, 0.1, 0, 0, 0, 0, 0, 0])
        v_cat2 = l2_normalize([1, 0.12, 0, 0, 0, 0, 0, 0])  # cosine > 0.99 with v_cat1
        v_query = l2_normalize([1, 0.05, 0, 0, 0, 0, 0, 0])

        fact1_text = "[阿明] 養了一隻叫豆漿的橘貓"
        fact2_text = "[阿明] 養了一隻叫豆漿的橘貓，很愛吃罐頭"

        payload_1 = {
            "guild_culture": {"group_vibe": "輕鬆搞笑", "inside_jokes": ["大保底", "豆漿貓"], "speech_style": "快節奏"},
            "user_updates": {"discord:1001": {"username": "阿明", "speaking_style": "幽默", "traits_and_interests": "養橘貓豆漿、喜歡拉麵"}},
            "new_facts": [{"user_id": "discord:1001", "fact_text": fact1_text, "category": "preference", "importance": 4}],
        }
        models = _FakeModels(
            text_to_vec={fact1_text: v_cat1, fact2_text: v_cat2, "豆漿最近怎樣": v_query},
            extraction_payload=payload_1,
        )
        client = _FakeAIClient(models)
        db_path = os.path.join(tmp_dir, "memory.db")

        # Boot #1: triggers legacy JSON migration + buffer 10 messages (simulating crash/restart before extraction)
        ms1 = MemoryService(ai_client=client, data_dir=tmp_dir, db_path=db_path, embedding_dim=8)
        await ms1.initialize()

        assert os.path.exists(os.path.join(tmp_dir, "guild_cultures.json.bak"))
        assert os.path.exists(os.path.join(tmp_dir, "user_profiles.json.bak"))

        for i in range(10):
            await ms1.add_message(
                channel_id="ch_1",
                scope_key=normalize_scope_key("999", "1001"),
                persona_id="persona_aemeath.txt",
                user_id="1001",
                username="阿明",
                content=f"對話測試第 {i} 句：我家橘貓豆漿超可愛",
                trigger_background=False,
            )
        await ms1.close()

        # Boot #2: verify pending_messages survived restart and extraction succeeds
        ms2 = MemoryService(ai_client=client, data_dir=tmp_dir, db_path=db_path, embedding_dim=8)
        await ms2.initialize()
        stats = await ms2.store.get_pending_channel_stats()
        assert len(stats) == 1 and stats[0][1] == 10, f"Expected 10 persisted pending messages, got {stats}"

        await ms2.extract_memories("ch_1")
        remaining_pending = await ms2.store.get_pending_messages("ch_1")
        assert len(remaining_pending) == OVERLAP_COUNT, f"Expected {OVERLAP_COUNT} overlap messages, got {len(remaining_pending)}"

        facts = await ms2.store.get_facts_by_partition("aemeath", "discord_guild:999", expected_dim=8)
        assert len(facts) == 1 and facts[0].fact_text == fact1_text

        # Second extraction with >= 0.88 similar fact -> must merge in place (still 1 row!)
        models.extraction_payload = {
            "guild_culture": payload_1["guild_culture"],
            "user_updates": payload_1["user_updates"],
            "new_facts": [{"user_id": "discord:1001", "fact_text": fact2_text, "category": "preference", "importance": 5}],
        }
        await ms2.add_message(
            channel_id="ch_1",
            scope_key="discord_guild:999",
            persona_id="aemeath",
            user_id="1001",
            username="阿明",
            content="豆漿超愛吃罐頭",
            trigger_background=False,
        )
        await ms2.extract_memories("ch_1")
        facts_after_dedup = await ms2.store.get_facts_by_partition("aemeath", "discord_guild:999", expected_dim=8)
        assert len(facts_after_dedup) == 1, f"Expected semantic dedup merge to keep 1 fact, got {len(facts_after_dedup)}"
        assert facts_after_dedup[0].fact_text == fact2_text
        assert facts_after_dedup[0].importance == 5

        # Persona Isolation: Aemeath recalls it; Mornye does NOT
        ctx_aemeath = await ms2.retrieve_context("aemeath", "discord_guild:999", "1001", "豆漿最近怎樣")
        assert "豆漿" in ctx_aemeath
        ctx_mornye = await ms2.retrieve_context("mornye", "discord_guild:999", "1001", "豆漿最近怎樣")
        assert "豆漿" not in ctx_mornye and ctx_mornye == ""

        # DM Isolation: User 1001 vs User 2002
        await ms2.add_message("dm_1001", normalize_scope_key(None, "1001"), "aemeath", "1001", "阿明", "秘密A", trigger_background=False)
        models.extraction_payload = {
            "guild_culture": {"group_vibe": "私訊", "inside_jokes": [], "speech_style": "親切"},
            "user_updates": {},
            "new_facts": [{"user_id": "discord:1001", "fact_text": "[阿明] 秘密密碼是1234", "category": "general", "importance": 4}],
        }
        await ms2.extract_memories("dm_1001")
        ctx_dm_other = await ms2.retrieve_context("aemeath", normalize_scope_key(None, "2002"), "2002", "秘密密碼")
        assert "1234" not in ctx_dm_other

        # Graceful degradation when Embedding API fails
        models.fail_embed = True
        ctx_degraded = await ms2.retrieve_context("aemeath", "discord_guild:999", "1001", "豆漿最近怎樣")
        assert "群體文化與氛圍" in ctx_degraded and "專屬印象" in ctx_degraded
        assert "相關歷史記憶片段" not in ctx_degraded

        # Failed embedding during extraction preserves pending_messages ("先算後清")
        before_cnt = len(await ms2.store.get_pending_messages("ch_1"))
        await ms2.add_message("ch_1", "discord_guild:999", "aemeath", "1001", "阿明", "不能遺失的訊息", trigger_background=False)
        await ms2.extract_memories("ch_1")
        after_cnt = len(await ms2.store.get_pending_messages("ch_1"))
        assert after_cnt == before_cnt + 1, "Pending messages must not be trimmed when embedding fails"

        # Forget fact & clear user memory
        models.fail_embed = False
        deleted = await ms2.forget_facts(persona_id="aemeath", scope_key="discord_guild:999", keyword="豆漿")
        assert deleted == 1
        snap = await ms2.get_memory_snapshot("aemeath", "discord_guild:999", "1001")
        assert len(snap["top_facts"]) == 0

        await ms2.close()
        print("    PASS")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_agent_engine_gemini_dialog_and_cap_reembed():
    print("[4] Testing AgentEngine, GeminiDialogService parallel gather, partition cap, and re-embed...")
    import datetime
    tmp_dir = tempfile.mkdtemp(prefix="dc_agent_test_")
    try:
        db_path = os.path.join(tmp_dir, "memory.db")
        v1 = l2_normalize([1, 0, 0, 0, 0, 0, 0, 0])
        models = _FakeModels(
            text_to_vec={"事實1": v1, "問一下": v1},
            chat_replies=["愛莉記得你喔！"],
        )
        client = _FakeAIClient(models)
        ms = MemoryService(ai_client=client, data_dir=tmp_dir, db_path=db_path, embedding_dim=8)
        await ms.initialize()

        # Test partition cap enforcement (max_facts=2)
        f_low = MemoryFact(persona_id="default", scope_key="discord_guild:1", fact_text="低重要度", importance=1,
                           embedding=_unit_vec(1, 8), embedding_dim=8, created_at=10.0, updated_at=10.0)
        f_mid = MemoryFact(persona_id="default", scope_key="discord_guild:1", fact_text="中重要度", importance=3,
                           embedding=_unit_vec(2, 8), embedding_dim=8, created_at=20.0, updated_at=20.0)
        f_high = MemoryFact(persona_id="default", scope_key="discord_guild:1", fact_text="高重要度", importance=5,
                            embedding=_unit_vec(3, 8), embedding_dim=8, created_at=30.0, updated_at=30.0)
        pruned = await ms.store.commit_extraction_batch(
            channel_id="cap_ch", max_processed_id=0, keep_overlap=0,
            guild_culture=None, user_profiles=[],
            facts_to_insert=[f_low, f_mid, f_high], facts_to_update=[],
            partition_persona_id="default", partition_scope_key="discord_guild:1", max_partition_facts=2,
        )
        assert pruned == [f_low.id], f"Expected lowest-importance fact pruned, got {pruned}"

        # Test AgentEngine with injected MemoryService
        engine = AgentEngine(api_key=None, personas_dir=os.path.join(tmp_dir, "personas"), data_dir=tmp_dir, memory_service=ms)
        engine.dialog_service.ai_client = client
        resp = await engine.process_message(
            IncomingMessage(
                channel_type="discord",
                channel_id="ch_eng",
                guild_id="1",
                user_id="u_eng",
                username="測試員",
                text="問一下",
                created_at=datetime.datetime.now(datetime.timezone.utc),
                is_direct_chat=True,
            )
        )
        assert resp is not None and resp.reply_text == "愛莉記得你喔！"
        pending = await ms.store.get_pending_messages("ch_eng")
        assert len(pending) == 1

        # Test GeminiDialogService parallel gather with mocked ContextBuilder
        class _FakeContextBuilder:
            @staticmethod
            def has_image_intent(_): return False
            async def fetch_recent_channel_context(self, **kwargs):
                return "【聊天室近況記錄】\n[剛才] [群友]: 嗨\n---\n", []

        class _FakePersonaMgr:
            current_persona_name = "persona_default.txt"
            persona_display_name = "愛莉"
            persona = "你是愛莉"
            def get_msg(self, key, **kwargs): return key

        class _FakeChannel:
            guild = pytypes.SimpleNamespace(id=1)

        models.chat_replies.append("並行檢索成功！")
        gds = GeminiDialogService(api_key=None, memory_service=ms)
        gds.ai_client = client
        g_reply = await gds.generate_response(
            channel_id=555,
            user_text="[測試員]: 問一下",
            persona_manager=_FakePersonaMgr(),
            context_builder=_FakeContextBuilder(),
            user_id=999,
            channel=_FakeChannel(),
            bot_user=object(),
        )
        assert g_reply == "並行檢索成功！"
        assert "555" in gds.channel_histories and len(gds.channel_histories["555"]) == 2
        assert "【聊天室近況記錄】" not in gds.channel_histories["555"][0].parts[0].text

        # Test re-embedding on dimension/model change
        await ms.close()
        ms_reembed = MemoryService(
            ai_client=client, data_dir=tmp_dir, db_path=db_path,
            embedding_model="new-embedding-model", embedding_dim=8
        )
        await ms_reembed.initialize()
        assert ms_reembed.store.embedding_mismatch is False
        await ms_reembed.close()
        assert hasattr(ai_roleplay_cog, "AIRoleplayCog")
        print("    PASS")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def main():
    await test_vector_index_math_and_lru()
    await test_dialog_service_history_and_reset()
    await test_dedup_persistence_isolation_and_fallback()
    await test_agent_engine_gemini_dialog_and_cap_reembed()
    print("\nALL VECTOR MEMORY TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
