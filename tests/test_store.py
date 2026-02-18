"""Tests for the SQLite valuation store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vc_audit_tool.store import ValuationStore

SAMPLE_RESULT: dict = {
    "valuation_result": {
        "company_name": "Acme Inc",
        "methodology": "last_round_market_adjusted",
        "as_of_date": "2026-02-18",
        "estimated_fair_value": {"amount": 120000000.0, "currency": "USD"},
        "assumptions": ["a1"],
        "inputs_used": {},
        "citations": [],
        "derivation_steps": ["step 1"],
        "confidence_indicators": {},
    },
    "audit_metadata": {
        "request_id": "abc-123",
        "generated_at_utc": "2026-02-18T00:00:00+00:00",
        "engine_version": "0.1.0",
    },
}


class TestValuationStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.store = ValuationStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmpdir.cleanup()

    def test_save_and_get(self) -> None:
        rid = self.store.save(SAMPLE_RESULT)
        self.assertEqual(rid, "abc-123")
        fetched = self.store.get_run(rid)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["valuation_result"]["company_name"], "Acme Inc")

    def test_list_runs(self) -> None:
        self.store.save(SAMPLE_RESULT)
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["company_name"], "Acme Inc")
        self.assertNotIn("payload", runs[0])  # summary only

    def test_get_nonexistent_returns_none(self) -> None:
        self.assertIsNone(self.store.get_run("does-not-exist"))

    def test_list_empty(self) -> None:
        self.assertEqual(self.store.list_runs(), [])

    def test_multiple_runs_ordering(self) -> None:
        for i in range(5):
            result = {
                "valuation_result": {
                    **SAMPLE_RESULT["valuation_result"],
                    "company_name": f"Company {i}",
                },
                "audit_metadata": {
                    **SAMPLE_RESULT["audit_metadata"],
                    "request_id": f"id-{i}",
                },
            }
            self.store.save(result)
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 5)
        # most recent first
        self.assertEqual(runs[0]["company_name"], "Company 4")
        self.assertEqual(runs[4]["company_name"], "Company 0")

    def test_limit(self) -> None:
        for i in range(10):
            result = {
                "valuation_result": {**SAMPLE_RESULT["valuation_result"]},
                "audit_metadata": {
                    **SAMPLE_RESULT["audit_metadata"],
                    "request_id": f"id-{i}",
                },
            }
            self.store.save(result)
        runs = self.store.list_runs(limit=3)
        self.assertEqual(len(runs), 3)


if __name__ == "__main__":
    unittest.main()
