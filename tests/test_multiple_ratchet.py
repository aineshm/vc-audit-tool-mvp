"""Tests for the Last-Round Multiple-Ratchet methodology.

Covers:
  - TechCo scenario (happy path with mock data)
  - Multiple compression (ratchet < 1)
  - Multiple expansion (ratchet > 1)
  - No discount path
  - Explicit peer_tickers path
  - Mean statistic path
  - Ratchet severity labels (SEVERE / HIGH / MODERATE / STABLE / EXPANSION)
  - Input validation: missing fields, zero/negative revenue, bad statistic, bad peer_tickers
  - Engine integration via evaluate_from_dict
  - Confidence indicator and audit-trail structure
"""

from __future__ import annotations

import unittest

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError


class TestMultipleRatchetHappyPaths(unittest.TestCase):
    """Happy-path tests with mock data."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    # ── TechCo scenario (mock enterprise_software median = 11.8x) ──

    def test_techco_scenario_basic_output(self) -> None:
        """TechCo: $100M last, $10M→$12M revenue, 20% discount, sector median."""
        payload = {
            "company_name": "TechCo",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "revenue_at_last_round": 10_000_000,
                "current_revenue": 12_000_000,
                "sector": "enterprise_software",
                "statistic": "median",
                "private_company_discount_pct": 20,
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]

        self.assertEqual(vr["methodology"], "last_round_multiple_ratchet")
        self.assertEqual(vr["company_name"], "TechCo")
        # Mock median for enterprise_software = 11.8
        # re-rated = 12M * 11.8 = 141.6M; final = 141.6M * 0.80 = 113.28M
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 113_280_000.0, places=2)

    def test_techco_implied_multiple_is_10(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertAlmostEqual(vr["inputs_used"]["implied_multiple_at_last_round"], 10.0)

    def test_techco_current_market_multiple(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertAlmostEqual(vr["inputs_used"]["current_market_multiple"], 11.8)

    def test_techco_ratchet_is_expansion(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertGreater(vr["confidence_indicators"]["multiple_ratchet"], 1.0)
        self.assertIn("EXPANSION", vr["confidence_indicators"]["ratchet_severity"])

    def test_techco_derivation_has_seven_steps(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertEqual(len(vr["derivation_steps"]), 7)

    def test_techco_citations_have_peer_data(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertEqual(len(vr["citations"]), 1)
        citation = vr["citations"][0]
        self.assertIn("resolved_data_points", citation)
        self.assertEqual(len(citation["resolved_data_points"]), 7)  # 7 enterprise_software peers

    def test_techco_assumptions_have_five_items(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertEqual(len(vr["assumptions"]), 5)

    def test_techco_peer_companies_returned_in_inputs(self) -> None:
        payload = self._techco_payload()
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertEqual(len(vr["inputs_used"]["peer_companies"]), 7)
        tickers = {p["ticker"] for p in vr["inputs_used"]["peer_companies"]}
        self.assertIn("SNOW", tickers)
        self.assertIn("MDB", tickers)

    # ── No discount ──

    def test_no_discount(self) -> None:
        payload = self._techco_payload()
        payload["inputs"]["private_company_discount_pct"] = 0
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        # 12M * 11.8 = 141.6M
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 141_600_000.0, places=2)

    # ── Mean statistic ──

    def test_mean_statistic(self) -> None:
        payload = self._techco_payload()
        payload["inputs"]["statistic"] = "mean"
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        # enterprise_software mean = (13.1+12.4+9.2+11.8+10.5+14.8+11.2)/7 ≈ 11.857...
        # re-rated ≈ 12M * 11.857 ≈ 142_285_714.29; final ≈ * 0.80
        self.assertGreater(vr["estimated_fair_value"]["amount"], 100_000_000)
        self.assertEqual(vr["inputs_used"]["statistic"], "mean")

    # ── Explicit peer tickers ──

    def test_peer_tickers_explicit(self) -> None:
        """Use only two tickers — should get MEDIUM quality."""
        payload = self._techco_payload()
        payload["inputs"]["peer_tickers"] = ["MDB", "SNOW"]
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        # median of 9.2 and 13.1 = 11.15
        expected_rerated = 12_000_000 * 11.15
        expected_final = expected_rerated * 0.80
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], expected_final, places=2)
        self.assertEqual(vr["confidence_indicators"]["peer_count"], 2)
        self.assertIn("LOW", vr["confidence_indicators"]["peer_set_quality"])

    def test_target_description_included_for_sector_comps(self) -> None:
        payload = self._techco_payload()
        payload["inputs"]["target_description"] = "AI observability for cloud infra"
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertEqual(
            vr["inputs_used"]["target_description"],
            "AI observability for cloud infra",
        )

    # Helper
    def _techco_payload(self) -> dict:
        return {
            "company_name": "TechCo",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "revenue_at_last_round": 10_000_000,
                "current_revenue": 12_000_000,
                "sector": "enterprise_software",
                "statistic": "median",
                "private_company_discount_pct": 20,
            },
        }


class TestMultipleCompressionScenario(unittest.TestCase):
    """Simulate multiple compression using infrastructure_software (lower multiples)."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def test_compression_with_infrastructure_software(self) -> None:
        """If implied multiple at last round was high, infrastructure_software may compress it."""
        # infrastructure_software median: median(16.1, 3.8, 5.3) = 5.3
        payload = {
            "company_name": "InfraStart",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 200_000_000,
                "revenue_at_last_round": 10_000_000,
                # implied multiple = 20x, but infra median is 5.3 → ratchet = 5.3/20 = 0.265
                "current_revenue": 15_000_000,
                "sector": "infrastructure_software",
                "statistic": "median",
                "private_company_discount_pct": 25,
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]

        # re-rated = 15M * 5.3 = 79.5M; final = 79.5M * 0.75 = 59.625M
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 59_625_000.0, places=2)

        # Ratchet < 1 → compression
        ratchet = vr["confidence_indicators"]["multiple_ratchet"]
        self.assertLess(ratchet, 1.0)
        self.assertIn("SEVERE", vr["confidence_indicators"]["ratchet_severity"])


