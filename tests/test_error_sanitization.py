"""Tests that error responses never leak internal details."""
from __future__ import annotations

import json
from unittest.mock import patch

from starlette.testclient import TestClient

from vc_audit_tool.server import app

client = TestClient(app)


class TestResearchErrorSanitization:
    def test_research_agent_crash_returns_generic_500(self) -> None:
        with patch(
            "vc_audit_tool.agent.research.CompanyResearchAgent",
            side_effect=RuntimeError("/Users/dev/.venv/lib/python3.10/site-packages/langgraph/core.py line 42"),
        ):
            resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
            assert resp.status_code == 500
            body = resp.json()
            assert "error" in body
            assert "/Users/" not in body["error"]
            assert "site-packages" not in body["error"]
            assert "Traceback" not in body["error"]

    def test_reconcile_agent_crash_returns_generic_500(self) -> None:
        with patch(
            "vc_audit_tool.agent.research.CompanyResearchAgent",
            side_effect=RuntimeError("sqlite3.OperationalError: database is locked"),
        ):
            resp = client.post("/reconcile", content=json.dumps({"company_name": "TestCo"}))
            assert resp.status_code == 500
            body = resp.json()
            assert "error" in body
            assert "database is locked" not in body["error"]
