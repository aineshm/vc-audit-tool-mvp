"""Tests for the Scorecard methodology."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import ValuationRequest


def _all_ones() -> dict[str, float]:
    return {
        "strength_of_team": 1.0,
        "size_of_opportunity": 1.0,
        "product_technology": 1.0,
        "competitive_environment": 1.0,
        "marketing_sales_channels": 1.0,
        "need_for_additional_investment": 1.0,
        "other": 1.0,
    }


class TestScorecardHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ValuationEngine()
        self.as_of = date(2026, 2, 22)

    def test_all_factors_one(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": _all_ones(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # All factors = 1.0, sum of (score*weight) = 1.0
        self.assertEqual(
            result.estimated_fair_value.amount,
            Decimal("5000000.00"),
        )

    def test_all_factors_zero(self) -> None:
        factors = {k: 0.0 for k in _all_ones()}
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("0.00"))

    def test_partial_scores(self) -> None:
        factors = _all_ones()
        factors["strength_of_team"] = 1.5  # 30% weight
        factors["size_of_opportunity"] = 0.5  # 25% weight
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # weighted sum = 1.5*0.30 + 0.5*0.25 + 1*0.15 + 1*0.10 + 1*0.10 + 1*0.05 + 1*0.05
        #              = 0.45 + 0.125 + 0.15 + 0.10 + 0.10 + 0.05 + 0.05 = 1.025
        expected = Decimal("5000000") * Decimal("1.025")
        self.assertEqual(
            result.estimated_fair_value.amount,
            expected.quantize(Decimal("0.01")),
        )

    def test_derivation_steps_count(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": _all_ones(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # 7 factor steps + 1 summary step = 8
        self.assertEqual(len(result.derivation_steps), 8)

    def test_confidence_indicators(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": _all_ones(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(
            result.confidence_indicators["data_source_type"],
            "analyst_assessment",
        )
        self.assertIn("factor_completeness", result.confidence_indicators)

    def test_zero_regional_median(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 0,
                "factors": _all_ones(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("0.00"))

    def test_assumptions_list_factor_weights(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": _all_ones(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # Should have 1 regional median + 7 factor weight assumptions
        self.assertEqual(len(result.assumptions), 8)


class TestScorecardValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ValuationEngine()
        self.as_of = date(2026, 2, 22)

    def test_missing_factor_key(self) -> None:
        factors = _all_ones()
        del factors["other"]
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.engine.evaluate(req)
        self.assertIn("other", str(ctx.exception))

    def test_factor_above_range(self) -> None:
        factors = _all_ones()
        factors["strength_of_team"] = 2.5
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={
                "regional_median_pre_money": 5_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.engine.evaluate(req)
        self.assertIn("outside valid range", str(ctx.exception))

    def test_missing_factors_dict(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={"regional_median_pre_money": 5_000_000},
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError):
            self.engine.evaluate(req)

    def test_missing_regional_median(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="scorecard",
            inputs={"factors": _all_ones()},
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError):
            self.engine.evaluate(req)


if __name__ == "__main__":
    unittest.main()
