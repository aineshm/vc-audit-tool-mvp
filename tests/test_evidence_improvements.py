"""Tests for evidence quality improvements."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from typing import Any

from vc_audit_tool.agent.llm_adapter import (
    _extract_json_robust,
    _llm_judge_valuation,
    _needs_judgment,
)
from vc_audit_tool.data_sources.evidence_collector import (
    EvidencePackage,
    ValuationEvidence,
    _date_sortable,
    _extract_revenue_signals,
    _extract_round_date_signals,
    _filter_outliers,
    extract_evidence,
)
from vc_audit_tool.data_sources.evidence_patterns import (
    _DATE_NEAR_SIGNAL,
    _is_delta_context,
    _is_rumoured_round,
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


class RaiseValuationDisambiguationTests(unittest.TestCase):
    """Tests for raise/valuation disambiguation across all common article phrasings.

    The system must extract the POST-MONEY VALUATION (not the raise amount)
    regardless of how the article phrases it.  These tests cover the seven
    canonical phrasings identified during World Labs / OpenAI debugging.
    """

    AS_OF = date(2026, 3, 1)

    def _extract_amounts(self, snippet: str, company: str = "World Labs") -> list[float]:
        pkg = extract_evidence([snippet], ["TechCrunch"], company, as_of=self.AS_OF)
        return [e.amount_usd for e in pkg.evidence]

    def _has_signal_near(self, amounts: list[float], target: float, tol: float = 0.10) -> bool:
        return any(abs(a - target) / target < tol for a in amounts)

    # ── Phrasing 1: classic "raised $X at a $Y valuation" ────────────────

    def test_raised_x_at_y_valuation_extracts_y(self) -> None:
        """'raised $1B at a $5B valuation' → $5B captured, not $1B."""
        amounts = self._extract_amounts("World Labs raised $1B at a $5B valuation.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )
        self.assertFalse(
            self._has_signal_near(amounts, 1e9),
            f"$1B (raise amount) should NOT appear in {amounts}",
        )

    # ── Phrasing 2: "lands $X round, valued at $Y" ───────────────────────

    def test_lands_x_round_valued_at_y(self) -> None:
        """'lands $1B round, valued at $5B' → $5B captured."""
        amounts = self._extract_amounts("World Labs lands $1B round, valued at $5B.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── Phrasing 3: "closes $X funding at $Y post-money" ─────────────────

    def test_closes_x_at_y_post_money(self) -> None:
        """'closes $1B funding at $5B post-money' → $5B captured."""
        amounts = self._extract_amounts("World Labs closes $1B funding at $5B post-money.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── Phrasing 4: "inks $X deal at $Y" ─────────────────────────────────

    def test_inks_x_deal_at_y(self) -> None:
        """'inks $1B deal at $5B' → $5B captured."""
        amounts = self._extract_amounts("World Labs inks $1B deal at $5B.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── Phrasing 5: "$X round puts company valuation at $Y" ──────────────

    def test_round_puts_valuation_at_y(self) -> None:
        """'$1B round puts company valuation at $5B' → $5B captured."""
        amounts = self._extract_amounts("The $1B round puts World Labs company valuation at $5B.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── Phrasing 6: "new $X funding values [company] at $Y" ──────────────

    def test_funding_values_company_at_y(self) -> None:
        """'new $1B funding values World Labs at $5B' → $5B captured."""
        amounts = self._extract_amounts("New $1B funding values World Labs at $5B.")
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── Phrasing 7: "$Y valuation for [company], which raised $X" ─────────

    def test_y_valuation_for_company_which_raised_x(self) -> None:
        """'$5B valuation for World Labs, which raised $1B' → $5B captured."""
        amounts = self._extract_amounts(
            "$5B valuation for World Labs, which raised $1B in its latest round."
        )
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts}",
        )

    # ── No valuation disclosed: raise-only mention ────────────────────────

    def test_raises_x_no_valuation_disclosed(self) -> None:
        """'World Labs raises $1B' with no valuation → no evidence extracted."""
        amounts = self._extract_amounts(
            "World Labs raises $1B in new funding round, no valuation disclosed."
        )
        billion_signals = [a for a in amounts if a >= 1e9]
        # Allow empty or non-1B signals; just ensure $1B is NOT taken as valuation
        has_only_raise = self._has_signal_near(billion_signals, 1e9) and not self._has_signal_near(
            billion_signals, 5e9
        )
        self.assertFalse(has_only_raise)
        # Specifically: no $1B valuation-sized signal
        self.assertFalse(
            self._has_signal_near(amounts, 1e9),
            f"$1B raise amount should NOT appear as a valuation in {amounts}",
        )

    # ── Critical regression: "in talks to raise … at $Y valuation" ────────

    def test_raise_verb_distant_from_valuation_not_suppressed(self) -> None:
        """'reportedly in talks to raise a new round at a $5B valuation' → $5B kept."""
        snippet = (
            "World Labs is reportedly in talks to raise a new funding round at a $5B valuation."
        )
        amounts = self._extract_amounts(snippet)
        self.assertTrue(
            self._has_signal_near(amounts, 5e9),
            f"Expected ~$5B in {amounts} — raise verb was too far away to suppress it",
        )

    # ── OpenAI scenario: $110B raise amount vs $840B valuation ───────────

    def test_openai_raise_not_captured_as_valuation(self) -> None:
        """'$110B raise at $840B valuation' → $840B captured, $110B suppressed."""
        snippet = "OpenAI closed a staggering $110 billion fundraise at an $840B Valuation."
        amounts = self._extract_amounts(snippet, company="OpenAI")
        self.assertTrue(
            self._has_signal_near(amounts, 840e9),
            f"Expected ~$840B in {amounts}",
        )
        self.assertFalse(
            self._has_signal_near(amounts, 110e9),
            f"$110B raise amount should NOT appear in {amounts}",
        )


class NeedsJudgmentTests(unittest.TestCase):
    """Tests for _needs_judgment() trigger logic."""

    def _make_ev(self, amount: float) -> ValuationEvidence:
        return ValuationEvidence(amount, "post_money_fresh", "snippet")

    def test_single_candidate_no_judgment(self) -> None:
        """Single candidate → nothing to compare, no judgment needed."""
        self.assertFalse(_needs_judgment([self._make_ev(5e9)]))

    def test_empty_candidates_no_judgment(self) -> None:
        self.assertFalse(_needs_judgment([]))

    def test_identical_amounts_no_judgment(self) -> None:
        """Zero spread → consensus already clear."""
        self.assertFalse(_needs_judgment([self._make_ev(5e9), self._make_ev(5e9)]))

    def test_small_spread_no_judgment(self) -> None:
        """$4.9B vs $5.1B = 4% spread → no judgment."""
        self.assertFalse(_needs_judgment([self._make_ev(4.9e9), self._make_ev(5.1e9)]))

    def test_large_spread_triggers_judgment(self) -> None:
        """$1B vs $5B = 80% spread → judgment needed."""
        self.assertTrue(_needs_judgment([self._make_ev(1e9), self._make_ev(5e9)]))

    def test_all_below_1M_no_judgment(self) -> None:
        """Sub-million amounts are not real valuation signals."""
        self.assertFalse(_needs_judgment([self._make_ev(100_000), self._make_ev(500_000)]))

    def test_exactly_20pct_spread_boundary(self) -> None:
        """Spread of exactly 20% sits at boundary — result is False (not >20%)."""
        lo, hi = 4e9, 5e9  # (5-4)/5 = 20% exactly
        self.assertFalse(_needs_judgment([self._make_ev(lo), self._make_ev(hi)]))

    def test_just_above_20pct_triggers(self) -> None:
        """Spread of 21% triggers judgment."""
        lo, hi = 3.95e9, 5e9  # (5-3.95)/5 = 21%
        self.assertTrue(_needs_judgment([self._make_ev(lo), self._make_ev(hi)]))


class LlmJudgeValuationTests(unittest.TestCase):
    """Tests for _llm_judge_valuation() using a mock LLM."""

    def _mock_llm(self, json_response: str) -> Any:
        """Return a minimal mock LLM that returns the given string as content."""

        class _Response:
            content = json_response
            usage_metadata = None

        class _MockLLM:
            def invoke(self, messages: Any) -> Any:
                return _Response()

        return _MockLLM()

    def _make_ev(
        self,
        amount: float,
        ev_type: str = "post_money_fresh",
        date: str | None = "2026-01-15",
    ) -> ValuationEvidence:
        return ValuationEvidence(
            amount,
            ev_type,
            f"snippet about ${amount / 1e9:.1f}B",
            date_mentioned=date,
        )

    def test_returns_none_when_no_candidates(self) -> None:
        """Empty candidate list → short-circuit, no LLM call."""
        val, reason = _llm_judge_valuation(
            self._mock_llm('{"validated_valuation": 5e9}'),
            "mock/model",
            "TestCo",
            [],
            [],
        )
        self.assertIsNone(val)
        self.assertIsNone(reason)

    def test_returns_validated_valuation(self) -> None:
        """Judge picks $5B from a $1B vs $5B conflict."""
        candidates = [self._make_ev(1e9, date="2024-03"), self._make_ev(5e9, date="2026-01")]
        val, reason = _llm_judge_valuation(
            self._mock_llm('{"validated_valuation": 5000000000, "reason": "most recent"}'),
            "mock/model",
            "World Labs",
            candidates,
            ["World Labs raised $1B at a $5B valuation."],
        )
        self.assertEqual(val, 5_000_000_000.0)
        self.assertEqual(reason, "most recent")

    def test_returns_none_when_llm_says_null(self) -> None:
        """Judge returns null → no override."""
        candidates = [self._make_ev(1e9), self._make_ev(5e9)]
        val, reason = _llm_judge_valuation(
            self._mock_llm('{"validated_valuation": null, "reason": "all raise amounts"}'),
            "mock/model",
            "TestCo",
            candidates,
            [],
        )
        self.assertIsNone(val)

    def test_returns_none_on_bad_json(self) -> None:
        """Unparseable LLM response → None (no crash)."""
        candidates = [self._make_ev(1e9), self._make_ev(5e9)]
        val, _reason = _llm_judge_valuation(
            self._mock_llm("I cannot determine the valuation from the given context."),
            "mock/model",
            "TestCo",
            candidates,
            [],
        )
        self.assertIsNone(val)

    def test_returns_none_on_sub_million_value(self) -> None:
        """Judge returns an implausibly tiny number → rejected."""
        candidates = [self._make_ev(1e9), self._make_ev(5e9)]
        val, _reason = _llm_judge_valuation(
            self._mock_llm('{"validated_valuation": 500, "reason": "bad extraction"}'),
            "mock/model",
            "TestCo",
            candidates,
            [],
        )
        self.assertIsNone(val)

    def test_markdown_fence_json_parsed(self) -> None:
        """Judge response wrapped in code fences is still parsed."""
        candidates = [self._make_ev(1e9), self._make_ev(5e9)]
        val, reason = _llm_judge_valuation(
            self._mock_llm(
                '```json\n{"validated_valuation": 5000000000, "reason": "2026 round"}\n```'
            ),
            "mock/model",
            "TestCo",
            candidates,
            [],
        )
        self.assertEqual(val, 5_000_000_000.0)
        self.assertEqual(reason, "2026 round")


class RumourPatternsTests(unittest.TestCase):
    """Tests for extended _RUMOUR_PATTERNS (Bug 1)."""

    def test_is_rumoured_round_according_to_sources(self) -> None:
        self.assertTrue(_is_rumoured_round("valued at $700 million according to sources"))

    def test_is_rumoured_round_sources_say(self) -> None:
        self.assertTrue(_is_rumoured_round("sources say the company is worth $5B"))

    def test_is_rumoured_round_people_familiar(self) -> None:
        self.assertTrue(_is_rumoured_round("people familiar with the deal say $5B valuation"))

    def test_is_rumoured_round_familiar_with_the_matter(self) -> None:
        self.assertTrue(_is_rumoured_round("familiar with the matter say the valuation is $5B"))

    def test_is_rumoured_round_confirmed_does_not_trigger(self) -> None:
        self.assertFalse(_is_rumoured_round("Stripe raised $600M at a $6.5B valuation"))


class RevenueContaminationTests(unittest.TestCase):
    """Tests for revenue signal contamination guard (Bug 2)."""

    def test_valuation_revenue_seo_title_suppressed(self) -> None:
        pkg = EvidencePackage(company_name="TestCo")
        snippet = "TestCo: Valuation, Revenue & Financial Statements - Growjo | $700M valuation"
        _extract_revenue_signals(snippet, pkg)
        self.assertEqual(pkg.revenue_signals, [])

    def test_clean_revenue_snippet_passes(self) -> None:
        pkg = EvidencePackage(company_name="TestCo")
        _extract_revenue_signals("TestCo annual revenue of $50M ARR in 2025", pkg)
        self.assertIn(50_000_000, pkg.revenue_signals)

    def test_revenue_run_rate_not_suppressed(self) -> None:
        pkg = EvidencePackage(company_name="TestCo")
        _extract_revenue_signals("TestCo crosses $10M ARR run rate", pkg)
        self.assertGreater(len(pkg.revenue_signals), 0)


class RoundDatePrecisionTests(unittest.TestCase):
    """Tests for round date day-level precision (Bug 3)."""

    def test_date_near_signal_captures_day_level(self) -> None:
        m = _DATE_NEAR_SIGNAL.search("closed November 20, 2025 at $5B")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "November 20, 2025")

    def test_date_sortable_day_level(self) -> None:
        self.assertEqual(_date_sortable("November 20, 2025"), "2025-11-20")
        self.assertEqual(_date_sortable("November 20 2025"), "2025-11-20")

    def test_date_sortable_month_level(self) -> None:
        self.assertEqual(_date_sortable("November 2025"), "2025-11-01")

    def test_extract_round_date_day_level(self) -> None:
        pkg = EvidencePackage(company_name="TestCo")
        _extract_round_date_signals("closed Series B November 20, 2025", pkg)
        self.assertIn("November 20, 2025", pkg.round_date_signals)


if __name__ == "__main__":
    unittest.main()
