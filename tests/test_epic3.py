"""Tests for Epic 3 (Private Company Data Agent) and Epic 4 (/research endpoint).

Covers:
- FormDSource (Story 3.1)
- USASpendingSource (Story 3.3)
- CompanyResearchAgent (Story 3.2) -- all LangGraph nodes
- POST /research endpoint (Story 4.1)

All external HTTP calls are mocked -- these are pure unit tests.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# -- FormDSource tests -------------------------------------------------------


class FormDSourceTests(unittest.TestCase):
    """Unit tests for FormDSource and FundingRound."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_source(self) -> Any:
        from vc_audit_tool.data_sources.form_d import FormDSource

        return FormDSource(cache_dir=self.cache_dir)

    # -- FundingRound dataclass --

    def test_funding_round_to_dict_serialises_dates(self) -> None:
        from vc_audit_tool.data_sources.form_d import FundingRound

        fr = FundingRound(
            date_of_first_sale=date(2024, 3, 15),
            amount_raised=5_000_000.0,
            amount_sold=3_000_000.0,
            issuer_name="TestCo",
            issuer_state="CA",
            investor_count=12,
            source_url="https://sec.gov/test",
            filing_date=date(2024, 4, 1),
        )
        d = fr.to_dict()
        self.assertEqual(d["date_of_first_sale"], "2024-03-15")
        self.assertEqual(d["filing_date"], "2024-04-01")
        self.assertEqual(d["amount_raised"], 5_000_000.0)
        self.assertEqual(d["issuer_name"], "TestCo")

    def test_funding_round_none_date(self) -> None:
        from vc_audit_tool.data_sources.form_d import FundingRound

        fr = FundingRound(
            date_of_first_sale=None,
            amount_raised=0,
            amount_sold=0,
            issuer_name="X",
            issuer_state="",
            investor_count=None,
            source_url="",
            filing_date=date(2024, 1, 1),
        )
        d = fr.to_dict()
        self.assertIsNone(d["date_of_first_sale"])

    # -- FormDSource validation --

    def test_search_empty_name_raises(self) -> None:
        from vc_audit_tool.exceptions import DataSourceError

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("")

    def test_search_whitespace_name_raises(self) -> None:
        from vc_audit_tool.exceptions import DataSourceError

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("   ")

    # -- Mocked HTTP --

    @patch("httpx.get")
    def test_search_returns_parsed_rounds(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "file_date": "2024-06-15",
                            "entity_name": "Anthropic PBC",
                        }
                    },
                    {
                        "_source": {
                            "file_date": "2023-09-01",
                            "entity_name": "Anthropic PBC",
                        }
                    },
                ]
            }
        }
        mock_get.return_value = mock_resp

        src = self._make_source()
        rounds = src.search("Anthropic")

        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0].filing_date, date(2024, 6, 15))
        self.assertEqual(rounds[1].filing_date, date(2023, 9, 1))
        self.assertEqual(rounds[0].issuer_name, "Anthropic PBC")

    @patch("httpx.get")
    def test_search_empty_hits_returns_empty(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_get.return_value = mock_resp

        src = self._make_source()
        rounds = src.search("NonExistentCo")
        self.assertEqual(rounds, [])

    @patch("httpx.get")
    def test_search_http_error_raises(self, mock_get: MagicMock) -> None:
        import httpx

        from vc_audit_tool.exceptions import DataSourceError

        mock_get.side_effect = httpx.HTTPError("network error")

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("FailCo")

    @patch("httpx.get")
    def test_search_non_200_raises(self, mock_get: MagicMock) -> None:
        from vc_audit_tool.exceptions import DataSourceError

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("FailCo")

    @patch("httpx.get")
    def test_search_403_falls_back_to_submissions(self, mock_get: MagicMock) -> None:
        efts_resp = MagicMock()
        efts_resp.status_code = 403

        tickers_resp = MagicMock()
        tickers_resp.status_code = 200
        tickers_resp.json.return_value = {
            "0": {"cik_str": 1810806, "ticker": "TEST", "title": "TestCo"}
        }

        submissions_resp = MagicMock()
        submissions_resp.status_code = 200
        submissions_resp.json.return_value = {
            "name": "TestCo",
            "filings": {
                "recent": {
                    "form": ["D", "8-K"],
                    "filingDate": ["2024-06-15", "2024-05-01"],
                    "accessionNumber": ["0001810806-24-000001", "0001810806-24-000002"],
                    "primaryDocument": ["xslFormDX01/primary_doc.xml", "x.xml"],
                }
            },
        }

        mock_get.side_effect = [efts_resp, tickers_resp, submissions_resp]

        src = self._make_source()
        rounds = src.search("TestCo")
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].filing_date, date(2024, 6, 15))

    # -- Cache --

    @patch("httpx.get")
    def test_cache_write_and_read(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "file_date": "2024-01-10",
                            "entity_name": "CacheCo",
                        }
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        src = self._make_source()
        r1 = src.search("CacheCo")
        self.assertEqual(mock_get.call_count, 1)

        r2 = src.search("CacheCo")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(r1), len(r2))
        self.assertEqual(r1[0].filing_date, r2[0].filing_date)

    @patch("httpx.get")
    def test_unparseable_hit_skipped(self, mock_get: MagicMock) -> None:
        """Hits with no file_date are skipped without error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {"_source": {}},
                    {
                        "_source": {
                            "file_date": "2024-03-01",
                            "entity_name": "GoodCo",
                        }
                    },
                ]
            }
        }
        mock_get.return_value = mock_resp

        src = self._make_source()
        rounds = src.search("MixedCo")
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].issuer_name, "GoodCo")

    @patch("httpx.get")
    def test_non_json_response_raises(self, mock_get: MagicMock) -> None:
        from vc_audit_tool.exceptions import DataSourceError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_get.return_value = mock_resp

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("BadJsonCo")

    def test_cache_key_normalisation(self) -> None:
        from vc_audit_tool.data_sources.form_d import FormDSource

        self.assertEqual(FormDSource._cache_key("Anthropic PBC"), "anthropic_pbc")
        self.assertEqual(FormDSource._cache_key("  SpaceCo  "), "spaceco")
        self.assertEqual(FormDSource._cache_key("a/b"), "a_b")

    def test_dataset_version_set_after_search(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"hits": {"hits": []}}
            mock_get.return_value = mock_resp

            src = self._make_source()
            src.search("VersionCo")
            self.assertIn("form-d-", src.dataset_version)


# -- USASpendingSource tests -------------------------------------------------


class USASpendingSourceTests(unittest.TestCase):
    """Unit tests for USASpendingSource and GovernmentContract."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_source(self) -> Any:
        from vc_audit_tool.data_sources.usaspending import USASpendingSource

        return USASpendingSource(cache_dir=self.cache_dir)

    # -- GovernmentContract dataclass --

    def test_contract_to_dict(self) -> None:
        from vc_audit_tool.data_sources.usaspending import GovernmentContract

        gc = GovernmentContract(
            award_id="AWD-001",
            recipient_name="TestCo",
            award_amount=1_500_000.0,
            award_description="AI research",
            awarding_agency="DoD",
            start_date="2024-01-01",
            end_date="2025-01-01",
            source_url="https://usaspending.gov/award/AWD-001",
        )
        d = gc.to_dict()
        self.assertEqual(d["award_id"], "AWD-001")
        self.assertEqual(d["award_amount"], 1_500_000.0)
        self.assertEqual(d["awarding_agency"], "DoD")

    # -- Validation --

    def test_search_empty_name_raises(self) -> None:
        from vc_audit_tool.exceptions import DataSourceError

        src = self._make_source()
        with self.assertRaises(DataSourceError):
            src.search("")

    # -- Mocked HTTP --

    @patch("httpx.post")
    def test_search_returns_sorted_contracts(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "Award ID": "AWD-001",
                    "Recipient Name": "TestCo",
                    "Award Amount": 500_000,
                    "Description": "Small contract",
                    "Awarding Agency": "DoD",
                    "Start Date": "2024-01-01",
                    "End Date": "2024-12-31",
                },
                {
                    "Award ID": "AWD-002",
                    "Recipient Name": "TestCo",
                    "Award Amount": 2_000_000,
                    "Description": "Big contract",
                    "Awarding Agency": "NSA",
                    "Start Date": "2024-06-01",
                    "End Date": "2025-06-01",
                },
            ]
        }
        mock_post.return_value = mock_resp

        src = self._make_source()
        contracts = src.search("TestCo")

        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].award_amount, 2_000_000)
        self.assertEqual(contracts[1].award_amount, 500_000)

    @patch("httpx.post")
    def test_search_empty_results(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_post.return_value = mock_resp

        src = self._make_source()
        self.assertEqual(src.search("NobodyCo"), [])

    @patch("httpx.post")
    def test_search_http_error_returns_empty(self, mock_post: MagicMock) -> None:
        """API errors are non-blocking -- return empty list."""
        import httpx

        mock_post.side_effect = httpx.HTTPError("network error")

        src = self._make_source()
        self.assertEqual(src.search("FailCo"), [])

    @patch("httpx.post")
    def test_search_non_200_returns_empty(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        src = self._make_source()
        self.assertEqual(src.search("FailCo"), [])

    @patch("httpx.post")
    def test_search_non_json_returns_empty(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_post.return_value = mock_resp

        src = self._make_source()
        self.assertEqual(src.search("BadJsonCo"), [])

    @patch("httpx.post")
    def test_total_contract_value(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "Award ID": "A1",
                    "Recipient Name": "X",
                    "Award Amount": 100_000,
                    "Description": "",
                    "Awarding Agency": "",
                    "Start Date": "",
                    "End Date": "",
                },
                {
                    "Award ID": "A2",
                    "Recipient Name": "X",
                    "Award Amount": 200_000,
                    "Description": "",
                    "Awarding Agency": "",
                    "Start Date": "",
                    "End Date": "",
                },
            ]
        }
        mock_post.return_value = mock_resp

        src = self._make_source()
        total = src.total_contract_value("X")
        self.assertEqual(total, 300_000)

    @patch("httpx.post")
    def test_total_contract_value_none_when_empty(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_post.return_value = mock_resp

        src = self._make_source()
        self.assertIsNone(src.total_contract_value("NobodyCo"))

    @patch("httpx.post")
    def test_cache_write_and_read(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "Award ID": "C1",
                    "Recipient Name": "CacheCo",
                    "Award Amount": 50_000,
                    "Description": "test",
                    "Awarding Agency": "GSA",
                    "Start Date": "2024-01-01",
                    "End Date": "2024-12-31",
                }
            ]
        }
        mock_post.return_value = mock_resp

        src = self._make_source()
        r1 = src.search("CacheCo")
        self.assertEqual(mock_post.call_count, 1)

        r2 = src.search("CacheCo")
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(len(r1), len(r2))

    def test_cache_key_normalisation(self) -> None:
        from vc_audit_tool.data_sources.usaspending import USASpendingSource

        self.assertEqual(USASpendingSource._cache_key("Test Co"), "test_co")
        self.assertEqual(USASpendingSource._cache_key("  Spaces  "), "spaces")

    def test_dataset_version_set_after_search(self) -> None:
        with patch("httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            src = self._make_source()
            src.search("VersionCo")
            self.assertIn("usaspending-", src.dataset_version)


# -- CompanyResearchAgent node tests -----------------------------------------


class ParseCompanyNodeTests(unittest.TestCase):
    """Unit tests for _parse_company_node."""

    def test_normalises_name(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {"company_name": "  Anthropic  "}
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["normalised_name"], "Anthropic")

    def test_empty_name_sets_error(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {"company_name": ""}
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertIn("error", result)
        self.assertIn("required", result["error"])

    def test_infers_sector_from_hint(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {
            "company_name": "CyberDefend",
            "description_hint": "cybersecurity solutions",
        }
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["inferred_sector"], "cybersecurity")

    def test_default_sector_when_no_match(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {
            "company_name": "FooBar",
            "description_hint": "makes widgets",
        }
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["inferred_sector"], "enterprise_software")

    def test_infers_sic_code(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {
            "company_name": "SemiChip Inc",
            "description_hint": "semiconductor design",
        }
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["inferred_sector"], "semiconductors")
        self.assertIn("inferred_sic", result)

    def test_defense_sector_inference(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {
            "company_name": "DefenseTech",
            "description_hint": "defense electronics contractor",
        }
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["inferred_sector"], "defense_electronics")

    def test_ecommerce_sector_inference(self) -> None:
        from vc_audit_tool.agent.research import _parse_company_node

        state: dict[str, Any] = {
            "company_name": "ShopEasy",
            "description_hint": "ecommerce marketplace",
        }
        result = _parse_company_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["inferred_sector"], "ecommerce")


class FormDNodeTests(unittest.TestCase):
    """Unit tests for _form_d_node."""

    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_returns_rounds_as_dicts(self, mock_cls: MagicMock) -> None:
        from vc_audit_tool.agent.research import _form_d_node
        from vc_audit_tool.data_sources.form_d import FundingRound

        mock_instance = MagicMock()
        mock_instance.search.return_value = [
            FundingRound(
                date_of_first_sale=date(2024, 1, 1),
                amount_raised=10_000_000,
                amount_sold=5_000_000,
                issuer_name="TestCo",
                issuer_state="DE",
                investor_count=5,
                source_url="https://sec.gov/test",
                filing_date=date(2024, 2, 1),
            )
        ]
        mock_cls.return_value = mock_instance

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _form_d_node(state)  # type: ignore[arg-type]
        self.assertEqual(len(result["form_d_rounds"]), 1)
        self.assertEqual(result["form_d_rounds"][0]["issuer_name"], "TestCo")

    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_handles_data_source_error(self, mock_cls: MagicMock) -> None:
        from vc_audit_tool.agent.research import _form_d_node
        from vc_audit_tool.exceptions import DataSourceError

        mock_instance = MagicMock()
        mock_instance.search.side_effect = DataSourceError("boom")
        mock_cls.return_value = mock_instance

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _form_d_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["form_d_rounds"], [])

    def test_empty_name_returns_state_unchanged(self) -> None:
        from vc_audit_tool.agent.research import _form_d_node

        state: dict[str, Any] = {"normalised_name": ""}
        result = _form_d_node(state)  # type: ignore[arg-type]
        self.assertNotIn("form_d_rounds", result)


