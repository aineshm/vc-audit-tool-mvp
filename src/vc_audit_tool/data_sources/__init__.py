"""Data-source adapters (mock and live).

Re-exports from ``mock`` so that existing ``from vc_audit_tool.data_sources import …``
statements continue to work without changes.
"""

from vc_audit_tool.data_sources.mock import (
    COMPS_DATASET_VERSION as COMPS_DATASET_VERSION,
)
from vc_audit_tool.data_sources.mock import (
    MARKET_INDEX_DATASET_VERSION as MARKET_INDEX_DATASET_VERSION,
)
from vc_audit_tool.data_sources.mock import (
    ComparableCompany as ComparableCompany,
)
from vc_audit_tool.data_sources.mock import (
    MarketIndexPoint as MarketIndexPoint,
)
from vc_audit_tool.data_sources.mock import (
    MockComparableCompanySource as MockComparableCompanySource,
)
from vc_audit_tool.data_sources.mock import (
    MockMarketIndexSource as MockMarketIndexSource,
)

__all__ = [
    "COMPS_DATASET_VERSION",
    "ComparableCompany",
    "EdgarCompanyUniverse",
    "EdgarYFinanceComparableCompanySource",
    "EmbeddingCompsRanker",
    "MARKET_INDEX_DATASET_VERSION",
    "MarketIndexPoint",
    "MockComparableCompanySource",
    "MockMarketIndexSource",
    "YFinanceMetricsFetcher",
]


def __getattr__(name: str) -> object:
    """Lazy imports for heavy Epic-2 modules (avoid loading yfinance/torch at startup)."""
    if name == "EdgarCompanyUniverse":
        from vc_audit_tool.data_sources.edgar_universe import EdgarCompanyUniverse

        return EdgarCompanyUniverse
    if name == "EdgarYFinanceComparableCompanySource":
        from vc_audit_tool.data_sources.edgar_comps import EdgarYFinanceComparableCompanySource

        return EdgarYFinanceComparableCompanySource
    if name == "EmbeddingCompsRanker":
        from vc_audit_tool.data_sources.embedding_ranker import EmbeddingCompsRanker

        return EmbeddingCompsRanker
    if name == "YFinanceMetricsFetcher":
        from vc_audit_tool.data_sources.yfinance_metrics import YFinanceMetricsFetcher

        return YFinanceMetricsFetcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
