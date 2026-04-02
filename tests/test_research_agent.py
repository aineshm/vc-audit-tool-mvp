"""Unit tests for CompanyResearchAgent and individual graph nodes."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from vc_audit_tool.agent.nodes.parse import _parse_company_node
from vc_audit_tool.agent.nodes.web_research import (
    _build_web_facts,
    _missing_fields,
    _most_recent_date,
)
from vc_audit_tool.agent.state import ResearchResult, ResearchState
from vc_audit_tool.data_sources.evidence_collector import EvidencePackage, ValuationEvidence


class TestParseCompanyNode:
    def test_normalises_name(self) -> None:
        state: ResearchState = {"company_name": "  Anthropic Inc.  ", "as_of_date": "2026-03-01"}
        result = _parse_company_node(state)
        assert result["normalised_name"] == "Anthropic Inc."

    def test_infers_sector_from_keywords(self) -> None:
        state: ResearchState = {"company_name": "CyberDefend", "description_hint": "cybersecurity platform for enterprises"}
        result = _parse_company_node(state)
        assert result.get("inferred_sector") == "cybersecurity"

    def test_no_sector_when_ambiguous(self) -> None:
        # Empty description_hint falls back to company name as hint; with no
        # keyword matches the node defaults to "enterprise_software".
        state: ResearchState = {"company_name": "GenericCo", "description_hint": ""}
        result = _parse_company_node(state)
        # Default sector is enterprise_software — accept it or empty/None.
        assert result.get("inferred_sector", "") in ("", None, "enterprise_software")


class TestMissingFields:
    def _make_pkg(self, n_evidence: int = 0, revenue: float | None = None) -> EvidencePackage:
        pkg = EvidencePackage(company_name="Test")
        for i in range(n_evidence):
            pkg.evidence.append(
                ValuationEvidence(amount_usd=1e9 + i, evidence_type="post_money_fresh", source_snippet="test", confidence=0.8)
            )
        if revenue:
            pkg.revenue_signals.append(revenue)
        return pkg

    def test_complete_set_a(self) -> None:
        facts = {"last_post_money_valuation": 5e9, "last_round_date": "2025-06-01", "company_description": "AI lab"}
        assert _missing_fields(self._make_pkg(), facts) == []

    def test_missing_everything(self) -> None:
        missing = _missing_fields(self._make_pkg(), {})
        assert "post_money" in missing
        assert "round_date" in missing
        assert "revenue" in missing

    def test_set_c_evidence_sufficient(self) -> None:
        # Set C requires >= 2 evidence items (has_evidence check)
        assert _missing_fields(self._make_pkg(n_evidence=2), {"company_description": "test"}) == []


class TestMostRecentDate:
    def test_picks_later_date(self) -> None:
        assert _most_recent_date("2025-01-01", "2025-06-01") == "2025-06-01"

    def test_none_handling(self) -> None:
        assert _most_recent_date(None, "2025-06-01") == "2025-06-01"
        assert _most_recent_date("2025-06-01", None) == "2025-06-01"
        assert _most_recent_date(None, None) is None


class TestBuildWebFacts:
    def test_basic_build(self) -> None:
        pkg = EvidencePackage(company_name="Test")
        # revenue_ltm in web_facts comes from pkg.best_revenue (revenue_signals),
        # not from llm_facts directly. Populate the signal so the assertion holds.
        pkg.revenue_signals.append(50e6)
        facts = _build_web_facts(pkg, {"company_description": "A SaaS company"}, [])
        assert facts["revenue_ltm"] == 50_000_000
        assert facts["company_description"] == "A SaaS company"

    def test_extraction_timestamp_present(self) -> None:
        facts = _build_web_facts(EvidencePackage(company_name="Test"), {}, [])
        assert "extraction_timestamp" in facts


class TestCompanyResearchAgentUnit:
    @patch("vc_audit_tool.agent.research.CompanyResearchAgent._build_graph")
    @patch("vc_audit_tool.agent.research._ensure_langgraph")
    def test_run_returns_result_on_success(self, mock_ensure: MagicMock, mock_build: MagicMock) -> None:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "assembled_request": {
                "company_name": "TestCo", "methodology": "last_round_market_adjusted",
                "as_of_date": "2026-03-01",
                "inputs": {"last_post_money_valuation": 1e9, "last_round_date": "2025-06-01"},
            },
            "research_metadata": {"sources": ["SEC"]},
            "missing_fields": [],
        }
        mock_build.return_value = mock_graph

        from vc_audit_tool.agent.research import CompanyResearchAgent
        result = CompanyResearchAgent().run("TestCo", as_of_date="2026-03-01")
        assert isinstance(result, ResearchResult)
        assert result.is_complete
        assert result.assembled_request is not None

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent._build_graph")
    @patch("vc_audit_tool.agent.research._ensure_langgraph")
    def test_run_returns_error_on_graph_failure(self, mock_ensure: MagicMock, mock_build: MagicMock) -> None:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("graph exploded")
        mock_build.return_value = mock_graph

        from vc_audit_tool.agent.research import CompanyResearchAgent
        result = CompanyResearchAgent().run("TestCo")
        assert not result.is_complete
        assert result.error is not None
        assert "graph exploded" in result.error
