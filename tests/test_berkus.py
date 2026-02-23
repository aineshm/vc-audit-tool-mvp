"""Tests for the Berkus methodology."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import ValuationRequest


def _all_true() -> dict[str, bool]:
    return {
        "sound_idea": True,
        "prototype": True,
        "quality_management": True,
        "strategic_relationships": True,
        "product_rollout": True,
    }


class TestBerkusHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()
        self.as_of = date(2026, 2, 22)

    def test_all_true(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": _all_true(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(
            result.estimated_fair_value.amount,
            Decimal("2000000.00"),
        )

    def test_all_false(self) -> None:
        factors = {k: False for k in _all_true()}
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("0.00"))

    def test_partial_float_factors(self) -> None:
        factors: dict[str, bool | float] = {
            "sound_idea": 0.75,
            "prototype": 0.5,
            "quality_management": False,
            "strategic_relationships": True,
            "product_rollout": 0.25,
        }
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # per_factor_max = 400_000
        # 0.75*400k + 0.5*400k + 0*400k + 1.0*400k + 0.25*400k
        # = 300000 + 200000 + 0 + 400000 + 100000 = 1000000
        self.assertEqual(
            result.estimated_fair_value.amount,
            Decimal("1000000.00"),
        )

    def test_mixed_bool_and_float(self) -> None:
        factors: dict[str, bool | float] = {
            "sound_idea": True,
            "prototype": 0.5,
            "quality_management": False,
            "strategic_relationships": True,
            "product_rollout": False,
        }
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 1_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # per_factor = 200_000
        # 200k + 100k + 0 + 200k + 0 = 500_000
        self.assertEqual(
            result.estimated_fair_value.amount,
            Decimal("500000.00"),
        )

    def test_factor_completeness_indicator(self) -> None:
        factors: dict[str, bool | float] = {
            "sound_idea": True,
            "prototype": True,
            "quality_management": False,
            "strategic_relationships": True,
            "product_rollout": False,
        }
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(
            result.confidence_indicators["factor_completeness"],
            "3/5 factors present",
        )

    def test_zero_max_valuation(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 0,
                "factors": _all_true(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("0.00"))

    def test_derivation_steps_count(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": _all_true(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        # 5 factor steps + 1 summary = 6
        self.assertEqual(len(result.derivation_steps), 6)


class TestBerkusValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ValuationEngine.mock()
        self.as_of = date(2026, 2, 22)

    def test_missing_factor(self) -> None:
        factors = _all_true()
        del factors["sound_idea"]
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.engine.evaluate(req)
        self.assertIn("sound_idea", str(ctx.exception))

    def test_float_out_of_range(self) -> None:
        factors: dict[str, bool | float] = {
            "sound_idea": 1.5,
            "prototype": True,
            "quality_management": True,
            "strategic_relationships": True,
            "product_rollout": True,
        }
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 2_000_000,
                "factors": factors,
            },
            as_of_date=self.as_of,
        )
        with self.assertRaises(ValidationError):
            self.engine.evaluate(req)

    def test_legacy_factor_aliases_are_accepted(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 1_000_000,
                "factors": {
                    "sound_idea": True,
                    "working_prototype": True,
                    "quality_management": True,
                    "strategic_relationships": True,
                    "product_rollout_or_sales": True,
                },
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("1000000.00"))
        self.assertIn("prototype", result.inputs_used["factors"])
        self.assertIn("product_rollout", result.inputs_used["factors"])

    def test_berkus_factors_key_is_accepted(self) -> None:
        req = ValuationRequest(
            company_name="TestCo",
            methodology="berkus",
            inputs={
                "max_pre_money_valuation": 1_000_000,
                "berkus_factors": _all_true(),
            },
            as_of_date=self.as_of,
        )
        result = self.engine.evaluate(req)
        self.assertEqual(result.estimated_fair_value.amount, Decimal("1000000.00"))


if __name__ == "__main__":
    unittest.main()
