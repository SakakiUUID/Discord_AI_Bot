import asyncio
import logging
from typing import Any, List, Optional
import numpy as np
from google import genai
from google.genai import types

import config

logger = logging.getLogger("core.memory.embeddings")


def l2_normalize(vector: Any) -> Optional[np.ndarray]:
    """Convert raw vector sequence into an L2-normalized float32 unit ndarray."""
    if vector is None:
        return None
    arr = np.asarray(vector, dtype=np.float32).ravel()
    if arr.size == 0:
        return None
    norm = float(np.linalg.norm(arr))
    if norm <= 1e-12 or not np.isfinite(norm):
        return None
    return (arr / norm).astype(np.float32)


class EmbeddingService:
    """Generates L2-normalized Gemini embeddings with safe fallback on API failure."""

    def __init__(
        self,
        ai_client: Optional[genai.Client] = None,
        model: str = config.GEMINI_EMBEDDING_MODEL,
        dim: int = config.MEMORY_EMBEDDING_DIM
    ):
        self.ai_client = ai_client
        self.model = model
        self.dim = dim

    def _build_config(self, task_type: str) -> types.EmbedContentConfig:
        kwargs = {"task_type": task_type}
        if self.dim and self.dim > 0:
            kwargs["output_dimensionality"] = self.dim
        return types.EmbedContentConfig(**kwargs)

    async def embed_query(self, text: str) -> Optional[np.ndarray]:
        """Embed a single retrieval query and return L2-normalized float32 vector, or None on failure."""
        if not self.ai_client or not text or not text.strip():
            return None

        try:
            response = await asyncio.to_thread(
                self.ai_client.models.embed_content,
                model=self.model,
                contents=text.strip(),
                config=self._build_config("RETRIEVAL_QUERY"),
            )
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                logger.warning("Gemini embed_content returned empty embeddings for query.")
                return None

            vec = l2_normalize(getattr(embeddings[0], "values", None))
            if vec is None:
                return None
            if self.dim and vec.size != self.dim:
                logger.warning(f"Embedding dimension mismatch: expected {self.dim}, got {vec.size}.")
                return None
            return vec
        except Exception as e:
            logger.warning(f"Embedding query failed (degrading gracefully without vector recall): {e}")
            return None

    async def embed_documents(
        self,
        texts: List[str],
        batch_size: int = 50
    ) -> Optional[List[Optional[np.ndarray]]]:
        """Embed a list of memory facts in batches, returning None if API fails."""
        if not texts:
            return []
        if not self.ai_client:
            return None

        cleaned = [t.strip() for t in texts]
        results: List[Optional[np.ndarray]] = []

        try:
            for start in range(0, len(cleaned), batch_size):
                chunk = cleaned[start:start + batch_size]
                response = await asyncio.to_thread(
                    self.ai_client.models.embed_content,
                    model=self.model,
                    contents=chunk,
                    config=self._build_config("RETRIEVAL_DOCUMENT"),
                )
                embeddings = getattr(response, "embeddings", None) or []
                if len(embeddings) != len(chunk):
                    logger.warning(
                        f"Batch embedding count mismatch: expected {len(chunk)}, got {len(embeddings)}."
                    )
                    return None

                for emb in embeddings:
                    vec = l2_normalize(getattr(emb, "values", None))
                    if vec is None or (self.dim and vec.size != self.dim):
                        logger.warning("Invalid or mismatched embedding vector in batch response.")
                        return None
                    results.append(vec)
            return results
        except Exception as e:
            logger.warning(f"Batch document embedding failed: {e}")
            return None
