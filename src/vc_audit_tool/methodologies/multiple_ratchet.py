"""Last-round multiple-ratchet valuation model.

Adjusts a prior-round valuation by comparing the *implied revenue multiple*
at the last round against the *current market multiple* for the same
sector, then applies the company's actual revenue performance.

This methodology captures **multiple compression** (or expansion) and
**company-specific performance** — two forces that a broad market-index
approach cannot distinguish.

Derivation:
    implied_multiple_at_last_round  = last_post_money / revenue_at_last_round
    current_market_multiple         = median(peer EV/Revenue)
    multiple_ratchet                = current_market_multiple / implied_multiple_at_last_round
    rerated_value                   = current_revenue × current_market_multiple
    discounted_value                = rerated_value × (1 - private_company_discount_pct / 100)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology


class LastRoundMultipleRatchetMethodology(ValuationMethodology):
    """Re-rate a prior-round valuation using current sector multiples and actual revenue."""

    name = "last_round_multiple_ratchet"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs

        # ── Required inputs ──
        last_post_money = parse_decimal(
            require_field(inputs, "last_post_money_valuation", (int, float, str)),
            "last_post_money_valuation",
        )
        revenue_at_last_round = parse_decimal(
            require_field(inputs, "revenue_at_last_round", (int, float, str)),
            "revenue_at_last_round",
        )
        current_revenue = parse_decimal(
            require_field(inputs, "current_revenue", (int, float, str)),
            "current_revenue",
        )
        sector = require_field(inputs, "sector", str)

        # ── Optional inputs ──
        statistic = inputs.get("statistic", "median")
        if statistic not in {"median", "mean"}:
            raise ValidationError("Field 'statistic' must be either 'median' or 'mean'.")
        private_discount_pct = parse_decimal(
            inputs.get("private_company_discount_pct", 0),
            "private_company_discount_pct",
        )
        target_description_raw = inputs.get("target_description")
        if target_description_raw is not None and not isinstance(target_description_raw, str):
            raise ValidationError("Field 'target_description' must be a string when provided.")
        target_description = target_description_raw.strip() if target_description_raw else None
        if private_discount_pct > Decimal("100"):
            raise ValidationError("Field 'private_company_discount_pct' cannot exceed 100.")

        # Guard against non-positive revenue
        if revenue_at_last_round <= 0:
            raise ValidationError("Field 'revenue_at_last_round' must be positive.")
        if current_revenue <= 0:
            raise ValidationError("Field 'current_revenue' must be positive.")

        # ── Step 1: Implied multiple at last round ──
        implied_multiple = (last_post_money / revenue_at_last_round).quantize(Decimal("0.01"))

        # ── Step 2: Current market multiple from comps ──
        src = context.comps_source
        tickers = inputs.get("peer_tickers")
        if tickers:
            if not isinstance(tickers, list):
                raise ValidationError("Field 'peer_tickers' must be a list of ticker symbols.")
            comps = src.list_by_tickers(tickers)
            peer_group_descriptor = f"explicit peer list ({', '.join([c.ticker for c in comps])})"
        else:
            comps = src.list_by_sector(sector, target_description=target_description)
            peer_group_descriptor = f"sector peer set '{sector}'"

        current_market_multiple = src.aggregate_multiple(comps, statistic)

        # ── Step 3: Multiple ratchet ──
        multiple_ratchet = (current_market_multiple / implied_multiple).quantize(Decimal("0.0001"))

        # ── Step 4: Revenue growth ──
        revenue_growth = ((current_revenue / revenue_at_last_round) - Decimal("1")) * Decimal("100")
        revenue_growth = revenue_growth.quantize(Decimal("0.01"))

        # ── Step 5: Re-rated value = current_revenue × current_market_multiple ──
        rerated_value = (current_revenue * current_market_multiple).quantize(Decimal("0.01"))

        # ── Step 6: Apply private-company discount ──
        discount_multiplier = (Decimal("100") - private_discount_pct) / Decimal("100")
        final_value = (rerated_value * discount_multiplier).quantize(Decimal("0.01"))

        # ── Citation metadata ──
        ds_label: str = getattr(src, "source_label", "Comparable company dataset")
        ds_version: str = getattr(src, "dataset_version", "")
        data_source_type: str = "mock" if "mock" in ds_version else "live"

        # ── Build audit trail ──
        assumptions = [
            f"Last-round implied revenue multiple: {implied_multiple:.2f}x "
            f"({float(last_post_money):,.0f} / {float(revenue_at_last_round):,.0f}).",
            f"Current {statistic} market multiple from {peer_group_descriptor}: "
            f"{current_market_multiple:.2f}x.",
            f"Multiple ratchet (current / implied): {float(multiple_ratchet):.4f} "
            f"({'compression' if multiple_ratchet < 1 else 'expansion'}: "
            f"{float((Decimal('1') - multiple_ratchet) * Decimal('100')):.1f}%).",
            f"Company revenue grew {float(revenue_growth):.1f}% "
            f"({float(revenue_at_last_round):,.0f} → {float(current_revenue):,.0f}).",
            f"Applied private-company discount of {private_discount_pct:.2f}%.",
        ]

        derivation_steps = [
            f"Step 1: Implied multiple at last round = "
            f"{float(last_post_money):,.2f} / {float(revenue_at_last_round):,.2f} "
            f"= {implied_multiple:.2f}x.",
            f"Step 2: Current market {statistic} EV/Revenue multiple = "
            f"{current_market_multiple:.2f}x.",
            f"Step 3: Multiple ratchet = {current_market_multiple:.2f} / "
            f"{implied_multiple:.2f} = {float(multiple_ratchet):.4f} "
            f"({'↓' if multiple_ratchet < 1 else '↑'} "
            f"{abs(float((Decimal('1') - multiple_ratchet) * Decimal('100'))):.1f}%).",
            f"Step 4: Revenue performance = {float(current_revenue):,.2f} "
            f"(+{float(revenue_growth):.1f}% vs last round).",
            f"Step 5: Re-rated value = current revenue × market multiple = "
            f"{float(current_revenue):,.2f} × {current_market_multiple:.2f} "
            f"= {float(rerated_value):,.2f} USD.",
            f"Step 6: Discount multiplier = (100 - {float(private_discount_pct):.2f}) / 100 "
            f"= {float(discount_multiplier):.4f}.",
            f"Step 7: Final value = {float(rerated_value):,.2f} × {float(discount_multiplier):.4f} "
            f"= {float(final_value):,.2f} USD.",
        ]

        citations = [
            Citation(
                label=ds_label,
                detail=(
                    f"EV/Revenue multiples for {peer_group_descriptor} "
                    f"(source: {ds_label}, version: {ds_version})."
                ),
                dataset_version=ds_version,
                resolved_data_points=tuple(f"{c.ticker}:ev_rev={c.ev_to_revenue}" for c in comps),
            )
        ]

        # ── Confidence indicators ──
        peer_count = len(comps)
        multiples = [float(c.ev_to_revenue) for c in comps]
        spread = max(multiples) - min(multiples) if multiples else 0.0

        if peer_count < 3:
            peer_set_quality = "LOW – fewer than 3 comparable companies"
        elif peer_count < 5:
            peer_set_quality = "MEDIUM – 3-4 comparable companies"
        else:
            peer_set_quality = "HIGH – 5+ comparable companies"

        ratchet_float = float(multiple_ratchet)
        if ratchet_float < 0.5:
            ratchet_severity = "SEVERE – multiples compressed >50%"
        elif ratchet_float < 0.75:
            ratchet_severity = "HIGH – multiples compressed 25-50%"
        elif ratchet_float < 0.9:
            ratchet_severity = "MODERATE – multiples compressed 10-25%"
        elif ratchet_float <= 1.1:
            ratchet_severity = "STABLE – multiples roughly unchanged"
        else:
            ratchet_severity = "EXPANSION – multiples increased"

        confidence_indicators: dict[str, Any] = {
            "peer_count": peer_count,
            "multiple_spread": round(spread, 2),
            "peer_set_quality": peer_set_quality,
            "implied_multiple_at_last_round": float(implied_multiple),
            "current_market_multiple": float(current_market_multiple),
            "multiple_ratchet": ratchet_float,
            "ratchet_severity": ratchet_severity,
            "revenue_growth_pct": float(revenue_growth),
            "data_source_type": data_source_type,
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(final_value),
            assumptions=assumptions,
            inputs_used={
                "last_post_money_valuation": float(last_post_money),
                "revenue_at_last_round": float(revenue_at_last_round),
                "current_revenue": float(current_revenue),
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
                "implied_multiple_at_last_round": float(implied_multiple),
                "current_market_multiple": float(current_market_multiple),
                "private_company_discount_pct": float(private_discount_pct),
                "target_description": target_description,
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
