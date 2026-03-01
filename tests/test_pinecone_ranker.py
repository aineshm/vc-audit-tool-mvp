"""Tests for Pinecone comps ranker and ranker factory.

Suite includes:
  1. Factory function tests — verify environment variable handling
  2. Pinecone ranker unit tests — mock the API client
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
# Pinecone ranker unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_pinecone_ranker_init():
    """PineconeCompsRanker initializes with defaults."""
    ranker = PineconeCompsRanker(index_name="test-index")
    assert ranker._index_name == "test-index"
    assert ranker._embedding_model == "multilingual-e5-large"
    assert ranker.dataset_version == "pinecone-multilingual-e5-large-v1"
    assert ranker.source_label == "Pinecone hosted-inference ranker"


def test_pinecone_ranker_rank_empty_candidates():
    """rank() returns [] immediately for empty candidates."""
    ranker = PineconeCompsRanker(index_name="test-index")
    result = ranker.rank("target description", [], top_k=5)
    assert result == []


def test_pinecone_ranker_rank_mocks_client(monkeypatch):
    """rank() correctly embeds, upserts, and queries with mocked client."""
    # Create mock Pinecone client
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index

    # Mock inference.embed responses
    target_embedding = [0.1, 0.2, 0.3, 0.4]
    candidate_embeddings = [
        [0.15, 0.22, 0.35, 0.45],  # AAPL
        [0.05, 0.15, 0.25, 0.35],  # MSFT
    ]
    mock_index.inference.embed.side_effect = [
        candidate_embeddings,  # First call: embed candidates
        target_embedding,  # Second call: embed target
    ]

    # Mock query response (2 matches)
    mock_index.query.return_value = {
        "matches": [
            {
                "score": 0.95,
                "metadata": {"ticker": "AAPL", "company_name": "Apple Inc."},
            },
            {
                "score": 0.85,
                "metadata": {"ticker": "MSFT", "company_name": "Microsoft Corp."},
            },
        ]
    }

    # Patch Pinecone module import
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

    # Verify result
    assert len(result) == 2
    assert result[0].ticker == "AAPL"
    assert result[0].similarity == 0.95
    assert result[1].ticker == "MSFT"
    assert result[1].similarity == 0.85

    # Verify client calls
    mock_client.Index.assert_called_with("test-index")
    mock_index.upsert.assert_called_once()
    mock_index.query.assert_called_once()


def test_pinecone_ranker_rank_returns_ranked_company_objects():
    """rank() returns proper RankedCompany dataclass instances."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index

    mock_index.inference.embed.side_effect = [
        [[0.1, 0.2]],  # Candidates
        [0.15, 0.25],  # Target
    ]

    mock_index.query.return_value = {
        "matches": [
            {
                "score": 0.92,
                "metadata": {"ticker": "GOOGL", "company_name": "Alphabet Inc."},
            }
        ]
    }

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

    mock_index.inference.embed.side_effect = [
        [[0.1]] * 5,  # 5 candidates
        [0.15],  # Target
    ]

    # Return 5 matches but only ask for top_k=2
    mock_index.query.return_value = {
        "matches": [
            {"score": 0.95, "metadata": {"ticker": f"T{i}", "company_name": f"Company {i}"}}
            for i in range(5)
        ]
    }

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
    """Wraps Pinecone query errors in DataSourceError."""
    mock_index = MagicMock()
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index

    mock_index.inference.embed.side_effect = [
        [[0.1]],  # Candidates embed succeeds
        RuntimeError("Query failed"),  # Target embed fails
    ]

    with patch("vc_audit_tool.data_sources.pinecone_ranker._ensure_pinecone") as mock_ensure:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone.return_value = mock_client
        mock_ensure.return_value = mock_pinecone

        ranker = PineconeCompsRanker(index_name="test-index")
        with pytest.raises(DataSourceError) as exc_info:
            ranker.rank("target", [{"ticker": "T1", "company_name": "C1", "description": "D1"}])

        assert "Pinecone ranking failed" in str(exc_info.value)
