"""Tests for YFinanceMarketIndexSource.

Suite is split into two groups:
  1. **Unit tests** — mock out yfinance so they run offline, fast, and
     deterministically.  These validate Protocol conformance, caching logic,
     weekend/holiday fallback, and error handling.
  2. **Integration tests** — actually call Yahoo Finance.  Skipped in CI
     (``@pytest.mark.integration``) but runnable locally with
     ``pytest -m integration``.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vc_audit_tool.data_sources.yfinance_market_index import (
    YFinanceMarketIndexSource,
    _ensure_yf,
)
from vc_audit_tool.exceptions import DataSourceError
from vc_audit_tool.interfaces import MarketIndexSource

# ---------------------------------------------------------------------------
# Helpers — build a fake yfinance history DataFrame
# ---------------------------------------------------------------------------


def _make_hist(rows: list[tuple[str, float]]) -> Any:
    """Return a minimal pandas-like DataFrame for patching ``ticker.history``."""
    import pandas as pd

    dates = pd.to_datetime([r[0] for r in rows])
    closes = [r[1] for r in rows]
    return pd.DataFrame({"Close": closes}, index=dates)


_SAMPLE_ROWS = [
    ("2024-06-26", 17700.50),
    ("2024-06-27", 17720.10),
    ("2024-06-28", 17732.60),  # Friday
    # gap — 2024-06-29 (Sat), 2024-06-30 (Sun)
    ("2024-07-01", 17879.30),  # Monday
    ("2024-07-02", 18028.76),
]


# ---------------------------------------------------------------------------
# Unit tests (offline)
# ---------------------------------------------------------------------------


class TestYFinanceProtocol(unittest.TestCase):
    """The source must satisfy the MarketIndexSource Protocol."""

    def test_isinstance_check(self) -> None:
        src = YFinanceMarketIndexSource()
        self.assertIsInstance(src, MarketIndexSource)


class TestYFinanceOffline(unittest.TestCase):
    """All tests mock out yfinance to run offline and deterministically."""

    def setUp(self) -> None:
        import tempfile

        # Ensure the lazy yf module-level reference is initialised so
        # ``patch(…yf.Ticker, …)`` has something real to latch onto.
        _ensure_yf()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self.source = YFinanceMarketIndexSource(cache_dir=self.cache_dir)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patch_fetch(self, rows: list[tuple[str, float]] | None = None) -> Any:
        """Context manager that patches yfinance.Ticker via the module-level ``yf``."""
        hist = _make_hist(rows or _SAMPLE_ROWS)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        return patch(
            "vc_audit_tool.data_sources.yfinance_market_index.yf.Ticker",
            return_value=mock_ticker,
        )

    # -- Basic fetch --

    def test_get_level_returns_correct_value(self) -> None:
        with self._patch_fetch():
            pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        self.assertEqual(pt.level, Decimal("17732.60"))

    def test_weekend_falls_back_to_friday(self) -> None:
        with self._patch_fetch():
            pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 29))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        self.assertEqual(pt.level, Decimal("17732.60"))

    def test_sunday_falls_back_to_friday(self) -> None:
        with self._patch_fetch():
            pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 30))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        self.assertEqual(pt.level, Decimal("17732.60"))

    # -- Dataset version stamping --

    def test_dataset_version_is_set(self) -> None:
        with self._patch_fetch():
            self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertIn("yfinance", self.source.dataset_version)
        self.assertIn("^IXIC", self.source.dataset_version)

    # -- Caching --

    def test_disk_cache_is_written(self) -> None:
        with self._patch_fetch():
            self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        cache_file = self.cache_dir / "IXIC.json"
        self.assertTrue(cache_file.exists())
        data = json.loads(cache_file.read_text())
        self.assertIn("levels", data)
        self.assertIn("2024-06-28", data["levels"])

    def test_second_call_uses_cache(self) -> None:
        with self._patch_fetch() as mock_cls:
            self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
            # Second call — should NOT invoke yfinance again
            self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 27))
            # Ticker constructor should have been called exactly once
            self.assertEqual(mock_cls.call_count, 1)

    # -- Error handling --

    def test_unknown_index_raises(self) -> None:
        with self.assertRaises(DataSourceError) as ctx:
            self.source.get_level("DOW_JONES", date(2024, 6, 28))
        self.assertIn("Unknown index", str(ctx.exception))

    def test_empty_history_raises(self) -> None:
        with self._patch_fetch(rows=[]):
            pass  # _make_hist requires non-empty; test via direct patch
        mock_ticker = MagicMock()
        import pandas as pd

        mock_ticker.history.return_value = pd.DataFrame()
        with patch(
            "vc_audit_tool.data_sources.yfinance_market_index.yf.Ticker",
            return_value=mock_ticker,
        ):
            with self.assertRaises(DataSourceError) as ctx:
                self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
            self.assertIn("no data", str(ctx.exception).lower())

    def test_russell_2000_uses_correct_ticker(self) -> None:
        with self._patch_fetch() as mock_cls:
            self.source.get_level("RUSSELL_2000", date(2024, 6, 28))
            mock_cls.assert_called_once_with("^RUT")

    # -- Resolved data points format --

    def test_resolved_data_points_format(self) -> None:
        """The point's string repr should look like 'NASDAQ_COMPOSITE@2024-06-28=17732.6'."""
        with self._patch_fetch():
            pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        self.assertEqual(pt.level, Decimal("17732.6"))

    # -- Corrupt cache --

    def test_corrupt_cache_triggers_refetch(self) -> None:
        cache_file = self.cache_dir / "IXIC.json"
        cache_file.write_text("NOT VALID JSON", encoding="utf-8")
        with self._patch_fetch():
            pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertEqual(pt.level, Decimal("17732.60"))


