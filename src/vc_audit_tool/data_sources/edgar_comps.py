"""Real comparable-company source wiring EDGAR + embeddings + yfinance.

Implements the ``ComparableCompanySource`` Protocol as a drop-in
replacement for ``MockComparableCompanySource``.

Pipeline:
  1. EDGAR universe → list of tickers for a SIC / sector
  2. yfinance metrics → EV/Revenue for each ticker + business descriptions
  3. Embedding ranker → rank by semantic similarity to the target description
  4. Return top-k as ``ComparableCompany`` objects

Story 2.4 of the Production Upgrade Plan.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

from vc_audit_tool.data_sources.edgar_universe import (
    SIC_SECTOR_MAP,
    EdgarCompanyUniverse,
)
from vc_audit_tool.data_sources.embedding_ranker import EmbeddingCompsRanker
from vc_audit_tool.data_sources.mock import ComparableCompany
from vc_audit_tool.data_sources.yfinance_metrics import YFinanceMetricsFetcher
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# Reverse map: our internal sector name → SIC codes
_SECTOR_TO_SIC: dict[str, list[str]] = {}
for _sic, _sector in SIC_SECTOR_MAP.items():
    _SECTOR_TO_SIC.setdefault(_sector, []).append(_sic)


class EdgarYFinanceComparableCompanySource:
    """Real comparable-company source backed by EDGAR, yfinance, and embeddings.

    Satisfies the ``ComparableCompanySource`` Protocol.

    Attributes
    ----------
    dataset_version:
        Composite version string from all three sub-sources.
    source_label:
        Human-readable label for citation purposes.
    warnings:
        List of warnings produced during the last ``list_by_sector`` call
        (e.g. tickers with missing financial data).
    """

    dataset_version: str = ""
    source_label: str = "EDGAR + yfinance + embedding ranker"

    def __init__(
        self,
        *,
        edgar: EdgarCompanyUniverse | None = None,
        metrics: YFinanceMetricsFetcher | None = None,
        ranker: EmbeddingCompsRanker | None = None,
        cache_root: Path = Path("data"),
        top_k: int = 5,
        target_description: str = "",
    ) -> None:
        self._edgar = edgar or EdgarCompanyUniverse(cache_dir=cache_root / "edgar_cache")
        self._metrics = metrics or YFinanceMetricsFetcher(
            cache_dir=cache_root / "yfinance_metrics_cache"
        )
        self._ranker = ranker or EmbeddingCompsRanker(cache_dir=cache_root / "embedding_cache")
        self._top_k = top_k
        self._target_description = target_description
        self.warnings: list[str] = []

    # ── ComparableCompanySource Protocol methods ──

    def list_by_sector(
        self, sector: str, *, target_description: str | None = None
    ) -> list[ComparableCompany]:
        """Return top-k comparable companies for *sector*, ranked by
        embedding similarity if a target description is set.
        """
        self.warnings = []

        # Resolve sector → SIC codes
        sic_codes = _SECTOR_TO_SIC.get(sector)
        if not sic_codes:
            raise DataSourceError(
                f"No SIC code mapping for sector '{sector}'. "
                f"Supported sectors: {', '.join(sorted(_SECTOR_TO_SIC))}."
            )

        # Step 1: Build company universe from EDGAR
        all_companies = []
        for sic in sic_codes:
            try:
                all_companies.extend(self._edgar.list_by_sic(sic))
            except DataSourceError as exc:
                self.warnings.append(f"EDGAR SIC {sic}: {exc}")

        if not all_companies:
            raise DataSourceError(
                f"No companies found in EDGAR for sector '{sector}' (SICs: {sic_codes})."
            )

        tickers = list({c.ticker for c in all_companies})
        logger.info("EDGAR universe for '%s': %d unique tickers", sector, len(tickers))

        # Step 2: Fetch financial metrics (this also gives us business descriptions)
        metrics_list = self._metrics.fetch_many(tickers)
        valid_metrics = [m for m in metrics_list if m.has_valid_multiple]

        skipped = len(metrics_list) - len(valid_metrics)
        if skipped > 0:
            self.warnings.append(f"{skipped} tickers excluded: missing EV or Revenue data.")

        if not valid_metrics:
            raise DataSourceError(f"No tickers with valid EV/Revenue in sector '{sector}'.")

        logger.info(
            "%d tickers with valid metrics out of %d fetched",
            len(valid_metrics),
            len(metrics_list),
        )

        # Step 3: Rank by embedding similarity (if target description provided)
        effective_target_description = (
            target_description
            if target_description is not None
            else self._target_description
        )
        if effective_target_description:
            candidates = [
                {
                    "ticker": m.ticker,
                    "company_name": m.company_name,
                    "description": m.business_summary,
                }
                for m in valid_metrics
            ]
            ranked = self._ranker.rank(
                effective_target_description, candidates, top_k=self._top_k
            )
            # Reorder valid_metrics to match ranking
            metrics_by_ticker = {m.ticker: m for m in valid_metrics}
            ordered_metrics = [
                metrics_by_ticker[r.ticker] for r in ranked if r.ticker in metrics_by_ticker
            ]
        else:
            # No description — just take top_k by largest market cap
            ordered_metrics = sorted(
                valid_metrics,
                key=lambda m: m.market_cap or Decimal("0"),
                reverse=True,
            )[: self._top_k]

        # Step 4: Build ComparableCompany objects
        comps = [
            ComparableCompany(
                ticker=m.ticker,
                company_name=m.company_name,
                sector=sector,
                ev_to_revenue=m.ev_to_revenue or Decimal("0"),
            )
            for m in ordered_metrics
        ]

        # Stamp composite dataset version
        self.dataset_version = f"edgar+yfinance+embeddings-{self._edgar.dataset_version}"

        logger.info("returning %d comps for sector '%s'", len(comps), sector)
        return comps

    def list_by_tickers(self, tickers: Iterable[str]) -> list[ComparableCompany]:
        """Fetch real metrics for explicit tickers."""
        self.warnings = []
        ticker_list = [t.upper() for t in tickers]
        metrics_list = self._metrics.fetch_many(ticker_list)

        comps: list[ComparableCompany] = []
        for m in metrics_list:
            if not m.has_valid_multiple:
                self.warnings.append(f"{m.ticker}: excluded (missing EV or Revenue)")
                continue
            comps.append(
                ComparableCompany(
                    ticker=m.ticker,
                    company_name=m.company_name,
                    sector=m.sector or m.industry or "unknown",
                    ev_to_revenue=m.ev_to_revenue or Decimal("0"),
                )
            )

        missing = sorted(set(ticker_list) - {c.ticker for c in comps})
        if missing:
            raise DataSourceError(f"Missing or invalid comps for tickers: {', '.join(missing)}.")

        self.dataset_version = f"yfinance-metrics-{self._metrics.dataset_version}"
        return comps

    @staticmethod
    def aggregate_multiple(comps: list[ComparableCompany], statistic: str) -> Decimal:
        """Compute median or mean of EV/Revenue multiples."""
        multiples = [comp.ev_to_revenue for comp in comps]
        if statistic == "median":
            return Decimal(str(median(multiples)))
        if statistic == "mean":
            return Decimal(str(mean(multiples)))
        raise DataSourceError(f"Unsupported statistic '{statistic}'.")
