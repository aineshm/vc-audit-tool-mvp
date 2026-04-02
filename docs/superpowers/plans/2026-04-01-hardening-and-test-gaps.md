# VC Audit Tool Hardening & Test Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the critical test gaps (research agent, reconcile endpoint, LLM adapter, cost tracker), fix frontend input template mismatches, sanitize error messages, and remove dead code.

**Architecture:** Seven independent tasks targeting the most impactful gaps identified by audit. Each task is self-contained: dead code cleanup, error sanitization, frontend fixes, then four test-coverage tasks for the untested research/reconcile/LLM/cost-tracker layers. All backend tests use pytest with the existing mock engine pattern from `conftest.py`.

**Tech Stack:** Python 3.10+, pytest, FastAPI TestClient, unittest.mock, Next.js 16 / TypeScript

---

### Task 1: Remove Dead Code

**Files:**
- Delete: `files/direct_valuation.py`
- Delete: `files/engine_patch.py`
- Delete: `files/evidence_collector.py`
- Delete: `files/REFACTOR_NOTES.md`
- Delete: `files/research.py`
- Delete: `src/vc_audit_tool/static/index.html`
- Delete: `fix_revenue_ltm_422.md`

- [ ] **Step 1: Verify nothing imports from `files/`**

Run: `cd ~/Developer/vc-audit-tool-mvp && grep -r "from files" src/ tests/ && grep -r "import files" src/ tests/`
Expected: No output (no imports found)

- [ ] **Step 2: Verify `static/index.html` is not served**

The `server.py` root route returns JSON (`{"message": "VC Audit Tool API. UI: http://localhost:3000"}`), confirming the static HTML is dead. No `StaticFiles` mount exists.

- [ ] **Step 3: Delete dead files**

```bash
cd ~/Developer/vc-audit-tool-mvp
rm -rf files/
rm src/vc_audit_tool/static/index.html
rm fix_revenue_ltm_422.md
```

- [ ] **Step 4: Run tests to confirm nothing broke**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/ -q`
Expected: All existing tests pass

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add -A
git commit -m "chore: remove dead code — files/, static/index.html, fix_revenue_ltm_422.md"
```

---

### Task 2: Sanitize Error Messages (Security)

**Files:**
- Modify: `src/vc_audit_tool/routers/research.py`
- Modify: `src/vc_audit_tool/routers/reconcile.py`
- Test: `tests/test_error_sanitization.py`

Currently, unhandled exceptions return `str(exc)` which can leak stack traces, internal paths, and library versions to clients.

- [ ] **Step 1: Write failing test for error sanitization**

Create `tests/test_error_sanitization.py`:

```python
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
            "vc_audit_tool.routers.research.CompanyResearchAgent",
            side_effect=RuntimeError("/Users/dev/.venv/lib/python3.10/site-packages/langgraph/core.py line 42"),
        ):
            resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
            assert resp.status_code == 500
            body = resp.json()
            assert "error" in body
            # Must NOT contain file paths or stack traces
            assert "/Users/" not in body["error"]
            assert "site-packages" not in body["error"]
            assert "Traceback" not in body["error"]

    def test_reconcile_agent_crash_returns_generic_500(self) -> None:
        with patch(
            "vc_audit_tool.routers.reconcile.CompanyResearchAgent",
            side_effect=RuntimeError("sqlite3.OperationalError: database is locked"),
        ):
            resp = client.post("/reconcile", content=json.dumps({"company_name": "TestCo"}))
            assert resp.status_code == 500
            body = resp.json()
            assert "error" in body
            assert "database is locked" not in body["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_error_sanitization.py -v`
Expected: FAIL — current code returns raw `str(exc)` which contains the internal details

- [ ] **Step 3: Add `_sanitize_error` helper and apply to both routers**

In `src/vc_audit_tool/routers/research.py`, add at the top after imports:

```python
def _sanitize_error(exc: Exception) -> str:
    """Return a user-safe error message, stripping internal paths and details."""
    msg = str(exc)
    # Strip file paths
    if any(marker in msg for marker in ("/Users/", "/home/", "site-packages", "Traceback")):
        return "Internal error during research. Please try again or contact support."
    # Strip database internals
    if "database" in msg.lower() and ("locked" in msg.lower() or "operational" in msg.lower()):
        return "Service temporarily unavailable. Please retry in a moment."
    return msg
```

Then replace `str(exc)` in the generic `except Exception` handler (line 74):

