"""Comparable-company valuation model."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology


class ComparableCompaniesMethodology(ValuationMethodology):
    name = "comparable_companies"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs
        revenue = parse_decimal(
            require_field(inputs, "revenue_ltm", (int, float, str)), "revenue_ltm"
        )
        sector = require_field(inputs, "sector", str)
        statistic = inputs.get("statistic", "median")
        if statistic not in {"median", "mean"}:
            raise ValidationError("Field 'statistic' must be either 'median' or 'mean'.")
        private_discount_pct = parse_decimal(
            inputs.get("private_company_discount_pct", 0),
            "private_company_discount_pct",
        )
        if private_discount_pct > Decimal("100"):
            raise ValidationError("Field 'private_company_discount_pct' cannot exceed 100.")

        tickers = inputs.get("peer_tickers")
        if tickers:
            if not isinstance(tickers, list):
                raise ValidationError("Field 'peer_tickers' must be a list of ticker symbols.")
            comps = context.comps_source.list_by_tickers(tickers)
            peer_group_descriptor = f"explicit peer list ({', '.join([c.ticker for c in comps])})"
        else:
            comps = context.comps_source.list_by_sector(sector)
            peer_group_descriptor = f"sector peer set '{sector}'"

        selected_multiple = context.comps_source.aggregate_multiple(comps, statistic)
        gross_value = revenue * selected_multiple
        discount_multiplier = (Decimal("100") - private_discount_pct) / Decimal("100")
        adjusted_value = (gross_value * discount_multiplier).quantize(Decimal("0.01"))

        assumptions = [
            f"Comparable universe based on {peer_group_descriptor}.",
            f"Applied {statistic} EV/Revenue multiple of {selected_multiple:.2f}x.",
            f"Applied private-company discount of {private_discount_pct:.2f}%.",
        ]
        derivation_steps = [
            f"Select peer multiple ({statistic}): {selected_multiple:.2f}x.",
            f"Apply multiple to LTM revenue: {float(revenue):,.2f} * "
            f"{selected_multiple:.2f} = {float(gross_value):,.2f} USD.",
            f"Compute discount multiplier: (100 - {float(private_discount_pct):.2f}) / 100 "
            f"= {float(discount_multiplier):.4f}.",
            f"Apply private-company discount: {float(gross_value):,.2f} * "
            f"{float(discount_multiplier):.4f} = {float(adjusted_value):,.2f} USD.",
        ]
        citations = [
            Citation(
                label="Mock public comp dataset",
                detail=(
                    "In-memory EV/Revenue multiples by ticker and sector "
                    "(vc_audit_tool.data_sources.MockComparableCompanySource)."
                ),
            )
        ]

        # ── Confidence / risk indicators ──
        peer_count = len(comps)
        multiples = [float(c.ev_to_revenue) for c in comps]
        spread = max(multiples) - min(multiples) if multiples else 0.0

        if peer_count < 3:
            peer_set_quality = "LOW – fewer than 3 comparable companies"
        elif peer_count < 5:
            peer_set_quality = "MEDIUM – 3-4 comparable companies"
        else:
            peer_set_quality = "HIGH – 5+ comparable companies"

        confidence_indicators: dict[str, Any] = {
            "peer_count": peer_count,
            "multiple_spread": round(spread, 2),
            "peer_set_quality": peer_set_quality,
            "data_source_type": "mock",
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(adjusted_value),
            assumptions=assumptions,
            inputs_used={
                "revenue_ltm": float(revenue),
                "sector": sector,
                "statistic": statistic,
                "peer_companies": [
                    {
                        "ticker": comp.ticker,
                        "company_name": comp.company_name,
                        "ev_to_revenue": float(comp.ev_to_revenue),
                    }
                    for comp in comps
                ],
                "private_company_discount_pct": float(private_discount_pct),
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
