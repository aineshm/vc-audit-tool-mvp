"""Edge-case and boundary tests for both valuation methodologies."""

from __future__ import annotations

import unittest

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError


class LastRoundEdgeCaseTests(unittest.TestCase):
    """Edge and boundary cases for the last-round market-adjusted method."""

    def setUp(self) -> None:
        self.engine = ValuationEngine()

    def _payload(self, **overrides: object) -> dict:
        base = {
            "company_name": "TestCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
                "public_index": "NASDAQ_COMPOSITE",
            },
        }
        base["inputs"].update(overrides)  # type: ignore[union-attr]
        return base

    # ── Happy-path edge cases ──

    def test_same_day_round_and_as_of(self) -> None:
        """When round date equals as-of date, value should be unchanged."""
        payload = self._payload(last_round_date="2026-02-18")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 100_000_000.0, places=2)

    def test_zero_valuation(self) -> None:
        """Zero post-money should produce zero fair value."""
        payload = self._payload(last_post_money_valuation=0)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 0.0)

    def test_string_valuation_accepted(self) -> None:
        """Numeric strings should be accepted for last_post_money_valuation."""
        payload = self._payload(last_post_money_valuation="50000000")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertGreater(vr["estimated_fair_value"]["amount"], 0)

    def test_russell_2000_index(self) -> None:
        """Alternative index should work."""
        payload = self._payload(public_index="RUSSELL_2000")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertGreater(vr["estimated_fair_value"]["amount"], 0)

    def test_confidence_staleness_high(self) -> None:
        """Round >12 months ago should produce HIGH staleness risk."""
        payload = self._payload(last_round_date="2024-06-30")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("HIGH", vr["confidence_indicators"]["staleness_risk"])

    def test_confidence_staleness_low(self) -> None:
        """Recent round should produce LOW staleness risk."""
        payload = self._payload(last_round_date="2025-12-31")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("LOW", vr["confidence_indicators"]["staleness_risk"])

    # ── Negative / error cases ──

    def test_missing_valuation_raises(self) -> None:
        payload = {
            "company_name": "TestCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {"last_round_date": "2024-06-30"},
        }
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_round_date_raises(self) -> None:
        payload = {
            "company_name": "TestCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {"last_post_money_valuation": 100_000_000},
        }
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_malformed_round_date_raises(self) -> None:
        payload = self._payload(last_round_date="June 30, 2024")
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_unknown_index_raises(self) -> None:
        payload = self._payload(public_index="SP500")
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)

    def test_negative_valuation_raises(self) -> None:
        payload = self._payload(last_post_money_valuation=-100)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_non_numeric_valuation_raises(self) -> None:
        payload = self._payload(last_post_money_valuation="abc")
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_bool_valuation_rejected(self) -> None:
        """bool is technically int subclass but must be rejected for numeric fields."""
        payload = self._payload(last_post_money_valuation=True)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_date_before_all_index_data_raises(self) -> None:
        payload = self._payload(last_round_date="2020-01-01")
        # as_of_date is fine but last_round_date is before any data
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)


