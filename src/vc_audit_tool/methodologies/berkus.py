"""Berkus valuation methodology for pre-revenue startups.

Assigns up to a configurable maximum value across five risk-mitigating
factors.  Each factor contributes up to ``max_pre_money_valuation / 5``.
Boolean ``True`` = full value, ``False`` = zero, float [0.0, 1.0] = partial.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology

_BERKUS_FACTORS: tuple[str, ...] = (
    "sound_idea",
    "working_prototype",
    "quality_management",
    "strategic_relationships",
    "product_rollout_or_sales",
)


class BerkusMethodology(ValuationMethodology):
    """Berkus method — pre-revenue startup valuation."""

    name = "berkus"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs
        max_val = parse_decimal(
            require_field(inputs, "max_pre_money_valuation", (int, float, str)),
            "max_pre_money_valuation",
        )

        factors_raw = require_field(inputs, "factors", dict)

        # Validate all required factor keys
        for key in _BERKUS_FACTORS:
            if key not in factors_raw:
                raise ValidationError(f"Missing required factor: '{key}'.")

        per_factor_max = (max_val / Decimal("5")).quantize(Decimal("0.01"))

        total = Decimal("0")
        derivation_steps: list[str] = []
        non_zero_count = 0
        for key in _BERKUS_FACTORS:
            raw = factors_raw[key]
            if isinstance(raw, bool):
                multiplier = Decimal("1") if raw else Decimal("0")
            elif isinstance(raw, (int, float)):
                multiplier = Decimal(str(raw))
                if multiplier < Decimal("0") or multiplier > Decimal("1"):
                    raise ValidationError(
                        f"Factor '{key}' must be bool or float in [0.0, 1.0], got {raw}."
                    )
            else:
                raise ValidationError(
                    f"Factor '{key}' must be bool or float, received {type(raw).__name__}."
                )

            contribution = (per_factor_max * multiplier).quantize(Decimal("0.01"))
            total += contribution
            if contribution > 0:
                non_zero_count += 1

            derivation_steps.append(
                f"Factor '{key}': value={raw} → multiplier={float(multiplier):.2f} "
                f"× per-factor max {float(per_factor_max):,.2f} = {float(contribution):,.2f} USD."
            )

        # Cap at max_pre_money_valuation
        estimated_value = min(total, max_val).quantize(Decimal("0.01"))
        derivation_steps.append(
            f"Sum of contributions = {float(total):,.2f} USD "
            f"(capped at max {float(max_val):,.2f}). "
            f"Estimated fair value = {float(estimated_value):,.2f} USD."
        )

        assumptions = [
            f"Maximum pre-money valuation: {float(max_val):,.2f} USD.",
            f"Equal split: each of 5 factors contributes up to {float(per_factor_max):,.2f} USD.",
            "Factor values: True=full, False=zero, float [0,1]=partial.",
        ]

        citations = [
            Citation(
                label="Berkus Method",
                detail=(
                    "Risk-factor-based pre-revenue startup valuation "
                    "per Dave Berkus's angel investment framework."
                ),
            )
        ]

        confidence_indicators: dict[str, Any] = {
            "data_source_type": "analyst_assessment",
            "factor_completeness": f"{non_zero_count}/{len(_BERKUS_FACTORS)} factors present",
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(estimated_value),
            assumptions=assumptions,
            inputs_used={
                "max_pre_money_valuation": float(max_val),
                "factors": {k: factors_raw[k] for k in _BERKUS_FACTORS},
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
