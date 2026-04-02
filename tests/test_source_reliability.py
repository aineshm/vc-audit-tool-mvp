"""Tests for source reliability multiplier and its integration with confidence scoring."""

from __future__ import annotations

import unittest
from datetime import date

from vc_audit_tool.data_sources.evidence_collector import (
    ValuationEvidence,
    extract_evidence,
)
from vc_audit_tool.data_sources.evidence_patterns import (
    SOURCE_RELIABILITY_TIERS,
    _classify_evidence_type,
    _source_reliability_multiplier,
)


class SourceReliabilityMultiplierTests(unittest.TestCase):
    """Unit tests for _source_reliability_multiplier()."""

    def test_tier1_bloomberg_returns_095(self) -> None:
        mult, tier = _source_reliability_multiplier("Bloomberg - Stripe valued at $65B")
        self.assertAlmostEqual(mult, 0.95)
        self.assertEqual(tier, "tier_1_premier_financial")

    def test_tier1_case_insensitive(self) -> None:
        mult, tier = _source_reliability_multiplier("REUTERS: Company report")
        self.assertAlmostEqual(mult, 0.95)
        self.assertEqual(tier, "tier_1_premier_financial")

    def test_tier1_cnbc(self) -> None:
        mult, _ = _source_reliability_multiplier("CNBC exclusive: Stripe secondary market")
        self.assertAlmostEqual(mult, 0.95)

    def test_tier2_techcrunch(self) -> None:
        mult, tier = _source_reliability_multiplier("TechCrunch - Latest funding round")
        self.assertAlmostEqual(mult, 0.85)
        self.assertEqual(tier, "tier_2_specialist_tech")

    def test_tier2_crunchbase(self) -> None:
        mult, tier = _source_reliability_multiplier("Crunchbase Funding Data")
        self.assertAlmostEqual(mult, 0.85)
        self.assertEqual(tier, "tier_2_specialist_tech")

    def test_tier3_venturebeat(self) -> None:
        mult, tier = _source_reliability_multiplier("VentureBeat AI report")
        self.assertAlmostEqual(mult, 0.75)
        self.assertEqual(tier, "tier_3_general_press")

    def test_tier3_yahoo_finance(self) -> None:
        mult, tier = _source_reliability_multiplier("Yahoo Finance - Market analysis")
        self.assertAlmostEqual(mult, 0.75)
        self.assertEqual(tier, "tier_3_general_press")

    def test_tier4_unknown_source(self) -> None:
        mult, tier = _source_reliability_multiplier("randomsite.com article about Stripe")
        self.assertAlmostEqual(mult, 0.65)
        self.assertEqual(tier, "tier_4_unrecognized")

    def test_tier5_reddit(self) -> None:
        mult, tier = _source_reliability_multiplier("reddit.com - r/startups discussion")
        self.assertAlmostEqual(mult, 0.50)
        self.assertEqual(tier, "tier_5_low_quality")

    def test_tier5_medium(self) -> None:
        mult, tier = _source_reliability_multiplier("medium.com: Stripe valuation analysis")
        self.assertAlmostEqual(mult, 0.50)
        self.assertEqual(tier, "tier_5_low_quality")

    def test_llm_extraction_special_case(self) -> None:
        mult, tier = _source_reliability_multiplier("LLM extraction")
        self.assertAlmostEqual(mult, 0.80)
        self.assertEqual(tier, "tier_llm_synthetic")

    def test_none_source_returns_default(self) -> None:
        mult, tier = _source_reliability_multiplier(None)
        self.assertAlmostEqual(mult, 0.65)
        self.assertEqual(tier, "tier_4_unrecognized")

    def test_empty_string_returns_default(self) -> None:
        mult, tier = _source_reliability_multiplier("")
        self.assertAlmostEqual(mult, 0.65)
        self.assertEqual(tier, "tier_4_unrecognized")


