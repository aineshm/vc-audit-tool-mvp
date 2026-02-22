"""Embedding-based comparable company ranker.

Uses ``sentence-transformers`` with the ``all-MiniLM-L6-v2`` model to
rank candidate companies by cosine similarity to a target company's
business description.

Story 2.2 of the Production Upgrade Plan.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/embedding_cache")
_DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

# Lazy but patchable
_st_module: ModuleType | None = None


def _ensure_st() -> ModuleType:
    """Import sentence_transformers on first call."""
    global _st_module  # noqa: PLW0603
    if _st_module is None:
        try:
            import sentence_transformers

            _st_module = sentence_transformers
        except ImportError as exc:
            raise DataSourceError(
                "sentence-transformers is required for embedding-based ranking. "
                "Install it with: pip install sentence-transformers"
            ) from exc
    return _st_module


@dataclass(frozen=True)
class RankedCompany:
    """A candidate company with its similarity score."""

    ticker: str
    company_name: str
    similarity: float
    description_snippet: str


class EmbeddingCompsRanker:
    """Rank candidate companies by semantic similarity to a target description.

    Attributes
    ----------
    dataset_version:
        Includes the embedding model name/version.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = f"{_DEFAULT_MODEL_NAME}-v1.0"
    source_label: str = "Sentence-transformer embedding ranker"

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL_NAME,
        cache_dir: Path = _DEFAULT_CACHE_DIR,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._model: Any = None  # lazy-loaded SentenceTransformer
        self.dataset_version = f"{model_name}-v1.0"

    def rank(
        self,
        target_description: str,
        candidates: list[dict[str, str]],
        top_k: int = 5,
    ) -> list[RankedCompany]:
        """Rank *candidates* by cosine similarity to *target_description*.

        Parameters
        ----------
        target_description:
            The business description of the company being valued.
        candidates:
            Each dict must have ``"ticker"``, ``"company_name"``, and
            ``"description"`` keys.
        top_k:
            How many top results to return.

        Returns
        -------
        list[RankedCompany]
            Sorted by descending similarity.
        """
        if not candidates:
            return []

        model = self._get_model()

        # Collect descriptions
        descs = [c.get("description", "") for c in candidates]
        all_texts = [target_description] + descs

        # Encode all at once
        embeddings = model.encode(all_texts, show_progress_bar=False)

        # Compute cosine similarities (target vs each candidate)
        import numpy as np

        target_emb = embeddings[0]
        candidate_embs = embeddings[1:]

        norms = np.linalg.norm(candidate_embs, axis=1)
        target_norm = float(np.linalg.norm(target_emb))

        results: list[RankedCompany] = []
        for i, cand in enumerate(candidates):
            norm_val = float(norms[i])
            if norm_val == 0 or target_norm == 0:
                sim = 0.0
            else:
                sim = float(np.dot(target_emb, candidate_embs[i]) / (target_norm * norm_val))
            results.append(
                RankedCompany(
                    ticker=cand.get("ticker", ""),
                    company_name=cand.get("company_name", ""),
                    similarity=round(sim, 4),
                    description_snippet=cand.get("description", "")[:200],
                )
            )

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:top_k]

    def mean_similarity(self, ranked: list[RankedCompany]) -> float:
        """Average similarity of the ranked set, for confidence scoring."""
        if not ranked:
            return 0.0
        return round(sum(r.similarity for r in ranked) / len(ranked), 4)

    def peer_set_quality(self, ranked: list[RankedCompany]) -> str:
        """Map mean similarity to a confidence label."""
        ms = self.mean_similarity(ranked)
        if ms > 0.75:
            return "HIGH"
        if ms >= 0.5:
            return "MEDIUM"
        return "LOW"

    # ── Private helpers ──

    def _get_model(self) -> Any:
        """Lazy-load the SentenceTransformer model."""
        if self._model is not None:
            return self._model
        st = _ensure_st()
        logger.info("loading embedding model: %s", self._model_name)
        self._model = st.SentenceTransformer(self._model_name)
        return self._model

    @staticmethod
    def _desc_hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]
