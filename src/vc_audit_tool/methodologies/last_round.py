"""Last-round market-adjusted valuation model."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vc_audit_tool.data_sources import MARKET_INDEX_DATASET_VERSION
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_date, parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology


class LastRoundMarketAdjustedMethodology(ValuationMethodology):
    name = "last_round_market_adjusted"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs
        last_post_money = parse_decimal(
            require_field(inputs, "last_post_money_valuation", (int, float, str)),
            "last_post_money_valuation",
        )
        last_round_date = parse_date(require_field(inputs, "last_round_date", str))
        public_index = inputs.get("public_index", "NASDAQ_COMPOSITE")

        last_round_level = context.index_source.get_level(public_index, last_round_date)
        as_of_level = context.index_source.get_level(public_index, request.as_of_date)
        pct_change = (as_of_level.level / last_round_level.level) - Decimal("1")
        multiplier = Decimal("1") + pct_change
        adjusted_value = (last_post_money * multiplier).quantize(Decimal("0.01"))

        assumptions = [
            f"Method assumes valuation moves proportionally with {public_index}.",
            f"Used index level on {last_round_level.as_of_date.isoformat()} "
            f"for last round and {as_of_level.as_of_date.isoformat()} for as-of date.",
        ]
        derivation_steps = [
            f"Start with last post-money valuation: {float(last_post_money):,.2f} USD.",
            f"Compute index change: ({as_of_level.level} / {last_round_level.level}) - 1 "
            f"= {float(pct_change * Decimal('100')):.4f}%.",
            f"Compute adjustment multiplier: 1 + {float(pct_change):.6f} "
            f"= {float(multiplier):.6f}.",
            f"Apply multiplier to last valuation: {float(last_post_money):,.2f} * "
            f"{float(multiplier):.6f} = {float(adjusted_value):,.2f} USD.",
        ]
        citations = [
            Citation(
                label="Mock market index dataset",
                detail=(
                    "In-memory monthly index levels for NASDAQ Composite and Russell 2000 "
                    "(vc_audit_tool.data_sources.MockMarketIndexSource)."
                ),
                dataset_version=MARKET_INDEX_DATASET_VERSION,
                resolved_data_points=(
                    f"{public_index}@{last_round_level.as_of_date.isoformat()}"
                    f"={last_round_level.level}",
                    f"{public_index}@{as_of_level.as_of_date.isoformat()}={as_of_level.level}",
                ),
            )
        ]

        # ── Confidence / risk indicators ──
        days_since_round = (request.as_of_date - last_round_date).days
        index_data_gap_days = (request.as_of_date - as_of_level.as_of_date).days
        abs_pct = abs(float(pct_change * Decimal("100")))

        if days_since_round > 365:
            staleness_risk = "HIGH – last round >12 months ago"
        elif days_since_round > 180:
            staleness_risk = "MEDIUM – last round >6 months ago"
        else:
            staleness_risk = "LOW"

        confidence_indicators: dict[str, Any] = {
            "days_since_last_round": days_since_round,
            "index_data_freshness_gap_days": index_data_gap_days,
            "absolute_index_change_pct": round(abs_pct, 4),
            "staleness_risk": staleness_risk,
            "data_source_type": "mock",
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(adjusted_value),
            assumptions=assumptions,
            inputs_used={
                "last_post_money_valuation": float(last_post_money),
                "last_round_date": last_round_date.isoformat(),
                "public_index": public_index,
                "index_level_last_round": float(last_round_level.level),
                "index_level_as_of_date": float(as_of_level.level),
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