class ConfidenceFormulaIntegrationTests(unittest.TestCase):
    """Tests that confidence = base * recency * source_reliability."""

    AS_OF = date(2026, 3, 1)

    def test_classify_returns_three_tuple(self) -> None:
        result = _classify_evidence_type(
            "secondary",
            159_000_000_000,
            "Stripe secondary market tender offer $159B",
            "2026-02-28",
            self.AS_OF,
            source_title="Bloomberg - Stripe tender offer",
        )
        self.assertEqual(len(result), 3)
        ev_type, confidence, tier = result
        self.assertIsInstance(ev_type, str)
        self.assertIsInstance(confidence, float)
        self.assertIsInstance(tier, str)

    def test_bloomberg_fresh_secondary_high_confidence(self) -> None:
        """Bloomberg + fresh secondary market: 0.90 * 1.00 * 0.95 = 0.855."""
        ev_type, confidence, tier = _classify_evidence_type(
            "secondary",
            159_000_000_000,
            "Stripe secondary market tender offer $159B",
            "2026-02-28",
            self.AS_OF,
            source_title="Bloomberg - Stripe tender offer",
        )
        self.assertEqual(ev_type, "secondary_market")
        self.assertAlmostEqual(confidence, 0.855, places=2)
        self.assertEqual(tier, "tier_1_premier_financial")

    def test_reddit_same_signal_lower_confidence(self) -> None:
        """reddit.com + fresh secondary market: 0.90 * 1.00 * 0.50 = 0.45."""
        ev_type, confidence, tier = _classify_evidence_type(
            "secondary",
            159_000_000_000,
            "Stripe secondary market tender offer $159B",
            "2026-02-28",
            self.AS_OF,
            source_title="reddit.com - r/investing Stripe discussion",
        )
        self.assertEqual(ev_type, "secondary_market")
        self.assertAlmostEqual(confidence, 0.45, places=2)
        self.assertEqual(tier, "tier_5_low_quality")

    def test_bloomberg_higher_confidence_than_reddit(self) -> None:
        """Bloomberg signal confidence must exceed Reddit signal confidence."""
        _, bloomberg_conf, _ = _classify_evidence_type(
            "secondary",
            159_000_000_000,
            "Stripe secondary market $159B tender offer",
            "2026-02-28",
            self.AS_OF,
            source_title="Bloomberg",
        )
        _, reddit_conf, _ = _classify_evidence_type(
            "secondary",
            159_000_000_000,
            "Stripe secondary market $159B tender offer",
            "2026-02-28",
            self.AS_OF,
            source_title="reddit.com",
        )
        self.assertGreater(bloomberg_conf, reddit_conf)

    def test_source_reliability_tier_in_evidence_dict(self) -> None:
        """ValuationEvidence.to_dict() must include source_reliability_tier."""
        snippet = "Stripe valued at $159 billion in a tender offer"
        pkg = extract_evidence(
            [snippet],
            ["Bloomberg - Stripe"],
            "Stripe",
            as_of=date(2026, 3, 1),
            source_dates=["2026-02-28"],
        )
        self.assertGreater(len(pkg.evidence), 0)
        d = pkg.evidence[0].to_dict()
        self.assertIn("source_reliability_tier", d)
        self.assertIsNotNone(d["source_reliability_tier"])

    def test_valuation_evidence_has_tier_field(self) -> None:
        """ValuationEvidence dataclass must accept source_reliability_tier."""
        ev = ValuationEvidence(
            amount_usd=159e9,
            evidence_type="secondary_market",
            source_snippet="test",
            source_reliability_tier="tier_1_premier_financial",
        )
        self.assertEqual(ev.source_reliability_tier, "tier_1_premier_financial")

    def test_valuation_evidence_tier_defaults_none(self) -> None:
        """ValuationEvidence.source_reliability_tier should default to None."""
        ev = ValuationEvidence(
            amount_usd=159e9,
            evidence_type="secondary_market",
            source_snippet="test",
        )
        self.assertIsNone(ev.source_reliability_tier)

    def test_no_source_title_uses_default_tier(self) -> None:
        """When source_title is None, tier should be tier_4_unrecognized."""
        _, _, tier = _classify_evidence_type(
            "direct",
            159_000_000_000,
            "Stripe valued at $159 billion",
            "2026-02-28",
            self.AS_OF,
            source_title=None,
        )
        self.assertEqual(tier, "tier_4_unrecognized")


class SourceReliabilityTiersStructureTests(unittest.TestCase):
    """Tests for the SOURCE_RELIABILITY_TIERS data structure."""

    def test_tiers_list_is_non_empty(self) -> None:
        self.assertGreater(len(SOURCE_RELIABILITY_TIERS), 0)

    def test_all_multipliers_in_valid_range(self) -> None:
        for keyword, mult, _label in SOURCE_RELIABILITY_TIERS:
            self.assertGreaterEqual(mult, 0.50, f"Multiplier for {keyword} too low")
            self.assertLessEqual(mult, 1.00, f"Multiplier for {keyword} too high")

    def test_all_tiers_have_valid_labels(self) -> None:
        valid_labels = {
            "tier_1_premier_financial",
            "tier_2_specialist_tech",
            "tier_3_general_press",
            "tier_4b_press_release",
            "tier_4c_aggregator",
            "tier_5_low_quality",
        }
        for _, _, label in SOURCE_RELIABILITY_TIERS:
            self.assertIn(label, valid_labels, f"Unknown label: {label}")

    def test_bloomberg_is_tier1(self) -> None:
        bloomberg_entries = [(k, m, t) for k, m, t in SOURCE_RELIABILITY_TIERS if k == "bloomberg"]
        self.assertEqual(len(bloomberg_entries), 1)
        self.assertAlmostEqual(bloomberg_entries[0][1], 0.95)


if __name__ == "__main__":
    unittest.main()