class CompsEdgeCaseTests(unittest.TestCase):
    """Edge and boundary cases for the comparable-companies method."""

    def setUp(self) -> None:
        self.engine = ValuationEngine()

    def _payload(self, **overrides: object) -> dict:
        base = {
            "company_name": "TestCo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {
                "sector": "enterprise_software",
                "revenue_ltm": 10_000_000,
                "statistic": "median",
                "private_company_discount_pct": 0,
            },
        }
        base["inputs"].update(overrides)  # type: ignore[union-attr]
        return base

    # ── Happy-path edge cases ──

    def test_zero_revenue_produces_zero_value(self) -> None:
        payload = self._payload(revenue_ltm=0)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 0.0)

    def test_100_pct_discount_produces_zero_value(self) -> None:
        payload = self._payload(private_company_discount_pct=100)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 0.0)

    def test_no_discount_matches_gross(self) -> None:
        payload = self._payload(private_company_discount_pct=0)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        # 4 enterprise_software comps, median of [13.1, 12.4, 9.2, 11.8] = 12.1
        expected_gross = 10_000_000 * 12.1
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], expected_gross, places=0)

    def test_mean_statistic(self) -> None:
        payload = self._payload(statistic="mean")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertGreater(vr["estimated_fair_value"]["amount"], 0)
        self.assertEqual(vr["inputs_used"]["statistic"], "mean")

    def test_explicit_peer_tickers(self) -> None:
        payload = self._payload(peer_tickers=["SNOW", "DDOG"])
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        tickers = [p["ticker"] for p in vr["inputs_used"]["peer_companies"]]
        self.assertEqual(sorted(tickers), ["DDOG", "SNOW"])

    def test_confidence_peer_set_quality_high(self) -> None:
        """Sector with 4 comps → should be MEDIUM."""
        payload = self._payload(sector="enterprise_software")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("MEDIUM", vr["confidence_indicators"]["peer_set_quality"])

    def test_confidence_peer_set_quality_high_with_5_plus(self) -> None:
        """Explicit 5 tickers → HIGH quality."""
        payload = self._payload(peer_tickers=["SNOW", "DDOG", "MDB", "ZS", "S"])
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("HIGH", vr["confidence_indicators"]["peer_set_quality"])

    # ── Negative / error cases ──

    def test_missing_revenue_raises(self) -> None:
        payload = {
            "company_name": "TestCo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {"sector": "enterprise_software"},
        }
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_sector_raises(self) -> None:
        payload = {
            "company_name": "TestCo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-18",
            "inputs": {"revenue_ltm": 10_000_000},
        }
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_invalid_statistic_raises(self) -> None:
        payload = self._payload(statistic="mode")
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_discount_over_100_raises(self) -> None:
        payload = self._payload(private_company_discount_pct=101)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_negative_revenue_raises(self) -> None:
        payload = self._payload(revenue_ltm=-500)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_peer_tickers_not_list_raises(self) -> None:
        payload = self._payload(peer_tickers="SNOW")
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_unknown_ticker_raises(self) -> None:
        payload = self._payload(peer_tickers=["SNOW", "FAKE"])
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)

    def test_unknown_sector_raises(self) -> None:
        payload = self._payload(sector="quantum_computing")
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)

    def test_non_numeric_revenue_raises(self) -> None:
        payload = self._payload(revenue_ltm="lots")
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_bool_revenue_rejected(self) -> None:
        """bool is technically int subclass but must be rejected for numeric fields."""
        payload = self._payload(revenue_ltm=False)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_bool_discount_rejected(self) -> None:
        """bool must be rejected for discount percentage as well."""
        payload = self._payload(private_company_discount_pct=True)
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)


class RequestParsingEdgeCases(unittest.TestCase):
    """Top-level request parsing edge cases."""

    def setUp(self) -> None:
        self.engine = ValuationEngine()

    def test_missing_company_name_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(
                {"methodology": "last_round_market_adjusted", "inputs": {}}
            )

    def test_missing_methodology_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict({"company_name": "X", "inputs": {}})

    def test_missing_inputs_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(
                {"company_name": "X", "methodology": "last_round_market_adjusted"}
            )

    def test_empty_payload_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict({})

    def test_inputs_wrong_type_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(
                {
                    "company_name": "X",
                    "methodology": "last_round_market_adjusted",
                    "inputs": "not-a-dict",
                }
            )

    def test_malformed_as_of_date_raises(self) -> None:
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(
                {
                    "company_name": "X",
                    "methodology": "last_round_market_adjusted",
                    "inputs": {
                        "last_post_money_valuation": 100,
                        "last_round_date": "2024-06-30",
                    },
                    "as_of_date": "not-a-date",
                }
            )


if __name__ == "__main__":
    unittest.main()