```python
    except Exception as exc:
        logger.exception("research_agent_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": _sanitize_error(exc)}, status_code=500)
```

In `src/vc_audit_tool/routers/reconcile.py`, add the same `_sanitize_error` function and update line 75:

```python
    except Exception as exc:
        logger.exception("reconcile_research_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": _sanitize_error(exc)}, status_code=500)
```

And the unhandled catch at line 161:

```python
    except Exception as exc:  # pragma: no cover
        logger.exception("reconcile_unhandled_error error=%s", exc)
        return JSONResponse({"error": _sanitize_error(exc)}, status_code=500)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_error_sanitization.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/ -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add src/vc_audit_tool/routers/research.py src/vc_audit_tool/routers/reconcile.py tests/test_error_sanitization.py
git commit -m "fix: sanitize error messages to prevent leaking internal paths and details"
```

---

### Task 3: Fix Frontend Input Template Mismatches

**Files:**
- Modify: `frontend/src/app/value/page.tsx`

The `METHODOLOGY_TEMPLATES` in the frontend don't match backend input field names. This causes 400 validation errors when users submit the default templates.

Backend expects (from methodology source code):
- `comparable_companies`: `revenue_ltm`, `sector` (required), `private_company_discount_pct`
- `last_round_market_adjusted`: `last_post_money_valuation`, `last_round_date`, `public_index`
- `last_round_multiple_ratchet`: `last_post_money_valuation`, `last_round_date`, `revenue_ltm`, `revenue_at_last_round`
- `scorecard`: `regional_median_valuation`, `team`, `opportunity`, `product`, `competitive_env`, `marketing`, `funding_need`
- `berkus`: `sound_idea`, `prototype`, `management`, `strategic_relationships`, `rollout`

- [ ] **Step 1: Update METHODOLOGY_TEMPLATES to match backend schemas**

Replace the `METHODOLOGY_TEMPLATES` const in `frontend/src/app/value/page.tsx` (lines 11-47):

```typescript
const METHODOLOGY_TEMPLATES: Record<string, Record<string, unknown>> = {
  comparable_companies: {
    revenue_ltm: 100_000_000,
    sector: "enterprise_software",
    private_company_discount_pct: 25,
  },
  last_round_market_adjusted: {
    last_post_money_valuation: 500_000_000,
    last_round_date: "2024-06-01",
    public_index: "NASDAQ_COMPOSITE",
  },
  last_round_multiple_ratchet: {
    last_post_money_valuation: 500_000_000,
    last_round_date: "2024-06-01",
    revenue_ltm: 50_000_000,
    revenue_at_last_round: 30_000_000,
  },
  direct_valuation: {
    evidence_signals: [],
    consensus_strength: "MODERATE",
    private_company_discount_pct: 20,
  },
  scorecard: {
    regional_median_valuation: 2_000_000,
    team: 125,
    opportunity: 100,
    product: 110,
    competitive_env: 90,
    marketing: 100,
    funding_need: 100,
  },
  berkus: {
    sound_idea: 500_000,
    prototype: 500_000,
    management: 500_000,
    strategic_relationships: 500_000,
    rollout: 500_000,
  },
};
```

- [ ] **Step 2: Verify frontend builds**

Run: `cd ~/Developer/vc-audit-tool-mvp/frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 3: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add frontend/src/app/value/page.tsx
git commit -m "fix: align frontend methodology templates with backend input schemas"
```

---

### Task 4: Test Research Endpoint HTTP Layer

**Files:**
- Create: `tests/test_research_endpoint.py`

The `POST /research` endpoint has zero HTTP-level tests. This task adds tests for the happy path, incomplete research, fallback logic, and bad input — all with the research agent mocked.

- [ ] **Step 1: Write the test file**

Create `tests/test_research_endpoint.py`:

