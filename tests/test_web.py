"""Tests for the web UI HTTP layer."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from vc_audit_tool.store import ValuationStore
from vc_audit_tool.web import WebHandler

LAST_ROUND_PAYLOAD = {
    "company_name": "Basis AI",
    "methodology": "last_round_market_adjusted",
    "as_of_date": "2026-02-18",
    "inputs": {
        "last_post_money_valuation": 100000000,
        "last_round_date": "2024-06-30",
        "public_index": "NASDAQ_COMPOSITE",
    },
}


def _get(url: str) -> tuple[int, bytes]:
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url: str, body: dict) -> tuple[int, dict]:  # type: ignore[type-arg]
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestWebServer(unittest.TestCase):
    server: ThreadingHTTPServer
    port: int
    tmpdir: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmpdir.name) / "test_web.db"
        WebHandler.store = ValuationStore(db_path)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
        cls.port = cls.server.server_address[1]
        t = threading.Thread(target=cls.server.serve_forever, daemon=True)
        t.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        WebHandler.store.close()
        cls.tmpdir.cleanup()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ── GET routes ──

    def test_root_returns_html(self) -> None:
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn(b"VC Audit Tool", body)
        self.assertIn(b"</html>", body)

    def test_health(self) -> None:
        status, body = _get(self.base + "/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")

    def test_not_found(self) -> None:
        status, _ = _get(self.base + "/nope")
        self.assertEqual(status, 404)

    # ── POST /api/value ──

    def test_valuation_round_trip(self) -> None:
        status, data = _post(self.base + "/api/value", LAST_ROUND_PAYLOAD)
        self.assertEqual(status, 200)
        self.assertEqual(data["valuation_result"]["company_name"], "Basis AI")
        self.assertIn("audit_metadata", data)
        rid = data["audit_metadata"]["request_id"]

        # verify persisted
        status2, body2 = _get(self.base + f"/api/runs/{rid}")
        self.assertEqual(status2, 200)
        data2 = json.loads(body2)
        self.assertEqual(data2["valuation_result"]["company_name"], "Basis AI")

    def test_runs_list(self) -> None:
        # insert a run first so we're not order-dependent
        _post(self.base + "/api/value", LAST_ROUND_PAYLOAD)
        status, body = _get(self.base + "/api/runs")
        self.assertEqual(status, 200)
        runs = json.loads(body)
        self.assertIsInstance(runs, list)
        self.assertGreaterEqual(len(runs), 1)

    def test_run_not_found(self) -> None:
        status, _ = _get(self.base + "/api/runs/nonexistent")
        self.assertEqual(status, 404)

    def test_bad_json(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/value",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_validation_error(self) -> None:
        status, data = _post(self.base + "/api/value", {"methodology": "bad"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
