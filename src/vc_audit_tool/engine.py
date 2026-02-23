"""Valuation engine orchestration."""

from __future__ import annotations

from typing import Any

from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource
from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource
from vc_audit_tool.methodologies.base import MethodologyContext, ValuationMethodology
from vc_audit_tool.methodologies.berkus import BerkusMethodology
from vc_audit_tool.methodologies.comps import ComparableCompaniesMethodology
from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
from vc_audit_tool.methodologies.multiple_ratchet import LastRoundMultipleRatchetMethodology
from vc_audit_tool.methodologies.scorecard import ScorecardMethodology
from vc_audit_tool.models import ValuationRequest, ValuationResult


class ValuationEngine:
    def __init__(
        self,
        *,
        index_source: MarketIndexSource | None = None,
        comps_source: ComparableCompanySource | None = None,
    ) -> None:
        self.context = MethodologyContext(
            index_source=index_source or MockMarketIndexSource(),
            comps_source=comps_source or MockComparableCompanySource(),
        )
        self._methodologies: dict[str, ValuationMethodology] = {
            LastRoundMarketAdjustedMethodology.name: LastRoundMarketAdjustedMethodology(),
            ComparableCompaniesMethodology.name: ComparableCompaniesMethodology(),
            LastRoundMultipleRatchetMethodology.name: LastRoundMultipleRatchetMethodology(),
            ScorecardMethodology.name: ScorecardMethodology(),
            BerkusMethodology.name: BerkusMethodology(),
        }

    def evaluate(self, request: ValuationRequest) -> ValuationResult:
        methodology = self._methodologies.get(request.methodology)
        if not methodology:
            available = ", ".join(sorted(self._methodologies.keys()))
            raise ValidationError(
                f"Unknown methodology '{request.methodology}'. Available: {available}."
            )
        return methodology.valuate(request, self.context)

    def evaluate_from_dict(self, payload: dict[str, Any]) -> ValuationResult:
        request = ValuationRequest.from_dict(payload)
        return self.evaluate(request)