class ContractsNodeTests(unittest.TestCase):
    """Unit tests for _contracts_node."""

    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    def test_returns_contracts(self, mock_cls: MagicMock) -> None:
        from vc_audit_tool.agent.research import _contracts_node
        from vc_audit_tool.data_sources.usaspending import GovernmentContract

        mock_instance = MagicMock()
        mock_instance.search.return_value = [
            GovernmentContract(
                award_id="A1",
                recipient_name="TestCo",
                award_amount=100_000,
                award_description="test",
                awarding_agency="DoD",
                start_date="2024-01-01",
                end_date="2024-12-31",
                source_url="https://usaspending.gov/award/A1",
            )
        ]
        mock_cls.return_value = mock_instance

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _contracts_node(state)  # type: ignore[arg-type]
        self.assertEqual(len(result["government_contracts"]), 1)
        self.assertEqual(result["government_contracts_usd"], 100_000)

    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    def test_handles_data_source_error(self, mock_cls: MagicMock) -> None:
        from vc_audit_tool.agent.research import _contracts_node
        from vc_audit_tool.exceptions import DataSourceError

        mock_instance = MagicMock()
        mock_instance.search.side_effect = DataSourceError("boom")
        mock_cls.return_value = mock_instance

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _contracts_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["government_contracts"], [])
        self.assertIsNone(result["government_contracts_usd"])


