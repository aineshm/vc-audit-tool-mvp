"""Mock data source adapters for market indices and public comps."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import mean, median

from vc_audit_tool.exceptions import DataSourceError


@dataclass(frozen=True)
class MarketIndexPoint:
    as_of_date: date
    level: Decimal


@dataclass(frozen=True)
class ComparableCompany:
    ticker: str
    company_name: str
    sector: str
    ev_to_revenue: Decimal


class MockMarketIndexSource:
    """Simple in-memory index history with previous-business-day fallback."""

    _INDEX_LEVELS: dict[str, dict[str, Decimal]] = {
        "NASDAQ_COMPOSITE": {
            "2023-12-31": Decimal("15011.35"),
            "2024-03-31": Decimal("16379.46"),
            "2024-06-30": Decimal("17637.12"),
            "2024-09-30": Decimal("16828.43"),
            "2024-12-31": Decimal("18842.12"),
            "2025-03-31": Decimal("18032.90"),
            "2025-06-30": Decimal("19422.55"),
            "2025-09-30": Decimal("20122.04"),
            "2025-12-31": Decimal("20905.88"),
            "2026-02-18": Decimal("21311.12"),
        },
        "RUSSELL_2000": {
            "2023-12-31": Decimal("2011.44"),
            "2024-03-31": Decimal("2107.88"),
            "2024-06-30": Decimal("2056.31"),
            "2024-09-30": Decimal("2190.04"),
            "2024-12-31": Decimal("2251.11"),
            "2025-03-31": Decimal("2176.92"),
            "2025-06-30": Decimal("2294.53"),
            "2025-09-30": Decimal("2340.19"),
            "2025-12-31": Decimal("2389.44"),
            "2026-02-18": Decimal("2412.90"),
        },
    }

    def get_level(self, index_name: str, as_of_date: date) -> MarketIndexPoint:
        if index_name not in self._INDEX_LEVELS:
            raise DataSourceError(f"Unknown index '{index_name}'.")

        history = self._INDEX_LEVELS[index_name]
        candidate_dates = sorted(date.fromisoformat(d) for d in history)
        available_dates = [d for d in candidate_dates if d <= as_of_date]
        if not available_dates:
            raise DataSourceError(
                f"No index level for {index_name} on or before {as_of_date.isoformat()}."
            )
        chosen_date = available_dates[-1]
        return MarketIndexPoint(as_of_date=chosen_date, level=history[chosen_date.isoformat()])


class MockComparableCompanySource:
    """In-memory public comps with sector-based filtering."""

    _COMPS: tuple[ComparableCompany, ...] = (
        ComparableCompany("SNOW", "Snowflake", "enterprise_software", Decimal("13.1")),
        ComparableCompany("DDOG", "Datadog", "enterprise_software", Decimal("12.4")),
        ComparableCompany("MDB", "MongoDB", "enterprise_software", Decimal("9.2")),
        ComparableCompany("ZS", "Zscaler", "enterprise_software", Decimal("11.8")),
        ComparableCompany("S", "SentinelOne", "cybersecurity", Decimal("8.6")),
        ComparableCompany("CRWD", "CrowdStrike", "cybersecurity", Decimal("14.2")),
        ComparableCompany("OKTA", "Okta", "cybersecurity", Decimal("7.7")),
        ComparableCompany("NET", "Cloudflare", "infrastructure_software", Decimal("16.1")),
        ComparableCompany("FSLY", "Fastly", "infrastructure_software", Decimal("3.8")),
        ComparableCompany("ESTC", "Elastic", "infrastructure_software", Decimal("5.3")),
    )

    def list_by_sector(self, sector: str) -> list[ComparableCompany]:
        comps = [comp for comp in self._COMPS if comp.sector == sector]
        if not comps:
            raise DataSourceError(f"No comps configured for sector '{sector}'.")
        return comps

    def list_by_tickers(self, tickers: Iterable[str]) -> list[ComparableCompany]:
        ticker_set = {ticker.upper() for ticker in tickers}
        comps = [comp for comp in self._COMPS if comp.ticker in ticker_set]
        missing = sorted(ticker_set.difference({c.ticker for c in comps}))
        if missing:
            raise DataSourceError(f"Missing comps for tickers: {', '.join(missing)}.")
        return comps

    @staticmethod
    def aggregate_multiple(comps: list[ComparableCompany], statistic: str) -> Decimal:
        multiples = [comp.ev_to_revenue for comp in comps]
        if statistic == "median":
            return Decimal(str(median(multiples)))
        if statistic == "mean":
            return Decimal(str(mean(multiples)))
        raise DataSourceError(f"Unsupported statistic '{statistic}'.")
