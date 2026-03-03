"""ReconciliationEngine — orchestrates multi-methodology valuation.

Wraps :class:`ValuationEngine` — it does not replace it.  The existing
engine is called once per applicable methodology; this layer selects,
dispatches, and reconciles.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError
from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource
from vc_audit_tool.models import ValuationRequest
from vc_audit_tool.reconciliation.models import (
    CompanyProfile,
    DataPackage,
    MethodologyPlan,
    ReconciledValuation,
)
from vc_audit_tool.reconciliation.reconciler import Reconciler
from vc_audit_tool.reconciliation.selector import MethodologySelector

logger = logging.getLogger(__name__)

_DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "methodology_rules_v1.yaml"


class ReconciliationEngine:
    """Run all applicable methodologies and reconcile to a single value."""

    def __init__(
        self,
        *,
        index_source: MarketIndexSource | None = None,
        comps_source: ComparableCompanySource | None = None,
        rules_path: Path | None = None,
    ) -> None:
        self._engine = ValuationEngine(
            index_source=index_source,
            comps_source=comps_source,
        )
        self._selector = MethodologySelector(rules_path or _DEFAULT_RULES_PATH)

    @classmethod
    def mock(cls) -> ReconciliationEngine:
        """Create a reconciliation engine backed by deterministic mock data."""
        from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource

        return cls(
            index_source=MockMarketIndexSource(),
            comps_source=MockComparableCompanySource(),
        )

    def value(
        self,
        profile: CompanyProfile,
        data_package: DataPackage,
        as_of_date: date,
        company_name: str,
        research_metadata: dict[str, Any] | None = None,
    ) -> ReconciledValuation:
        """Run all applicable methodologies and reconcile."""
        plan = self._selector.select(profile, data_package)
        results = self._run_methodologies(plan, data_package, company_name, as_of_date)

        if not results:
            raise DataSourceError(
                "All applicable methodologies failed at runtime. "
                "Cannot produce a reconciled valuation."
            )

        reconciliation = Reconciler.reconcile(results, plan, as_of_date)
        return ReconciledValuation(
            reconciliation=reconciliation,
            methodology_results=results,
            company_profile=profile,
            audit_metadata=_extract_audit_metadata(results),
            research_metadata=research_metadata,
        )

    # ── internal ───────────────────────────────────────────────────────

    def _run_methodologies(
        self,
        plan: MethodologyPlan,
        data_package: DataPackage,
        company_name: str,
        as_of_date: date,
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for weight in plan.weights:
            if not weight.data_requirements_met:
                continue
            request = _build_request(weight.methodology, data_package, company_name, as_of_date)
            try:
                result = self._engine.evaluate(request)
                results[weight.methodology] = result.to_dict()
            except Exception as exc:
                logger.warning("methodology %s failed: %s", weight.methodology, exc)
        return results


# ── Helpers ────────────────────────────────────────────────────────────


def _build_request(
    methodology: str,
    dp: DataPackage,
    company_name: str,
    as_of_date: date,
) -> ValuationRequest:
    """Map a DataPackage to a ValuationRequest for a specific methodology."""
    inputs: dict[str, Any] = {"sector": dp.sector}

    if methodology == "comparable_companies":
        if dp.revenue_ltm is not None:
            inputs["revenue_ltm"] = float(dp.revenue_ltm)
        inputs["private_company_discount_pct"] = float(dp.private_company_discount_pct)
        if dp.target_description:
            inputs["target_description"] = dp.target_description

    elif methodology == "last_round_market_adjusted":
        if dp.last_post_money is not None:
            inputs["last_post_money_valuation"] = float(dp.last_post_money)
        if dp.last_round_date is not None:
            inputs["last_round_date"] = dp.last_round_date.isoformat()
        inputs["public_index"] = dp.public_index

    elif methodology == "last_round_multiple_ratchet":
        if dp.last_post_money is not None:
            inputs["last_post_money_valuation"] = float(dp.last_post_money)
        if dp.revenue_at_last_round is not None:
            inputs["revenue_at_last_round"] = float(dp.revenue_at_last_round)
        if dp.current_revenue is not None:
            inputs["current_revenue"] = float(dp.current_revenue)
        inputs["private_company_discount_pct"] = float(dp.private_company_discount_pct)
        if dp.target_description:
            inputs["target_description"] = dp.target_description

    elif methodology == "scorecard":
        if dp.regional_median_pre_money is not None:
            inputs["regional_median_pre_money"] = float(dp.regional_median_pre_money)
        if dp.scorecard_factors is not None:
            inputs["factors"] = dp.scorecard_factors

    elif methodology == "berkus":
        if dp.max_pre_money_valuation is not None:
            inputs["max_pre_money_valuation"] = float(dp.max_pre_money_valuation)
        if dp.berkus_factors is not None:
            inputs["factors"] = dp.berkus_factors

    elif methodology == "direct_valuation":
        if dp.evidence_signals is not None:
            inputs["evidence_signals"] = dp.evidence_signals
        if dp.consensus_strength is not None:
            inputs["consensus_strength"] = dp.consensus_strength
        inputs["private_company_discount_pct"] = float(dp.private_company_discount_pct)

    return ValuationRequest(
        company_name=company_name,
        methodology=methodology,
        inputs=inputs,
        as_of_date=as_of_date,
    )


def _extract_audit_metadata(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pull audit_metadata from the first successful methodology result."""
    for result in results.values():
        meta = result.get("audit_metadata")
        if meta:
            return dict(meta)
    return {}
