"""Valuation engine orchestration."""

from __future__ import annotations

from typing import Any

from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource
from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.methodologies.base import MethodologyContext, ValuationMethodology
from vc_audit_tool.methodologies.comps import ComparableCompaniesMethodology
from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
from vc_audit_tool.models import ValuationRequest, ValuationResult


class ValuationEngine:
    def __init__(self) -> None:
        self.context = MethodologyContext(
            index_source=MockMarketIndexSource(),
            comps_source=MockComparableCompanySource(),
        )
        self._methodologies: dict[str, ValuationMethodology] = {
            LastRoundMarketAdjustedMethodology.name: LastRoundMarketAdjustedMethodology(),
            ComparableCompaniesMethodology.name: ComparableCompaniesMethodology(),
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
