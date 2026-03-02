"""Tests for Pinecone comps ranker and ranker factory.

Suite includes:
  1. Factory function tests — verify environment variable handling
  2. Pinecone ranker unit tests — mock the API client (SDK 8.x search() API)
  3. Error handling tests — verify DataSourceError wrapping
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vc_audit_tool.data_sources.embedding_ranker import (
    EmbeddingCompsRanker,
    RankedCompany,
)
from vc_audit_tool.data_sources.pinecone_ranker import PineconeCompsRanker
from vc_audit_tool.data_sources.ranker_factory import get_ranker
from vc_audit_tool.exceptions import DataSourceError

# ─────────────────────────────────────────────────────────────────────────────
# Factory tests
# ─────────────────────────────────────────────────────────────────────────────


def test_get_ranker_returns_embedding_when_no_key(monkeypatch):
    """Returns EmbeddingCompsRanker when PINECONE_API_KEY not set."""
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    ranker = get_ranker()
    assert isinstance(ranker, EmbeddingCompsRanker)


def test_get_ranker_returns_pinecone_when_key_set(monkeypatch):
    """Returns PineconeCompsRanker when PINECONE_API_KEY is set."""
    monkeypatch.setenv("PINECONE_API_KEY", "test-key-12345")
    ranker = get_ranker()
    assert isinstance(ranker, PineconeCompsRanker)


def test_get_ranker_respects_custom_index_name(monkeypatch):
    """Passes PINECONE_INDEX_NAME to PineconeCompsRanker."""
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "custom-index")
    ranker = get_ranker()
    assert isinstance(ranker, PineconeCompsRanker)
    assert ranker._index_name == "custom-index"


def test_get_ranker_respects_custom_embedding_model(monkeypatch):
    """Passes PINECONE_EMBEDDING_MODEL to PineconeCompsRanker."""
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setenv("PINECONE_EMBEDDING_MODEL", "custom-model")
    ranker = get_ranker()
    assert isinstance(ranker, PineconeCompsRanker)
    assert ranker._embedding_model == "custom-model"


# ─────────────────────────────────────────────────────────────────────────────
# Helper to build mock search response (SDK 8.x format)
# ─────────────────────────────────────────────────────────────────────────────


def _make_search_response(hits: list[dict]) -> dict:
    """Build a dict matching the SDK 8.x index.search() response shape."""
    return {"result": {"hits": hits}}


# ─────────────────────────────────────────────────────────────────────────────
# Pinecone ranker unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_pinecone_ranker_init():
    """PineconeCompsRanker initializes with defaults."""
    ranker = PineconeCompsRanker(index_name="test-index")
    assert ranker._index_name == "test-index"
    assert ranker._embedding_model == "multilingual-e5-large"
    assert ranker.dataset_version == "pinecone-multilingual-e5-large-v1"
    assert ranker.source_label == "Pinecone hosted-inference ranker"


def test_pinecone_ranker_rank_empty_candidates_and_description():
    """rank() returns [] immediately when both candidates and description are empty."""
    ranker = PineconeCompsRanker(index_name="test-index")
    result = ranker.rank("", [], top_k=5)
    assert result == []


def test_pinecone_ranker_rank_mocks_client_pre_seeded(monkeypatch):
    """rank() uses search-only mode when index has >10 pre-seeded records."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    mock_client.has_index.return_value = True

    # Simulate pre-seeded index with many records
    mock_index.describe_index_stats.return_value = {
        "namespaces": {"comps": {"record_count": 500}},
    }

    # index.search() returns SDK 8.x response shape
    mock_index.search.return_value = _make_search_response(
        [
            {"_score": 0.95, "fields": {
                "ticker": "AAPL", "company_name": "Apple Inc.", "description": "Tech",
            }},
            {"_score": 0.85, "fields": {
                "ticker": "MSFT", "company_name": "Microsoft Corp.", "description": "Software",
            }},
        ]
    )

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        candidates = [
            {"ticker": "AAPL", "company_name": "Apple Inc.", "description": "Tech"},
            {"ticker": "MSFT", "company_name": "Microsoft Corp.", "description": "Software"},
        ]

        result = ranker.rank("target company", candidates, top_k=5)

    assert len(result) == 2
    assert result[0].ticker == "AAPL"
    assert result[0].similarity == 0.95
    assert result[1].ticker == "MSFT"
    assert result[1].similarity == 0.85

    mock_client.Index.assert_called_with("test-index")
    # Pre-seeded mode: search only, no upsert
    mock_index.upsert_records.assert_not_called()
    mock_index.search.assert_called_once()

    # Verify search() was called with dict-based query containing inputs={"text": ...}
    search_kwargs = mock_index.search.call_args.kwargs
    assert search_kwargs["namespace"] == "comps"
    query = search_kwargs["query"]
    assert query["inputs"]["text"] == "target company"
    # Verify reranking is configured
    assert "rerank" in search_kwargs
    assert search_kwargs["rerank"]["model"] == "bge-reranker-v2-m3"