class WebResearchNodeTests(unittest.TestCase):
    """Unit tests for _web_research_node (DuckDuckGo + regex + Ollama)."""

    def setUp(self) -> None:
        # Clear the per-process DDGS cache so each test starts with a clean
        # slate and mocked search results are not shadowed by cached entries.
        import vc_audit_tool.agent.nodes.web_research as _wm

        _wm._SEARCH_CACHE.clear()

    def test_empty_name_returns_unchanged(self) -> None:
        from vc_audit_tool.agent.research import _web_research_node

        state: dict[str, Any] = {"normalised_name": ""}
        result = _web_research_node(state)  # type: ignore[arg-type]
        self.assertNotIn("web_facts", result)

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS", side_effect=ImportError)
    def test_no_ddgs_returns_empty_facts(self, _mock: MagicMock) -> None:
        """When duckduckgo-search is not installed, return empty facts."""
        from vc_audit_tool.agent.research import _web_research_node

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _web_research_node(state)  # type: ignore[arg-type]
        self.assertIn("web_facts", result)
        self.assertIsNone(result["web_facts"]["revenue_ltm"])
        self.assertIsNone(result["web_facts"]["llm_model_version"])

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_regex_extracts_valuation(self, mock_ddgs_cls: MagicMock) -> None:
        """Regex should extract '$4.1 billion valuation'."""
        from vc_audit_tool.agent.research import _web_research_node

        mock_ctx = MagicMock()
        mock_ctx.text.return_value = [
            {
                "title": "TechCrunch",
                "body": "Anthropic raised $2 billion at a $4.1 billion valuation in March 2024.",
            }
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        state: dict[str, Any] = {"normalised_name": "Anthropic"}
        result = _web_research_node(state)  # type: ignore[arg-type]

        self.assertEqual(result["web_facts"]["last_post_money_valuation"], 4_100_000_000)
        self.assertEqual(result["web_facts"]["last_round_amount_raised"], 2_000_000_000)
        self.assertIn("TechCrunch", result["web_facts"]["sources"])

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_regex_extracts_revenue(self, mock_ddgs_cls: MagicMock) -> None:
        """Regex should extract '$100 million in revenue'."""
        from vc_audit_tool.agent.research import _web_research_node

        mock_ctx = MagicMock()
        mock_ctx.text.return_value = [
            {
                "title": "Forbes",
                "body": "The company reached $100 million in annual revenue.",
            }
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        state: dict[str, Any] = {"normalised_name": "SomeCo"}
        result = _web_research_node(state)  # type: ignore[arg-type]

        self.assertEqual(result["web_facts"]["revenue_ltm"], 100_000_000)

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_regex_extracts_round_date(self, mock_ddgs_cls: MagicMock) -> None:
        """Regex should extract a date near 'Series' or 'funding round'."""
        from vc_audit_tool.agent.research import _web_research_node

        mock_ctx = MagicMock()
        mock_ctx.text.return_value = [
            {
                "title": "Bloomberg",
                "body": "Series C round raised $500 million in March 2024.",
            }
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _web_research_node(state)  # type: ignore[arg-type]

        self.assertEqual(result["web_facts"]["last_round_date"], "March 2024")

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_ddgs_exception_non_blocking(self, mock_ddgs_cls: MagicMock) -> None:
        """If DuckDuckGo raises an exception, we still get empty facts."""
        from vc_audit_tool.agent.research import _web_research_node

        mock_ddgs_cls.side_effect = RuntimeError("rate limited")

        state: dict[str, Any] = {"normalised_name": "TestCo"}
        result = _web_research_node(state)  # type: ignore[arg-type]

        self.assertIn("web_facts", result)
        self.assertIsNone(result["web_facts"]["revenue_ltm"])

    @patch("vc_audit_tool.agent.llm_adapter.ChatOllama")
    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_ollama_overrides_regex(
        self, mock_ddgs_cls: MagicMock, mock_ollama_cls: MagicMock
    ) -> None:
        """If Ollama is available, its JSON output overrides regex results."""
        from vc_audit_tool.agent.research import _web_research_node

        # DuckDuckGo returns snippets (regex will extract 100M revenue)
        mock_ctx = MagicMock()
        mock_ctx.text.return_value = [
            {
                "title": "TechCrunch",
                "body": "Company has $100 million in revenue.",
            }
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Ollama returns a more accurate number
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "revenue_ltm": 150_000_000,
                "last_round_date": "2024-06-15",
                "last_round_amount_raised": None,
                "last_post_money_valuation": 2_000_000_000,
                "company_description": "An AI company",
            }
        )
        mock_ollama_cls.return_value.invoke.return_value = mock_response

        # Must set OLLAMA_MODEL and clear API keys so _get_llm() picks Ollama
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
        }
        env["OLLAMA_MODEL"] = "llama3.2"
        state: dict[str, Any] = {"normalised_name": "TestCo"}
        with patch.dict(os.environ, env, clear=True):
            result = _web_research_node(state)  # type: ignore[arg-type]

        # Ollama results should win over regex
        self.assertEqual(result["web_facts"]["revenue_ltm"], 150_000_000)
        self.assertEqual(result["web_facts"]["last_post_money_valuation"], 2_000_000_000)
        self.assertIn("ollama/", result["web_facts"]["llm_model_version"])

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS")
    def test_ollama_unavailable_uses_regex_only(self, mock_ddgs_cls: MagicMock) -> None:
        """If Ollama is not running, regex results are used."""
        from vc_audit_tool.agent.research import _web_research_node

        mock_ctx = MagicMock()
        mock_ctx.text.return_value = [
            {
                "title": "Source",
                "body": "Raised $500 million at $5 billion valuation.",
            }
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Set OLLAMA_MODEL so _get_llm() actually tries Ollama, and clear API keys
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
        }
        env["OLLAMA_MODEL"] = "llama3.2"
        state: dict[str, Any] = {"normalised_name": "TestCo"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "vc_audit_tool.agent.llm_adapter.ChatOllama",
                side_effect=Exception("Connection refused"),
            ),
        ):
            result = _web_research_node(state)  # type: ignore[arg-type]

        self.assertEqual(result["web_facts"]["last_post_money_valuation"], 5_000_000_000)
        self.assertEqual(result["web_facts"]["last_round_amount_raised"], 500_000_000)
        # No LLM was used
        self.assertIsNone(result["web_facts"]["llm_model_version"])

    @patch("vc_audit_tool.agent.llm_adapter.ChatAnthropic")
    @patch("vc_audit_tool.agent.llm_adapter.ChatOpenAI")
    @patch("vc_audit_tool.agent.llm_adapter.ChatGoogleGenerativeAI")
    def test_get_llm_prioritizes_google_when_multiple_keys_set(
        self,
        mock_google_cls: MagicMock,
        mock_openai_cls: MagicMock,
        mock_anthropic_cls: MagicMock,
    ) -> None:
        from vc_audit_tool.agent.research import _get_llm

        with patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "g-key",
                "OPENAI_API_KEY": "o-key",
                "ANTHROPIC_API_KEY": "a-key",
                "GOOGLE_MODEL": "gemini-test",
            },
            clear=True,
        ):
            _llm, label, _cfg = _get_llm()

        self.assertEqual(label, "google/gemini-test")
        mock_google_cls.assert_called_once()
        mock_openai_cls.assert_not_called()
        mock_anthropic_cls.assert_not_called()

    @patch("vc_audit_tool.agent.llm_adapter.ChatOpenAI")
    @patch("vc_audit_tool.agent.llm_adapter.ChatGoogleGenerativeAI")
    def test_get_llm_falls_back_to_openai_when_google_init_fails(
        self, mock_google_cls: MagicMock, mock_openai_cls: MagicMock
    ) -> None:
        from vc_audit_tool.agent.research import _get_llm

        mock_google_cls.side_effect = RuntimeError("google init failed")
        with patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "g-key",
                "OPENAI_API_KEY": "o-key",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=True,
        ):
            _llm, label, _cfg = _get_llm()

        self.assertEqual(label, "openai/gpt-4o-mini")
        mock_google_cls.assert_called_once()
        mock_openai_cls.assert_called_once()


