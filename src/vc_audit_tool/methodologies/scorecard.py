"""Payne Scorecard valuation methodology for pre-revenue startups.

Starts from a regional/sector median pre-money valuation and applies
multipliers for seven qualitative factors with fixed weights defined
by the methodology.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology

# Fixed factor weights per the Payne Scorecard method
_FACTOR_WEIGHTS: dict[str, Decimal] = {
    "strength_of_team": Decimal("0.30"),
    "size_of_opportunity": Decimal("0.25"),
    "product_technology": Decimal("0.15"),
    "competitive_environment": Decimal("0.10"),
    "marketing_sales_channels": Decimal("0.10"),
    "need_for_additional_investment": Decimal("0.05"),
    "other": Decimal("0.05"),
}


class ScorecardMethodology(ValuationMethodology):
    """Payne Scorecard method — pre-revenue startup valuation."""

    name = "scorecard"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs
        regional_median = parse_decimal(
            require_field(inputs, "regional_median_pre_money", (int, float, str)),
            "regional_median_pre_money",
        )

        factors_raw = require_field(inputs, "factors", dict)

        # Validate all required factor keys are present
        for key in _FACTOR_WEIGHTS:
            if key not in factors_raw:
                raise ValidationError(f"Missing required factor: '{key}'.")

        # Parse and validate factor scores
        factors: dict[str, Decimal] = {}
        for key in _FACTOR_WEIGHTS:
            val = factors_raw[key]
            if isinstance(val, bool):
                raise ValidationError(f"Factor '{key}' must be numeric (0.0-2.0), received bool.")
            score = Decimal(str(val))
            if score < Decimal("0") or score > Decimal("2"):
                raise ValidationError(
                    f"Factor '{key}' score {float(score)} is outside valid range [0.0, 2.0]."
                )
            factors[key] = score

        # Compute weighted sum
        weighted_sum = Decimal("0")
        derivation_steps: list[str] = []
        for key, weight in _FACTOR_WEIGHTS.items():
            score = factors[key]
            contribution = (score * weight).quantize(Decimal("0.0001"))
            weighted_sum += contribution
            derivation_steps.append(
                f"Factor '{key}': score={float(score):.2f} × weight={float(weight):.2f} "
                f"= contribution {float(contribution):.4f}."
            )

        weighted_sum = weighted_sum.quantize(Decimal("0.0001"))
        estimated_value = (regional_median * weighted_sum).quantize(Decimal("0.01"))

        derivation_steps.append(
            f"Weighted sum = {float(weighted_sum):.4f}. "
            f"Estimated fair value = {float(regional_median):,.2f} × {float(weighted_sum):.4f} "
            f"= {float(estimated_value):,.2f} USD."
        )

        assumptions = [
            f"Regional/sector median pre-money valuation: {float(regional_median):,.2f} USD.",
        ]
        for key, weight in _FACTOR_WEIGHTS.items():
            assumptions.append(
                f"Factor '{key}' weight: {float(weight):.0%} (fixed by Payne Scorecard method)."
            )

        citations = [
            Citation(
                label="Payne Scorecard Method",
                detail=(
                    "Qualitative factor-based pre-revenue startup valuation "
                    "per Bill Payne's angel valuation framework."
                ),
            )
        ]

        non_zero = sum(1 for s in factors.values() if s > 0)
        confidence_indicators: dict[str, Any] = {
            "data_source_type": "analyst_assessment",
            "factor_completeness": f"{non_zero}/{len(factors)} factors non-zero",
            "weighted_sum": float(weighted_sum),
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(estimated_value),
            assumptions=assumptions,
            inputs_used={
                "regional_median_pre_money": float(regional_median),
                "factors": {k: float(v) for k, v in factors.items()},
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
