"""Methodology abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource
from vc_audit_tool.models import ValuationRequest, ValuationResult


@dataclass
class MethodologyContext:
    """Runtime context carrying provider implementations.

    Typed to Protocol interfaces so any conforming implementation
    (mock, live API adapter, cached proxy, etc.) can be injected.
    """

    index_source: MarketIndexSource
    comps_source: ComparableCompanySource


class ValuationMethodology(ABC):
    """Base class for all valuation methodologies.

    Subclasses MUST set ``name`` as a class attribute and implement ``valuate``.
    """

    name: str

    @abstractmethod
    def valuate(self, request: ValuationRequest, context: MethodologyContext) -> ValuationResult:
        """Execute the valuation and return an auditable result."""