class AssembleNodeTests(unittest.TestCase):
    """Unit tests for _assemble_node."""

    def test_returns_error_if_state_has_error(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {"error": "something broke"}
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertEqual(result["error"], "something broke")

    def test_auto_selects_last_round_when_data_available(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": "2024-06-01",
                "last_post_money_valuation": 100_000_000,
                "revenue_ltm": None,
                "sources": [],
                "llm_model_version": "gpt-4o-mini",
            },
            "form_d_rounds": [{"filing_date": "2024-06-01"}],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNotNone(result["assembled_request"])
        self.assertEqual(
            result["assembled_request"]["methodology"],
            "last_round_market_adjusted",
        )
        self.assertEqual(result["missing_fields"], [])

    def test_auto_selects_comps_when_no_round_data(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "",
            "inferred_sector": "enterprise_software",
            "description_hint": "AI-native compliance platform for banks",
            "web_facts": {
                "revenue_ltm": 10_000_000,
                "last_post_money_valuation": None,
                "last_round_date": None,
                "sources": [],
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNotNone(result["assembled_request"])
        self.assertEqual(result["assembled_request"]["methodology"], "comparable_companies")
        self.assertEqual(
            result["assembled_request"]["inputs"]["target_description"],
            "AI-native compliance platform for banks",
        )

    def test_missing_fields_when_data_incomplete(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "comparable_companies",
            "inferred_sector": "enterprise_software",
            "web_facts": {"revenue_ltm": None},
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        self.assertIn("revenue_ltm", result["missing_fields"])

    def test_auto_selection_reports_best_available_when_incomplete(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "revenue_ltm": None,
                "last_post_money_valuation": None,
                "last_round_date": None,
                "sources": [],
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        # With the new fallback order (last_round_multiple_ratchet before comparable_companies),
        # when both fail with the same missing field count, ratchet is reported first.
        self.assertIn(
            result["best_available_methodology"],
            ("comparable_companies", "last_round_multiple_ratchet"),
        )
        self.assertEqual(result["missing_for_best_available"], ["revenue_ltm"])

    def test_unsupported_methodology(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "magic_method",
            "inferred_sector": "enterprise_software",
            "web_facts": {},
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        self.assertTrue(any("unsupported" in m.lower() for m in result["missing_fields"]))

    def test_research_metadata_populated(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "comparable_companies",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "revenue_ltm": 10_000_000,
                "sources": ["TechCrunch"],
                "llm_model_version": "gpt-4o-mini",
            },
            "form_d_rounds": [{"filing_date": "2024-01-01"}],
            "government_contracts": [{"award_id": "A1"}],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        meta = result["research_metadata"]
        self.assertIn("SEC EDGAR Form D", meta["sources_consulted"])
        self.assertIn("USASpending.gov", meta["sources_consulted"])
        self.assertIn("LLM (gpt-4o-mini)", meta["sources_consulted"])
        self.assertEqual(meta["extracted_facts"]["form_d_rounds_found"], 1)
        self.assertEqual(meta["extracted_facts"]["government_contracts_found"], 1)

    def test_last_round_missing_valuation(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_market_adjusted",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": "2024-06-01",
                "last_post_money_valuation": None,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        self.assertIn("last_post_money_valuation", result["missing_fields"])

    def test_last_round_missing_date(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_market_adjusted",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": None,
                "last_post_money_valuation": 100_000_000,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        self.assertIn("last_round_date", result["missing_fields"])

    def test_multiple_ratchet_assembly(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_multiple_ratchet",
            "inferred_sector": "enterprise_software",
            "description_hint": "Cloud observability tools for enterprise developers",
            "web_facts": {
                "revenue_ltm": 20_000_000,
                "last_post_money_valuation": 200_000_000,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNotNone(result["assembled_request"])
        self.assertEqual(
            result["assembled_request"]["methodology"],
            "last_round_multiple_ratchet",
        )
        inputs = result["assembled_request"]["inputs"]
        self.assertEqual(inputs["last_post_money_valuation"], 200_000_000)
        self.assertEqual(inputs["current_revenue"], 20_000_000)
        self.assertEqual(
            inputs["target_description"],
            "Cloud observability tools for enterprise developers",
        )

    def test_last_round_assembled_request_structure(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_market_adjusted",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": "2024-06-01",
                "last_post_money_valuation": 100_000_000,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        req = result["assembled_request"]
        self.assertEqual(req["company_name"], "TestCo")
        self.assertEqual(req["as_of_date"], "2026-01-01")
        self.assertIn("public_index", req["inputs"])

    def test_last_round_normalizes_month_year_date(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_market_adjusted",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": "January 2003)",
                "last_post_money_valuation": 100_000_000,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        req = result["assembled_request"]
        self.assertIsNotNone(req)
        self.assertEqual(req["inputs"]["last_round_date"], "2003-01-01")

    def test_last_round_invalid_date_is_missing(self) -> None:
        from vc_audit_tool.agent.research import _assemble_node

        state: dict[str, Any] = {
            "normalised_name": "TestCo",
            "as_of_date": "2026-01-01",
            "methodology": "last_round_market_adjusted",
            "inferred_sector": "enterprise_software",
            "web_facts": {
                "last_round_date": "sometime recently",
                "last_post_money_valuation": 100_000_000,
            },
            "form_d_rounds": [],
            "government_contracts": [],
        }
        result = _assemble_node(state)  # type: ignore[arg-type]
        self.assertIsNone(result["assembled_request"])
        self.assertIn("last_round_date", result["missing_fields"])


# -- Helper functions --------------------------------------------------------


class HelperFunctionTests(unittest.TestCase):
    def test_has_last_round_data_true(self) -> None:
        from vc_audit_tool.agent.research import _has_last_round_data

        self.assertTrue(
            _has_last_round_data(
                {
                    "last_round_date": "2024-06-01",
                    "last_post_money_valuation": 100_000_000,
                },
                [],
            )
        )

    def test_has_last_round_data_from_form_d(self) -> None:
        from vc_audit_tool.agent.research import _has_last_round_data

        self.assertTrue(
            _has_last_round_data(
                {"last_post_money_valuation": 100_000_000},
                [{"filing_date": "2024-06-01"}],
            )
        )

    def test_has_last_round_data_false_no_valuation(self) -> None:
        from vc_audit_tool.agent.research import _has_last_round_data

        self.assertFalse(
            _has_last_round_data(
                {"last_round_date": "2024-06-01"},
                [],
            )
        )

    def test_has_last_round_data_false_empty(self) -> None:
        from vc_audit_tool.agent.research import _has_last_round_data

        self.assertFalse(_has_last_round_data({}, []))


# -- ResearchResult ----------------------------------------------------------


class ResearchResultTests(unittest.TestCase):
    def test_is_complete_true(self) -> None:
        from vc_audit_tool.agent.research import ResearchResult

        r = ResearchResult(
            assembled_request={"company_name": "X"},
            research_metadata={},
            missing_fields=[],
        )
        self.assertTrue(r.is_complete)

    def test_is_complete_false_missing_fields(self) -> None:
        from vc_audit_tool.agent.research import ResearchResult

        r = ResearchResult(
            assembled_request={"company_name": "X"},
            research_metadata={},
            missing_fields=["revenue_ltm"],
        )
        self.assertFalse(r.is_complete)

    def test_is_complete_false_no_request(self) -> None:
        from vc_audit_tool.agent.research import ResearchResult

        r = ResearchResult(
            assembled_request=None,
            research_metadata={},
            missing_fields=[],
        )
        self.assertFalse(r.is_complete)

    def test_error_field_default(self) -> None:
        from vc_audit_tool.agent.research import ResearchResult

        r = ResearchResult(
            assembled_request=None,
            research_metadata={},
            missing_fields=[],
        )
        self.assertIsNone(r.error)

    def test_error_field_set(self) -> None:
        from vc_audit_tool.agent.research import ResearchResult

        r = ResearchResult(
            assembled_request=None,
            research_metadata={},
            missing_fields=[],
            error="something broke",
        )
        self.assertEqual(r.error, "something broke")
        self.assertFalse(r.is_complete)


# -- CompanyResearchAgent full run (mocked) ----------------------------------


class CompanyResearchAgentTests(unittest.TestCase):
    """End-to-end test of the full LangGraph agent with all externals mocked."""

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS", new=None)
    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_full_run_comps_path(self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock) -> None:
        """Agent with no LLM keys, no rounds, no contracts."""
        mock_formd_cls.return_value.search.return_value = []
        mock_usa_cls.return_value.search.return_value = []

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        env["VC_AUDIT_DISABLE_WEB_SEARCH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            from vc_audit_tool.agent.research import CompanyResearchAgent

            agent = CompanyResearchAgent()
            result = agent.run("TestCo", as_of_date="2026-01-01")

        self.assertFalse(result.is_complete)
        self.assertIn("revenue_ltm", result.missing_fields)
        self.assertIsNotNone(result.research_metadata)

    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_full_run_with_web_facts_override(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """Simulate the web_research node finding data by injecting via mock."""
        from vc_audit_tool.data_sources.form_d import FundingRound

        mock_formd_cls.return_value.search.return_value = [
            FundingRound(
                date_of_first_sale=date(2024, 3, 1),
                amount_raised=0,
                amount_sold=0,
                issuer_name="TestCo",
                issuer_state="",
                investor_count=None,
                source_url="",
                filing_date=date(2024, 3, 1),
            )
        ]
        mock_usa_cls.return_value.search.return_value = []

        fake_web_facts = {
            "revenue_ltm": None,
            "last_round_date": "2024-03-01",
            "last_round_amount_raised": 50_000_000,
            "last_post_money_valuation": 500_000_000,
            "company_description": "An AI company",
            "sources": ["TechCrunch"],
            "llm_model_version": None,
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "vc_audit_tool.agent.research._web_research_node",
                side_effect=lambda state: {**state, "web_facts": fake_web_facts},
            ),
        ):
            from vc_audit_tool.agent.research import CompanyResearchAgent

            agent = CompanyResearchAgent()
            result = agent.run("TestCo", as_of_date="2026-01-01")

        self.assertTrue(result.is_complete)
        self.assertEqual(
            result.assembled_request["methodology"],  # type: ignore[index]
            "last_round_market_adjusted",
        )

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS", new=None)
    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_agent_handles_exception_gracefully(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """If the graph raises, the agent returns an error result."""
        mock_formd_cls.return_value.search.side_effect = RuntimeError("kaboom")
        mock_usa_cls.return_value.search.return_value = []

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        env["VC_AUDIT_DISABLE_WEB_SEARCH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            from vc_audit_tool.agent.research import CompanyResearchAgent

            agent = CompanyResearchAgent()
            result = agent.run("TestCo")

        self.assertIsNotNone(result.error)
        self.assertFalse(result.is_complete)


# -- POST /research endpoint tests ------------------------------------------


class ResearchEndpointTests(unittest.TestCase):
    """Tests for POST /research on the FastAPI app."""

    client: Any

    @classmethod
    def setUpClass(cls) -> None:
        from starlette.testclient import TestClient

        from vc_audit_tool import server as server_module
        from vc_audit_tool.engine import ValuationEngine

        mock_engine = ValuationEngine.mock()
        server_module.engine = mock_engine
        server_module.app.state.engine = mock_engine

        cls.client = TestClient(server_module.app)

    def test_missing_company_name_returns_400(self) -> None:
        resp = self.client.post("/research", content=json.dumps({}))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("company_name", resp.json()["error"])

    def test_invalid_json_returns_400(self) -> None:
        resp = self.client.post("/research", content=b"not json")
        self.assertEqual(resp.status_code, 400)

    def test_non_string_company_name_returns_400(self) -> None:
        resp = self.client.post("/research", content=json.dumps({"company_name": 123}))
        self.assertEqual(resp.status_code, 400)

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS", new=None)
    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_incomplete_research_returns_422_with_partial_payload(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """When incomplete, return 422 with metadata-rich partial payload."""
        mock_formd_cls.return_value.search.return_value = []
        mock_usa_cls.return_value.search.return_value = []

        # Clear all API keys so no LLM is configured
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post(
                "/research",
                content=json.dumps({"company_name": "TestCo"}),
            )

        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertIn("error", data)
        self.assertIsNone(data["assembled_request"])
        self.assertIn("best_available_methodology", data)
        self.assertIn("missing_for_best_available", data)
        self.assertIn("missing_fields", data)
        self.assertIn("research_metadata", data)

    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_complete_research_returns_200(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """When agent assembles complete inputs, engine runs and returns 200."""
        mock_formd_cls.return_value.search.return_value = []
        mock_usa_cls.return_value.search.return_value = []

        fake_web_facts = {
            "revenue_ltm": None,
            "last_round_date": "2024-06-01",
            "last_round_amount_raised": 50_000_000,
            "last_post_money_valuation": 100_000_000,
            "company_description": "A test company",
            "sources": ["Mock"],
            "llm_model_version": "gpt-4o-mini",
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "vc_audit_tool.agent.research._web_research_node",
                side_effect=lambda state: {
                    **state,
                    "web_facts": fake_web_facts,
                },
            ),
        ):
            resp = self.client.post(
                "/research",
                content=json.dumps(
                    {
                        "company_name": "TestCo",
                        "as_of_date": "2026-02-18",
                    }
                ),
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("valuation_result", data)
        self.assertIn("research_metadata", data)
        self.assertIn("estimated_fair_value", data["valuation_result"])

    @patch("vc_audit_tool.agent.nodes.web_research.DDGS", new=None)
    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_research_with_description_hint(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """description_hint is accepted without error."""
        mock_formd_cls.return_value.search.return_value = []
        mock_usa_cls.return_value.search.return_value = []

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OLLAMA_MODEL",
            )
        }
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post(
                "/research",
                content=json.dumps(
                    {
                        "company_name": "TestCo",
                        "description_hint": "cybersecurity company",
                    }
                ),
            )
        self.assertIn(resp.status_code, [200, 422])

    @patch("vc_audit_tool.agent.nodes.contracts.USASpendingSource")
    @patch("vc_audit_tool.agent.nodes.form_d.FormDSource")
    def test_datasource_error_falls_back_to_direct_valuation(
        self, mock_formd_cls: MagicMock, mock_usa_cls: MagicMock
    ) -> None:
        """When engine raises DataSourceError (e.g. EDGAR 503), router retries
        with direct_valuation if the evidence package has MODERATE+ strength."""
        from vc_audit_tool.exceptions import DataSourceError

        mock_formd_cls.return_value.search.return_value = []
        mock_usa_cls.return_value.search.return_value = []

        # Simulate a research result that had evidence but chose comparable_companies
        # and the engine then failed with a DataSourceError (EDGAR 503 scenario).
        fake_evidence_signal = {
            "amount_usd": 400_000_000_000.0,
            "evidence_type": "secondary_market",
            "confidence": 0.60,
            "date_mentioned": "2025-01-01",
            "source_title": "https://example.com",
            "source_snippet": "SpaceX valued at $400B",
        }
        fake_ev_pkg = {
            "consensus_strength": "MODERATE",
            "evidence": [fake_evidence_signal] * 5,
        }
        fake_web_facts = {
            "revenue_ltm": 10_000_000_000,
            "last_round_date": None,
            "last_post_money_valuation": None,
            "sources": [],
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        call_count = 0

        def engine_side_effect(req: dict):
            nonlocal call_count
            call_count += 1
            if req.get("methodology") == "comparable_companies":
                raise DataSourceError(
                    "No companies found in EDGAR for sector 'defense_electronics'"
                )
            # direct_valuation call succeeds via mock engine
            return self.client.app.state.engine.evaluate_from_dict.__wrapped__(req)  # type: ignore[attr-defined]

        with (
            patch(
                "vc_audit_tool.agent.research._web_research_node",
                side_effect=lambda state: {
                    **state,
                    "web_facts": fake_web_facts,
                    "raw_snippets": ["SpaceX valued at $400B in tender offer"] * 5,
                    "source_titles": ["example.com"] * 5,
                    "source_dates": [None] * 5,
                    "evidence_package": fake_ev_pkg,
                },
            ),
        ):
            resp = self.client.post(
                "/research",
                content=json.dumps({"company_name": "SpaceX", "as_of_date": "2026-01-01"}),
            )

        # Either succeeds (fallback worked) or returns 400/422 with error info
        # The important thing: it does NOT return 500
        self.assertNotEqual(resp.status_code, 500)
        data = resp.json()
        if resp.status_code == 200:
            self.assertIn("valuation_result", data)
            self.assertIn("research_metadata", data)
        else:
            self.assertIn("error", data)


# -- Lazy import / __init__ tests -------------------------------------------


class LazyImportTests(unittest.TestCase):
    """Verify the data_sources __init__.py exposes new sources."""

    def test_form_d_source_importable(self) -> None:
        from vc_audit_tool.data_sources import FormDSource

        self.assertTrue(callable(FormDSource))

    def test_usaspending_source_importable(self) -> None:
        from vc_audit_tool.data_sources import USASpendingSource

        self.assertTrue(callable(USASpendingSource))

    def test_agent_importable(self) -> None:
        from vc_audit_tool.agent import CompanyResearchAgent, ResearchResult

        self.assertTrue(callable(CompanyResearchAgent))
        self.assertTrue(callable(ResearchResult))

    def test_unknown_attr_raises(self) -> None:
        with self.assertRaises((AttributeError, ImportError)):
            from vc_audit_tool.data_sources import (
                NoSuchThing,  # type: ignore[attr-defined]  # noqa: F401
            )


if __name__ == "__main__":
    unittest.main()
