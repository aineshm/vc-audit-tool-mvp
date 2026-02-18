from __future__ import annotations

import unittest

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError


class ValuationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ValuationEngine()

    def test_last_round_market_adjusted(self) -> None:
        payload = {
            "company_name": "Basis AI",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
                "public_index": "NASDAQ_COMPOSITE",
            },
        }
        result = self.engine.evaluate_from_dict(payload).to_dict()

        self.assertEqual(result["methodology"], "last_round_market_adjusted")
        self.assertAlmostEqual(result["estimated_fair_value"]["amount"], 120_831_065.39, places=2)
        self.assertIn("index_level_last_round", result["inputs_used"])
        self.assertIn("derivation_steps", result)
        self.assertIn("confidence_indicators", result)
        self.assertIn("staleness_risk", result["confidence_indicators"])

    def test_comparable_companies(self) -> None:
        payload = {
            "company_name": "Inflo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {
                "sector": "enterprise_software",
                "revenue_ltm": 10_000_000,
                "statistic": "median",
                "private_company_discount_pct": 20,
            },
        }
        result = self.engine.evaluate_from_dict(payload).to_dict()

        self.assertEqual(result["methodology"], "comparable_companies")
        self.assertAlmostEqual(result["estimated_fair_value"]["amount"], 96_800_000.0)
        self.assertEqual(result["inputs_used"]["statistic"], "median")
        self.assertGreater(len(result["inputs_used"]["peer_companies"]), 0)
        self.assertIn("confidence_indicators", result)
        self.assertIn("peer_set_quality", result["confidence_indicators"])

    def test_unknown_methodology_raises(self) -> None:
        payload = {
            "company_name": "Basis AI",
            "methodology": "not_a_method",
            "inputs": {},
            "as_of_date": "2026-02-18",
        }
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_comps_sector_raises(self) -> None:
        payload = {
            "company_name": "Basis AI",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {
                "sector": "robotics",
                "revenue_ltm": 5_000_000,
            },
        }
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)


if __name__ == "__main__":
    unittest.main()
