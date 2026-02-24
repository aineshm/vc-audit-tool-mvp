"""Direct Valuation methodology.

Used when the web research agent finds strong direct evidence of a company's
valuation — e.g. secondary-market trades, recent press-confirmed rounds, or
analyst consensus that multiple sources agree on.

Instead of computing a valuation from scratch (comps, multiples, etc.), this
methodology takes the evidence package and produces a concluded value with:
  - A weighted-average point estimate (weighted by evidence confidence)
  - A range derived from evidence spread (or ±15% if only one signal)
  - An audit trail citing each evidence source

This is intentionally the *highest-priority* methodology when evidence
quality is STRONG or MODERATE. It should not be used when only a single
low-confidence signal exists.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationRequest, ValuationResult
from vc_audit_tool.validation import parse_decimal, require_field

from .base import MethodologyContext, ValuationMethodology


class DirectValuationMethodology(ValuationMethodology):
    """Synthesise a valuation directly from web-sourced evidence signals.

    Required inputs
    ---------------
    evidence_signals : list[dict]
        Each dict must have: ``amount_usd`` (float), ``confidence`` (float 0-1),
        ``evidence_type`` (str), ``source_snippet`` (str).
        Optional: ``date_mentioned``, ``source_title``.

    Optional inputs
    ---------------
    consensus_strength : str
        ``"STRONG"`` | ``"MODERATE"`` | ``"WEAK"`` — used for confidence
        indicator output.
    private_company_discount_pct : float
        Illiquidity discount (0-50%). Defaults to 15% for companies with
        direct secondary-market evidence (they already have price discovery).
    """

    name = "direct_valuation"

    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        inputs = request.inputs
        signals_raw = require_field(inputs, "evidence_signals", list)

        if not signals_raw:
            raise ValidationError("Field 'evidence_signals' must be a non-empty list.")

        # Parse evidence signals
        signals: list[dict[str, Any]] = []
        for raw in signals_raw:
            if not isinstance(raw, dict):
                raise ValidationError("Each evidence_signal must be a dict.")
            amount = float(raw.get("amount_usd", 0))
            confidence = float(raw.get("confidence", 0.5))
            if amount <= 0:
                continue
            if not (0 < confidence <= 1.0):
                confidence = 0.5
            signals.append({**raw, "amount_usd": amount, "confidence": confidence})

        if not signals:
            raise ValidationError("No valid evidence_signals with positive amounts.")

        # Weighted average point estimate
        total_weight = sum(s["confidence"] for s in signals)
        point_estimate = Decimal(
            str(sum(s["amount_usd"] * s["confidence"] for s in signals) / total_weight)
        ).quantize(Decimal("1"))

        # Range
        amounts = [Decimal(str(s["amount_usd"])) for s in signals]
        if len(amounts) >= 2:
            range_low = min(amounts)
            range_high = max(amounts)
        else:
            range_low = point_estimate * Decimal("0.85")
            range_high = point_estimate * Decimal("1.15")

        # Private-company discount (lower when secondary-market evidence exists)
        has_secondary = any(
            s.get("evidence_type") in ("secondary_market", "post_money_fresh")
            for s in signals
        )
        default_discount = Decimal("10") if has_secondary else Decimal("20")
        private_discount_pct = parse_decimal(
            inputs.get("private_company_discount_pct", default_discount),
            "private_company_discount_pct",
        )
        if private_discount_pct > Decimal("50"):
            raise ValidationError(
                "Field 'private_company_discount_pct' cannot exceed 50 for direct_valuation."
            )

        discount_multiplier = (Decimal("100") - private_discount_pct) / Decimal("100")
        adjusted_estimate = (point_estimate * discount_multiplier).quantize(Decimal("1"))
        adjusted_low = (range_low * discount_multiplier).quantize(Decimal("1"))
        adjusted_high = (range_high * discount_multiplier).quantize(Decimal("1"))

        consensus_strength = inputs.get("consensus_strength", "WEAK")
        discount_reason = (
            "secondary market pricing observed - lower discount applied"
            if has_secondary
            else "standard private-company discount"
        )

        # Audit trail
        assumptions = [
            f"Point estimate is a confidence-weighted average of {len(signals)} "
            f"web-sourced valuation signal(s).",
            f"Evidence consensus strength: {consensus_strength}.",
            f"Applied illiquidity discount of {float(private_discount_pct):.1f}% "
            f"({discount_reason}).",
        ]

        derivation_steps = []
        for i, s in enumerate(signals, 1):
            ev_type = s.get("evidence_type", "unknown")
            title = s.get("source_title", "web source")
            date_s = s.get("date_mentioned", "date unknown")
            derivation_steps.append(
                f"Signal {i}: ${s['amount_usd'] / 1e9:.2f}B "
                f"(type={ev_type}, confidence={s['confidence']:.2f}, "
                f"source='{title}', date={date_s})"
            )
        derivation_steps.append(
            f"Weighted-average gross estimate: "
            f"${float(point_estimate) / 1e9:.3f}B"
        )
        derivation_steps.append(
            f"Apply {float(private_discount_pct):.1f}% illiquidity discount "
            f"→ ${float(adjusted_estimate) / 1e9:.3f}B"
        )
        derivation_steps.append(
            f"Indicated range (pre-discount signals): "
            f"${float(range_low) / 1e9:.2f}B – ${float(range_high) / 1e9:.2f}B"
        )

        # Citations
        citations = []
        for s in signals:
            title = s.get("source_title") or "Web search result"
            snippet = s.get("source_snippet", "")[:200]
            ev_type = s.get("evidence_type", "unknown")
            date_s = s.get("date_mentioned", "")
            citations.append(
                Citation(
                    label=title,
                    detail=f"[{ev_type}] {snippet}",
                    dataset_version=f"web-evidence-{date_s}" if date_s else "web-evidence",
                    resolved_data_points=(
                        f"${s['amount_usd'] / 1e9:.2f}B @ confidence={s['confidence']:.2f}",
                    ),
                )
            )

        # Confidence indicators
        stdev_note = ""
        if len(amounts) >= 2:
            stdev_val = statistics.stdev([float(a) for a in amounts])
            cv = stdev_val / float(point_estimate) if float(point_estimate) > 0 else 0
            stdev_note = f"{cv * 100:.1f}% CV across signals"

        confidence_indicators: dict[str, Any] = {
            "evidence_count": len(signals),
            "consensus_strength": consensus_strength,
            "evidence_spread": stdev_note or "single signal",
            "has_secondary_market_evidence": has_secondary,
            "data_source_type": "web_evidence",
            "methodology_note": (
                "Direct evidence synthesis — bypasses comp selection. "
                "Use STRONG consensus results with high confidence; "
                "WEAK consensus should be cross-checked with traditional methods."
            ),
        }

        return ValuationResult(
            company_name=request.company_name,
            methodology=self.name,
            as_of_date=request.as_of_date,
            estimated_fair_value=MonetaryAmount(adjusted_estimate),
            assumptions=assumptions,
            inputs_used={
                "evidence_signals": [
                    {
                        "amount_usd": s["amount_usd"],
                        "evidence_type": s.get("evidence_type"),
                        "confidence": s["confidence"],
                        "date_mentioned": s.get("date_mentioned"),
                    }
                    for s in signals
                ],
                "private_company_discount_pct": float(private_discount_pct),
                "consensus_strength": consensus_strength,
                "indicated_range_low": float(adjusted_low),
                "indicated_range_high": float(adjusted_high),
            },
            citations=citations,
            derivation_steps=derivation_steps,
            confidence_indicators=confidence_indicators,
        )