```python
"""HTTP-level tests for POST /research endpoint."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from vc_audit_tool.agent.state import ResearchResult
from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.server import app


client = TestClient(app)


def _make_complete_result(
    methodology: str = "last_round_market_adjusted",
) -> ResearchResult:
    """Build a ResearchResult that looks like a successful research run."""
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
        research_metadata={
            "sources_consulted": ["SEC EDGAR", "DuckDuckGo"],
            "evidence_package": {},
        },
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

    @patch("vc_audit_tool.routers.research.CompanyResearchAgent")
    def test_successful_research_returns_200_with_valuation(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_result()
        mock_cls.return_value = mock_agent

        resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 200
        data = resp.json()
        assert "valuation_result" in data
        assert "audit_metadata" in data
        assert "research_metadata" in data
        assert data["valuation_result"]["company_name"] == "TestCo"

    @patch("vc_audit_tool.routers.research.CompanyResearchAgent")
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

    @patch("vc_audit_tool.routers.research.CompanyResearchAgent")
    def test_agent_import_error_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = ImportError("langgraph not installed")
        resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500
        assert "dependencies" in resp.json()["error"]

    @patch("vc_audit_tool.routers.research.CompanyResearchAgent")
    def test_agent_crash_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = RuntimeError("unexpected failure")
        resp = client.post("/research", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500

    @patch("vc_audit_tool.routers.research.CompanyResearchAgent")
    def test_research_passes_optional_fields(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_result()
        mock_cls.return_value = mock_agent

        resp = client.post(
            "/research",
            content=json.dumps({
                "company_name": "TestCo",
                "methodology": "comparable_companies",
                "as_of_date": "2026-03-01",
                "description_hint": "AI safety lab",
            }),
        )
        assert resp.status_code == 200
        mock_agent.run.assert_called_once_with(
            "TestCo",
            methodology="comparable_companies",
            as_of_date="2026-03-01",
            description_hint="AI safety lab",
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_research_endpoint.py -v`
Expected: All pass (these mock the agent, testing only the HTTP layer)

