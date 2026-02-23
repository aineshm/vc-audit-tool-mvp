"""MethodologySelector — rules engine for methodology weighting.

Loads a versioned YAML config and applies deterministic rules to
produce a :class:`MethodologyPlan` for any :class:`CompanyProfile`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from vc_audit_tool.exceptions import DataSourceError
from vc_audit_tool.reconciliation.models import (
    CompanyProfile,
    DataPackage,
    MethodologyPlan,
    MethodologyWeight,
)

logger = logging.getLogger(__name__)

_LAST_ROUND_METHODS = frozenset({"last_round_market_adjusted", "last_round_multiple_ratchet"})


class MethodologySelector:
    """Select and weight methodologies from a versioned rules config."""

    def __init__(self, rules_path: Path | None = None) -> None:
        if rules_path is None:
            rules_path = (
                Path(__file__).resolve().parents[3] / "config" / "methodology_rules_v1.yaml"
            )
        self._rules = self._load_rules(rules_path)
        self._version: str = self._rules["version"]

    # ── public API ─────────────────────────────────────────────────────

    def select(
        self,
        profile: CompanyProfile,
        data_package: DataPackage,
    ) -> MethodologyPlan:
        """Return a :class:`MethodologyPlan` for the given profile."""
        stage = profile.stage

        # Step 1: Load base weights for stage
        base = self._rules["base_weights"].get(stage, {})
        weights: dict[str, Decimal] = {k: Decimal(str(v)) for k, v in base.items()}

        # Step 2: Apply stage-level exclusions
        exclusions = self._rules.get("stage_exclusions", {}).get(stage, {})
        for method in exclusions.get("exclude", []):
            weights.pop(method, None)
        for method in exclusions.get("require", []):
            if method not in weights:
                # Add required methods with equal weight if not already present
                weights[method] = Decimal("0.50")

        # Step 3: Apply data-availability rules
        rationales: dict[str, str] = {}
        excluded_by_data: set[str] = set()

        # 3a: has_revenue rule
        if not profile.has_revenue:
            for rule in self._rules.get("data_rules", {}).get("has_revenue", []):
                if rule.get("condition") is False:
                    for m in rule.get("exclude", []):
                        if m in weights:
                            excluded_by_data.add(m)
                            rationales[m] = rule.get("rationale", "excluded by has_revenue rule")

        # 3b: last_round_age_months rules
        round_age = profile.last_round_age_months
        if round_age is not None:
            for rule in self._rules.get("data_rules", {}).get("last_round_age_months", []):
                cond = rule["condition"]
                if _matches_condition(round_age, cond):
                    if rule.get("exclude"):
                        for m in _LAST_ROUND_METHODS:
                            if m in weights:
                                excluded_by_data.add(m)
                                rationales[m] = rule.get("rationale", "excluded by round age rule")
                    else:
                        modifier = rule.get("weight_modifier", "MEDIUM")
                        for m in _LAST_ROUND_METHODS:
                            if m in weights:
                                rationale = rule.get(
                                    "rationale",
                                    f"round age modifier: {modifier}",
                                )
                                rationales[m] = rationale
                                weights[m] = _apply_modifier(weights[m], modifier, stage)
                    break  # first matching condition wins

        # 3c: peer_set_quality rules
        psq = data_package.peer_set_quality
        if psq is not None:
            for rule in self._rules.get("data_rules", {}).get("peer_set_quality", []):
                if rule["condition"] == psq:
                    modifier = rule.get("weight_modifier", "MEDIUM")
                    comps_methods = {"comparable_companies"}
                    for m in comps_methods:
                        if m in weights:
                            rationale = rule.get(
                                "rationale",
                                f"peer quality modifier: {modifier}",
                            )
                            rationales[m] = rationale
                            weights[m] = _apply_modifier(weights[m], modifier, stage)
                    break

        # Remove data-excluded methods
        for m in excluded_by_data:
            weights.pop(m, None)

        # Step 4: Check data requirements
        met_flags: dict[str, bool] = {}
        for method in list(weights.keys()):
            met_flags[method] = _check_data_requirements(method, data_package, profile)

        # Step 5: Normalise weights of applicable methods to sum to 1.0
        applicable = {m: w for m, w in weights.items() if met_flags.get(m, False)}
        if not applicable:
            raise DataSourceError(
                "No applicable methodologies — all methods were "
                "excluded or lack required data. "
                f"Profile: stage={profile.stage}, "
                f"has_revenue={profile.has_revenue}, "
                f"last_round_age_months={profile.last_round_age_months}"
            )

        total = sum(applicable.values())
        if total > 0:
            normalised = {m: (w / total).quantize(Decimal("0.0001")) for m, w in applicable.items()}
            # Fix rounding so they sum to exactly 1.0
            diff = Decimal("1.0") - sum(normalised.values())
            if diff != 0:
                first = next(iter(normalised))
                normalised[first] += diff
        else:
            count = Decimal(str(len(applicable)))
            normalised = {m: (Decimal("1") / count).quantize(Decimal("0.0001")) for m in applicable}

        # Step 6: Build MethodologyWeight entries
        mw_list: list[MethodologyWeight] = []
        for method in weights:
            is_met = met_flags.get(method, False)
            w = normalised.get(method, Decimal("0"))
            base_rationale = rationales.get(method, f"Base weight for {profile.stage} stage")
            if not is_met:
                base_rationale += " (data requirements not met — excluded from weighting)"
                w = Decimal("0")
            mw_list.append(
                MethodologyWeight(
                    methodology=method,
                    weight=w,
                    rationale=base_rationale,
                    data_requirements_met=is_met,
                )
            )

        applicable_count = sum(1 for mw in mw_list if mw.data_requirements_met)

        return MethodologyPlan(
            weights=tuple(mw_list),
            selector_version=self._version,
            applicable_count=applicable_count,
        )

    # ── internal ───────────────────────────────────────────────────────

    @staticmethod
    def _load_rules(path: Path) -> dict[str, Any]:
        with open(path) as f:
            rules: dict[str, Any] = yaml.safe_load(f)

        # Validate base weights sum to 1.0 for each stage
        for stage, methods in rules.get("base_weights", {}).items():
            total = sum(Decimal(str(v)) for v in methods.values())
            if abs(total - Decimal("1")) > Decimal("0.001"):
                raise ValueError(f"Base weights for stage '{stage}' sum to {total}, expected 1.0")
        return rules


# ── Helpers ────────────────────────────────────────────────────────────


def _matches_condition(value: float, condition: str) -> bool:
    """Evaluate a simple condition string against a numeric value."""
    condition = condition.strip()
    if condition.startswith("< "):
        return value < float(condition[2:])
    if condition.startswith("> "):
        return value > float(condition[2:])
    if " to " in condition:
        parts = condition.split(" to ")
        low, high = float(parts[0]), float(parts[1])
        return low <= value <= high
    return False


def _apply_modifier(base_weight: Decimal, modifier: str, stage: str) -> Decimal:
    """Adjust a weight based on a HIGH/MEDIUM/LOW modifier."""
    if modifier == "HIGH":
        return (base_weight * Decimal("1.25")).quantize(Decimal("0.0001"))
    if modifier == "LOW":
        return (base_weight * Decimal("0.60")).quantize(Decimal("0.0001"))
    # MEDIUM — no change
    return base_weight


def _check_data_requirements(
    method: str, data_package: DataPackage, profile: CompanyProfile
) -> bool:
    """Return True if the minimum data for *method* is available."""
    if method == "comparable_companies":
        return data_package.revenue_ltm is not None and data_package.revenue_ltm > 0
    if method == "last_round_market_adjusted":
        return data_package.last_post_money is not None and data_package.last_round_date is not None
    if method == "last_round_multiple_ratchet":
        return (
            data_package.last_post_money is not None
            and data_package.revenue_at_last_round is not None
            and data_package.current_revenue is not None
        )
    if method == "scorecard":
        return (
            data_package.regional_median_pre_money is not None
            and data_package.scorecard_factors is not None
        )
    if method == "berkus":
        return (
            data_package.max_pre_money_valuation is not None
            and data_package.berkus_factors is not None
        )
    # Unknown method — assume met
    return True
