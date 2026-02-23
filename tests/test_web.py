"""Tests for web UI routes in the FastAPI server."""

from __future__ import annotations

import json
import re
import unittest

from starlette.testclient import TestClient

from vc_audit_tool.server import app

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


class TestWebRoutes(unittest.TestCase):
    """Test the web-facing routes via TestClient."""

    client: TestClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    # -- GET / --

    def test_root_returns_html(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("VC Audit Tool", resp.text)

    def test_root_defaults_to_research_first_ui(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Research Mode (Default)", resp.text)
        self.assertIn("Run /research", resp.text)
        self.assertIn("Run /reconcile", resp.text)
        self.assertIn("Advanced Manual Mode (/api/value)", resp.text)
        self.assertIn("Run manual /api/value", resp.text)

    def test_root_research_mode_does_not_render_manual_comps_sector(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        match = re.search(
            r'<section class="mode-card" id="research-mode">(.*?)</section>',
            resp.text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertNotIn('id="cc_sector"', match.group(1))

    def test_root_manual_mode_keeps_comps_sector(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        match = re.search(
            r'<details class="manual-mode" id="manual-mode">(.*?)</details>',
            resp.text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn('id="cc_sector"', match.group(1))

    def test_health(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_not_found(self) -> None:
        resp = self.client.get("/nope")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/value --

    def test_valuation_round_trip(self) -> None:
        resp = self.client.post("/api/value", content=json.dumps(LAST_ROUND_PAYLOAD))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["valuation_result"]["company_name"], "Basis AI")
        self.assertIn("audit_metadata", data)
        rid = data["audit_metadata"]["request_id"]

        # verify persisted
        resp2 = self.client.get(f"/api/runs/{rid}")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["valuation_result"]["company_name"], "Basis AI")

    def test_runs_list(self) -> None:
        self.client.post("/api/value", content=json.dumps(LAST_ROUND_PAYLOAD))
        resp = self.client.get("/api/runs")
        self.assertEqual(resp.status_code, 200)
        runs = resp.json()
        self.assertIsInstance(runs, list)
        self.assertGreaterEqual(len(runs), 1)

    def test_run_not_found(self) -> None:
        resp = self.client.get("/api/runs/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_bad_json(self) -> None:
        resp = self.client.post("/api/value", content=b"not json")
        self.assertEqual(resp.status_code, 400)

    def test_validation_error(self) -> None:
        resp = self.client.post("/api/value", content=json.dumps({"methodology": "bad"}))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_manual_comps_without_sector_returns_validation_error(self) -> None:
        payload = {
            "company_name": "Basis AI",
            "methodology": "comparable_companies",
            "as_of_date": "2026-02-23",
            "inputs": {
                "revenue_ltm": 10000000,
                "private_company_discount_pct": 20,
            },
        }
        resp = self.client.post("/api/value", content=json.dumps(payload))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())


if __name__ == "__main__":
    unittest.main()
