"""Pinecone-hosted embedding ranker for comparable companies.

Uses Pinecone's hosted inference API with multilingual-e5-large model
to rank candidate companies by cosine similarity to a target description.

Story 2.2 of the Production Upgrade Plan.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any

from vc_audit_tool.data_sources.embedding_ranker import RankedCompany
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# Lazy but patchable
_pinecone_module: ModuleType | None = None


def _ensure_pinecone() -> ModuleType:
    """Import pinecone on first call."""
    global _pinecone_module  # noqa: PLW0603
    if _pinecone_module is None:
        try:
            import pinecone  # noqa: F401

            _pinecone_module = pinecone
        except ImportError as exc:
            raise DataSourceError(
                "pinecone is required for Pinecone-hosted inference ranking. "
                "Install it with: pip install pinecone-client"
            ) from exc
    return _pinecone_module


class PineconeCompsRanker:
    """Rank candidate companies using Pinecone hosted inference.

    Attributes
    ----------
    dataset_version:
        Includes the embedding model name/version.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = "pinecone-multilingual-e5-large-v1"
    source_label: str = "Pinecone hosted-inference ranker"

    def __init__(
        self,
        index_name: str,
        embedding_model: str = "multilingual-e5-large",
    ) -> None:
        """Initialize Pinecone ranker.

        Parameters
        ----------
        index_name:
            Name of the Pinecone index to upsert and query.
        embedding_model:
            Embedding model to use (e.g., "multilingual-e5-large").
        """
        self._index_name = index_name
        self._embedding_model = embedding_model
        self._client: Any = None  # lazy-loaded Pinecone client

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

        try:
            client = self._get_client()
            index = client.Index(self._index_name)

            # Upsert candidate vectors
            vectors_to_upsert = []
            for i, cand in enumerate(candidates):
                vec_id = f"{cand.get('ticker', 'unknown')}_{i}"
                vectors_to_upsert.append((vec_id, cand.get("description", "")))

            # Embed and upsert in batch
            if vectors_to_upsert:
                embeddings = index.inference.embed(
                    model=self._embedding_model,
                    inputs=[desc for _, desc in vectors_to_upsert],
                )
                records = [
                    (vec_id, emb, {"ticker": cand["ticker"], "company_name": cand["company_name"]})
                    for (vec_id, _), cand, emb in zip(
                        vectors_to_upsert, candidates, embeddings, strict=True
                    )
                ]
                index.upsert(records)

            # Query with target description
            query_embedding = index.inference.embed(
                model=self._embedding_model,
                inputs=[target_description],
            )[0]

            results = index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
            )

            # Map results back to RankedCompany objects
            ranked: list[RankedCompany] = []
            candidates_by_ticker = {c["ticker"]: c for c in candidates}

            for match in results.get("matches", []):
                metadata = match.get("metadata", {})
                ticker = metadata.get("ticker", "")
                company_name = metadata.get("company_name", "")

                if ticker in candidates_by_ticker:
                    cand = candidates_by_ticker[ticker]
                    ranked.append(
                        RankedCompany(
                            ticker=ticker,
                            company_name=company_name,
                            similarity=round(float(match.get("score", 0.0)), 4),
                            description_snippet=cand.get("description", "")[:200],
                        )
                    )

            return ranked[:top_k]

        except Exception as exc:
            raise DataSourceError(
                f"Pinecone ranking failed: {exc}. Ensure PINECONE_API_KEY is set and index exists."
            ) from exc

    # ── Private helpers ──

    def _get_client(self) -> Any:
        """Lazy-load the Pinecone client."""
        if self._client is not None:
            return self._client
        pinecone = _ensure_pinecone()
        logger.info("initializing Pinecone client for index: %s", self._index_name)
        self._client = pinecone.Pinecone()
        return self._client
