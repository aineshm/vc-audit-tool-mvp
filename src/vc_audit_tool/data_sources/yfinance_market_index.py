"""Real market-index data source powered by yfinance.

Fetches daily closing prices from Yahoo Finance and caches them
locally so repeated runs within the same session (or across sessions
that share the cache directory) avoid redundant network calls.

Implements the ``MarketIndexSource`` Protocol.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from vc_audit_tool.data_sources.mock import MarketIndexPoint
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# Lazy but patchable: ``yf`` is set to the real ``yfinance`` module on first
# use.  Keeping it at module scope lets test code ``patch(…yf.Ticker, …)``
# without import-order headaches.
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
                "yfinance is required for real market data. Install it with: pip install yfinance"
            ) from exc
    return yf


# Mapping from our internal index names to Yahoo Finance ticker symbols.
_TICKER_MAP: dict[str, str] = {
    "NASDAQ_COMPOSITE": "^IXIC",
    "RUSSELL_2000": "^RUT",
    "SP500": "^GSPC",
}

_DEFAULT_CACHE_DIR = Path("data/yfinance_cache")


class YFinanceMarketIndexSource:
    """Fetch historical index closing prices via yfinance with local JSON cache.

    Attributes
    ----------
    dataset_version:
        Stamp of the form ``"yfinance-{ticker}-{retrieval_date}"`` set after
        each ``get_level`` call so the methodology can embed it in citations.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = ""
    source_label: str = "Yahoo Finance market index data"

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # In-memory layer: ``{ticker_symbol: {iso_date_str: Decimal}}``
        self._mem_cache: dict[str, dict[str, Decimal]] = {}

    # ── Public API (satisfies MarketIndexSource Protocol) ──

    def get_level(self, index_name: str, as_of_date: date) -> MarketIndexPoint:
        """Return the closing level on *as_of_date*, falling back to the
        nearest prior trading day if the requested date is a weekend or
        holiday.

        Raises ``DataSourceError`` if the index is unknown or data
        cannot be retrieved.
        """
        yf_ticker = _TICKER_MAP.get(index_name)
        if yf_ticker is None:
            supported = ", ".join(sorted(_TICKER_MAP))
            raise DataSourceError(f"Unknown index '{index_name}'. Supported: {supported}.")

        history = self._get_history(yf_ticker, as_of_date)

        # Find the latest date <= as_of_date
        available = sorted(
            date.fromisoformat(d) for d in history if date.fromisoformat(d) <= as_of_date
        )
        if not available:
            raise DataSourceError(
                f"No index level for {index_name} ({yf_ticker}) on or before "
                f"{as_of_date.isoformat()}."
            )

        chosen = available[-1]
        level = history[chosen.isoformat()]

        # Stamp the dataset version for citation tracing
        retrieval_date = self._retrieval_date_for(yf_ticker)
        self.dataset_version = f"yfinance-{yf_ticker}-{retrieval_date}"

        return MarketIndexPoint(as_of_date=chosen, level=level)

    # ── Private helpers ──

    def _get_history(self, yf_ticker: str, as_of_date: date) -> dict[str, Decimal]:
        """Return date→level mapping, loading from cache or fetching."""
        if yf_ticker in self._mem_cache:
            cache = self._mem_cache[yf_ticker]
            # If the requested date might be covered, return cached data.
            # We'll extend the cache if needed.
            if any(date.fromisoformat(d) >= as_of_date for d in cache):
                return cache

        # Try disk cache
        disk = self._read_disk_cache(yf_ticker)
        if disk is not None:
            self._mem_cache[yf_ticker] = disk
            if any(date.fromisoformat(d) >= as_of_date for d in disk):
                return disk

        # Fetch from yfinance — pull a generous window so one fetch
        # covers most use-cases (3 years back from as_of_date).
        fetched = self._fetch(yf_ticker, as_of_date)

        # Merge with any existing cache (fetched data wins on conflicts)
        merged = {**(self._mem_cache.get(yf_ticker) or {}), **fetched}
        self._mem_cache[yf_ticker] = merged
        self._write_disk_cache(yf_ticker, merged)
        return merged

    def _fetch(self, yf_ticker: str, as_of_date: date) -> dict[str, Decimal]:
        """Call yfinance and return {iso_date: Decimal} of daily closes."""
        _yf = _ensure_yf()

        # Fetch 3 years of history to cover most valuation windows
        start_date = date(as_of_date.year - 3, as_of_date.month, as_of_date.day)
        # yfinance 'end' is exclusive, so add 1 day
        end_date = date(as_of_date.year, as_of_date.month, as_of_date.day)

        logger.info(
            "fetching %s from %s to %s via yfinance",
            yf_ticker,
            start_date.isoformat(),
            end_date.isoformat(),
        )

        try:
            ticker = _yf.Ticker(yf_ticker)
            hist = ticker.history(start=start_date.isoformat(), end=end_date.isoformat())
        except Exception as exc:
            raise DataSourceError(
                f"yfinance failed to fetch history for '{yf_ticker}': {exc}"
            ) from exc

        if hist.empty:
            raise DataSourceError(
                f"yfinance returned no data for {yf_ticker} between "
                f"{start_date.isoformat()} and {end_date.isoformat()}."
            )

        result: dict[str, Decimal] = {}
        for ts, row in hist.iterrows():
            day_str: str = ts.date().isoformat()
            close = row["Close"]
            result[day_str] = Decimal(str(round(float(close), 2)))

        logger.info("fetched %d trading days for %s", len(result), yf_ticker)
        return result

    # ── Disk cache ──

    def _cache_path(self, yf_ticker: str) -> Path:
        safe_name = yf_ticker.replace("^", "").replace("/", "_")
        return self._cache_dir / f"{safe_name}.json"

    def _read_disk_cache(self, yf_ticker: str) -> dict[str, Decimal] | None:
        path = self._cache_path(yf_ticker)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {k: Decimal(str(v)) for k, v in raw["levels"].items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("corrupt cache file %s — will re-fetch", path)
            return None

    def _write_disk_cache(self, yf_ticker: str, levels: dict[str, Decimal]) -> None:
        path = self._cache_path(yf_ticker)
        payload = {
            "ticker": yf_ticker,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "trading_days": len(levels),
            "levels": {k: float(v) for k, v in sorted(levels.items())},
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("wrote cache %s (%d days)", path, len(levels))

    def _retrieval_date_for(self, yf_ticker: str) -> str:
        """Return the retrieval timestamp from the disk cache, or today."""
        path = self._cache_path(yf_ticker)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                retrieved: str = raw["retrieved_at"][:10]  # just the date portion
                return retrieved
            except (json.JSONDecodeError, KeyError):
                pass
        return date.today().isoformat()
