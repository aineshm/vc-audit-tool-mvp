"""Tests for the CLI entry point (cli.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a subprocess so we capture exit codes and stdout."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    return subprocess.run(
        [sys.executable, "-m", "vc_audit_tool.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )


class CLITests(unittest.TestCase):
    """Test the CLI end-to-end via subprocess."""

    # ── Happy path ──

    def test_last_round_example_succeeds(self) -> None:
        result = _run_cli("--request-file", "examples/last_round_request.json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("estimated_fair_value", data)

    def test_comps_example_succeeds(self) -> None:
        result = _run_cli("--request-file", "examples/comps_request.json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("estimated_fair_value", data)

    def test_pretty_flag_produces_indented_json(self) -> None:
        result = _run_cli("--request-file", "examples/last_round_request.json", "--pretty")
        self.assertEqual(result.returncode, 0)
        # Pretty-printed JSON starts with {\n  "
        self.assertTrue(result.stdout.startswith("{\n"))

    # ── Error paths ──

    def test_missing_file_returns_exit_1(self) -> None:
        result = _run_cli("--request-file", "nonexistent.json")
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertIn("error", data)

    def test_malformed_json_file_returns_exit_1(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{{")
            f.flush()
            try:
                result = _run_cli("--request-file", f.name)
                self.assertEqual(result.returncode, 1)
                data = json.loads(result.stdout)
                self.assertIn("error", data)
            finally:
                os.unlink(f.name)

    def test_invalid_payload_returns_exit_1(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"company_name": "X"}, f)
            f.flush()
            try:
                result = _run_cli("--request-file", f.name)
                self.assertEqual(result.returncode, 1)
                data = json.loads(result.stdout)
                self.assertIn("error", data)
            finally:
                os.unlink(f.name)

    def test_no_args_prints_usage(self) -> None:
        result = _run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--request-file", result.stderr)


if __name__ == "__main__":
    unittest.main()
