"""HTTP-level tests for POST /research endpoint."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from vc_audit_tool.agent.state import ResearchResult
from vc_audit_tool.server import app

client = TestClient(app)


def _make_complete_result(methodology: str = "last_round_market_adjusted") -> ResearchResult:
    return ResearchResult(
        assembled_request={
            "company_name": "TestCo",
            "methodology": methodology,
            "as_of_date": "2026-03-01",
            "inputs": {
                "last_post_money_valuation": 1_000_000_000,
                "last_round_date": "2025-06-01",
            },
        },
        research_metadata={"sources_consulted": ["SEC EDGAR", "DuckDuckGo"], "evidence_package": {}},
        missing_fields=[],
    )


def _make_incomplete_result() -> ResearchResult:
    return ResearchResult(
        assembled_request=None,
        research_metadata={"error": "insufficient data"},
        missing_fields=["last_post_money_valuation", "last_round_date"],
        best_available_methodology="comparable_companies",
        missing_for_best_available=["sector"],
        web_facts={"revenue_ltm": 50_000_000},
        error="Could not find valuation data",
    )


class TestResearchEndpoint:
    def test_missing_company_name_returns_400(self) -> None:
        resp = client.post("/research", content=json.dumps({}))
        assert resp.status_code == 400
        assert "company_name" in resp.json()["error"]

    def test_empty_company_name_returns_400(self) -> None:
        resp = client.post("/research", content=json.dumps({"company_name": ""}))
        assert resp.status_code == 400

    def test_invalid_json_returns_400(self) -> None:
        resp = client.post("/research", content=b"not json")
        assert resp.status_code == 400
        assert "JSON" in resp.json()["error"]

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_successful_research_returns_200(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_result()
        mock_cls.return_value = mock_agent
        resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 200
        data = resp.json()
        assert "valuation_result" in data
        assert "audit_metadata" in data
        assert "research_metadata" in data

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_incomplete_research_returns_422(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_incomplete_result()
        mock_cls.return_value = mock_agent
        resp = client.post("/research", content=json.dumps({"company_name": "ObscureCo"}))
        assert resp.status_code == 422
        data = resp.json()
        assert "error" in data
        assert "missing_fields" in data
        assert len(data["missing_fields"]) > 0

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_agent_crash_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = RuntimeError("unexpected failure")
        resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_research_passes_optional_fields(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_result()
        mock_cls.return_value = mock_agent
        resp = client.post("/research", content=json.dumps({
            "company_name": "TestCo",
            "methodology": "comparable_companies",
            "as_of_date": "2026-03-01",
            "description_hint": "AI safety lab",
        }))
        assert resp.status_code == 200
        mock_agent.run.assert_called_once_with(
            "TestCo",
            methodology="comparable_companies",
            as_of_date="2026-03-01",
            description_hint="AI safety lab",
        )
