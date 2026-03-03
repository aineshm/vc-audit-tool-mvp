"""Reconciler — weighted average, range derivation, divergence detection.

All arithmetic uses :class:`Decimal` — no float intermediates.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from vc_audit_tool.reconciliation.models import (
    ConcludedValue,
    MethodologyPlan,
    MethodologyWeight,
    ReconciliationSummary,
)

# Spread factors used to derive per-methodology uncertainty ranges
_SPREAD_FACTORS: dict[str, Decimal] = {
    "comparable_companies": Decimal("0.25"),
    "last_round_market_adjusted": Decimal("0.20"),
    "last_round_multiple_ratchet": Decimal("0.25"),
    "scorecard": Decimal("0.35"),
    "berkus": Decimal("0.35"),
    "direct_valuation": Decimal("0.30"),
}


class Reconciler:
    """Synthesise per-methodology results into a concluded value."""

    DIVERGENCE_THRESHOLD = Decimal("0.40")

    @staticmethod
    def reconcile(
        results: dict[str, dict[str, Any]],
        plan: MethodologyPlan,
        as_of_date: date | None = None,
    ) -> ReconciliationSummary:
        """Compute the weighted concluded value from methodology results."""

        applicable_weights = [
            w for w in plan.weights if w.data_requirements_met and w.methodology in results
        ]

        if not applicable_weights:
            raise ValueError(
                "No applicable methodologies: none of the selected methods "
                "had both data_requirements_met=True and a computed result."
            )

        # Extract point estimates
        point_estimates: dict[str, Decimal] = {}
        for w in applicable_weights:
            result = results[w.methodology]
            vr = result.get("valuation_result", {})
            efv = vr.get("estimated_fair_value", {})
            amount = efv.get("amount", 0)
            point_estimates[w.methodology] = Decimal(str(amount))

        # Compute weighted point estimate
        concluded_point = Decimal("0")
        for w in applicable_weights:
            pe = point_estimates.get(w.methodology, Decimal("0"))
            concluded_point += pe * w.weight
        concluded_point = concluded_point.quantize(Decimal("0.01"))

        # Compute per-method ranges and weighted range
        range_low = Decimal("0")
        range_high = Decimal("0")
        for w in applicable_weights:
            pe = point_estimates.get(w.methodology, Decimal("0"))
            spread = _SPREAD_FACTORS.get(w.methodology, Decimal("0.25"))
            method_low = pe * (Decimal("1") - spread)
            method_high = pe * (Decimal("1") + spread)
            range_low += method_low * w.weight
            range_high += method_high * w.weight
        range_low = range_low.quantize(Decimal("0.01"))
        range_high = range_high.quantize(Decimal("0.01"))

        # Ensure range_low < point < range_high
        if range_low > concluded_point:
            range_low = concluded_point
        if range_high < concluded_point:
            range_high = concluded_point

        # Divergence detection
        divergence_flag = False
        divergence_note: str | None = None
        estimates = list(point_estimates.values())
        if len(estimates) >= 2:
            max_val = max(estimates)
            min_val = min(estimates)
            midpoint = (max_val + min_val) / Decimal("2")
            if midpoint > 0:
                divergence = (max_val - min_val) / midpoint
                if divergence > Reconciler.DIVERGENCE_THRESHOLD:
                    divergence_flag = True
                    divergence_note = _generate_divergence_note(
                        point_estimates, applicable_weights, divergence
                    )

        # Reconciliation rationale
        rationale_parts: list[str] = []
        for w in applicable_weights:
            pe = point_estimates.get(w.methodology, Decimal("0"))
            rationale_parts.append(
                f"{w.methodology} ({float(w.weight):.0%} weight, ${float(pe):,.0f}): {w.rationale}"
            )
        reconciliation_rationale = "Concluded value applies " + "; ".join(rationale_parts) + "."

        aod = as_of_date or date.today()
        concluded_value = ConcludedValue(
            point_estimate=concluded_point,
            range_low=range_low,
            range_high=range_high,
            currency="USD",
            as_of_date=aod,
        )

        return ReconciliationSummary(
            concluded_value=concluded_value,
            methodology_weights=plan.weights,
            divergence_flag=divergence_flag,
            divergence_note=divergence_note,
            reconciliation_rationale=reconciliation_rationale,
            selector_version=plan.selector_version,
        )


def _generate_divergence_note(
    estimates: dict[str, Decimal],
    weights: list[MethodologyWeight],
    divergence: Decimal,
) -> str:
    """Produce a structured divergence explanation."""
    sorted_methods = sorted(estimates.items(), key=lambda kv: kv[1], reverse=True)
    highest_name, highest_val = sorted_methods[0]
    lowest_name, lowest_val = sorted_methods[-1]
    pct = float(divergence * 100)
    return (
        f"{highest_name} (${float(highest_val):,.0f}) and "
        f"{lowest_name} (${float(lowest_val):,.0f}) "
        f"diverge significantly (divergence: {pct:.0f}%). "
        f"Possible explanations: "
        f"(1) Material market re-rating since the last funding round; "
        f"(2) Liquidation preference terms may have inflated the headline round figure; "
        f"(3) The comparable company set may not fully reflect the target company's positioning. "
        f"Manual review recommended before concluding."
    )