def test_pinecone_ranker_rank_mocks_client_upsert_fallback(monkeypatch):
    """rank() uses upsert-then-search fallback when index has no pre-seeded records."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    mock_client.has_index.return_value = True

    # Simulate empty index (not pre-seeded)
    mock_index.describe_index_stats.return_value = {
        "namespaces": {"comps": {"record_count": 5}},
    }

    mock_index.search.return_value = _make_search_response(
        [
            {"_score": 0.95, "fields": {"ticker": "AAPL", "company_name": "Apple Inc."}},
        ]
    )

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        candidates = [
            {"ticker": "AAPL", "company_name": "Apple Inc.", "description": "Tech"},
        ]
        result = ranker.rank("target company", candidates, top_k=5)

    assert len(result) == 1
    assert result[0].ticker == "AAPL"
    # Upsert fallback: records were upserted then searched in comps_tmp namespace
    mock_index.upsert_records.assert_called_once()
    search_kwargs = mock_index.search.call_args.kwargs
    assert search_kwargs["namespace"] == "comps_tmp"


def test_pinecone_ranker_rank_returns_ranked_company_objects():
    """rank() returns proper RankedCompany dataclass instances."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    mock_client.has_index.return_value = True

    mock_index.search.return_value = _make_search_response(
        [
            {"_score": 0.92, "fields": {"ticker": "GOOGL", "company_name": "Alphabet Inc."}},
        ]
    )

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        candidates = [
            {"ticker": "GOOGL", "company_name": "Alphabet Inc.", "description": "Search company"}
        ]

        result = ranker.rank("target", candidates, top_k=5)

    assert len(result) == 1
    ranked = result[0]
    assert isinstance(ranked, RankedCompany)
    assert ranked.ticker == "GOOGL"
    assert ranked.company_name == "Alphabet Inc."
    assert ranked.similarity == 0.92
    assert "Search company" in ranked.description_snippet


def test_pinecone_ranker_rank_respects_top_k():
    """rank() returns at most top_k results."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    mock_client.has_index.return_value = True

    # Pinecone returns 5 hits; top_k=2 should truncate to 2
    mock_index.search.return_value = _make_search_response(
        [
            {"_score": 0.95, "fields": {"ticker": f"T{i}", "company_name": f"Company {i}"}}
            for i in range(5)
        ]
    )

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        candidates = [
            {"ticker": f"T{i}", "company_name": f"Company {i}", "description": f"Desc {i}"}
            for i in range(5)
        ]

        result = ranker.rank("target", candidates, top_k=2)

    assert len(result) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Error handling tests
# ─────────────────────────────────────────────────────────────────────────────


def test_pinecone_ranker_missing_package_raises():
    """Raises DataSourceError if pinecone is not installed."""
    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pinecone":
            raise ImportError("No module named 'pinecone'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        # Clear the cached module
        import vc_audit_tool.data_sources.pinecone_ranker as pr_module

        pr_module._pinecone_module = None

        ranker = PineconeCompsRanker(index_name="test-index")
        with pytest.raises(DataSourceError) as exc_info:
            ranker.rank("target", [{"ticker": "T1", "company_name": "C1", "description": "D1"}])

        assert "pinecone" in str(exc_info.value).lower()
        assert "pip install" in str(exc_info.value)


def test_pinecone_ranker_api_error_wrapped():
    """Wraps Pinecone API errors in DataSourceError."""
    mock_client = MagicMock()
    mock_client.Index.side_effect = RuntimeError("Pinecone API error")

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        with pytest.raises(DataSourceError) as exc_info:
            ranker.rank("target", [{"ticker": "T1", "company_name": "C1", "description": "D1"}])

        assert "Pinecone ranking failed" in str(exc_info.value)


def test_pinecone_ranker_query_error_wrapped():
    """Wraps Pinecone search() errors in DataSourceError."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    mock_client.has_index.return_value = True

    # upsert_records succeeds; search fails
    mock_index.upsert_records.return_value = None
    mock_index.search.side_effect = RuntimeError("Query failed")

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        with pytest.raises(DataSourceError) as exc_info:
            ranker.rank("target", [{"ticker": "T1", "company_name": "C1", "description": "D1"}])

        assert "Pinecone ranking failed" in str(exc_info.value)
