"""Valuation engine orchestration.

By default the engine uses **live** data sources (Yahoo Finance for
market indices, EDGAR + yfinance + embeddings for comparable companies).
Pass explicit mock sources — or use the ``ValuationEngine.mock()``
class-method — when deterministic / offline behaviour is required
(e.g. unit tests).

Set the environment variable ``VC_AUDIT_MOCK=1`` to force mock sources
globally (useful for subprocess-based tests like the CLI suite).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource
from vc_audit_tool.methodologies.base import MethodologyContext, ValuationMethodology
from vc_audit_tool.methodologies.berkus import BerkusMethodology
from vc_audit_tool.methodologies.comps import ComparableCompaniesMethodology
from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
from vc_audit_tool.methodologies.multiple_ratchet import LastRoundMultipleRatchetMethodology
from vc_audit_tool.methodologies.scorecard import ScorecardMethodology
from vc_audit_tool.models import ValuationRequest, ValuationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default data-source factories (live, unless VC_AUDIT_MOCK=1)
# ---------------------------------------------------------------------------

_FORCE_MOCK = os.environ.get("VC_AUDIT_MOCK", "") == "1"


def _default_index_source() -> MarketIndexSource:
    """Return a live YFinance market-index source (or mock if ``VC_AUDIT_MOCK=1``)."""
    if _FORCE_MOCK:
        from vc_audit_tool.data_sources import MockMarketIndexSource

        return MockMarketIndexSource()
    from vc_audit_tool.data_sources.yfinance_market_index import YFinanceMarketIndexSource

    return YFinanceMarketIndexSource()


def _default_comps_source() -> ComparableCompanySource:
    """Return a live EDGAR + yfinance + embedding source (or mock if ``VC_AUDIT_MOCK=1``)."""
    if _FORCE_MOCK:
        from vc_audit_tool.data_sources import MockComparableCompanySource

        return MockComparableCompanySource()
    from vc_audit_tool.data_sources.edgar_comps import EdgarYFinanceComparableCompanySource

    return EdgarYFinanceComparableCompanySource()


class ValuationEngine:
    """Core valuation engine.

    Parameters
    ----------
    index_source, comps_source:
        Override the data sources.  When *both* are ``None`` (the default)
        the engine uses **live** sources backed by Yahoo Finance, EDGAR,
        and sentence-transformer embeddings.  Pass mock implementations
        for deterministic / offline operation.
    """

    def __init__(
        self,
        *,
        index_source: MarketIndexSource | None = None,
        comps_source: ComparableCompanySource | None = None,
    ) -> None:
        resolved_index = index_source if index_source is not None else _default_index_source()
        resolved_comps = comps_source if comps_source is not None else _default_comps_source()
        logger.info(
            "ValuationEngine init: index_source=%s  comps_source=%s",
            type(resolved_index).__name__,
            type(resolved_comps).__name__,
        )
        self.context = MethodologyContext(
            index_source=resolved_index,
            comps_source=resolved_comps,
        )
        self._methodologies: dict[str, ValuationMethodology] = {
            LastRoundMarketAdjustedMethodology.name: LastRoundMarketAdjustedMethodology(),
            ComparableCompaniesMethodology.name: ComparableCompaniesMethodology(),
            LastRoundMultipleRatchetMethodology.name: LastRoundMultipleRatchetMethodology(),
            ScorecardMethodology.name: ScorecardMethodology(),
            BerkusMethodology.name: BerkusMethodology(),
        }

    # ── Convenience constructors ──

    @classmethod
    def mock(cls) -> ValuationEngine:
        """Create an engine backed by deterministic in-memory mock data.

        Intended for unit tests and offline demos.
        """
        from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource

        return cls(
            index_source=MockMarketIndexSource(),
            comps_source=MockComparableCompanySource(),
        )

    # ── Core API ──

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
