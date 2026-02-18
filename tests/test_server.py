"""Integration tests for the HTTP API (server.py)."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vc_audit_tool.server import ValuationRequestHandler


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerIntegrationTests(unittest.TestCase):
    """Spin up a real HTTP server per test class and hit it with real requests."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    base_url: str

    @classmethod
    def setUpClass(cls) -> None:
        port = _find_free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", port), ValuationRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    # ── Health ──

    def test_health_endpoint(self) -> None:
        resp = urlopen(f"{self.base_url}/health")
        data = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["status"], "ok")

    # ── Successful valuation ──

    def test_post_valid_last_round(self) -> None:
        payload = {
            "company_name": "TestCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-02-18",
            "inputs": {
                "last_post_money_valuation": 100_000_000,
                "last_round_date": "2024-06-30",
            },
        }
        req = Request(
            f"{self.base_url}/value",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req)
        data = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertIn("estimated_fair_value", data)
        self.assertIn("audit_metadata", data)

    # ── Route errors ──

    def test_get_unknown_route_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            urlopen(f"{self.base_url}/nonexistent")
        self.assertEqual(ctx.exception.code, 404)

    def test_post_wrong_route_returns_404(self) -> None:
        req = Request(
            f"{self.base_url}/other",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 404)

    # ── Bad request bodies ──

    def test_post_invalid_json_returns_400(self) -> None:
        req = Request(
            f"{self.base_url}/value",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_post_empty_body_returns_400(self) -> None:
        req = Request(
            f"{self.base_url}/value",
            data=b"",
            headers={"Content-Type": "application/json", "Content-Length": "0"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_post_missing_fields_returns_400(self) -> None:
        req = Request(
            f"{self.base_url}/value",
            data=json.dumps({"company_name": "X"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_post_unknown_methodology_returns_400(self) -> None:
        payload = {
            "company_name": "X",
            "methodology": "magic",
            "inputs": {},
            "as_of_date": "2026-02-18",
        }
        req = Request(
            f"{self.base_url}/value",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    # ── Response contract ──

    def test_response_content_type_is_json(self) -> None:
        resp = urlopen(f"{self.base_url}/health")
        self.assertEqual(resp.headers["Content-Type"], "application/json")


if __name__ == "__main__":
    unittest.main()
