"""Pinecone integrated-inference ranker for comparable companies.

Uses Pinecone's ``create_index_for_model`` + ``upsert_records`` + ``search_records``
API (Pinecone SDK ≥ 5.1) so that Pinecone manages all embedding internally.
No local sentence-transformer call is needed; candidates and queries are sent
as plain text.

Story 2.2 of the Production Upgrade Plan.
"""

from __future__ import annotations

import logging
import os
from types import ModuleType
from typing import Any

from vc_audit_tool.data_sources.embedding_ranker import RankedCompany
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# Lazy but patchable module reference
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
                "Install it with: pip install pinecone"
            ) from exc
    return _pinecone_module


def _extract_hits(response: Any) -> list[Any]:
    """Pull hits list from either dict or SDK response object."""
    if isinstance(response, dict):
        result = response.get("result", response)
        return list(result.get("hits", []))
    result = getattr(response, "result", response)
    return list(getattr(result, "hits", []))


def _parse_hit(hit: Any) -> tuple[str, str, float]:
    """Extract (ticker, company_name, score) from a search hit."""
    if isinstance(hit, dict):
        fields = hit.get("fields", {})
        score = float(hit.get("_score", 0.0))
    else:
        fields = getattr(hit, "fields", {})
        score = float(hit["_score"])
    get_fn = fields.get if hasattr(fields, "get") else (lambda k, d="": getattr(fields, k, d))
    return str(get_fn("ticker", "")), str(get_fn("company_name", "")), score


