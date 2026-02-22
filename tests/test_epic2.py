"""Tests for Epic 2 — Real Comparable Companies Provider.

Suite is split into four groups matching the four stories:
  1. **Story 2.3** — ``YFinanceMetricsFetcher`` unit tests (offline, mocked)
  2. **Story 2.1** — ``EdgarCompanyUniverse`` unit tests (offline, mocked)
  3. **Story 2.2** — ``EmbeddingCompsRanker`` unit tests (offline, mocked)
  4. **Story 2.4** — ``EdgarYFinanceComparableCompanySource`` integration tests
     (offline — all sub-components mocked)

  Plus integration tests that hit real APIs (marked ``@pytest.mark.integration``).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vc_audit_tool.data_sources.edgar_comps import EdgarYFinanceComparableCompanySource
from vc_audit_tool.data_sources.edgar_universe import EdgarCompany, EdgarCompanyUniverse
from vc_audit_tool.data_sources.embedding_ranker import EmbeddingCompsRanker, RankedCompany
from vc_audit_tool.data_sources.mock import ComparableCompany
from vc_audit_tool.data_sources.yfinance_metrics import (
    TickerMetrics,
    YFinanceMetricsFetcher,
    _ensure_yf,
)
from vc_audit_tool.exceptions import DataSourceError
from vc_audit_tool.interfaces import ComparableCompanySource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_yf_info(
    ticker: str = "MSFT",
    name: str = "Microsoft Corporation",
    ev: int = 2_900_000_000_000,
    rev: int = 300_000_000_000,
    ev_rev: float = 9.67,
    mcap: int = 2_800_000_000_000,
    sector: str = "Technology",
    industry: str = "Software - Infrastructure",
    summary: str = "Microsoft develops software and cloud services.",
) -> dict[str, Any]:
    """Build a fake yfinance .info dict."""
    return {
        "shortName": name,
        "enterpriseValue": ev,
        "totalRevenue": rev,
        "enterpriseToRevenue": ev_rev,
        "marketCap": mcap,
        "sector": sector,
        "industry": industry,
        "longBusinessSummary": summary,
        "regularMarketPrice": 420.0,
    }


_SAMPLE_EDGAR_ATOM = """\
<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:tag:www.sec.gov:cik=0001640147</id>
    <content type="text/xml">
      <company-info>
        <cik>0001640147</cik>
        <name>Snowflake Inc.</name>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
  <entry>
    <id>urn:tag:www.sec.gov:cik=0001561550</id>
    <content type="text/xml">
      <company-info>
        <cik>0001561550</cik>
        <name>MongoDB Inc.</name>
        <sic>7372</sic>
      </company-info>
    </content>
  </entry>