class TestRatchetSeverityLabels(unittest.TestCase):
    """Test all five ratchet-severity labels with crafted multiples."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def _make_payload(
        self,
        last_val: int,
        rev_at_last: int,
        sector: str = "enterprise_software",
    ) -> dict:
        return {
            "company_name": "LabelTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": last_val,
                "revenue_at_last_round": rev_at_last,
                "current_revenue": rev_at_last,  # flat revenue
                "sector": sector,
                "private_company_discount_pct": 0,
            },
        }

    def test_expansion_label(self) -> None:
        # implied = 100M/20M = 5x; enterprise_software median = 11.8x → ratchet = 2.36 → EXPANSION
        payload = self._make_payload(100_000_000, 20_000_000)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("EXPANSION", vr["confidence_indicators"]["ratchet_severity"])

    def test_stable_label(self) -> None:
        # We need implied ≈ market. enterprise_software median = 11.8.
        # If rev_at_last = 100M/11.8 ≈ 8_474_576 → implied = 11.8 → ratchet ≈ 1.0
        rev = int(100_000_000 / 11.8)  # ~ 8_474_576
        payload = self._make_payload(100_000_000, rev)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("STABLE", vr["confidence_indicators"]["ratchet_severity"])

    def test_moderate_label(self) -> None:
        # Need ratchet in [0.75, 0.9). enterprise_software median = 11.8.
        # implied = 14.75x → ratchet = 11.8/14.75 ≈ 0.8 → MODERATE
        # rev_at_last = 100M / 14.75 ≈ 6_779_661
        rev = int(100_000_000 / 14.75)
        payload = self._make_payload(100_000_000, rev)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("MODERATE", vr["confidence_indicators"]["ratchet_severity"])

    def test_high_severity_label(self) -> None:
        # Need ratchet in [0.5, 0.75). enterprise_software median = 11.8.
        # implied = 20x → ratchet = 11.8/20 = 0.59 → HIGH
        payload = self._make_payload(100_000_000, 5_000_000)
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("HIGH", vr["confidence_indicators"]["ratchet_severity"])

    def test_severe_label(self) -> None:
        # implied = 100M/3M ≈ 33.3x; infra median = 5.3; ratchet = 5.3/33.3 ≈ 0.159 → SEVERE
        payload = self._make_payload(100_000_000, 3_000_000, sector="infrastructure_software")
        vr = self.engine.evaluate_from_dict(payload).to_dict()["valuation_result"]
        self.assertIn("SEVERE", vr["confidence_indicators"]["ratchet_severity"])


class TestMultipleRatchetValidation(unittest.TestCase):
    """Input validation tests."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def _base_payload(self) -> dict:
        return {
            "company_name": "ValTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "revenue_at_last_round": 10_000_000,
                "current_revenue": 12_000_000,
                "sector": "enterprise_software",
            },
        }

    def test_missing_last_post_money(self) -> None:
        payload = self._base_payload()
        del payload["inputs"]["last_post_money_valuation"]
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_revenue_at_last_round(self) -> None:
        payload = self._base_payload()
        del payload["inputs"]["revenue_at_last_round"]
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_current_revenue(self) -> None:
        payload = self._base_payload()
        del payload["inputs"]["current_revenue"]
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_missing_sector(self) -> None:
        payload = self._base_payload()
        del payload["inputs"]["sector"]
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_zero_revenue_at_last_round(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["revenue_at_last_round"] = 0
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_negative_revenue_at_last_round(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["revenue_at_last_round"] = -5_000_000
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_zero_current_revenue(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["current_revenue"] = 0
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_negative_current_revenue(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["current_revenue"] = -1
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_invalid_statistic(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["statistic"] = "mode"
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_discount_over_100(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["private_company_discount_pct"] = 150
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_peer_tickers_not_a_list(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["peer_tickers"] = "SNOW"
        with self.assertRaises(ValidationError):
            self.engine.evaluate_from_dict(payload)

    def test_unknown_sector_raises_data_source_error(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["sector"] = "quantum_computing"
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)

    def test_unknown_ticker_raises_data_source_error(self) -> None:
        payload = self._base_payload()
        payload["inputs"]["peer_tickers"] = ["FOOBAR"]
        with self.assertRaises(DataSourceError):
            self.engine.evaluate_from_dict(payload)


class TestMultipleRatchetConfidenceIndicators(unittest.TestCase):
    """Verify structure of confidence indicators."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def test_all_confidence_keys_present(self) -> None:
        payload = {
            "company_name": "ConfTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 50_000_000,
                "revenue_at_last_round": 5_000_000,
                "current_revenue": 8_000_000,
                "sector": "cybersecurity",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        ci = out["valuation_result"]["confidence_indicators"]
        expected_keys = {
            "peer_count",
            "multiple_spread",
            "peer_set_quality",
            "implied_multiple_at_last_round",
            "current_market_multiple",
            "multiple_ratchet",
            "ratchet_severity",
            "revenue_growth_pct",
            "data_source_type",
        }
        self.assertEqual(set(ci.keys()), expected_keys)

    def test_revenue_growth_pct_is_correct(self) -> None:
        payload = {
            "company_name": "GrowTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 50_000_000,
                "revenue_at_last_round": 10_000_000,
                "current_revenue": 15_000_000,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        ci = out["valuation_result"]["confidence_indicators"]
        self.assertAlmostEqual(ci["revenue_growth_pct"], 50.0)

    def test_data_source_type_is_mock(self) -> None:
        payload = {
            "company_name": "SourceTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 50_000_000,
                "revenue_at_last_round": 5_000_000,
                "current_revenue": 7_000_000,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        ci = out["valuation_result"]["confidence_indicators"]
        self.assertEqual(ci["data_source_type"], "mock")

    def test_cybersecurity_peer_count_is_five(self) -> None:
        payload = {
            "company_name": "CyberTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 50_000_000,
                "revenue_at_last_round": 5_000_000,
                "current_revenue": 7_000_000,
                "sector": "cybersecurity",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        ci = out["valuation_result"]["confidence_indicators"]
        self.assertEqual(ci["peer_count"], 5)
        self.assertIn("HIGH", ci["peer_set_quality"])


class TestMultipleRatchetAuditMetadata(unittest.TestCase):
    """Verify audit metadata envelope."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def test_audit_metadata_present(self) -> None:
        payload = {
            "company_name": "MetaTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 80_000_000,
                "revenue_at_last_round": 8_000_000,
                "current_revenue": 10_000_000,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        meta = out["audit_metadata"]
        self.assertIn("request_id", meta)
        self.assertIn("generated_at_utc", meta)
        self.assertIn("engine_version", meta)

    def test_currency_is_usd(self) -> None:
        payload = {
            "company_name": "CurrTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 80_000_000,
                "revenue_at_last_round": 8_000_000,
                "current_revenue": 10_000_000,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        self.assertEqual(out["valuation_result"]["estimated_fair_value"]["currency"], "USD")


class TestMultipleRatchetEdgeCases(unittest.TestCase):
    """Edge cases."""

    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()

    def test_very_small_revenue_still_works(self) -> None:
        payload = {
            "company_name": "TinyRev",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 1_000_000,
                "revenue_at_last_round": 1,
                "current_revenue": 1,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        # Should still produce a numeric result
        self.assertGreater(vr["estimated_fair_value"]["amount"], 0)

    def test_revenue_decline(self) -> None:
        """Revenue dropped — should still work, revenue_growth_pct < 0."""
        payload = {
            "company_name": "DeclineTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "revenue_at_last_round": 20_000_000,
                "current_revenue": 15_000_000,
                "sector": "enterprise_software",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        ci = out["valuation_result"]["confidence_indicators"]
        self.assertLess(ci["revenue_growth_pct"], 0)

    def test_string_inputs_coerced(self) -> None:
        """Revenue/valuation provided as strings should be parsed correctly."""
        payload = {
            "company_name": "StringTest",
            "methodology": "last_round_multiple_ratchet",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": "100000000",
                "revenue_at_last_round": "10000000",
                "current_revenue": "12000000",
                "sector": "enterprise_software",
                "private_company_discount_pct": "20",
            },
        }
        out = self.engine.evaluate_from_dict(payload).to_dict()
        vr = out["valuation_result"]
        self.assertAlmostEqual(vr["estimated_fair_value"]["amount"], 113_280_000.0, places=2)


if __name__ == "__main__":
    unittest.main()
