"""Tests for evidence quality improvements."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from vc_audit_tool.agent.llm_adapter import _extract_json_robust
from vc_audit_tool.data_sources.evidence_collector import (
    ValuationEvidence,
    _filter_outliers,
    extract_evidence,
)
from vc_audit_tool.data_sources.evidence_patterns import (
    _is_delta_context,
    _parse_relative_date,
    _recency_multiplier,
)


class RoundPatternTests(unittest.TestCase):
    """Tests for round pattern extraction (should ignore raise amounts)."""

    def test_round_pattern_ignores_raise_amount(self) -> None:
        """Stripe raised $150M should not appear; $9B valuation should."""
        snippet = "Stripe raised $150M at a $9B valuation."
        pkg = extract_evidence([snippet], ["TechCrunch"], "Stripe", as_of=date.today())

        amounts = [e.amount_usd for e in pkg.evidence]
        self.assertNotIn(150_000_000, amounts)

        has_9b_signal = any(abs(e.amount_usd - 9e9) / 9e9 < 0.10 for e in pkg.evidence)
        self.assertTrue(has_9b_signal, "Expected a signal within 10% of $9B")

    def test_round_pattern_ignores_plain_raise_amount(self) -> None:
        """Payments provider raised $150M should not appear; $9B post-money should."""
        snippet = "Payments provider Stripe has raised another $150M at a $9B post-money."
        pkg = extract_evidence([snippet], ["TechCrunch"], "Stripe", as_of=date.today())

        amounts = [e.amount_usd for e in pkg.evidence]
        self.assertNotIn(150_000_000, amounts)

        has_9b_signal = any(abs(e.amount_usd - 9e9) / 9e9 < 0.10 for e in pkg.evidence)
        self.assertTrue(has_9b_signal, "Expected a signal within 10% of $9B")


class RecencyMultiplierTests(unittest.TestCase):
    """Tests for recency decay function."""

    def test_recency_multiplier_decay_values(self) -> None:
        """Verify decay values for different time windows."""
        as_of = date(2026, 2, 27)

        # ~1 month old: 1.0 (< 6 months)
        self.assertEqual(_recency_multiplier("2026-02-01", as_of), 1.0)

        # ~25 months old: 0.55 (24-36 months)
        result_25mo = _recency_multiplier("2024-01-01", as_of)
        self.assertEqual(result_25mo, 0.55)

        # ~61 months old: 0.30 (max decay)
        self.assertEqual(_recency_multiplier("2021-01-01", as_of), 0.30)

        # Unknown date: 0.85
        self.assertEqual(_recency_multiplier(None, as_of), 0.85)

    def test_recency_multiplier_does_not_exceed_one(self) -> None:
        """Recent dates should not exceed 1.0."""
        as_of = date(2026, 2, 27)

        # Less than 1 month
        result_fresh = _recency_multiplier("2026-02-01", as_of)
        self.assertEqual(result_fresh, 1.0)

        # About 1 month
        result_one_mo = _recency_multiplier("2026-01-01", as_of)
        self.assertLessEqual(result_one_mo, 1.0)


class FilterOutliersTests(unittest.TestCase):
    """Tests for outlier filtering."""

    def test_filter_outliers_removes_noise(self) -> None:
        """Extreme outliers should be removed; high-confidence signals kept."""
        evidence = [
            ValuationEvidence(159e9, "secondary_market", "s1", confidence=0.90),
            ValuationEvidence(106e9, "post_money_fresh", "s2", confidence=0.85),
            ValuationEvidence(91e9, "secondary_market", "s3", confidence=0.90),
            ValuationEvidence(150e6, "post_money_fresh", "s4", confidence=0.68),
            ValuationEvidence(6.5e9, "post_money_fresh", "s5", confidence=0.68),
        ]

        filtered = _filter_outliers(evidence)

        filtered_amounts = [e.amount_usd for e in filtered]
        self.assertNotIn(150e6, filtered_amounts)
        self.assertIn(159e9, filtered_amounts)
        self.assertIn(106e9, filtered_amounts)

    def test_filter_outliers_skips_small_lists(self) -> None:
        """With < 3 items, filtering should return list unchanged."""
        evidence = [
            ValuationEvidence(159e9, "secondary_market", "s1", confidence=0.90),
            ValuationEvidence(91e9, "secondary_market", "s2", confidence=0.90),
        ]

        filtered = _filter_outliers(evidence)

        self.assertEqual(len(filtered), 2)


class ExtractJsonRobustTests(unittest.TestCase):
    """Tests for robust JSON extraction from LLM output."""

    def test_extract_json_robust_handles_truncation(self) -> None:
        """Truncated JSON should be recovered."""
        truncated = '{"last_post_money_valuation": 159000000000, "last_round_date": "2025-02'
        result = _extract_json_robust(truncated)

        self.assertIsNotNone(result)
        self.assertEqual(result["last_post_money_valuation"], 159_000_000_000)

    def test_extract_json_robust_handles_markdown_fences(self) -> None:
        """JSON in markdown code fences should be extracted."""
        fenced = '```json\n{"last_post_money_valuation": 159000000000}\n```'
        result = _extract_json_robust(fenced)

        self.assertIsNotNone(result)
        self.assertEqual(result["last_post_money_valuation"], 159_000_000_000)

    def test_extract_json_robust_returns_none_on_garbage(self) -> None:
        """Non-JSON input should return None."""
        result = _extract_json_robust("This is not JSON at all")
        self.assertIsNone(result)


class RelativeDateParsingTests(unittest.TestCase):
    """Tests for _parse_relative_date()."""

    AS_OF = date(2026, 2, 28)

    def test_n_days_ago(self) -> None:
        result = _parse_relative_date("4 days ago · Stripe valued at $159B", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=4)).isoformat())

    def test_n_weeks_ago(self) -> None:
        result = _parse_relative_date("2 weeks ago, Stripe announced", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=14)).isoformat())

    def test_n_months_ago(self) -> None:
        result = _parse_relative_date("3 months ago the deal closed", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=90)).isoformat())

    def test_n_years_ago(self) -> None:
        result = _parse_relative_date("1 year ago Stripe raised", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=365)).isoformat())

    def test_yesterday(self) -> None:
        result = _parse_relative_date("yesterday · Fintech news", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=1)).isoformat())

    def test_last_week(self) -> None:
        result = _parse_relative_date("last week update on Stripe", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=7)).isoformat())

    def test_last_month(self) -> None:
        result = _parse_relative_date("last month Stripe announced funding", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=30)).isoformat())

    def test_last_year(self) -> None:
        result = _parse_relative_date("last year's valuation was $95B", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=365)).isoformat())

    def test_returns_none_no_match(self) -> None:
        result = _parse_relative_date("Stripe valuation $159B in recent tender offer", self.AS_OF)
        self.assertIsNone(result)

    def test_case_insensitive(self) -> None:
        result = _parse_relative_date("4 Days Ago · Stripe news", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=4)).isoformat())

    def test_singular_day(self) -> None:
        result = _parse_relative_date("1 day ago", self.AS_OF)
        self.assertEqual(result, (self.AS_OF - timedelta(days=1)).isoformat())


class DeltaContextFilterTests(unittest.TestCase):
    """Tests for _is_delta_context()."""

    def test_boost_by_is_delta(self) -> None:
        snippet = "could boost Stripe's valuation by $15 billion from last year"
        idx = snippet.index("$")
        self.assertTrue(_is_delta_context(snippet, idx))

    def test_increase_by_is_delta(self) -> None:
        snippet = "increase by $15 billion from previous round"
        idx = snippet.index("$")
        self.assertTrue(_is_delta_context(snippet, idx))

    def test_up_by_is_delta(self) -> None:
        snippet = "up by $10 billion in the latest tender"
        idx = snippet.index("$")
        self.assertTrue(_is_delta_context(snippet, idx))

    def test_grew_by_is_delta(self) -> None:
        snippet = "market cap grew by $8 billion this quarter"
        idx = snippet.index("$")
        self.assertTrue(_is_delta_context(snippet, idx))

    def test_down_by_is_delta(self) -> None:
        snippet = "valuation came down by $20 billion"
        idx = snippet.index("$")
        self.assertTrue(_is_delta_context(snippet, idx))

    def test_valued_at_not_delta(self) -> None:
        snippet = "Stripe is valued at $159 billion"
        idx = snippet.index("$")
        self.assertFalse(_is_delta_context(snippet, idx))

    def test_grew_to_not_delta(self) -> None:
        # "grew" appears AFTER $159B, so lookback check should not fire
        snippet = "$159 billion valuation after the company grew significantly"
        idx = snippet.index("$")
        self.assertFalse(_is_delta_context(snippet, idx))

    def test_tender_offer_not_delta(self) -> None:
        snippet = "$159 billion secondary market tender offer valuation"
        idx = snippet.index("$")
        self.assertFalse(_is_delta_context(snippet, idx))


class SecondaryMarketRecencyTests(unittest.TestCase):
    """Tests for Phase 4: unknown-date secondary_market penalty."""

    AS_OF = date(2026, 2, 28)

    def test_unknown_date_secondary_market_gets_0_70(self) -> None:
        result = _recency_multiplier(None, self.AS_OF, evidence_type="secondary_market")
        self.assertEqual(result, 0.70)

    def test_unknown_date_analyst_still_gets_0_85(self) -> None:
        result = _recency_multiplier(None, self.AS_OF, evidence_type="analyst_consensus")
        self.assertEqual(result, 0.85)

    def test_unknown_date_no_type_still_gets_0_85(self) -> None:
        """Backward compatibility: no evidence_type → 0.85."""
        result = _recency_multiplier(None, self.AS_OF)
        self.assertEqual(result, 0.85)

    def test_known_date_secondary_market_uses_age_bucket(self) -> None:
        """When date IS known, secondary_market gets normal age-based multiplier."""
        # 4 days old → < 6 months → 1.00
        fresh_date = (self.AS_OF - timedelta(days=4)).isoformat()
        result = _recency_multiplier(fresh_date, self.AS_OF, evidence_type="secondary_market")
        self.assertEqual(result, 1.00)


class SourceDatePassthroughTests(unittest.TestCase):
    """Tests for Phase 3: source_dates param in extract_evidence()."""

    AS_OF = date(2026, 2, 28)

    def test_source_date_overrides_text_extraction(self) -> None:
        """Structured source_date should be used as date_mentioned."""
        snippet = "Stripe valued at $159 billion in a tender offer"
        source_date = "2026-02-24"
        pkg = extract_evidence(
            [snippet], ["CNBC"], "Stripe", as_of=self.AS_OF, source_dates=[source_date]
        )
        # The signal should be present
        self.assertGreater(len(pkg.evidence), 0)
        # The date should match the source_date, not text-extracted date
        sig = next((e for e in pkg.evidence if abs(e.amount_usd - 159e9) / 159e9 < 0.05), None)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.date_mentioned, source_date)

    def test_source_date_none_falls_back_to_text(self) -> None:
        """None entry in source_dates triggers text-based date extraction."""
        snippet = "Stripe valued at $159 billion in January 2025 tender offer"
        pkg = extract_evidence(
            [snippet], ["Reuters"], "Stripe", as_of=self.AS_OF, source_dates=[None]
        )
        sig = next((e for e in pkg.evidence if abs(e.amount_usd - 159e9) / 159e9 < 0.05), None)
        self.assertIsNotNone(sig)
        assert sig is not None
        # Text extraction should find "January 2025"
        self.assertIsNotNone(sig.date_mentioned)

    def test_source_dates_shorter_than_snippets(self) -> None:
        """source_dates shorter than snippets should not raise IndexError."""
        snippets = [
            "Stripe valued at $159 billion in a tender offer",
            "Stripe secondary market at $91.5 billion last year",
        ]
        # Only one date for two snippets
        source_dates = ["2026-02-24"]
        try:
            pkg = extract_evidence(
                snippets, ["CNBC", "WSJ"], "Stripe", as_of=self.AS_OF, source_dates=source_dates
            )
            # Should not raise; second snippet uses text extraction
            self.assertIsInstance(pkg.evidence, list)
        except IndexError:
            self.fail("extract_evidence raised IndexError with shorter source_dates list")


class DeltaFilterIntegrationTests(unittest.TestCase):
    """Integration test: delta amounts are excluded from the evidence package."""

    AS_OF = date(2026, 2, 28)

    def test_boost_delta_not_extracted_as_valuation(self) -> None:
        """$15B in 'boost by $15B from $70B' should be filtered out."""
        snippet = (
            "Stripe nears $85 billion valuation in employee share sale "
            "-- If the deal goes through, it could boost Stripe's valuation "
            "by $15 billion from last year's $70 billion in a similar tender offer."
        )
        pkg = extract_evidence([snippet], ["Bloomberg"], "Stripe", as_of=self.AS_OF)

        amounts = [e.amount_usd for e in pkg.evidence]
        self.assertNotIn(15_000_000_000.0, amounts, "$15B delta should not appear as a signal")

    def test_valid_valuation_still_extracted(self) -> None:
        """Valid $85B valuation mention alongside delta should still be extracted."""
        snippet = (
            "Stripe nears $85 billion valuation in employee share sale "
            "-- If the deal goes through, it could boost Stripe's valuation "
            "by $15 billion from last year's $70 billion in a similar tender offer."
        )
        pkg = extract_evidence([snippet], ["Bloomberg"], "Stripe", as_of=self.AS_OF)

        # $85B (or similar) should be captured
        has_large_signal = any(e.amount_usd >= 50_000_000_000 for e in pkg.evidence)
        self.assertTrue(has_large_signal, "Expected at least one signal >= $50B")


if __name__ == "__main__":
    unittest.main()