class PineconeCompsRanker:
    """Rank candidate companies using Pinecone integrated inference.

    The index is created with ``create_index_for_model`` so Pinecone embeds
    records automatically on upsert and queries automatically on search.
    No local embedding model is needed.

    Attributes
    ----------
    dataset_version:
        Includes the embedding model name/version for citation purposes.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = "pinecone-multilingual-e5-large-v1"
    source_label: str = "Pinecone hosted-inference ranker"

    # Record field that holds the text Pinecone will embed on upsert.
    # Must match the right-hand side of ``field_map`` in create_index_for_model.
    _TEXT_FIELD = "description"
    # The model's input key used in field_map and SearchQuery.inputs.
    # For multilingual-e5-large this is always "text".
    _EMBED_INPUT_KEY = "text"
    _NAMESPACE = "comps"

    def __init__(
        self,
        index_name: str,
        embedding_model: str = "multilingual-e5-large",
    ) -> None:
        self._index_name = index_name
        self._embedding_model = embedding_model
        self._pc: Any = None  # lazy Pinecone client
        self._index: Any = None  # lazy Index handle

    def rank(
        self,
        target_description: str,
        candidates: list[dict[str, str]],
        top_k: int = 5,
    ) -> list[RankedCompany]:
        """Rank by semantic similarity to *target_description*.

        Strategy
        --------
        1. **Search-first (preferred)**: if the index has been pre-seeded via
           ``scripts/seed_pinecone.py`` the method performs a direct semantic
           search against the pre-seeded namespace — no upsert needed.
        2. **Upsert-then-search (fallback)**: when the pre-seeded namespace is
           empty, the candidates are upserted into a temporary namespace and
           searched immediately (original behaviour).

        Parameters
        ----------
        target_description:
            Compact business description of the company being valued.
        candidates:
            Dicts with ``"ticker"``, ``"company_name"``, ``"description"`` keys.
            Used for the upsert fallback and for description_snippet in results.
        top_k:
            Maximum results to return.
        """
        if not candidates and not target_description:
            return []

        try:
            index = self._get_index()

            # ── Check whether the pre-seeded namespace has records ────────
            pre_seeded = self._namespace_has_records(index)

            if pre_seeded:
                logger.info("pinecone_ranker: search-only mode (pre-seeded index) top_k=%d", top_k)
                response = index.search(
                    namespace=self._NAMESPACE,
                    query={
                        "inputs": {self._EMBED_INPUT_KEY: target_description},
                        "top_k": top_k,
                    },
                    rerank={
                        "model": "bge-reranker-v2-m3",
                        "top_n": top_k,
                        "rank_fields": [self._TEXT_FIELD],
                    },
                )
                hits = _extract_hits(response)
                ranked: list[RankedCompany] = []
                for hit in hits:
                    ticker, company_name, score = _parse_hit(hit)
                    if isinstance(hit, dict):
                        hit_fields: dict[str, Any] = hit.get("fields", {})
                    else:
                        hit_fields = getattr(hit, "fields", {})
                    raw_snippet = (
                        hit_fields.get(self._TEXT_FIELD, "")
                        if isinstance(hit_fields, dict)
                        else getattr(hit_fields, self._TEXT_FIELD, "")
                    )
                    snippet = str(raw_snippet)[:200]
                    ranked.append(
                        RankedCompany(
                            ticker=ticker,
                            company_name=company_name,
                            similarity=round(score, 4),
                            description_snippet=snippet,
                        )
                    )
                return ranked[:top_k]

            # ── Upsert-then-search fallback ───────────────────────────────
            if not candidates:
                return []

            logger.info(
                "pinecone_ranker: upsert-then-search fallback (index not pre-seeded) candidates=%d",
                len(candidates),
            )
            tmp_namespace = f"{self._NAMESPACE}_tmp"
            records = [
                {
                    "_id": cand.get("ticker", f"cand_{i}"),
                    self._TEXT_FIELD: cand.get("description", ""),
                    "ticker": cand.get("ticker", ""),
                    "company_name": cand.get("company_name", ""),
                }
                for i, cand in enumerate(candidates)
            ]
            index.upsert_records(namespace=tmp_namespace, records=records)

            response = index.search(
                namespace=tmp_namespace,
                query={
                    "inputs": {self._EMBED_INPUT_KEY: target_description},
                    "top_k": top_k,
                },
                rerank={
                    "model": "bge-reranker-v2-m3",
                    "top_n": top_k,
                    "rank_fields": [self._TEXT_FIELD],
                },
            )

            candidates_by_ticker = {c["ticker"]: c for c in candidates}
            ranked = []
            hits = _extract_hits(response)
            for hit in hits:
                ticker, company_name, score = _parse_hit(hit)
                cand = candidates_by_ticker.get(ticker)
                ranked.append(
                    RankedCompany(
                        ticker=ticker,
                        company_name=company_name,
                        similarity=round(score, 4),
                        description_snippet=(cand.get("description", "")[:200] if cand else ""),
                    )
                )
            return ranked[:top_k]

        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(
                f"Pinecone ranking failed: {exc}. "
                "Ensure PINECONE_API_KEY is set and the index exists."
            ) from exc

    def _namespace_has_records(self, index: Any) -> bool:
        """Return True when the pre-seeded 'comps' namespace has records."""
        try:
            stats = index.describe_index_stats()
            if isinstance(stats, dict):
                ns = stats.get("namespaces", {}).get(self._NAMESPACE, {})
            else:
                namespaces = getattr(stats, "namespaces", {})
                ns = namespaces.get(self._NAMESPACE, {}) if namespaces else {}
            if isinstance(ns, dict):
                count = int(ns.get("record_count", ns.get("vector_count", 0)) or 0)
            else:
                count = int(getattr(ns, "record_count", 0) or 0)
            return count > 10  # >10 means it's been seeded, not just test data
        except Exception as exc:
            logger.warning("could not check namespace record count: %s — assuming not seeded", exc)
            return False

    # ── Private helpers ──

    def _get_index(self) -> Any:
        """Lazy-load the Pinecone client, create index if needed, return Index."""
        if self._index is not None:
            return self._index
        pinecone = _ensure_pinecone()
        api_key = os.getenv("PINECONE_API_KEY", "")
        self._pc = pinecone.Pinecone(api_key=api_key)
        self._ensure_index(pinecone)
        self._index = self._pc.Index(self._index_name)
        return self._index

    def _ensure_index(self, pinecone: Any) -> None:
        """Create the integrated-inference index if it does not already exist."""
        try:
            exists = self._pc.has_index(self._index_name)
        except Exception as exc:
            logger.warning("could not check Pinecone index: %s — skipping auto-create", exc)
            return

        if exists:
            logger.info("Pinecone index '%s' already exists — reusing", self._index_name)
            return

        logger.info(
            "Pinecone index '%s' not found — creating with model '%s'",
            self._index_name,
            self._embedding_model,
        )
        try:
            self._pc.create_index_for_model(
                name=self._index_name,
                cloud="aws",
                region="us-east-1",
                embed={
                    "model": self._embedding_model,
                    "field_map": {"text": self._TEXT_FIELD},
                },
            )
            logger.info("Pinecone index '%s' created successfully", self._index_name)
        except Exception as exc:
            logger.warning(
                "could not auto-create Pinecone index '%s': %s — "
                "ensure the index exists before running comps ranking",
                self._index_name,
                exc,
            )
