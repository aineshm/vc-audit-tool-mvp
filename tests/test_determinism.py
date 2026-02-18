"""Determinism and raw-request replay tests.

Acceptance criteria
-------------------
* Repeated identical requests produce byte-identical ``valuation_result``.
* ``audit_metadata`` (request_id, generated_at_utc) is allowed to differ.
* Raw example JSON files can be replayed and yield stable outputs.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from vc_audit_tool.engine import ValuationEngine

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


class DeterminismTests(unittest.TestCase):
    """Identical inputs must always produce byte-identical valuation_result."""

    def setUp(self) -> None:
        self.engine = ValuationEngine()

    # ── Last-round methodology ──

    def test_last_round_determinism(self) -> None:
        payload = {
            "company_name": "DeterminCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
                "public_index": "NASDAQ_COMPOSITE",
            },
        }
        runs = [
            json.dumps(
                self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"],
                sort_keys=True,
            )
            for _ in range(5)
        ]
        self.assertTrue(
            all(r == runs[0] for r in runs),
            "valuation_result must be byte-identical across repeated runs",
        )

    def test_last_round_metadata_varies(self) -> None:
        """audit_metadata fields (request_id) should differ between runs."""
        payload = {
            "company_name": "DeterminCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
            },
        }
        ids = {
            self.engine.evaluate_from_dict(payload).to_dict()["audit_metadata"]["request_id"]
            for _ in range(3)
        }
        self.assertEqual(len(ids), 3, "Each run should produce a unique request_id")

    # ── Comps methodology ──

    def test_comps_determinism(self) -> None:
        payload = {
            "company_name": "DeterminCo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {
                "sector": "enterprise_software",
                "revenue_ltm": 10_000_000,
                "statistic": "median",
                "private_company_discount_pct": 20,
            },
        }
        runs = [
            json.dumps(
                self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"],
                sort_keys=True,
            )
            for _ in range(5)
        ]
        self.assertTrue(
            all(r == runs[0] for r in runs),
            "valuation_result must be byte-identical across repeated runs",
        )


class RawRequestReplayTests(unittest.TestCase):
    """Load example JSON files and verify replay produces stable output."""

    def setUp(self) -> None:
        self.engine = ValuationEngine()

    def _replay(self, filename: str, n: int = 3) -> list[str]:
        """Run the same example file *n* times and return serialised valuation_results."""
        path = EXAMPLES_DIR / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            json.dumps(
                self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"],
                sort_keys=True,
            )
            for _ in range(n)
        ]

    def test_last_round_example_replay(self) -> None:
        runs = self._replay("last_round_request.json")
        self.assertTrue(all(r == runs[0] for r in runs))

    def test_comps_example_replay(self) -> None:
        runs = self._replay("comps_request.json")
        self.assertTrue(all(r == runs[0] for r in runs))

    def test_last_round_example_envelope_structure(self) -> None:
        """Replayed example must produce full envelope with citation traces."""
        payload = json.loads((EXAMPLES_DIR / "last_round_request.json").read_text(encoding="utf-8"))
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        meta = out["audit_metadata"]

        # Deterministic section
        self.assertIn("estimated_fair_value", vr)
        self.assertIn("citations", vr)
        self.assertTrue(len(vr["citations"]) > 0)

        # Citation trace
        citation = vr["citations"][0]
        self.assertIn("dataset_version", citation)
        self.assertIn("resolved_data_points", citation)
        self.assertTrue(len(citation["resolved_data_points"]) > 0)

        # Audit metadata
        self.assertIn("request_id", meta)
        self.assertIn("generated_at_utc", meta)
        self.assertIn("engine_version", meta)

    def test_comps_example_envelope_structure(self) -> None:
        """Replayed comps example must include citation trace."""
        payload = json.loads((EXAMPLES_DIR / "comps_request.json").read_text(encoding="utf-8"))
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]

        self.assertIn("citations", vr)
        citation = vr["citations"][0]
        self.assertIn("dataset_version", citation)
        self.assertIn("resolved_data_points", citation)
        self.assertTrue(len(citation["resolved_data_points"]) > 0)

    def test_cross_methodology_results_differ(self) -> None:
        """Different methodologies on the same as_of_date should produce different values."""
        lr_payload = json.loads(
            (EXAMPLES_DIR / "last_round_request.json").read_text(encoding="utf-8")
        )
        comps_payload = json.loads(
            (EXAMPLES_DIR / "comps_request.json").read_text(encoding="utf-8")
        )
        lr_val = self.engine.evaluate_from_dict(lr_payload).to_dict()["valuation_result"]
        comps_val = self.engine.evaluate_from_dict(comps_payload).to_dict()["valuation_result"]

        self.assertNotEqual(
            lr_val["estimated_fair_value"]["amount"],
            comps_val["estimated_fair_value"]["amount"],
        )


if __name__ == "__main__":
    unittest.main()
