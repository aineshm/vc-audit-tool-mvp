"""Data models for the reconciliation layer.

Every downstream component imports from here.  All types are frozen
dataclasses so that reconciliation artefacts are immutable once built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

# ── Company profiling ──────────────────────────────────────────────────

CompanyStage = Literal["pre_seed", "seed", "early", "growth", "late"]


@dataclass(frozen=True)
class CompanyProfile:
    """Facts about a company that drive methodology selection."""

    name: str
    stage: CompanyStage
    age_years: float | None
    has_revenue: bool
    estimated_arr: Decimal | None
    last_round_age_months: float | None
    last_round_amount: Decimal | None
    last_post_money: Decimal | None
    sector: str
    sic_code: str | None
    headcount: int | None
    government_contracts_usd: Decimal | None
    profile_summary: str
    sources_used: tuple[str, ...]


# ── Methodology selection ──────────────────────────────────────────────


@dataclass(frozen=True)
class MethodologyWeight:
    """A single methodology and its assigned weight in the plan."""

    methodology: str
    weight: Decimal
    rationale: str
    data_requirements_met: bool


@dataclass(frozen=True)
class MethodologyPlan:
    """Output of :class:`MethodologySelector` — which methods to run."""

    weights: tuple[MethodologyWeight, ...]
    selector_version: str
    applicable_count: int


# ── Data package (inputs for methodology execution) ────────────────────


@dataclass(frozen=True)
class DataPackage:
    """Available data for methodology execution.

    Mirrors the assembled_request inputs but in a typed struct so the
    reconciliation layer never has to reach into untyped dicts.
    """

    last_post_money: Decimal | None
    last_round_date: date | None
    revenue_ltm: Decimal | None
    sector: str
    peer_set_quality: str | None
    government_contracts_usd: Decimal | None
    as_of_date: date
    # Optional fields for new methodologies
    regional_median_pre_money: Decimal | None = None
    scorecard_factors: dict[str, float] | None = None
    berkus_factors: dict[str, bool | float] | None = None
    max_pre_money_valuation: Decimal | None = None
    # Extra fields forwarded to specific methodologies
    revenue_at_last_round: Decimal | None = None
    current_revenue: Decimal | None = None
    private_company_discount_pct: Decimal = Decimal("25")
    public_index: str = "NASDAQ_COMPOSITE"

    @staticmethod
    def from_assembled_request(
        assembled: dict[str, Any],
        as_of_date: date | None = None,
    ) -> DataPackage:
        """Build a ``DataPackage`` from a research-agent assembled request."""

        inputs = assembled.get("inputs", {})

        def _dec(key: str) -> Decimal | None:
            v = inputs.get(key)
            if v is None:
                return None
            return Decimal(str(v))

        def _date(key: str) -> date | None:
            v = inputs.get(key)
            if v is None:
                return None
            if isinstance(v, date):
                return v
            return date.fromisoformat(str(v))

        aod = as_of_date
        if aod is None:
            raw = assembled.get("as_of_date")
            if raw is not None:
                aod = date.fromisoformat(str(raw)) if not isinstance(raw, date) else raw
            else:
                aod = date.today()

        return DataPackage(
            last_post_money=_dec("last_post_money_valuation"),
            last_round_date=_date("last_round_date"),
            revenue_ltm=_dec("revenue_ltm"),
            sector=inputs.get("sector", assembled.get("sector", "enterprise_software")),
            peer_set_quality=inputs.get("peer_set_quality"),
            government_contracts_usd=_dec("government_contracts_usd"),
            as_of_date=aod,
            revenue_at_last_round=_dec("revenue_at_last_round"),
            current_revenue=_dec("current_revenue"),
            private_company_discount_pct=Decimal(
                str(inputs.get("private_company_discount_pct", 25))
            ),
            public_index=inputs.get("public_index", "NASDAQ_COMPOSITE"),
        )


# ── Reconciled output ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ConcludedValue:
    """Final single-number valuation output."""

    point_estimate: Decimal
    range_low: Decimal
    range_high: Decimal
    currency: str
    as_of_date: date


@dataclass(frozen=True)
class ReconciliationSummary:
    """Synthesis of multiple methodology results."""

    concluded_value: ConcludedValue
    methodology_weights: tuple[MethodologyWeight, ...]
    divergence_flag: bool
    divergence_note: str | None
    reconciliation_rationale: str
    selector_version: str


@dataclass
class ReconciledValuation:
    """Top-level output of the reconciliation engine."""

    reconciliation: ReconciliationSummary
    methodology_results: dict[str, dict[str, Any]]
    company_profile: CompanyProfile
    audit_metadata: dict[str, Any] = field(default_factory=dict)
    research_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Produce a fully JSON-serialisable envelope."""
        cv = self.reconciliation.concluded_value
        mw_list: list[dict[str, Any]] = []
        for w in self.reconciliation.methodology_weights:
            entry: dict[str, Any] = {
                "methodology": w.methodology,
                "weight": float(w.weight),
                "rationale": w.rationale,
                "data_requirements_met": w.data_requirements_met,
            }
            # Attach per-method point estimate if available
            mr = self.methodology_results.get(w.methodology)
            if mr is not None:
                vr = mr.get("valuation_result", {})
                efv = vr.get("estimated_fair_value", {})
                entry["point_estimate"] = efv.get("amount")
            mw_list.append(entry)

        recon: dict[str, Any] = {
            "methodology_weights": mw_list,
            "divergence_flag": self.reconciliation.divergence_flag,
            "divergence_note": self.reconciliation.divergence_note,
            "reconciliation_rationale": self.reconciliation.reconciliation_rationale,
            "selector_version": self.reconciliation.selector_version,
        }

        profile_dict: dict[str, Any] = {
            "name": self.company_profile.name,
            "stage": self.company_profile.stage,
            "age_years": self.company_profile.age_years,
            "has_revenue": self.company_profile.has_revenue,
            "estimated_arr": (
                float(self.company_profile.estimated_arr)
                if self.company_profile.estimated_arr is not None
                else None
            ),
            "last_round_age_months": self.company_profile.last_round_age_months,
            "last_round_amount": (
                float(self.company_profile.last_round_amount)
                if self.company_profile.last_round_amount is not None
                else None
            ),
            "last_post_money": (
                float(self.company_profile.last_post_money)
                if self.company_profile.last_post_money is not None
                else None
            ),
            "sector": self.company_profile.sector,
            "sic_code": self.company_profile.sic_code,
            "headcount": self.company_profile.headcount,
            "government_contracts_usd": (
                float(self.company_profile.government_contracts_usd)
                if self.company_profile.government_contracts_usd is not None
                else None
            ),
            "profile_summary": self.company_profile.profile_summary,
            "sources_used": list(self.company_profile.sources_used),
        }

        return {
            "concluded_value": {
                "point_estimate": float(cv.point_estimate),
                "range_low": float(cv.range_low),
                "range_high": float(cv.range_high),
                "currency": cv.currency,
                "as_of_date": cv.as_of_date.isoformat(),
            },
            "reconciliation": recon,
            "methodology_results": self.methodology_results,
            "company_profile": profile_dict,
            "audit_metadata": self.audit_metadata,
            "research_metadata": self.research_metadata,
        }
