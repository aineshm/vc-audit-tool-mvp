"""Fetch financial metrics for public companies via yfinance.

Provides EV, Revenue, EV/Revenue, and business description for a
given ticker.  Results are cached by ticker + date so repeated runs
on the same day return identical values (determinism guarantee).

Story 2.3 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/yfinance_metrics_cache")

# Lazy but patchable — same pattern as yfinance_market_index.py
yf: ModuleType | None = None


def _ensure_yf() -> ModuleType:
    """Import yfinance on first call; raise a clear error if missing."""
    global yf  # noqa: PLW0603
    if yf is None:
        try:
            import yfinance as _yf

            yf = _yf
        except ImportError as exc:
            raise DataSourceError(
                "yfinance is required for financial metrics. Install it with: pip install yfinance"
            ) from exc
    return yf


@dataclass(frozen=True)
class TickerMetrics:
    """Financial metrics for a single public company ticker."""

    ticker: str
    company_name: str
    sector: str
    industry: str
    enterprise_value: Decimal | None
    total_revenue: Decimal | None
    ev_to_revenue: Decimal | None
    market_cap: Decimal | None
    business_summary: str
    retrieved_at: str

    @property
    def has_valid_multiple(self) -> bool:
        """True if both EV and Revenue are available and positive."""
        return (
            self.enterprise_value is not None
            and self.total_revenue is not None
            and self.enterprise_value > 0
            and self.total_revenue > 0
            and self.ev_to_revenue is not None
            and self.ev_to_revenue > 0
        )


class YFinanceMetricsFetcher:
    """Fetch and cache financial metrics for public tickers via yfinance.

    Attributes
    ----------
    dataset_version:
        Stamp set after each fetch, of the form
        ``"yfinance-metrics-{date}"``.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = ""
    source_label: str = "Yahoo Finance financial metrics"

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem_cache: dict[str, TickerMetrics] = {}

    def fetch(self, ticker: str) -> TickerMetrics:
        """Return financial metrics for *ticker*.

        Raises ``DataSourceError`` if the ticker cannot be looked up at all.
        """
        ticker = ticker.upper()

        # Check memory cache
        if ticker in self._mem_cache:
            return self._mem_cache[ticker]

        # Check disk cache (keyed by ticker + today's date)
        cached = self._read_disk_cache(ticker)
        if cached is not None:
            self._mem_cache[ticker] = cached
            return cached

        # Fetch from yfinance
        metrics = self._fetch_from_yfinance(ticker)
        self._mem_cache[ticker] = metrics
        self._write_disk_cache(ticker, metrics)

        self.dataset_version = f"yfinance-metrics-{date.today().isoformat()}"
        return metrics

    def fetch_many(self, tickers: list[str]) -> list[TickerMetrics]:
        """Fetch metrics for multiple tickers. Never raises for individual failures."""
        results: list[TickerMetrics] = []
        for t in tickers:
            try:
                results.append(self.fetch(t))
            except DataSourceError:
                logger.warning("skipping ticker %s — fetch failed", t)
        return results

    # ── Private helpers ──

    def _fetch_from_yfinance(self, ticker: str) -> TickerMetrics:
        """Call yfinance and build a TickerMetrics."""
        _yf = _ensure_yf()
        try:
            t = _yf.Ticker(ticker)
            info: dict[str, object] = t.info
        except Exception as exc:
            raise DataSourceError(f"yfinance failed to fetch info for '{ticker}': {exc}") from exc

        if not info or info.get("regularMarketPrice") is None:
            raise DataSourceError(f"No data returned by yfinance for '{ticker}'.")

        def _dec(key: str) -> Decimal | None:
            val = info.get(key)
            if val is None:
                return None
            try:
                return Decimal(str(val))
            except Exception:
                return None

        ev = _dec("enterpriseValue")
        rev = _dec("totalRevenue")
        ev_rev = _dec("enterpriseToRevenue")
        # If yfinance didn't compute ev/rev but we have both, compute it ourselves
        if ev_rev is None and ev is not None and rev is not None and rev > 0:
            ev_rev = (ev / rev).quantize(Decimal("0.001"))

        now_iso = datetime.now(timezone.utc).isoformat()

        return TickerMetrics(
            ticker=ticker,
            company_name=str(info.get("shortName", ticker)),
            sector=str(info.get("sector", "")),
            industry=str(info.get("industry", "")),
            enterprise_value=ev,
            total_revenue=rev,
            ev_to_revenue=ev_rev,
            market_cap=_dec("marketCap"),
            business_summary=str(info.get("longBusinessSummary", "")),
            retrieved_at=now_iso,
        )

    # ── Disk cache ──

    def _cache_path(self, ticker: str) -> Path:
        today = date.today().isoformat()
        return self._cache_dir / f"{ticker}_{today}.json"

    def _read_disk_cache(self, ticker: str) -> TickerMetrics | None:
        path = self._cache_path(ticker)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return TickerMetrics(
                ticker=raw["ticker"],
                company_name=raw["company_name"],
                sector=raw.get("sector", ""),
                industry=raw.get("industry", ""),
                enterprise_value=Decimal(str(raw["enterprise_value"]))
                if raw.get("enterprise_value") is not None
                else None,
                total_revenue=Decimal(str(raw["total_revenue"]))
                if raw.get("total_revenue") is not None
                else None,
                ev_to_revenue=Decimal(str(raw["ev_to_revenue"]))
                if raw.get("ev_to_revenue") is not None
                else None,
                market_cap=Decimal(str(raw["market_cap"]))
                if raw.get("market_cap") is not None
                else None,
                business_summary=raw.get("business_summary", ""),
                retrieved_at=raw.get("retrieved_at", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("corrupt metrics cache %s — will re-fetch", path)
            return None

    def _write_disk_cache(self, ticker: str, m: TickerMetrics) -> None:
        path = self._cache_path(ticker)
        payload = {
            "ticker": m.ticker,
            "company_name": m.company_name,
            "sector": m.sector,
            "industry": m.industry,
            "enterprise_value": float(m.enterprise_value) if m.enterprise_value else None,
            "total_revenue": float(m.total_revenue) if m.total_revenue else None,
            "ev_to_revenue": float(m.ev_to_revenue) if m.ev_to_revenue else None,
            "market_cap": float(m.market_cap) if m.market_cap else None,
            "business_summary": m.business_summary,
            "retrieved_at": m.retrieved_at,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("wrote metrics cache %s", path)
