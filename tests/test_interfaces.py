"""Tests verifying that mock data sources conform to Protocol interfaces."""

from __future__ import annotations

import unittest

from vc_audit_tool.data_sources import MockComparableCompanySource, MockMarketIndexSource
from vc_audit_tool.interfaces import ComparableCompanySource, MarketIndexSource


class ProtocolConformanceTests(unittest.TestCase):
    """Verify mock implementations satisfy their Protocol contracts at runtime."""

    def test_mock_market_index_is_market_index_source(self) -> None:
        source = MockMarketIndexSource()
        self.assertIsInstance(source, MarketIndexSource)

    def test_mock_comps_is_comparable_company_source(self) -> None:
        source = MockComparableCompanySource()
        self.assertIsInstance(source, ComparableCompanySource)


if __name__ == "__main__":
    unittest.main()