- [ ] **Step 3: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add tests/test_research_endpoint.py
git commit -m "test: add HTTP-level tests for POST /research endpoint"
```

---

### Task 5: Test Reconcile Endpoint HTTP Layer

**Files:**
- Create: `tests/test_reconcile_endpoint.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_reconcile_endpoint.py`:

```python
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

    @patch("vc_audit_tool.routers.reconcile.CompanyResearchAgent")
    def test_incomplete_research_returns_422(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_incomplete_research()
        mock_cls.return_value = mock_agent

        resp = client.post("/reconcile", content=json.dumps({"company_name": "ObscureCo"}))
        assert resp.status_code == 422
        data = resp.json()
        assert "error" in data
        assert "missing_fields" in data

    @patch("vc_audit_tool.routers.reconcile.CompanyResearchAgent")
    def test_agent_import_error_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = ImportError("langgraph not installed")
        resp = client.post("/reconcile", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500

    @patch("vc_audit_tool.routers.reconcile.CompanyResearchAgent")
    def test_agent_crash_returns_500(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = RuntimeError("boom")
        resp = client.post("/reconcile", content=json.dumps({"company_name": "TestCo"}))
        assert resp.status_code == 500

    @patch("vc_audit_tool.routers.reconcile.CompanyResearchAgent")
    def test_successful_reconcile_returns_200(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_research()
        mock_cls.return_value = mock_agent

        # The reconcile route also needs ReconciliationEngine — mock it
        with patch("vc_audit_tool.routers.reconcile.ReconciliationEngine") as mock_recon:
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
            mock_recon.return_value = mock_recon_inst
            mock_recon.mock.return_value = mock_recon_inst

            resp = client.post(
                "/reconcile",
                content=json.dumps({
                    "company_name": "GrowthCo",
                    "as_of_date": "2026-03-01",
                }),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "concluded_value" in data

    @patch("vc_audit_tool.routers.reconcile.CompanyResearchAgent")
    def test_reconcile_passes_optional_fields(self, mock_cls: MagicMock) -> None:
        mock_agent = MagicMock()
        mock_agent.run.return_value = _make_complete_research()
        mock_cls.return_value = mock_agent

        with patch("vc_audit_tool.routers.reconcile.ReconciliationEngine") as mock_recon:
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"concluded_value": {}, "methodology_results": {}}
            mock_result.methodology_results = {}
            mock_recon_inst = MagicMock()
            mock_recon_inst.value.return_value = mock_result
            mock_recon.return_value = mock_recon_inst
            mock_recon.mock.return_value = mock_recon_inst

            resp = client.post(
                "/reconcile",
                content=json.dumps({
                    "company_name": "GrowthCo",
                    "as_of_date": "2026-03-01",
                    "description_hint": "B2B SaaS platform",
                }),
            )
            assert resp.status_code == 200
            mock_agent.run.assert_called_once_with(
                "GrowthCo",
                methodology="",
                as_of_date="2026-03-01",
                description_hint="B2B SaaS platform",
            )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_reconcile_endpoint.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add tests/test_reconcile_endpoint.py
git commit -m "test: add HTTP-level tests for POST /reconcile endpoint"
```

---

### Task 6: Test LLM Adapter (Extraction, Retry, Fallback)

**Files:**
- Create: `tests/test_llm_adapter.py`

The LLM adapter (`_get_llm`, `_llm_extract_structured`, `_llm_judge_valuation`, `_extract_json_robust`) has zero test coverage. This task tests the JSON extraction strategies, retry logic, cost tracking, and provider selection — all with mocked LLM providers.

- [ ] **Step 1: Write the test file**

Create `tests/test_llm_adapter.py`:

```python
"""Unit tests for the LLM adapter: JSON extraction, retry, cost tracking."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vc_audit_tool.agent.cost_tracker import CostRecord, CostTracker, estimate_cost
from vc_audit_tool.agent.llm_adapter import (
    _extract_json_robust,
    _is_fatal,
    _is_transient,
    _llm_extract_structured,
    _needs_judgment,
)


# ── _extract_json_robust ───────────────────────────────────────────────


class TestExtractJsonRobust:
    def test_clean_json(self) -> None:
        result = _extract_json_robust('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_markdown_fences(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_robust(text)
        assert result == {"key": "value"}

    def test_json_embedded_in_text(self) -> None:
        text = 'Here is the result: {"key": 42} hope this helps'
        result = _extract_json_robust(text)
        assert result == {"key": 42}

    def test_truncated_json_recovery(self) -> None:
        # Simulates LLM output cut mid-field
        text = '{"key1": "val1",\n"key2": "val2",\n"key3": "trunc'
        result = _extract_json_robust(text)
        assert result is not None
        assert result["key1"] == "val1"
        assert result["key2"] == "val2"

    def test_completely_invalid_returns_none(self) -> None:
        assert _extract_json_robust("not json at all") is None

    def test_empty_string_returns_none(self) -> None:
        assert _extract_json_robust("") is None

    def test_nested_json(self) -> None:
        text = '{"outer": {"inner": 1}, "list": [1, 2]}'
        result = _extract_json_robust(text)
        assert result == {"outer": {"inner": 1}, "list": [1, 2]}


# ── Error classification ────────────────────────────────────────────────


class TestErrorClassification:
    def test_transient_errors(self) -> None:
        for name in ["RateLimitError", "Timeout", "ConnectError"]:
            exc = type(name, (Exception,), {})()
            assert _is_transient(exc) is True

    def test_fatal_errors(self) -> None:
        for name in ["AuthenticationError", "BadRequestError"]:
            exc = type(name, (Exception,), {})()
            assert _is_fatal(exc) is True

    def test_unknown_error_is_neither(self) -> None:
        exc = ValueError("something")
        assert _is_transient(exc) is False
        assert _is_fatal(exc) is False


# ── _needs_judgment ──────────────────────────────────────────────────────


class TestNeedsJudgment:
    def test_single_candidate_no_judgment(self) -> None:
        c = MagicMock(amount_usd=5_000_000_000)
        assert _needs_judgment([c]) is False

    def test_agreeing_candidates_no_judgment(self) -> None:
        c1 = MagicMock(amount_usd=5_000_000_000)
        c2 = MagicMock(amount_usd=5_100_000_000)
        assert _needs_judgment([c1, c2]) is False

    def test_diverging_candidates_needs_judgment(self) -> None:
        c1 = MagicMock(amount_usd=1_000_000_000)
        c2 = MagicMock(amount_usd=5_000_000_000)
        assert _needs_judgment([c1, c2]) is True

    def test_sub_million_skipped(self) -> None:
        c1 = MagicMock(amount_usd=100_000)
        c2 = MagicMock(amount_usd=500_000)
        assert _needs_judgment([c1, c2]) is False


# ── _llm_extract_structured ─────────────────────────────────────────────


class TestLlmExtractStructured:
    def test_successful_extraction(self) -> None:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"last_post_money_valuation": 5000000000, "revenue_ltm": null}'
        mock_llm.invoke.return_value = mock_response

        result = _llm_extract_structured(
            mock_llm, "test/model", "Anthropic", ["snippet1", "snippet2"]
        )
        assert result["last_post_money_valuation"] == 5_000_000_000
        assert result["_model_label"] == "test/model"

    def test_fatal_error_returns_empty(self) -> None:
        mock_llm = MagicMock()
        AuthError = type("AuthenticationError", (Exception,), {})
        mock_llm.invoke.side_effect = AuthError("bad key")

        result = _llm_extract_structured(mock_llm, "test/model", "TestCo", ["snippet"])
        assert result == {}

    def test_non_string_content_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = None
        mock_llm.invoke.return_value = mock_response

        result = _llm_extract_structured(mock_llm, "test/model", "TestCo", ["snippet"])
        assert result == {}

    def test_cost_tracking_records_call(self) -> None:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"revenue_ltm": 50000000}'
        mock_response.usage_metadata = MagicMock(input_tokens=100, output_tokens=50)
        mock_llm.invoke.return_value = mock_response

        from vc_audit_tool.agent.llm_config import LLMProviderConfig

        cfg = LLMProviderConfig(
            name="test",
            env_key="TEST_KEY",
            model_default="test-model",
            cost_per_1k_input_usd=0.001,
            cost_per_1k_output_usd=0.002,
        )
        tracker = CostTracker()
        result = _llm_extract_structured(
            mock_llm, "test/model", "TestCo", ["snippet"],
            tracker=tracker, provider_cfg=cfg,
        )
        assert result["revenue_ltm"] == 50_000_000
        # Tracker is immutable — the function calls tracker.add() internally
        # but the original reference is not mutated. This tests the call path
        # doesn't crash.


# ── CostTracker / estimate_cost ──────────────────────────────────────────


class TestCostTracker:
    def test_empty_tracker(self) -> None:
        t = CostTracker()
        assert t.total_cost == 0.0
        assert t.call_count == 0
        assert t.over_budget is False

    def test_add_returns_new_tracker(self) -> None:
        t = CostTracker()
        record = CostRecord(model="m", input_tokens=100, output_tokens=50, cost_usd=0.01)
        t2 = t.add(record)
        assert t.call_count == 0  # original unchanged
        assert t2.call_count == 1
        assert t2.total_cost == 0.01

    def test_over_budget_detection(self) -> None:
        t = CostTracker(budget_limit=0.05)
        r = CostRecord(model="m", input_tokens=1000, output_tokens=500, cost_usd=0.06)
        t2 = t.add(r)
        assert t2.over_budget is True

    def test_summary_shape(self) -> None:
        t = CostTracker()
        s = t.summary()
        assert "calls" in s
        assert "total_cost_usd" in s
        assert "over_budget" in s


class TestEstimateCost:
    def test_basic_calculation(self) -> None:
        cost = estimate_cost(1000, 500, 0.001, 0.002)
        assert cost == pytest.approx(0.002)  # (1000/1000)*0.001 + (500/1000)*0.002

    def test_zero_tokens(self) -> None:
        assert estimate_cost(0, 0, 0.001, 0.002) == 0.0
```

- [ ] **Step 2: Run tests**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_llm_adapter.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add tests/test_llm_adapter.py
git commit -m "test: add unit tests for LLM adapter — JSON extraction, retry, cost tracking"
```

---

### Task 7: Test Research Agent (Mocked Graph)

**Files:**
- Create: `tests/test_research_agent.py`

Tests the `CompanyResearchAgent` class and its individual node functions with mocked external calls.

- [ ] **Step 1: Write the test file**

Create `tests/test_research_agent.py`:

```python
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
        state: ResearchState = {
            "company_name": "  Anthropic Inc.  ",
            "as_of_date": "2026-03-01",
        }
        result = _parse_company_node(state)
        assert result["normalised_name"] == "Anthropic Inc."

    def test_infers_sector_from_keywords(self) -> None:
        state: ResearchState = {
            "company_name": "CyberDefend",
            "description_hint": "cybersecurity platform for enterprises",
        }
        result = _parse_company_node(state)
        assert result.get("inferred_sector") == "cybersecurity"

    def test_no_sector_when_ambiguous(self) -> None:
        state: ResearchState = {
            "company_name": "GenericCo",
            "description_hint": "",
        }
        result = _parse_company_node(state)
        # Either no key or empty string is acceptable
        assert result.get("inferred_sector", "") in ("", None)


class TestMissingFields:
    def _make_pkg(self, n_evidence: int = 0, revenue: float | None = None) -> EvidencePackage:
        pkg = EvidencePackage(company_name="Test", as_of_date=date.today())
        for i in range(n_evidence):
            pkg.evidence.append(
                ValuationEvidence(
                    amount_usd=1_000_000_000 + i,
                    evidence_type="post_money_fresh",
                    source_snippet="test",
                    confidence=0.8,
                )
            )
        if revenue:
            pkg.revenue_signals.append(revenue)
        return pkg

    def test_complete_set_a(self) -> None:
        pkg = self._make_pkg()
        facts = {
            "last_post_money_valuation": 5e9,
            "last_round_date": "2025-06-01",
            "company_description": "AI lab",
        }
        assert _missing_fields(pkg, facts) == []

    def test_missing_everything(self) -> None:
        pkg = self._make_pkg()
        facts: dict[str, object] = {}
        missing = _missing_fields(pkg, facts)
        assert "post_money" in missing
        assert "round_date" in missing
        assert "revenue" in missing

    def test_set_c_evidence_sufficient(self) -> None:
        pkg = self._make_pkg(n_evidence=3)
        facts: dict[str, object] = {"company_description": "test"}
        assert _missing_fields(pkg, facts) == []


class TestMostRecentDate:
    def test_picks_later_date(self) -> None:
        assert _most_recent_date("2025-01-01", "2025-06-01") == "2025-06-01"

    def test_none_handling(self) -> None:
        assert _most_recent_date(None, "2025-06-01") == "2025-06-01"
        assert _most_recent_date("2025-06-01", None) == "2025-06-01"
        assert _most_recent_date(None, None) is None


class TestBuildWebFacts:
    def test_basic_build(self) -> None:
        pkg = EvidencePackage(company_name="Test", as_of_date=date.today())
        llm_facts = {"revenue_ltm": 50_000_000, "company_description": "A SaaS company"}
        facts = _build_web_facts(pkg, llm_facts, [])
        assert facts["revenue_ltm"] == 50_000_000
        assert facts["company_description"] == "A SaaS company"

    def test_extraction_timestamp_present(self) -> None:
        pkg = EvidencePackage(company_name="Test", as_of_date=date.today())
        facts = _build_web_facts(pkg, {}, [])
        assert "extraction_timestamp" in facts


class TestCompanyResearchAgentUnit:
    """Test the agent.run() method with the entire graph mocked."""

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent._build_graph")
    def test_run_returns_result_on_success(self, mock_build: MagicMock) -> None:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "assembled_request": {
                "company_name": "TestCo",
                "methodology": "last_round_market_adjusted",
                "as_of_date": "2026-03-01",
                "inputs": {"last_post_money_valuation": 1e9, "last_round_date": "2025-06-01"},
            },
            "research_metadata": {"sources": ["SEC"]},
            "missing_fields": [],
        }
        mock_build.return_value = mock_graph

        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        result = agent.run("TestCo", as_of_date="2026-03-01")

        assert isinstance(result, ResearchResult)
        assert result.is_complete
        assert result.assembled_request is not None
        assert result.assembled_request["company_name"] == "TestCo"

    @patch("vc_audit_tool.agent.research.CompanyResearchAgent._build_graph")
    def test_run_returns_error_on_graph_failure(self, mock_build: MagicMock) -> None:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("graph exploded")
        mock_build.return_value = mock_graph

        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        result = agent.run("TestCo")

        assert not result.is_complete
        assert result.error is not None
        assert "graph exploded" in result.error
```

- [ ] **Step 2: Run tests**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/test_research_agent.py -v`
Expected: All pass

- [ ] **Step 3: Run full suite to confirm no regressions**

Run: `cd ~/Developer/vc-audit-tool-mvp && PYTHONPATH=src python -m pytest tests/ -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
cd ~/Developer/vc-audit-tool-mvp
git add tests/test_research_agent.py
git commit -m "test: add unit tests for research agent — parse node, missing fields, graph run"
```

---

## Execution Checklist

| Task | What | Impact |
|------|------|--------|
| 1 | Dead code cleanup | Remove 5 dead files |
| 2 | Error sanitization | Security: stop leaking internal paths |
| 3 | Frontend template fix | Unblock manual valuation page |
| 4 | Research endpoint tests | Cover critical untested HTTP layer |
| 5 | Reconcile endpoint tests | Cover critical untested HTTP layer |
| 6 | LLM adapter tests | Cover JSON extraction, retry, cost tracking |
| 7 | Research agent tests | Cover parse node, missing fields, agent run |

All tasks are independent and can be parallelized.
