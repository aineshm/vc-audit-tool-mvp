"""HTTP-level tests for POST /reconcile endpoint."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from vc_audit_tool.agent.state import ResearchResult
from vc_audit_tool.server import app

client = TestClient(app)


def _make_complete_research() -> ResearchResult:
    return ResearchResult(
        assembled_request={
            "company_name": "GrowthCo",
            "methodology": "last_round_market_adjusted",
            "as_of_date": "2026-03-01",
            "inputs": {
                "last_post_money_valuation": 2_000_000_000,
                "last_round_date": "2025-06-01",
                "revenue_ltm": 100_000_000,
            },
        },
        research_metadata={
            "sources_consulted": ["SEC EDGAR", "DuckDuckGo"],
            "evidence_package": {"consensus_strength": "MODERATE"},
        },
        missing_fields=[],
        company_profile=None,
    )


def _make_incomplete_research() -> ResearchResult:
    return ResearchResult(
        assembled_request=None,
        research_metadata={"error": "insufficient data"},
        missing_fields=["last_post_money_valuation"],
    )


class TestReconcileEndpoint:
    def test_missing_company_name_returns_400(self) -> None:
        resp = client.post("/reconcile", content=json.dumps({}))
        assert resp.status_code == 400
        assert "company_name" in resp.json()["error"]

    def test_invalid_json_returns_400(self) -> None:
        resp = client.post("/reconcile", content=b"not json")
        assert resp.status_code == 400

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_incomplete_research_returns_422(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_incomplete_research()
        mock_cls.return_value = mock_agent
        resp = client.post("/reconcile", content=json.dumps({"company_name": "ObscureCo"}))
        assert resp.status_code == 422
        data = resp.json()
        assert "error" in data
        assert "missing_fields" in data

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_agent_crash_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = RuntimeError("boom")
        resp = client.post("/reconcile", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500

    @patch("vc_audit_tool.reconciliation.engine.ReconciliationEngine")
    @patch("vc_audit_tool.agent.research.CompanyResearchAgent")
    def test_successful_reconcile_returns_200(self, mock_agent_cls: MagicMock, mock_recon_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_research()
        mock_agent_cls.return_value = mock_agent

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "concluded_value": {
                "point_estimate": 1_800_000_000,
                "range_low": 1_400_000_000,
                "range_high": 2_200_000_000,
                "currency": "USD",
                "as_of_date": "2026-03-01",
            },
            "methodology_results": {},
            "reconciliation": {"divergence_flag": False},
        }
        mock_result.methodology_results = {"last_round_market_adjusted": {}}
        mock_recon_inst = MagicMock()
        mock_recon_inst.value.return_value = mock_result
        mock_recon_cls.return_value = mock_recon_inst

        resp = client.post(
            "/reconcile",
            content=json.dumps({"company_name": "GrowthCo", "as_of_date": "2026-03-01"}),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "concluded_value" in data
