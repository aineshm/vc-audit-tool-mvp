from __future__ import annotations

import unittest

from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource
from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource


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
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]

        self.assertEqual(vr["methodology"], "last_round_market_adjusted")
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 120_831_065.39, places=2)
        self.assertIn("index_level_last_round", vr["inputs_used"])
        self.assertIn("derivation_steps", vr)
        self.assertIn("confidence_indicators", vr)
        self.assertIn("staleness_risk", vr["confidence_indicators"])

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
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]

        self.assertEqual(vr["methodology"], "comparable_companies")
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 94_400_000.0)
        self.assertEqual(vr["inputs_used"]["statistic"], "median")
        self.assertGreater(len(vr["inputs_used"]["peer_companies"]), 0)
        self.assertIn("confidence_indicators", vr)
        self.assertIn("peer_set_quality", vr["confidence_indicators"])

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


class ProtocolConformanceTests(unittest.TestCase):
    """Verify mock implementations satisfy their Protocol contracts at runtime."""

    def test_mock_market_index_is_market_index_source(self) -> None:
        source = MockMarketIndexSource()
        self.assertIsInstance(source, MarketIndexSource)

    def test_mock_comps_is_comparable_company_source(self) -> None:
        source = MockComparableCompanySource()
        self.assertIsInstance(source, ComparableCompanySource)


if __name__ == "__main__":
    unittest.main()