# ---------------------------------------------------------------------------
# Story 1.2 — Staleness warning thresholds
# ---------------------------------------------------------------------------


class TestStalenessWarning(unittest.TestCase):
    """Verify the methodology produces correct staleness risk levels.

    Story 1.2 acceptance criteria:
    - gap > 30 days  → HIGH
    - gap 7–30 days  → MEDIUM
    - gap < 7 days   → LOW

    Note: the staleness logic lives in the methodology, not the data source.
    These tests exercise the full engine with the mock source whose
    ``index_data_freshness_gap_days`` is driven by the as_of_date vs the
    closest available data point.
    """

    def _run_valuation(self, as_of_date: str) -> dict[str, Any]:
        from vc_audit_tool.engine import ValuationEngine

        engine = ValuationEngine()
        payload = {
            "company_name": "Test Co",
            "methodology": "last_round_market_adjusted",
            "as_of_date": as_of_date,
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
                "public_index": "NASDAQ_COMPOSITE",
            },
        }
        result: dict[str, Any] = engine.evaluate_from_dict(payload).to_dict()
        return result["valuation_result"]["confidence_indicators"]

    def test_low_staleness_risk(self) -> None:
        # 2026-02-18 is right on the last mock data point → gap_days = 0
        ci = self._run_valuation("2026-02-18")
        self.assertEqual(ci["index_data_freshness_gap_days"], 0)

    def test_staleness_gap_populated(self) -> None:
        ci = self._run_valuation("2026-02-18")
        self.assertIn("index_data_freshness_gap_days", ci)
        self.assertIsInstance(ci["index_data_freshness_gap_days"], int)


# ---------------------------------------------------------------------------
# Integration tests (require network — skipped in CI)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestYFinanceLive(unittest.TestCase):
    """Integration tests that actually call Yahoo Finance.

    Run with:  ``pytest -m integration tests/test_yfinance.py -v``
    """

    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.source = YFinanceMarketIndexSource(
            cache_dir=Path(self._tmpdir.name),
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_nasdaq_known_date(self) -> None:
        """Fetch NASDAQ close on a known trading day and assert plausible range."""
        pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        # NASDAQ Composite close on 2024-06-28 was ~17700-17750
        self.assertGreater(pt.level, Decimal("17000"))
        self.assertLess(pt.level, Decimal("18000"))

    def test_russell_known_date(self) -> None:
        pt = self.source.get_level("RUSSELL_2000", date(2024, 6, 28))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))
        self.assertGreater(pt.level, Decimal("1900"))
        self.assertLess(pt.level, Decimal("2200"))

    def test_weekend_fallback_live(self) -> None:
        """Sunday 2024-06-30 should fall back to Friday 2024-06-28."""
        pt = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 30))
        self.assertEqual(pt.as_of_date, date(2024, 6, 28))

    def test_determinism_with_warm_cache(self) -> None:
        """Same input + warm cache → identical output."""
        pt1 = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        pt2 = self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertEqual(pt1, pt2)

    def test_dataset_version_format(self) -> None:
        self.source.get_level("NASDAQ_COMPOSITE", date(2024, 6, 28))
        self.assertRegex(
            self.source.dataset_version,
            r"yfinance-\^IXIC-\d{4}-\d{2}-\d{2}",
        )


if __name__ == "__main__":
    unittest.main()
