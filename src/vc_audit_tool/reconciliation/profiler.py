"""CompanyProfiler — deterministic stage classification from ResearchResult.

Pure logic, no LLM calls, no external API calls.  Converts untyped
research output into a frozen :class:`CompanyProfile`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from vc_audit_tool.reconciliation.models import CompanyProfile, CompanyStage


class CompanyProfiler:
    """Build a :class:`CompanyProfile` from an agent's ``ResearchResult``."""

    # ── public API ─────────────────────────────────────────────────────

    @staticmethod
    def build_from_research(
        research_result: Any,
        *,
        as_of_date: date | None = None,
    ) -> CompanyProfile:
        """Create a profile from a ``ResearchResult`` dataclass."""
        assembled = research_result.assembled_request or {}
        metadata = research_result.research_metadata or {}
        return CompanyProfiler._build(assembled, metadata, as_of_date)

    @staticmethod
    def build_from_dict(
        assembled: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        as_of_date: date | None = None,
    ) -> CompanyProfile:
        """Create a profile from raw dicts (useful for /reconcile endpoint)."""
        return CompanyProfiler._build(assembled, metadata or {}, as_of_date)

    # ── internal ───────────────────────────────────────────────────────

    @staticmethod
    def _build(
        assembled: dict[str, Any],
        metadata: dict[str, Any],
        as_of_date: date | None,
    ) -> CompanyProfile:
        inputs = assembled.get("inputs", {})
        aod = as_of_date or _parse_date_or_today(assembled.get("as_of_date"))

        name: str = assembled.get("company_name", "Unknown")
        sector: str = inputs.get("sector", assembled.get("sector", "enterprise_software"))
        sic_code: str | None = inputs.get("sic_code") or assembled.get("sic_code")

        # Revenue
        revenue_raw = inputs.get("revenue_ltm")
        has_revenue = revenue_raw is not None and float(revenue_raw) > 0
        estimated_arr = Decimal(str(revenue_raw)) if revenue_raw is not None else None

        # Round data
        last_post_money_raw = inputs.get("last_post_money_valuation")
        last_post_money = (
            Decimal(str(last_post_money_raw)) if last_post_money_raw is not None else None
        )
        last_round_amount_raw = inputs.get("last_round_amount")
        last_round_amount = (
            Decimal(str(last_round_amount_raw)) if last_round_amount_raw is not None else None
        )

        last_round_date_raw = inputs.get("last_round_date")
        last_round_age_months: float | None = None
        if last_round_date_raw is not None:
            lrd = _parse_date_or_today(last_round_date_raw)
            delta_days = (aod - lrd).days
            last_round_age_months = round(delta_days / 30.44, 1)

        # Age
        founded_raw = metadata.get("founded_date") or inputs.get("founded_date")
        age_years: float | None = None
        if founded_raw is not None:
            fd = _parse_date_or_today(founded_raw)
            age_years = round((aod - fd).days / 365.25, 1)

        headcount_raw = inputs.get("headcount") or metadata.get("headcount")
        headcount: int | None = int(headcount_raw) if headcount_raw is not None else None

        gov_raw = inputs.get("government_contracts_usd")
        gov_usd = Decimal(str(gov_raw)) if gov_raw is not None else None

        # Sources
        sources_used: list[str] = []
        for src in metadata.get("sources_consulted", []):
            if isinstance(src, str):
                sources_used.append(src)

        # Stage classification
        stage, rule = _classify_stage(
            age_years=age_years,
            has_revenue=has_revenue,
            estimated_arr=estimated_arr,
            last_round_age_months=last_round_age_months,
            last_post_money=last_post_money,
        )

        profile_summary = f"{_stage_label(stage)} company in {sector}. Classification rule: {rule}."

        return CompanyProfile(
            name=name,
            stage=stage,
            age_years=age_years,
            has_revenue=has_revenue,
            estimated_arr=estimated_arr,
            last_round_age_months=last_round_age_months,
            last_round_amount=last_round_amount,
            last_post_money=last_post_money,
            sector=sector,
            sic_code=sic_code,
            headcount=headcount,
            government_contracts_usd=gov_usd,
            profile_summary=profile_summary,
            sources_used=tuple(sources_used),
        )


# ── Stage classification rules ────────────────────────────────────────


def _classify_stage(
    *,
    age_years: float | None,
    has_revenue: bool,
    estimated_arr: Decimal | None,
    last_round_age_months: float | None,
    last_post_money: Decimal | None,
) -> tuple[CompanyStage, str]:
    """Return (stage, rule_description).  Rules applied in priority order."""

    arr = float(estimated_arr) if estimated_arr is not None else 0.0
    has_round = last_post_money is not None

    # Rule 1: pre_seed
    if age_years is not None and age_years < 1.5:
        return "pre_seed", "age < 18 months"
    if not has_revenue and not has_round:
        return "pre_seed", "no revenue and no institutional round"

    # Rule 2: seed
    if (
        age_years is not None
        and 1.5 <= age_years <= 3.0
        and arr < 1_000_000
        and (last_post_money is None or last_post_money < Decimal("5_000_000"))
    ):
        return "seed", "age 18-36 months with revenue <$1M and round <$5M"

    # Rule 3: early
    if (age_years is not None and age_years < 4.0) and (1_000_000 <= arr <= 10_000_000):
        return "early", "age <4 years with revenue $1M-$10M"
    if last_post_money is not None and last_post_money < Decimal("50_000_000") and arr < 10_000_000:
        return "early", "round size suggests Series A/B stage"

    # Rule 4: growth
    if age_years is not None and 3.0 <= age_years <= 8.0 and 10_000_000 <= arr <= 100_000_000:
        return "growth", "age 3-8 years with revenue $10M-$100M"
    if last_post_money is not None and Decimal("50_000_000") <= last_post_money < Decimal(
        "500_000_000"
    ):
        return "growth", "post-money valuation suggests Series C/D stage"

    # Rule 5: late
    if age_years is not None and age_years > 6.0 and arr > 100_000_000:
        return "late", "age >6 years with revenue >$100M"
    if last_post_money is not None and last_post_money >= Decimal("500_000_000"):
        return "late", "post-money valuation >=500M suggests late stage"

    # Default: growth (safest assumption for a company with some data)
    return "growth", "default classification — insufficient data for precise staging"


def _stage_label(stage: CompanyStage) -> str:
    return {
        "pre_seed": "Pre-seed-stage",
        "seed": "Seed-stage",
        "early": "Early-stage",
        "growth": "Growth-stage",
        "late": "Late-stage",
    }.get(stage, stage.capitalize())


def _parse_date_or_today(raw: Any) -> date:
    if raw is None:
        return date.today()
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))
