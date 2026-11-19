import math
import time
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

import config
from core.memory.models import MemoryFact, ScoredFact

logger = logging.getLogger("core.memory.vector_index")


@dataclass
class _PartitionCache:
    fact_ids: List[int] = field(default_factory=list)
    facts: Dict[int, MemoryFact] = field(default_factory=dict)
    matrix: Optional[np.ndarray] = None  # Shape (N, dim), float32, L2-normalized rows


class VectorIndex:
    """
    Partitioned in-memory NumPy vector cache indexed by (persona_id, scope_key).
    Supports lazy loading, write-through updates, and LRU partition eviction.
    """

    def __init__(
        self,
        expected_dim: int = config.MEMORY_EMBEDDING_DIM,
        max_partitions: int = 64
    ):
        self.expected_dim = expected_dim
        self.max_partitions = max(1, max_partitions)
        self._partitions: OrderedDict[Tuple[str, str], _PartitionCache] = OrderedDict()

    def has_partition(self, persona_id: str, scope_key: str) -> bool:
        return (persona_id, scope_key) in self._partitions

    def _touch(self, key: Tuple[str, str]) -> _PartitionCache:
        self._partitions.move_to_end(key)
        return self._partitions[key]

    def _evict_if_needed(self):
        while len(self._partitions) > self.max_partitions:
            evicted_key, _ = self._partitions.popitem(last=False)
            logger.debug(f"Evicted LRU vector cache partition: {evicted_key}")

    def load_partition(self, persona_id: str, scope_key: str, facts: List[MemoryFact]):
        """Populate or replace a partition in cache from SQLite records."""
        key = (persona_id, scope_key)
        fact_ids: List[int] = []
        fact_map: Dict[int, MemoryFact] = {}
        vectors: List[np.ndarray] = []
        skipped = 0

        for fact in facts:
            if fact.id is None or fact.embedding is None:
                continue
            vec = np.asarray(fact.embedding, dtype=np.float32).ravel()
            if vec.size != self.expected_dim:
                skipped += 1
                continue
            fact_ids.append(fact.id)
            fact_map[fact.id] = fact
            vectors.append(vec)

        if skipped > 0:
            logger.warning(
                f"Skipped {skipped} incompatible vectors in partition {key} (expected dim={self.expected_dim})."
            )

        matrix = np.vstack(vectors).astype(np.float32) if vectors else np.empty((0, self.expected_dim), dtype=np.float32)
        self._partitions[key] = _PartitionCache(fact_ids=fact_ids, facts=fact_map, matrix=matrix)
        self._touch(key)
        self._evict_if_needed()

    def upsert_fact(self, fact: MemoryFact):
        """Write-through update or insert for an already-loaded partition."""
        if fact.id is None or fact.embedding is None:
            return
        key = (fact.persona_id, fact.scope_key)
        if key not in self._partitions:
            return

        vec = np.asarray(fact.embedding, dtype=np.float32).ravel()
        if vec.size != self.expected_dim:
            return

        entry = self._touch(key)
        if fact.id in entry.facts:
            idx = entry.fact_ids.index(fact.id)
            entry.facts[fact.id] = fact
            entry.matrix[idx] = vec
        else:
            entry.fact_ids.append(fact.id)
            entry.facts[fact.id] = fact
            if entry.matrix is None or entry.matrix.shape[0] == 0:
                entry.matrix = vec.reshape(1, self.expected_dim)
            else:
                entry.matrix = np.vstack([entry.matrix, vec.reshape(1, self.expected_dim)])

    def remove_facts(self, persona_id: str, scope_key: str, fact_ids: List[int]):
        """Remove deleted fact IDs from cache if partition is loaded."""
        if not fact_ids:
            return
        key = (persona_id, scope_key)
        if key not in self._partitions:
            return

        entry = self._touch(key)
        remove_set = set(fact_ids)
        keep_indices = [i for i, fid in enumerate(entry.fact_ids) if fid not in remove_set]

        for fid in remove_set:
            entry.facts.pop(fid, None)

        entry.fact_ids = [entry.fact_ids[i] for i in keep_indices]
        if keep_indices and entry.matrix is not None:
            entry.matrix = entry.matrix[keep_indices]
        else:
            entry.matrix = np.empty((0, self.expected_dim), dtype=np.float32)

    def mark_recalled(self, persona_id: str, scope_key: str, fact_ids: List[int], recalled_at: float):
        """Synchronize recall counters in cached metadata."""
        key = (persona_id, scope_key)
        entry = self._partitions.get(key)
        if not entry:
            return
        for fid in fact_ids:
            fact = entry.facts.get(fid)
            if fact:
                fact.last_recalled_at = recalled_at
                fact.recall_count += 1

    def clear_partition(self, persona_id: str, scope_key: str):
        self._partitions.pop((persona_id, scope_key), None)

    def clear_all(self):
        self._partitions.clear()

    def search(
        self,
        persona_id: str,
        scope_key: str,
        query_vec: np.ndarray,
        top_k: int = 5,
        min_sim: float = 0.45,
        half_life_days: float = 30.0,
        now: Optional[float] = None
    ) -> List[ScoredFact]:
        """Run hybrid cosine similarity + recency decay + importance search on a loaded partition."""
        key = (persona_id, scope_key)
        entry = self._partitions.get(key)
        if not entry or entry.matrix is None or entry.matrix.shape[0] == 0:
            return []

        self._touch(key)
        q = np.asarray(query_vec, dtype=np.float32).ravel()
        if q.size != self.expected_dim:
            logger.warning(f"Query vector dim {q.size} != expected {self.expected_dim}")
            return []

        sims = entry.matrix @ q
        current_ts = time.time() if now is None else now
        decay_lambda = math.log(2.0) / max(1.0, half_life_days)

        scored: List[ScoredFact] = []
        candidate_indices = np.where(sims >= min_sim)[0]
        for idx in candidate_indices:
            fid = entry.fact_ids[int(idx)]
            fact = entry.facts[fid]
            cos_sim = float(sims[int(idx)])
            age_days = max(0.0, (current_ts - fact.updated_at) / 86400.0)
            recency_decay = math.exp(-decay_lambda * age_days)
            importance_norm = max(1, min(5, fact.importance)) / 5.0
            final_score = 0.65 * cos_sim + 0.20 * recency_decay + 0.15 * importance_norm
            scored.append(
                ScoredFact(
                    fact=fact,
                    cosine_sim=cos_sim,
                    recency_decay=recency_decay,
                    final_score=final_score
                )
            )

        scored.sort(key=lambda s: s.final_score, reverse=True)
        return scored[:top_k]

    def find_most_similar(
        self,
        persona_id: str,
        scope_key: str,
        vec: np.ndarray,
        user_id: Optional[str] = None
    ) -> Optional[Tuple[MemoryFact, float]]:
        """Find the single highest cosine-similarity fact matching the same user_id (or None for group facts)."""
        key = (persona_id, scope_key)
        entry = self._partitions.get(key)
        if not entry or entry.matrix is None or entry.matrix.shape[0] == 0:
            return None

        self._touch(key)
        q = np.asarray(vec, dtype=np.float32).ravel()
        if q.size != self.expected_dim:
            return None

        candidate_indices = [
            i for i, fid in enumerate(entry.fact_ids)
            if entry.facts[fid].user_id == user_id
        ]
        if not candidate_indices:
            return None

        sub_matrix = entry.matrix[candidate_indices]
        sims = sub_matrix @ q
        best_local = int(np.argmax(sims))
        best_idx = candidate_indices[best_local]
        best_fact = entry.facts[entry.fact_ids[best_idx]]
        return best_fact, float(sims[best_local])

    def get_stats(self) -> Dict[str, int]:
        total_vectors = sum(
            len(entry.fact_ids) for entry in self._partitions.values()
        )
        return {
            "partitions": len(self._partitions),
            "vectors": total_vectors,
        }
