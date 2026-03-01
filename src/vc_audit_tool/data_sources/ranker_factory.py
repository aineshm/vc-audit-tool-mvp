"""Factory for creating the appropriate comps ranker based on configuration.

Uses Pinecone if PINECONE_API_KEY is set; falls back to local EmbeddingCompsRanker.
"""

from __future__ import annotations

import os
from pathlib import Path

from vc_audit_tool.data_sources.embedding_ranker import EmbeddingCompsRanker
from vc_audit_tool.data_sources.pinecone_ranker import PineconeCompsRanker

_DEFAULT_CACHE = Path("data/embedding_cache")


def get_ranker(cache_dir: Path = _DEFAULT_CACHE) -> EmbeddingCompsRanker | PineconeCompsRanker:
    """Return PineconeCompsRanker if PINECONE_API_KEY is set, else local EmbeddingCompsRanker.

    Parameters
    ----------
    cache_dir:
        Cache directory for local ranker (ignored if Pinecone is used).

    Returns
    -------
    EmbeddingCompsRanker | PineconeCompsRanker
        Pinecone ranker if PINECONE_API_KEY env var is set, else local ranker.
    """
    if os.getenv("PINECONE_API_KEY"):
        return PineconeCompsRanker(
            index_name=os.getenv("PINECONE_INDEX_NAME", "vc-audit-edgar-comps"),
            embedding_model=os.getenv("PINECONE_EMBEDDING_MODEL", "multilingual-e5-large"),
        )
    return EmbeddingCompsRanker(cache_dir=cache_dir)