</feed>
"""

_SAMPLE_TICKERS_JSON: dict[str, dict[str, Any]] = {
    "0": {"cik_str": 1640147, "ticker": "SNOW", "title": "Snowflake Inc."},
    "1": {"cik_str": 1561550, "ticker": "MDB", "title": "MongoDB Inc."},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


# ═══════════════════════════════════════════════════════════════════════════
# Story 2.3 — YFinanceMetricsFetcher (offline)
# ═══════════════════════════════════════════════════════════════════════════


class TestYFinanceMetricsProtocol(unittest.TestCase):
    """Sanity: TickerMetrics dataclass behaves as expected."""

    def test_has_valid_multiple_true(self) -> None:
        m = TickerMetrics(
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            industry="Software",
            enterprise_value=Decimal("2900000000000"),
            total_revenue=Decimal("300000000000"),
            ev_to_revenue=Decimal("9.67"),
            market_cap=Decimal("2800000000000"),
            business_summary="Microsoft develops software.",
            retrieved_at="2026-01-15T00:00:00Z",
        )
        self.assertTrue(m.has_valid_multiple)

    def test_has_valid_multiple_false_when_missing_rev(self) -> None:
        m = TickerMetrics(
            ticker="XYZ",
            company_name="Bogus",
            sector="",
            industry="",
            enterprise_value=Decimal("1000"),
            total_revenue=None,
            ev_to_revenue=None,
            market_cap=None,
            business_summary="",
            retrieved_at="",
        )
        self.assertFalse(m.has_valid_multiple)


class TestYFinanceMetricsFetcher(unittest.TestCase):
    """All tests mock out yfinance to run offline."""

    def setUp(self) -> None:
        _ensure_yf()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self.fetcher = YFinanceMetricsFetcher(cache_dir=self.cache_dir)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patch_ticker_info(self, info: dict[str, Any]) -> Any:
        mock_ticker = MagicMock()
        mock_ticker.info = info
        return patch(
            "vc_audit_tool.data_sources.yfinance_metrics.yf.Ticker",
            return_value=mock_ticker,
        )

    def test_fetch_returns_ticker_metrics(self) -> None:
        with self._patch_ticker_info(_make_yf_info()):
            m = self.fetcher.fetch("MSFT")
        self.assertEqual(m.ticker, "MSFT")
        self.assertEqual(m.company_name, "Microsoft Corporation")
        self.assertEqual(m.ev_to_revenue, Decimal("9.67"))

    def test_fetch_uppercases_ticker(self) -> None:
        with self._patch_ticker_info(_make_yf_info(ticker="msft")):
            m = self.fetcher.fetch("msft")
        self.assertEqual(m.ticker, "MSFT")

    def test_fetch_caches_to_disk(self) -> None:
        with self._patch_ticker_info(_make_yf_info()):
            self.fetcher.fetch("MSFT")
        today = date.today().isoformat()
        cache_file = self.cache_dir / f"MSFT_{today}.json"
        self.assertTrue(cache_file.exists())
        data = json.loads(cache_file.read_text())
        self.assertEqual(data["ticker"], "MSFT")

    def test_second_call_uses_cache(self) -> None:
        with self._patch_ticker_info(_make_yf_info()) as mock_cls:
            self.fetcher.fetch("MSFT")
            self.fetcher.fetch("MSFT")
            self.assertEqual(mock_cls.call_count, 1)

    def test_missing_ev_produces_none(self) -> None:
        info = _make_yf_info()
        info["enterpriseValue"] = None
        with self._patch_ticker_info(info):
            m = self.fetcher.fetch("MSFT")
        self.assertIsNone(m.enterprise_value)
        self.assertFalse(m.has_valid_multiple)

    def test_missing_revenue_produces_none(self) -> None:
        info = _make_yf_info()
        info["totalRevenue"] = None
        with self._patch_ticker_info(info):
            m = self.fetcher.fetch("MSFT")
        self.assertIsNone(m.total_revenue)
        self.assertFalse(m.has_valid_multiple)

    def test_no_data_raises(self) -> None:
        info = {"regularMarketPrice": None}
        with self._patch_ticker_info(info), self.assertRaises(DataSourceError):
            self.fetcher.fetch("BOGUS")

    def test_fetch_many_skips_failures(self) -> None:
        def side_effect(ticker: str) -> MagicMock:
            m = MagicMock()
            if ticker == "FAIL":
                m.info = {"regularMarketPrice": None}
            else:
                m.info = _make_yf_info(ticker=ticker)
            return m

        with patch(
            "vc_audit_tool.data_sources.yfinance_metrics.yf.Ticker",
            side_effect=side_effect,
        ):
            results = self.fetcher.fetch_many(["MSFT", "FAIL", "GOOGL"])
        self.assertEqual(len(results), 2)
        tickers = {m.ticker for m in results}
        self.assertIn("MSFT", tickers)
        self.assertIn("GOOGL", tickers)

    def test_dataset_version_set_after_fetch(self) -> None:
        with self._patch_ticker_info(_make_yf_info()):
            self.fetcher.fetch("MSFT")
        self.assertIn("yfinance-metrics", self.fetcher.dataset_version)

    def test_corrupt_cache_triggers_refetch(self) -> None:
        today = date.today().isoformat()
        cache_file = self.cache_dir / f"MSFT_{today}.json"
        cache_file.write_text("CORRUPT JSON", encoding="utf-8")
        with self._patch_ticker_info(_make_yf_info()):
            m = self.fetcher.fetch("MSFT")
        self.assertEqual(m.ticker, "MSFT")

    def test_computes_ev_rev_when_missing(self) -> None:
        info = _make_yf_info()
        info["enterpriseToRevenue"] = None
        info["enterpriseValue"] = 1000
        info["totalRevenue"] = 100
        with self._patch_ticker_info(info):
            m = self.fetcher.fetch("MSFT")
        self.assertIsNotNone(m.ev_to_revenue)
        self.assertEqual(m.ev_to_revenue, Decimal("10.000"))


# ═══════════════════════════════════════════════════════════════════════════
# Story 2.1 — EdgarCompanyUniverse (offline)
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgarCompanyUniverse(unittest.TestCase):
    """All tests mock out httpx to run offline.

    ``httpx`` is imported lazily inside the methods, so we patch
    ``httpx.get`` at the module level rather than trying to patch a
    module-scope attribute that doesn't exist.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self.universe = EdgarCompanyUniverse(cache_dir=self.cache_dir)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_tickers_cache(self) -> None:
        """Pre-seed the company_tickers.json disk cache."""
        from datetime import datetime, timezone

        path = self.cache_dir / "company_tickers.json"
        payload = {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(_SAMPLE_TICKERS_JSON),
            "data": _SAMPLE_TICKERS_JSON,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _mock_ciks_response(self) -> MagicMock:
        """Build a mock httpx.get response for the EDGAR company search."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_EDGAR_ATOM
        return mock_resp

    def test_list_by_sic_returns_companies(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            companies = self.universe.list_by_sic("7372")
        self.assertGreaterEqual(len(companies), 1)
        tickers = {c.ticker for c in companies}
        self.assertTrue(tickers.issubset({"SNOW", "MDB", "MSFT"}))

    def test_edgar_company_has_required_fields(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            companies = self.universe.list_by_sic("7372")
        c = companies[0]
        self.assertIsInstance(c, EdgarCompany)
        self.assertTrue(c.ticker)
        self.assertTrue(c.cik)
        self.assertTrue(c.company_name)
        self.assertEqual(c.sic, "7372")

    def test_cache_written_after_fetch(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            self.universe.list_by_sic("7372")
        cache_path = self.cache_dir / "sic_7372.json"
        self.assertTrue(cache_path.exists())

    def test_cached_result_avoids_network(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            self.universe.list_by_sic("7372")
        # Second call should use cache — no httpx.get called
        with patch("httpx.get") as mock_get:
            result2 = self.universe.list_by_sic("7372")
            mock_get.assert_not_called()
        self.assertGreaterEqual(len(result2), 1)

    def test_dataset_version_set(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            self.universe.list_by_sic("7372")
        self.assertIn("edgar-sic-7372", self.universe.dataset_version)

    def test_sector_mapping_applied(self) -> None:
        self._seed_tickers_cache()
        with patch("httpx.get", return_value=self._mock_ciks_response()):
            companies = self.universe.list_by_sic("7372")
        for c in companies:
            self.assertEqual(c.sector, "enterprise_software")


# ═══════════════════════════════════════════════════════════════════════════
# Story 2.2 — EmbeddingCompsRanker (offline)
# ═══════════════════════════════════════════════════════════════════════════


class TestEmbeddingCompsRanker(unittest.TestCase):
    """Mock the SentenceTransformer model for offline tests."""

    def setUp(self) -> None:
        import numpy as np

        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self.ranker = EmbeddingCompsRanker(cache_dir=self.cache_dir)

        # Build fake embeddings — the "target" is at index 0
        # Candidates: one very similar (0.95), one medium (0.5), one low (0.1)
        self._fake_embeddings = np.array(
            [
                [1.0, 0.0, 0.0],  # target
                [0.95, 0.31, 0.0],  # similar
                [0.5, 0.87, 0.0],  # medium
                [0.0, 0.1, 1.0],  # irrelevant
            ],
            dtype=np.float32,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _mock_model(self) -> MagicMock:
        mock = MagicMock()
        mock.encode.return_value = self._fake_embeddings
        return mock

    def test_rank_returns_sorted_by_similarity(self) -> None:
        self.ranker._model = self._mock_model()
        candidates = [
            {"ticker": "A", "company_name": "Similar Inc", "description": "AI software"},
            {"ticker": "B", "company_name": "Medium LLC", "description": "cloud platform"},
            {"ticker": "C", "company_name": "Irrelevant Corp", "description": "fast food"},
        ]
        ranked = self.ranker.rank("enterprise AI software", candidates, top_k=3)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[0].ticker, "A")
        self.assertGreater(ranked[0].similarity, ranked[1].similarity)
        self.assertGreater(ranked[1].similarity, ranked[2].similarity)

    def test_rank_top_k_limits_results(self) -> None:
        self.ranker._model = self._mock_model()
        candidates = [
            {"ticker": "A", "company_name": "A", "description": "AI"},
            {"ticker": "B", "company_name": "B", "description": "cloud"},
            {"ticker": "C", "company_name": "C", "description": "food"},
        ]
        ranked = self.ranker.rank("AI", candidates, top_k=1)
        self.assertEqual(len(ranked), 1)

    def test_rank_empty_candidates(self) -> None:
        ranked = self.ranker.rank("target", [], top_k=5)
        self.assertEqual(ranked, [])

    def test_mean_similarity(self) -> None:
        items = [
            RankedCompany("A", "A", 0.9, ""),
            RankedCompany("B", "B", 0.7, ""),
        ]
        ms = self.ranker.mean_similarity(items)
        self.assertAlmostEqual(ms, 0.8, places=2)

    def test_peer_set_quality_high(self) -> None:
        items = [
            RankedCompany("A", "A", 0.9, ""),
            RankedCompany("B", "B", 0.85, ""),
        ]
        self.assertEqual(self.ranker.peer_set_quality(items), "HIGH")

    def test_peer_set_quality_medium(self) -> None:
        items = [
            RankedCompany("A", "A", 0.6, ""),
            RankedCompany("B", "B", 0.55, ""),
        ]
        self.assertEqual(self.ranker.peer_set_quality(items), "MEDIUM")

    def test_peer_set_quality_low(self) -> None:
        items = [
            RankedCompany("A", "A", 0.2, ""),
            RankedCompany("B", "B", 0.1, ""),
        ]
        self.assertEqual(self.ranker.peer_set_quality(items), "LOW")

    def test_dataset_version_contains_model_name(self) -> None:
        self.assertIn("all-MiniLM-L6-v2", self.ranker.dataset_version)

    def test_ranked_company_has_snippet(self) -> None:
        self.ranker._model = self._mock_model()
        candidates = [
            {"ticker": "A", "company_name": "A", "description": "long " * 100},
        ]
        import numpy as np

        self.ranker._model.encode.return_value = np.array(
            [[1.0, 0.0], [0.99, 0.14]], dtype=np.float32
        )
        ranked = self.ranker.rank("target", candidates, top_k=1)
        self.assertLessEqual(len(ranked[0].description_snippet), 200)


# ═══════════════════════════════════════════════════════════════════════════
# Story 2.4 — EdgarYFinanceComparableCompanySource (fully mocked)
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgarYFinanceProtocol(unittest.TestCase):
    """The composite source must satisfy ComparableCompanySource Protocol."""

    def test_isinstance_check(self) -> None:
        src = EdgarYFinanceComparableCompanySource()
        self.assertIsInstance(src, ComparableCompanySource)


class TestEdgarYFinanceCompositeOffline(unittest.TestCase):
    """Full offline tests: all three sub-components mocked."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_root = Path(self._tmpdir.name)

        # Mock EDGAR universe
        self.mock_edgar = MagicMock(spec=EdgarCompanyUniverse)
        self.mock_edgar.list_by_sic.return_value = [
            EdgarCompany("0001", "SNOW", "Snowflake Inc.", "7372", "enterprise_software", "NYSE"),
            EdgarCompany("0002", "MDB", "MongoDB Inc.", "7372", "enterprise_software", "NASDAQ"),
            EdgarCompany("0003", "DDOG", "Datadog Inc.", "7372", "enterprise_software", "NASDAQ"),
        ]
        self.mock_edgar.dataset_version = "edgar-sic-7372-test"

        # Mock metrics fetcher
        self.mock_metrics = MagicMock(spec=YFinanceMetricsFetcher)
        self.mock_metrics.dataset_version = "yfinance-metrics-test"
        self.mock_metrics.fetch_many.return_value = [
            TickerMetrics(
                "SNOW",
                "Snowflake",
                "Technology",
                "Software",
                Decimal("58000000000"),
                Decimal("4400000000"),
                Decimal("13.2"),
                Decimal("55000000000"),
                "Snowflake provides cloud data platform.",
                "2026-01-15",
            ),
            TickerMetrics(
                "MDB",
                "MongoDB",
                "Technology",
                "Software",
                Decimal("20000000000"),
                Decimal("2000000000"),
                Decimal("10.0"),
                Decimal("18000000000"),
                "MongoDB provides NoSQL database.",
                "2026-01-15",
            ),
            TickerMetrics(
                "DDOG",
                "Datadog",
                "Technology",
                "Software",
                Decimal("30000000000"),
                Decimal("2500000000"),
                Decimal("12.0"),
                Decimal("28000000000"),
                "Datadog provides monitoring and analytics.",
                "2026-01-15",
            ),
        ]

        # Mock embedding ranker
        self.mock_ranker = MagicMock(spec=EmbeddingCompsRanker)
        self.mock_ranker.rank.return_value = [
            RankedCompany("SNOW", "Snowflake", 0.92, "cloud data platform"),
            RankedCompany("DDOG", "Datadog", 0.85, "monitoring"),
        ]
        self.mock_ranker.dataset_version = "all-MiniLM-L6-v2-v1.0"

        self.source = EdgarYFinanceComparableCompanySource(
            edgar=self.mock_edgar,
            metrics=self.mock_metrics,
            ranker=self.mock_ranker,
            cache_root=self.cache_root,
            top_k=2,
            target_description="enterprise cloud data analytics",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_list_by_sector_returns_comps(self) -> None:
        comps = self.source.list_by_sector("enterprise_software")
        self.assertEqual(len(comps), 2)
        self.assertIsInstance(comps[0], ComparableCompany)

    def test_list_by_sector_tickers_match_ranking(self) -> None:
        comps = self.source.list_by_sector("enterprise_software")
        tickers = [c.ticker for c in comps]
        self.assertEqual(tickers, ["SNOW", "DDOG"])

    def test_list_by_sector_ev_to_revenue_from_yfinance(self) -> None:
        comps = self.source.list_by_sector("enterprise_software")
        # SNOW should have 13.2 from our mock metrics
        snow = next(c for c in comps if c.ticker == "SNOW")
        self.assertEqual(snow.ev_to_revenue, Decimal("13.2"))

    def test_list_by_sector_sets_dataset_version(self) -> None:
        self.source.list_by_sector("enterprise_software")
        self.assertIn("edgar", self.source.dataset_version)

    def test_list_by_sector_calls_edgar_ranker_metrics(self) -> None:
        self.source.list_by_sector("enterprise_software")
        self.mock_edgar.list_by_sic.assert_called()
        self.mock_metrics.fetch_many.assert_called_once()
        self.mock_ranker.rank.assert_called_once()

    def test_unknown_sector_raises(self) -> None:
        with self.assertRaises(DataSourceError) as ctx:
            self.source.list_by_sector("underwater_basket_weaving")
        self.assertIn("No SIC code mapping", str(ctx.exception))

    def test_warnings_when_metrics_missing(self) -> None:
        # Make one ticker return invalid metrics
        self.mock_metrics.fetch_many.return_value = [
            TickerMetrics(
                "SNOW",
                "Snowflake",
                "Technology",
                "Software",
                Decimal("58000000000"),
                Decimal("4400000000"),
                Decimal("13.2"),
                Decimal("55000000000"),
                "cloud data",
                "2026-01-15",
            ),
            TickerMetrics(
                "MDB",
                "MongoDB",
                "Technology",
                "Software",
                None,
                None,
                None,
                None,
                "database",
                "2026-01-15",
            ),
        ]
        self.source.list_by_sector("enterprise_software")
        self.assertTrue(any("excluded" in w for w in self.source.warnings))

    def test_list_by_tickers_returns_real_metrics(self) -> None:
        self.mock_metrics.fetch_many.return_value = [
            TickerMetrics(
                "MSFT",
                "Microsoft",
                "Technology",
                "Software",
                Decimal("2900000000000"),
                Decimal("300000000000"),
                Decimal("9.67"),
                Decimal("2800000000000"),
                "Microsoft develops software.",
                "2026-01-15",
            ),
        ]
        comps = self.source.list_by_tickers(["MSFT"])
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0].ticker, "MSFT")
        self.assertEqual(comps[0].ev_to_revenue, Decimal("9.67"))

    def test_list_by_tickers_missing_raises(self) -> None:
        self.mock_metrics.fetch_many.return_value = []
        with self.assertRaises(DataSourceError) as ctx:
            self.source.list_by_tickers(["MSFT"])
        self.assertIn("Missing", str(ctx.exception))

    def test_aggregate_multiple_median(self) -> None:
        comps = [
            ComparableCompany("A", "A", "s", Decimal("10")),
            ComparableCompany("B", "B", "s", Decimal("20")),
            ComparableCompany("C", "C", "s", Decimal("15")),
        ]
        result = EdgarYFinanceComparableCompanySource.aggregate_multiple(comps, "median")
        self.assertEqual(result, Decimal("15"))

    def test_aggregate_multiple_mean(self) -> None:
        comps = [
            ComparableCompany("A", "A", "s", Decimal("10")),
            ComparableCompany("B", "B", "s", Decimal("20")),
        ]
        result = EdgarYFinanceComparableCompanySource.aggregate_multiple(comps, "mean")
        self.assertEqual(result, Decimal("15"))

    def test_aggregate_unsupported_raises(self) -> None:
        with self.assertRaises(DataSourceError):
            EdgarYFinanceComparableCompanySource.aggregate_multiple([], "mode")

    def test_without_description_falls_back_to_market_cap(self) -> None:
        """When no target description is set, top-k by market cap."""
        source = EdgarYFinanceComparableCompanySource(
            edgar=self.mock_edgar,
            metrics=self.mock_metrics,
            ranker=self.mock_ranker,
            cache_root=self.cache_root,
            top_k=2,
            target_description="",  # no description
        )
        comps = source.list_by_sector("enterprise_software")
        self.assertEqual(len(comps), 2)
        # Should NOT have called the ranker
        self.mock_ranker.rank.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Engine integration — verify the real source plugs into the engine
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineWithRealComps(unittest.TestCase):
    """Verify the engine works with EdgarYFinanceComparableCompanySource."""

    def test_comps_methodology_with_real_source(self) -> None:
        """Mock all sub-components but wire through the real engine path."""
        from vc_audit_tool.engine import ValuationEngine

        mock_edgar = MagicMock(spec=EdgarCompanyUniverse)
        mock_edgar.list_by_sic.return_value = [
            EdgarCompany("0001", "SNOW", "Snowflake", "7372", "enterprise_software", ""),
            EdgarCompany("0002", "MDB", "MongoDB", "7372", "enterprise_software", ""),
        ]
        mock_edgar.dataset_version = "edgar-test"

        mock_metrics = MagicMock(spec=YFinanceMetricsFetcher)
        mock_metrics.dataset_version = "metrics-test"
        mock_metrics.fetch_many.return_value = [
            TickerMetrics(
                "SNOW",
                "Snowflake",
                "Tech",
                "SW",
                Decimal("58000000000"),
                Decimal("4400000000"),
                Decimal("13.2"),
                Decimal("55000000000"),
                "cloud data",
                "2026-01-15",
            ),
            TickerMetrics(
                "MDB",
                "MongoDB",
                "Tech",
                "SW",
                Decimal("20000000000"),
                Decimal("2000000000"),
                Decimal("10.0"),
                Decimal("18000000000"),
                "database",
                "2026-01-15",
            ),
        ]

        mock_ranker = MagicMock(spec=EmbeddingCompsRanker)
        mock_ranker.rank.return_value = [
            RankedCompany("SNOW", "Snowflake", 0.90, "cloud"),
            RankedCompany("MDB", "MongoDB", 0.80, "database"),
        ]

        comps_source = EdgarYFinanceComparableCompanySource(
            edgar=mock_edgar,
            metrics=mock_metrics,
            ranker=mock_ranker,
            top_k=2,
            target_description="cloud data analytics",
        )

        engine = ValuationEngine(comps_source=comps_source)
        payload = {
            "company_name": "Test Co",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-01",
            "inputs": {
                "revenue_ltm": 50_000_000,
                "sector": "enterprise_software",
                "private_company_discount_pct": 25,
            },
        }
        result = engine.evaluate_from_dict(payload)
        d = result.to_dict()

        self.assertIn("valuation_result", d)
        vr = d["valuation_result"]
        self.assertGreater(vr["estimated_fair_value"]["amount"], 0)
        self.assertIn("live", vr["confidence_indicators"]["data_source_type"])


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests (require network — skipped in CI)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestYFinanceMetricsLive(unittest.TestCase):
    """Live tests for YFinanceMetricsFetcher."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.fetcher = YFinanceMetricsFetcher(cache_dir=Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_msft_has_positive_ev_revenue(self) -> None:
        m = self.fetcher.fetch("MSFT")
        self.assertIsNotNone(m.ev_to_revenue)
        assert m.ev_to_revenue is not None
        self.assertGreater(m.ev_to_revenue, 0)

    def test_googl_has_positive_ev_revenue(self) -> None:
        m = self.fetcher.fetch("GOOGL")
        self.assertIsNotNone(m.ev_to_revenue)
        assert m.ev_to_revenue is not None
        self.assertGreater(m.ev_to_revenue, 0)

    def test_determinism_with_cache(self) -> None:
        m1 = self.fetcher.fetch("MSFT")
        m2 = self.fetcher.fetch("MSFT")
        self.assertEqual(m1, m2)


@pytest.mark.integration
class TestEdgarLive(unittest.TestCase):
    """Live tests for EdgarCompanyUniverse."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.universe = EdgarCompanyUniverse(cache_dir=Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_sic_7372_returns_companies(self) -> None:
        companies = self.universe.list_by_sic("7372")
        self.assertGreaterEqual(len(companies), 5)
        self.assertTrue(all(c.sic == "7372" for c in companies))

    def test_companies_have_tickers(self) -> None:
        companies = self.universe.list_by_sic("7372")
        with_tickers = [c for c in companies if c.ticker]
        self.assertGreaterEqual(len(with_tickers), 5)


@pytest.mark.integration
class TestEmbeddingRankerLive(unittest.TestCase):
    """Live tests for EmbeddingCompsRanker with real model."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.ranker = EmbeddingCompsRanker(cache_dir=Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_relevant_ranks_above_irrelevant(self) -> None:
        candidates = [
            {
                "ticker": "SNOW",
                "company_name": "Snowflake",
                "description": "Cloud data platform for enterprise analytics and data warehousing",
            },
            {
                "ticker": "MCD",
                "company_name": "McDonald's",
                "description": "Global fast food restaurant chain",
            },
        ]
        ranked = self.ranker.rank(
            "enterprise AI software for business analytics", candidates, top_k=2
        )
        self.assertEqual(ranked[0].ticker, "SNOW")
        self.assertGreater(ranked[0].similarity, ranked[1].similarity)


if __name__ == "__main__":
    unittest.main()
