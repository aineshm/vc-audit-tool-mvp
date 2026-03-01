"""Tests for private company discount transparency and configuration."""

from __future__ import annotations

import unittest

from vc_audit_tool.methodologies._discount_config import (
    clamp_discount,
    get_discount_default,
)


class DiscountConfigDefaultsTests(unittest.TestCase):
    """Tests for get_discount_default() loading from config/fallback."""

    def test_comps_default_25(self) -> None:
        self.assertEqual(get_discount_default("comparable_companies"), 25.0)

    def test_ratchet_default_25(self) -> None:
        self.assertEqual(get_discount_default("last_round_multiple_ratchet"), 25.0)

    def test_last_round_default_10(self) -> None:
        self.assertEqual(get_discount_default("last_round_market_adjusted"), 10.0)

    def test_direct_valuation_with_secondary_10(self) -> None:
        result = get_discount_default("direct_valuation", has_secondary_evidence=True)
        self.assertEqual(result, 10.0)

    def test_direct_valuation_without_secondary_20(self) -> None:
        result = get_discount_default("direct_valuation", has_secondary_evidence=False)
        self.assertEqual(result, 20.0)

    def test_scorecard_returns_zero(self) -> None:
        result = get_discount_default("scorecard")
        self.assertEqual(result, 0.0)

    def test_berkus_returns_zero(self) -> None:
        result = get_discount_default("berkus")
        self.assertEqual(result, 0.0)

    def test_unknown_methodology_returns_zero(self) -> None:
        result = get_discount_default("nonexistent_methodology")
        self.assertEqual(result, 0.0)

    def test_return_type_is_float(self) -> None:
        result = get_discount_default("comparable_companies")
        self.assertIsInstance(result, float)


class DiscountClampTests(unittest.TestCase):
    """Tests for clamp_discount()."""

    def test_normal_value_unchanged(self) -> None:
        self.assertEqual(clamp_discount(25.0), 25.0)

    def test_zero_allowed(self) -> None:
        self.assertEqual(clamp_discount(0.0), 0.0)

    def test_negative_clamped_to_zero(self) -> None:
        self.assertEqual(clamp_discount(-5.0), 0.0)

    def test_above_max_clamped_to_50(self) -> None:
        self.assertEqual(clamp_discount(75.0), 50.0)

    def test_exactly_50_allowed(self) -> None:
        self.assertEqual(clamp_discount(50.0), 50.0)

    def test_custom_max(self) -> None:
        self.assertEqual(clamp_discount(30.0, max_pct=25.0), 25.0)

    def test_integer_input_works(self) -> None:
        self.assertEqual(clamp_discount(25), 25.0)


class LastRoundDiscountAuditTrailTests(unittest.TestCase):
    """Tests that last_round_market_adjusted includes discount in audit trail."""

    def _run_last_round(self, discount_override: float | None = None) -> dict:
        """Run the last_round methodology with mock context."""
        import datetime
        from decimal import Decimal
        from unittest.mock import MagicMock

        from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
        from vc_audit_tool.models import ValuationRequest

        inputs: dict = {
            "last_post_money_valuation": 100_000_000_000,
            "last_round_date": "2024-06-01",
        }
        if discount_override is not None:
            inputs["private_company_discount_pct"] = discount_override

        request = ValuationRequest(
            company_name="TestCo",
            methodology="last_round_market_adjusted",
            as_of_date=datetime.date(2026, 3, 1),
            inputs=inputs,
        )

        # Mock index source
        mock_level_last = MagicMock()
        mock_level_last.level = Decimal("5000")
        mock_level_last.as_of_date = datetime.date(2024, 6, 1)

        mock_level_now = MagicMock()
        mock_level_now.level = Decimal("5500")
        mock_level_now.as_of_date = datetime.date(2026, 3, 1)

        mock_index_source = MagicMock()
        mock_index_source.get_level.side_effect = [mock_level_last, mock_level_now]
        mock_index_source.source_label = "Mock Index"
        mock_index_source.dataset_version = "mock_v1"

        context = MagicMock()
        context.index_source = mock_index_source

        methodology = LastRoundMarketAdjustedMethodology()
        result = methodology.valuate(request, context)
        return {
            "fair_value": float(result.estimated_fair_value.amount),
            "assumptions": result.assumptions,
            "derivation_steps": result.derivation_steps,
            "inputs_used": result.inputs_used,
        }

    def test_default_discount_applied(self) -> None:
        """Default 10% discount reduces fair value below market-adjusted value."""
        result = self._run_last_round()
        # Index grew 10%: $100B × 1.10 = $110B market-adjusted
        # Then 10% discount: $110B × 0.90 = $99B
        self.assertAlmostEqual(result["fair_value"], 99_000_000_000, delta=500_000_000)

    def test_zero_discount_gives_unadjusted_value(self) -> None:
        """With discount=0, result equals market-adjusted value (no discount)."""
        result_no_discount = self._run_last_round(discount_override=0)
        result_with_discount = self._run_last_round()
        self.assertGreater(result_no_discount["fair_value"], result_with_discount["fair_value"])

    def test_discount_in_assumptions(self) -> None:
        """Discount must appear in the assumptions list."""
        result = self._run_last_round()
        text = " ".join(result["assumptions"]).lower()
        self.assertIn("discount", text)

    def test_discount_in_derivation_steps(self) -> None:
        """Discount calculation must appear in derivation steps."""
        result = self._run_last_round()
        text = " ".join(result["derivation_steps"]).lower()
        self.assertIn("discount", text)

    def test_discount_in_inputs_used(self) -> None:
        """private_company_discount_pct must appear in inputs_used."""
        result = self._run_last_round()
        self.assertIn("private_company_discount_pct", result["inputs_used"])
        self.assertEqual(result["inputs_used"]["private_company_discount_pct"], 10.0)

    def test_custom_discount_override(self) -> None:
        """User-supplied discount overrides the default."""
        result_20 = self._run_last_round(discount_override=20)
        result_10 = self._run_last_round()
        # 20% discount → lower fair value than 10% discount
        self.assertLess(result_20["fair_value"], result_10["fair_value"])


if __name__ == "__main__":
    unittest.main()
