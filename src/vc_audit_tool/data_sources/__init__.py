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
    "MARKET_INDEX_DATASET_VERSION",
    "MarketIndexPoint",
    "MockComparableCompanySource",
    "MockMarketIndexSource",
]
