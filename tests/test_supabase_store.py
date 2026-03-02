"""Integration tests for SupabaseValuationStore.

These tests hit a real Supabase instance and are skipped unless both
SUPABASE_URL and SUPABASE_KEY environment variables are set.

Run with:
    pytest tests/test_supabase_store.py -m integration -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY")
_SKIP_REASON = "SUPABASE_URL and SUPABASE_KEY must be set for integration tests"


def _requires_supabase() -> bool:
    return bool(_SUPABASE_URL and _SUPABASE_KEY)


def _make_result(company_name: str = "TestCo", request_id: str | None = None) -> dict[str, Any]:
    """Build a minimal valuation result dict that matches the store schema."""
    rid = request_id or str(uuid.uuid4())
    return {
        "valuation_result": {
            "company_name": company_name,
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2024-01-01",
            "estimated_fair_value": {"amount": 1_000_000.0, "currency": "USD"},
        },
        "audit_metadata": {
            "request_id": rid,
            "generated_at_utc": "2024-01-01T00:00:00Z",
        },
        "derivation_steps": [],
        "evidence_package": {},
    }


@pytest.mark.integration
@pytest.mark.skipif(not _requires_supabase(), reason=_SKIP_REASON)
class TestSupabaseValuationStore:
    @pytest.fixture(autouse=True)
    def store(self) -> Any:
        from vc_audit_tool.store_supabase import SupabaseValuationStore

        return SupabaseValuationStore(url=_SUPABASE_URL, key=_SUPABASE_KEY)  # type: ignore[arg-type]

    def test_save_returns_request_id(self, store: Any) -> None:
        result = _make_result()
        rid = result["audit_metadata"]["request_id"]
        saved_id = store.save(result)
        assert saved_id == rid

    def test_list_runs_returns_summary_without_payload(self, store: Any) -> None:
        result = _make_result(company_name="ListTestCo")
        store.save(result)
        runs = store.list_runs(limit=10)
        assert isinstance(runs, list)
        assert len(runs) >= 1
        # Verify summary columns present
        first = runs[0]
        assert "request_id" in first
        assert "company_name" in first
        assert "fair_value" in first
        # Payload must NOT be included in summary
        assert "payload" not in first

    def test_get_run_retrieves_full_payload(self, store: Any) -> None:
        result = _make_result(company_name="GetRunCo")
        rid = result["audit_metadata"]["request_id"]
        store.save(result)
        retrieved = store.get_run(rid)
        assert retrieved is not None
        assert retrieved["audit_metadata"]["request_id"] == rid
        assert retrieved["valuation_result"]["company_name"] == "GetRunCo"

    def test_get_run_nonexistent_returns_none(self, store: Any) -> None:
        result = store.get_run("nonexistent-run-id-that-does-not-exist")
        assert result is None
