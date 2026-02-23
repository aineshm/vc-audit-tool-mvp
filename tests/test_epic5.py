"""Tests for Epic 5 — cache management and confidence reporting.

Story 5.1:  cache list / cache clear
Story 5.2:  confidence report
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vc_audit_tool.cache import (
    _parse_iso,
    _retrieved_at_from_json,
    clear_cache,
    list_cache,
)
from vc_audit_tool.confidence import (
    _classify,
    format_confidence_report,
)
from vc_audit_tool.store import ValuationStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cache_file(
    directory: Path,
    name: str,
    retrieved_at: str | None = None,
    payload: dict[str, object] | None = None,
) -> Path:
    """Create a fake cache JSON file with optional ``retrieved_at``."""
    directory.mkdir(parents=True, exist_ok=True)
    data = payload or {}
    if retrieved_at is not None:
        data["retrieved_at"] = retrieved_at
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    env["VC_AUDIT_MOCK"] = "1"  # keep CLI subprocess tests deterministic/offline
    return subprocess.run(
        [sys.executable, "-m", "vc_audit_tool.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )


# ===================================================================
# Story 5.1 — Cache management
# ===================================================================


class TestCacheListUnit(unittest.TestCase):
    """Unit tests for :func:`list_cache`."""

    def test_empty_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            summary = list_cache(data_root=Path(td))
            self.assertEqual(summary.total_files, 0)
            self.assertEqual(summary.total_bytes, 0)
            self.assertEqual(summary.entries, ())

    def test_discovers_files_in_known_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ts = "2025-06-01T00:00:00+00:00"
            _make_cache_file(root / "yfinance_cache", "aapl", retrieved_at=ts)
            _make_cache_file(root / "edgar_cache", "sic_7372", retrieved_at=ts)
            # Unknown subdir should be ignored
            _make_cache_file(root / "unknown_dir", "junk", retrieved_at=ts)

            summary = list_cache(data_root=root)
            self.assertEqual(summary.total_files, 2)
            sources = {e.source for e in summary.entries}
            self.assertEqual(sources, {"yfinance_cache", "edgar_cache"})

    def test_file_size_is_correct(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _make_cache_file(
                root / "form_d_cache",
                "anthropic",
                retrieved_at="2025-01-01T00:00:00+00:00",
            )
            expected_size = p.stat().st_size
            summary = list_cache(data_root=root)
            self.assertEqual(summary.total_bytes, expected_size)
            self.assertEqual(summary.entries[0].size_bytes, expected_size)

    def test_retrieved_at_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ts = "2025-03-15T12:30:00+00:00"
            _make_cache_file(root / "usaspending_cache", "x", retrieved_at=ts)
            summary = list_cache(data_root=root)
            self.assertEqual(summary.entries[0].retrieved_at, ts)

    def test_missing_retrieved_at_returns_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cache_file(root / "yfinance_cache", "no_ts")
            summary = list_cache(data_root=root)
            self.assertEqual(summary.entries[0].retrieved_at, "")


class TestCacheClearUnit(unittest.TestCase):
    """Unit tests for :func:`clear_cache`."""

    def test_clear_all_removes_everything(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cache_file(root / "yfinance_cache", "a", retrieved_at="2025-01-01T00:00:00+00:00")
            _make_cache_file(root / "edgar_cache", "b", retrieved_at="2025-06-01T00:00:00+00:00")
            removed = clear_cache(older_than=None, data_root=root)
            self.assertEqual(len(removed), 2)
            # Verify files are gone
            summary = list_cache(data_root=root)
            self.assertEqual(summary.total_files, 0)

    def test_clear_older_than_filters_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
            new_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            _make_cache_file(root / "yfinance_cache", "old", retrieved_at=old_ts)
            _make_cache_file(root / "yfinance_cache", "new", retrieved_at=new_ts)

            removed = clear_cache(older_than=timedelta(days=30), data_root=root)
            self.assertEqual(len(removed), 1)
            self.assertIn("old", removed[0].name)
            # New file should still exist
            summary = list_cache(data_root=root)
            self.assertEqual(summary.total_files, 1)
            self.assertIn("new", summary.entries[0].path.name)

    def test_clear_older_than_skips_files_without_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cache_file(root / "yfinance_cache", "no_ts")
            removed = clear_cache(older_than=timedelta(days=1), data_root=root)
            self.assertEqual(len(removed), 0)
            # File should still exist
            summary = list_cache(data_root=root)
            self.assertEqual(summary.total_files, 1)

    def test_clear_empty_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            removed = clear_cache(older_than=None, data_root=Path(td))
            self.assertEqual(len(removed), 0)


class TestCacheHelpers(unittest.TestCase):
    """Unit tests for cache helper functions."""

    def test_parse_iso_valid(self) -> None:
        dt = _parse_iso("2025-06-15T12:30:00+00:00")
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(dt.year, 2025)
        self.assertEqual(dt.month, 6)

    def test_parse_iso_z_suffix(self) -> None:
        dt = _parse_iso("2025-06-15T12:30:00Z")
        self.assertIsNotNone(dt)

    def test_parse_iso_empty_returns_none(self) -> None:
        self.assertIsNone(_parse_iso(""))

    def test_parse_iso_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_iso("not-a-date"))

    def test_retrieved_at_from_valid_json(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"retrieved_at": "2025-01-01T00:00:00+00:00", "data": []}, f)
            f.flush()
            try:
                ts = _retrieved_at_from_json(Path(f.name))
                self.assertEqual(ts, "2025-01-01T00:00:00+00:00")
            finally:
                os.unlink(f.name)

    def test_retrieved_at_from_json_missing_key(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"data": []}, f)
            f.flush()
            try:
                ts = _retrieved_at_from_json(Path(f.name))
                self.assertEqual(ts, "")
            finally:
                os.unlink(f.name)

    def test_retrieved_at_from_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{{invalid")
            f.flush()
            try:
                ts = _retrieved_at_from_json(Path(f.name))
                self.assertEqual(ts, "")
            finally:
                os.unlink(f.name)


# ===================================================================
# Story 5.2 — Confidence report
# ===================================================================

_SAMPLE_RESULT: dict[str, object] = {
    "valuation_result": {
        "company_name": "Acme Corp",
        "methodology": "last_round_market_adjusted",
        "as_of_date": "2025-06-15",
        "estimated_fair_value": {"amount": 150_000_000.0, "currency": "USD"},
        "assumptions": [],
        "inputs_used": {},
        "citations": [],
        "derivation_steps": [],
        "confidence_indicators": {
            "staleness_risk": "HIGH – last round >12 months ago",
            "index_data_freshness_gap_days": 45,
            "peer_set_quality": "MEDIUM",
        },
    },
    "audit_metadata": {
        "request_id": "test-uuid-1234",
        "generated_at_utc": "2025-06-15T00:00:00+00:00",
        "engine_version": "0.1.0",
    },
}


class TestClassify(unittest.TestCase):
    """Unit tests for confidence severity classification."""

    def test_high_string(self) -> None:
        self.assertEqual(_classify("HIGH – last round >12 months ago"), "HIGH")

    def test_stale_triggers_high(self) -> None:
        self.assertEqual(_classify("stale data detected"), "HIGH")

    def test_medium_string(self) -> None:
        self.assertEqual(_classify("MEDIUM"), "MEDIUM")

    def test_moderate_triggers_medium(self) -> None:
        self.assertEqual(_classify("moderate risk"), "MEDIUM")

    def test_low_string(self) -> None:
        self.assertEqual(_classify("LOW"), "LOW")

    def test_numeric_high(self) -> None:
        self.assertEqual(_classify(400), "HIGH")

    def test_numeric_medium(self) -> None:
        self.assertEqual(_classify(200), "MEDIUM")

    def test_numeric_low(self) -> None:
        self.assertEqual(_classify(10), "LOW")


class TestFormatConfidenceReport(unittest.TestCase):
    """Unit tests for the report formatter."""

    def test_report_contains_company_name(self) -> None:
        report = format_confidence_report(_SAMPLE_RESULT)  # type: ignore[arg-type]
        self.assertIn("Acme Corp", report)

    def test_report_contains_methodology(self) -> None:
        report = format_confidence_report(_SAMPLE_RESULT)  # type: ignore[arg-type]
        self.assertIn("last_round_market_adjusted", report)

    def test_report_contains_all_indicators(self) -> None:
        report = format_confidence_report(_SAMPLE_RESULT)  # type: ignore[arg-type]
        self.assertIn("Staleness Risk", report)
        self.assertIn("Index Data Freshness Gap Days", report)
        self.assertIn("Peer Set Quality", report)

    def test_report_contains_severity_labels(self) -> None:
        report = format_confidence_report(_SAMPLE_RESULT)  # type: ignore[arg-type]
        self.assertIn("[HIGH]", report)
        self.assertIn("[MEDIUM]", report)

    def test_empty_indicators(self) -> None:
        result = {
            "valuation_result": {
                "company_name": "X",
                "methodology": "comps",
                "as_of_date": "2025-01-01",
                "confidence_indicators": {},
            },
            "audit_metadata": {"request_id": "abc"},
        }
        report = format_confidence_report(result)
        self.assertIn("No confidence indicators", report)

    def test_missing_indicators_key(self) -> None:
        result = {
            "valuation_result": {"company_name": "X"},
            "audit_metadata": {"request_id": "abc"},
        }
        report = format_confidence_report(result)
        self.assertIn("No confidence indicators", report)


class TestConfidenceReportStore(unittest.TestCase):
    """Test confidence report lookup from SQLite store."""

    def setUp(self) -> None:
        self._tmp_fd, self._tmp_name = tempfile.mkstemp(suffix=".db")
        os.close(self._tmp_fd)
        self.store = ValuationStore(db_path=Path(self._tmp_name))

    def tearDown(self) -> None:
        self.store.close()
        os.unlink(self._tmp_name)

    def test_lookup_existing_run(self) -> None:
        from vc_audit_tool.confidence import confidence_report_for_request_id

        self.store.save(_SAMPLE_RESULT)  # type: ignore[arg-type]
        report = confidence_report_for_request_id("test-uuid-1234", store=self.store)
        self.assertIn("Acme Corp", report)
        self.assertIn("[HIGH]", report)

    def test_lookup_missing_run_raises(self) -> None:
        from vc_audit_tool.confidence import confidence_report_for_request_id

        with self.assertRaises(KeyError):
            confidence_report_for_request_id("nonexistent-id", store=self.store)


# ===================================================================
# CLI integration tests (subprocess)
# ===================================================================


class TestCacheCLI(unittest.TestCase):
    """Test ``vc-audit cache list`` and ``vc-audit cache clear`` via subprocess."""

    def test_cache_list_runs(self) -> None:
        result = _run_cli("cache", "list")
        self.assertEqual(result.returncode, 0)

    def test_cache_clear_all_runs(self) -> None:
        result = _run_cli("cache", "clear", "--all")
        self.assertEqual(result.returncode, 0)

    def test_cache_clear_older_than_runs(self) -> None:
        result = _run_cli("cache", "clear", "--older-than", "30d")
        self.assertEqual(result.returncode, 0)

    def test_cache_clear_no_flag_errors(self) -> None:
        result = _run_cli("cache", "clear")
        self.assertNotEqual(result.returncode, 0)


class TestConfidenceCLI(unittest.TestCase):
    """Test ``vc-audit confidence`` via subprocess."""

    def test_confidence_missing_id_errors(self) -> None:
        result = _run_cli("confidence", "nonexistent-uuid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error", result.stdout)


class TestValueSubcommandCLI(unittest.TestCase):
    """Verify the ``value`` subcommand still works end-to-end."""

    def test_value_subcommand(self) -> None:
        result = _run_cli("value", "--request-file", "examples/last_round_request.json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("valuation_result", data)


if __name__ == "__main__":
    unittest.main()
