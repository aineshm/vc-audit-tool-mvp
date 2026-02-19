"""Integration tests for the FastAPI server (server.py)."""

from __future__ import annotations

import json
import unittest

from starlette.testclient import TestClient

from vc_audit_tool.server import app


class ServerIntegrationTests(unittest.TestCase):
    """Hit the FastAPI app via TestClient -- no real socket needed."""

    client: TestClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    # -- Health --

    def test_health_endpoint(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    # -- Successful valuation --

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
        resp = self.client.post("/value", content=json.dumps(payload))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("valuation_result", data)
        self.assertIn("audit_metadata", data)
        self.assertIn("estimated_fair_value", data["valuation_result"])

    # -- Route errors --

    def test_get_unknown_route_returns_404(self) -> None:
        resp = self.client.get("/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_post_wrong_route_returns_404(self) -> None:
        resp = self.client.post("/other", content=b"{}")
        self.assertEqual(resp.status_code, 404)

    # -- Bad request bodies --

    def test_post_invalid_json_returns_400(self) -> None:
        resp = self.client.post("/value", content=b"not json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_post_empty_body_returns_400(self) -> None:
        resp = self.client.post("/value", content=b"")
        self.assertEqual(resp.status_code, 400)

    def test_post_missing_fields_returns_400(self) -> None:
        resp = self.client.post("/value", content=json.dumps({"company_name": "X"}))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_post_unknown_methodology_returns_400(self) -> None:
        payload = {
            "company_name": "X",
            "methodology": "magic",
            "inputs": {},
            "as_of_date": "2026-02-18",
        }
        resp = self.client.post("/value", content=json.dumps(payload))
        self.assertEqual(resp.status_code, 400)

    # -- Response contract --

    def test_response_content_type_is_json(self) -> None:
        resp = self.client.get("/health")
        self.assertIn("application/json", resp.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
