"""Provider interfaces for data sources.

Using Protocol (PEP 544) so mock and real implementations satisfy
the same structural contract without inheritance coupling.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from vc_audit_tool.data_sources import ComparableCompany, MarketIndexPoint


@runtime_checkable
class MarketIndexSource(Protocol):
    """Contract for any market-index data provider."""

    def get_level(self, index_name: str, as_of_date: date) -> MarketIndexPoint: ...


@runtime_checkable
class ComparableCompanySource(Protocol):
    """Contract for any comparable-company data provider."""

    def list_by_sector(self, sector: str) -> list[ComparableCompany]: ...

    def list_by_tickers(self, tickers: Iterable[str]) -> list[ComparableCompany]: ...

    @staticmethod
    def aggregate_multiple(comps: list[ComparableCompany], statistic: str) -> Decimal: ...
