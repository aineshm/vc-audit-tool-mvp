"""FastAPI server -- JSON API + optional web UI + SQLite persistence.

Routes
------
GET  /health           -> liveness probe
POST /value            -> run valuation, return auditable JSON
POST /research         -> run research agent, then single-method valuation
POST /reconcile        -> run multi-methodology reconciled valuation
GET  /                 -> HTML single-page UI
POST /api/value        -> run valuation, persist, return JSON
GET  /api/runs         -> list recent runs (summary)
GET  /api/runs/{id}    -> full payload for a single run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.store import ValuationStore

logger = logging.getLogger("vc_audit_tool.server")

engine = ValuationEngine()
store = ValuationStore()

app = FastAPI(
    title="VC Audit Tool",
    description="Auditable valuation engine for private VC portfolio companies.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_json(request: Request) -> dict[str, Any]:
    """Read and parse the JSON body, raising JSONDecodeError on failure."""
    body = await request.body()
    result: dict[str, Any] = json.loads(body)
    return result


def _run_valuation(payload: dict[str, Any], *, persist: bool = False) -> JSONResponse:
    """Run the engine and optionally persist to the store."""
    start = time.monotonic()
    try:
        result = engine.evaluate_from_dict(payload)
        result_dict = result.to_dict()
        if persist:
            store.save(result_dict)
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "valuation_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
            result.company_name,
            result.methodology,
            result.request_id,
            elapsed_ms,
        )
        return JSONResponse(result_dict, status_code=200)
    except (ValidationError, DataSourceError) as exc:
        logger.warning("validation_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # pragma: no cover
        logger.exception("unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Core API routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/value")
async def post_value(request: Request) -> JSONResponse:
    """Run a valuation and return the auditable envelope."""
    try:
        payload = await _read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)
    return _run_valuation(payload, persist=False)


@app.post("/research")
async def post_research(request: Request) -> JSONResponse:
    """Run the research agent, then pass assembled inputs to the engine.

    Request body::

        {
          "company_name": "Anthropic",
          "methodology": "comparable_companies",  // optional
          "as_of_date": "2026-02-22"               // optional
        }

    Response extends the standard valuation envelope with a
    ``research_metadata`` key that is clearly separated from
    ``valuation_result``.
    """
    try:
        payload = await _read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    company_name = payload.get("company_name")
    if not company_name or not isinstance(company_name, str):
        return JSONResponse(
            {"error": "Missing required field: 'company_name'."},
            status_code=400,
        )

    methodology = payload.get("methodology", "")
    as_of_date = payload.get("as_of_date", "")
    description_hint = payload.get("description_hint", "")

    start = time.monotonic()
    try:
        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        research = agent.run(
            company_name,
            methodology=methodology or "",
            as_of_date=as_of_date or "",
            description_hint=description_hint or "",
        )
    except ImportError as exc:
        return JSONResponse(
            {"error": f"Research agent dependencies not installed: {exc}"},
            status_code=500,
        )
    except Exception as exc:
        logger.exception("research_agent_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not research.is_complete:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "research_incomplete company=%s missing=%s elapsed_ms=%.1f",
            company_name,
            research.missing_fields,
            elapsed_ms,
        )
        return JSONResponse(
            {
                "error": "Could not assemble complete valuation inputs.",
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
            },
            status_code=422,
        )

    # Run the engine with assembled inputs
    try:
        result = engine.evaluate_from_dict(research.assembled_request)  # type: ignore[arg-type]
        result_dict = result.to_dict()
        # Attach research_metadata as a separate top-level key
        result_dict["research_metadata"] = research.research_metadata
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "research_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
            result.company_name,
            result.methodology,
            result.request_id,
            elapsed_ms,
        )
        return JSONResponse(result_dict, status_code=200)
    except (ValidationError, DataSourceError) as exc:
        logger.warning("research_valuation_error error=%s", exc)
        return JSONResponse(
            {
                "error": str(exc),
                "research_metadata": research.research_metadata,
            },
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("research_unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/reconcile")
async def post_reconcile(request: Request) -> JSONResponse:
    """Run the research agent, profile the company, then reconcile.

    Uses all applicable methodologies (weighted by stage / data rules)
    and returns a single reconciled valuation with divergence analysis.

    Request body::

        {
          "company_name": "Anthropic",
          "as_of_date": "2026-02-22",          // optional
          "description_hint": "AI safety lab"   // optional
        }

    Response: ``ReconciledValuation.to_dict()`` envelope.
    """
    try:
        payload = await _read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    company_name = payload.get("company_name")
    if not company_name or not isinstance(company_name, str):
        return JSONResponse(
            {"error": "Missing required field: 'company_name'."},
            status_code=400,
        )

    as_of_date_str = payload.get("as_of_date", "")
    description_hint = payload.get("description_hint", "")

    start = time.monotonic()

    # ── Step 1: Research ───────────────────────────────────────────────
    try:
        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        research = agent.run(
            company_name,
            methodology="",  # let reconciliation engine pick
            as_of_date=as_of_date_str or "",
            description_hint=description_hint or "",
        )
    except ImportError as exc:
        return JSONResponse(
            {"error": f"Research agent dependencies not installed: {exc}"},
            status_code=500,
        )
    except Exception as exc:
        logger.exception("reconcile_research_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not research.is_complete:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "reconcile_incomplete company=%s missing=%s elapsed_ms=%.1f",
            company_name,
            research.missing_fields,
            elapsed_ms,
        )
        return JSONResponse(
            {
                "error": "Could not assemble complete valuation inputs.",
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
            },
            status_code=422,
        )

    # ── Step 2: Build profile + data package ──────────────────────────
    try:
        from datetime import date as _date

        from vc_audit_tool.reconciliation.engine import ReconciliationEngine
        from vc_audit_tool.reconciliation.models import DataPackage
        from vc_audit_tool.reconciliation.profiler import CompanyProfiler

        assembled = research.assembled_request
        assert assembled is not None  # guarded by is_complete

        aod = _date.fromisoformat(as_of_date_str) if as_of_date_str else _date.today()

        profile = (
            research.company_profile
            if research.company_profile is not None
            else CompanyProfiler.build_from_dict(
                assembled,
                research.research_metadata,
                aod,
            )
        )

        data_package = DataPackage.from_assembled_request(assembled, aod)

        recon_engine = ReconciliationEngine()
        result = recon_engine.value(
            profile=profile,
            data_package=data_package,
            as_of_date=aod,
            company_name=company_name,
            research_metadata=research.research_metadata,
        )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "reconcile_ok company=%s methods=%d elapsed_ms=%.1f",
            company_name,
            len(result.methodology_results),
            elapsed_ms,
        )
        return JSONResponse(result.to_dict(), status_code=200)

    except (ValidationError, DataSourceError) as exc:
        logger.warning("reconcile_valuation_error error=%s", exc)
        return JSONResponse(
            {
                "error": str(exc),
                "research_metadata": research.research_metadata,
            },
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("reconcile_unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Web UI routes (superset: includes /api/value with persistence)
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def web_root() -> HTMLResponse:
    """Serve the single-page web UI."""
    return HTMLResponse(HTML_PAGE)


@app.post("/api/value")
async def api_value(request: Request) -> JSONResponse:
    """Run a valuation, persist to SQLite, return JSON (used by the web UI)."""
    try:
        payload = await _read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)
    return _run_valuation(payload, persist=True)


@app.get("/api/runs")
def api_runs() -> Any:
    """List recent valuation runs (summary only)."""
    return store.list_runs()


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str) -> JSONResponse:
    """Return the full payload for a single run."""
    run = store.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return JSONResponse(run, status_code=200)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VC Audit Tool FastAPI service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--db", default="valuation_runs.db", help="SQLite database path.")
    parser.add_argument(
        "--mode",
        choices=["live", "mock"],
        default="live",
        help="Data-source mode for valuation engine (default: live).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO).",
    )
    return parser


def main() -> int:
    import uvicorn

    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # ``--mode`` is authoritative and overrides ``VC_AUDIT_MOCK``.
    os.environ["VC_AUDIT_MOCK"] = "1" if args.mode == "mock" else "0"

    global engine  # noqa: PLW0603
    engine = ValuationEngine()

    # Re-initialise the module-level store with the user-chosen DB path.
    global store  # noqa: PLW0603
    store = ValuationStore(Path(args.db))

    logger.info("starting FastAPI server on http://%s:%d mode=%s", args.host, args.port, args.mode)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    store.close()
    return 0


# ---------------------------------------------------------------------------
# Embedded HTML template (single-page app, no build step)
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VC Audit Tool</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
  :root{
    --sand:#f2efe5;
    --ink:#142321;
    --muted:#4d615e;
    --card:#fffdf7;
    --line:#d8d4c3;
    --teal:#0d7a78;
    --teal-dark:#0a5e5d;
    --orange:#c85e28;
    --error:#a11f14;
    --ok:#1f7a3a;
    --radius:14px;
    --shadow:0 12px 30px rgba(20,35,33,.12);
  }
  *,*::before,*::after{box-sizing:border-box}
  body{
    margin:0;
    min-height:100vh;
    font-family:'Space Grotesk', 'Avenir Next', 'Segoe UI', sans-serif;
    color:var(--ink);
    background:
      radial-gradient(1200px 600px at 5% -10%, #f7cfa8 0%, transparent 45%),
      radial-gradient(1000px 500px at 95% 110%, #9ed4cd 0%, transparent 45%),
      linear-gradient(160deg, #f8f4e7 0%, #efe6d0 100%);
  }
  .shell{
    width:min(1240px, 96vw);
    margin:24px auto;
    display:grid;
    grid-template-columns:300px 1fr;
    gap:18px;
  }
  .panel{
    background:var(--card);
    border:1px solid var(--line);
    border-radius:var(--radius);
    box-shadow:var(--shadow);
  }
  .sidebar{padding:14px;max-height:calc(100vh - 48px);overflow:auto}
  .title{
    margin:0 0 4px;
    font-size:1.32rem;
    letter-spacing:.01em;
  }
  .sub{
    margin:0 0 16px;
    color:var(--muted);
    font-size:.9rem;
  }
  .runs-title{
    margin:0 0 10px;
    font-size:.78rem;
    text-transform:uppercase;
    letter-spacing:.12em;
    color:var(--muted);
  }
  .run-item{
    border:1px solid var(--line);
    border-radius:10px;
    background:#fff;
    padding:10px;
    margin-bottom:8px;
    cursor:pointer;
    transition:transform .16s ease, border-color .16s ease;
  }
  .run-item:hover{transform:translateY(-1px);border-color:var(--teal)}
  .run-item .company{font-weight:700;font-size:.92rem}
  .run-item .meta{font-family:'IBM Plex Mono', monospace;color:var(--muted);font-size:.75rem}
  .empty{
    border:1px dashed var(--line);
    border-radius:10px;
    padding:16px;
    text-align:center;
    color:var(--muted);
    font-size:.87rem;
    background:#fff;
  }
  .main{padding:18px}
  .top-row{
    display:flex;
    justify-content:space-between;
    gap:16px;
    flex-wrap:wrap;
    align-items:flex-end;
    margin-bottom:14px;
  }
  .kicker{
    margin:0;
    font-size:.78rem;
    text-transform:uppercase;
    letter-spacing:.14em;
    color:var(--teal-dark);
  }
  .headline{
    margin:2px 0 0;
    font-size:1.72rem;
    line-height:1.15;
  }
  .chip{
    border:1px solid var(--line);
    border-radius:999px;
    padding:6px 10px;
    background:#fff;
    color:var(--muted);
    font-size:.79rem;
    font-family:'IBM Plex Mono', monospace;
  }
  .error{
    display:none;
    background:#ffd7d1;
    border:1px solid #eaa69d;
    color:var(--error);
    border-radius:10px;
    padding:10px 12px;
    margin-bottom:12px;
    font-size:.9rem;
  }
  .grid{
    display:grid;
    grid-template-columns:repeat(12,minmax(0,1fr));
    gap:10px;
    margin-bottom:12px;
  }
  .mode-card{
    border:1px solid var(--line);
    border-radius:12px;
    background:#fff;
    padding:12px;
    margin-bottom:12px;
  }
  .mode-title{
    margin:0 0 6px;
    font-size:1rem;
  }
  .mode-copy{
    margin:0 0 10px;
    color:var(--muted);
    font-size:.87rem;
  }
  .manual-mode{
    border:1px solid var(--line);
    border-radius:12px;
    background:#fff;
    padding:10px 12px;
  }
  .manual-mode summary{
    cursor:pointer;
    font-weight:700;
    color:var(--teal-dark);
    margin-bottom:10px;
  }
  .manual-mode[open] summary{
    margin-bottom:12px;
  }
  .field{display:flex;flex-direction:column;gap:4px;grid-column:span 4}
  .field.wide{grid-column:span 6}
  .field.full{grid-column:span 12}
  .field label{font-size:.77rem;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
  .field input,.field select,.field textarea{
    border:1px solid var(--line);
    background:#fff;
    color:var(--ink);
    border-radius:10px;
    padding:9px 10px;
    font-size:.9rem;
    font-family:inherit;
  }
  .field textarea{min-height:68px;resize:vertical}
  .field input:focus,.field select:focus,.field textarea:focus{outline:2px solid #94d5d4;outline-offset:1px}
  .method-card{
    display:none;
    border:1px solid var(--line);
    border-radius:12px;
    padding:12px;
    background:#fff;
  }
  .method-card.active{display:block}
  .method-title{margin:0 0 6px;font-size:.98rem}
  .actions{
    display:flex;
    gap:8px;
    flex-wrap:wrap;
    margin-top:10px;
  }
  .btn{
    border:none;
    border-radius:11px;
    padding:10px 14px;
    font-size:.9rem;
    font-weight:700;
    cursor:pointer;
    transition:transform .14s ease, filter .14s ease;
  }
  .btn:hover{transform:translateY(-1px);filter:brightness(1.03)}
  .btn:disabled{opacity:.55;cursor:not-allowed;transform:none}
  .btn-primary{background:var(--teal);color:#fff}
  .btn-secondary{background:#e8f4f3;color:var(--teal-dark)}
  .btn-warm{background:#f5e3d9;color:#8f3f1a}
  .source-badge{
    display:inline-flex;
    align-items:center;
    padding:2px 8px;
    border-radius:999px;
    font-size:.74rem;
    font-weight:700;
    letter-spacing:.04em;
    text-transform:uppercase;
    background:#e8f4f3;
    color:#0e5857;
    margin-left:8px;
  }
  .report{
    margin-top:14px;
    border:1px solid var(--line);
    border-radius:12px;
    padding:14px;
    background:#fff;
  }
  .report h2{margin:0 0 8px;font-size:1.1rem}
  .value{
    font-size:1.95rem;
    color:var(--ok);
    font-weight:700;
    margin:8px 0 14px;
  }
  .mono{font-family:'IBM Plex Mono', monospace}
  .section{margin:12px 0}
  .section h3{
    margin:0 0 5px;
    font-size:.78rem;
    letter-spacing:.1em;
    text-transform:uppercase;
    color:var(--muted);
  }
  .item{
    border-top:1px solid #efeee8;
    padding:6px 0;
    font-size:.88rem;
  }
  .pills{display:flex;flex-wrap:wrap;gap:6px}
  .pill{
    border-radius:999px;
    font-size:.75rem;
    padding:3px 8px;
    background:#dff4f3;
    color:#0e5857;
  }
  pre{
    margin:0;
    border:1px solid var(--line);
    border-radius:10px;
    background:#f8f7f1;
    padding:10px;
    font-size:.78rem;
    overflow:auto;
  }
  @media (max-width:1040px){
    .shell{grid-template-columns:1fr}
    .sidebar{max-height:none}
  }
  @media (max-width:760px){
    .field,.field.wide{grid-column:span 12}
    .headline{font-size:1.4rem}
  }
</style>
</head>
<body>
  <div class="shell">
    <aside class="panel sidebar">
      <h1 class="title">VC Audit Tool</h1>
      <p class="sub">Auditable valuation workflows with live or mock sources.</p>
      <div class="runs-title">Saved Runs (/api/value)</div>
      <div id="runs-list"><div class="empty">No runs yet.</div></div>
    </aside>

    <main class="panel main">
      <div class="top-row">
        <div>
          <p class="kicker">Valuation Workbench</p>
          <h2 class="headline">Research-first valuation with optional advanced manual inputs</h2>
        </div>
        <span class="chip">Endpoints: /research, /reconcile, /api/value</span>
      </div>

      <div id="error-banner" class="error"></div>

      <section class="mode-card" id="research-mode">
        <h3 class="mode-title">Research Mode (Default)</h3>
        <p class="mode-copy">Methodology is selected automatically unless you explicitly override it.</p>
        <div class="grid">
          <div class="field">
            <label for="company_name">Company Name</label>
            <input id="company_name" value="Basis AI">
          </div>
          <div class="field">
            <label for="as_of_date">As-of Date (optional)</label>
            <input id="as_of_date" type="date">
          </div>
          <div class="field wide">
            <label for="description_hint">Description Hint (recommended)</label>
            <input id="description_hint" placeholder="AI-native compliance software for enterprise fintech teams">
          </div>
          <div class="field full">
            <label><input id="research_override_enabled" type="checkbox"> Override methodology (advanced)</label>
            <select id="research_methodology" disabled>
              <option value="">Auto-select</option>
              <option value="last_round_market_adjusted">last_round_market_adjusted</option>
              <option value="comparable_companies">comparable_companies</option>
              <option value="last_round_multiple_ratchet">last_round_multiple_ratchet</option>
            </select>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-primary" id="run-research">Run /research</button>
          <button class="btn btn-warm" id="run-reconcile">Run /reconcile</button>
        </div>
      </section>

      <details class="manual-mode" id="manual-mode">
        <summary>Advanced Manual Mode (/api/value)</summary>
        <p class="mode-copy">You must provide methodology-specific structured inputs in manual mode (including sector for manual comparable_companies requests).</p>

        <div class="grid">
          <div class="field full">
            <label for="methodology">Manual Methodology</label>
            <select id="methodology">
              <option value="last_round_market_adjusted">last_round_market_adjusted</option>
              <option value="comparable_companies">comparable_companies</option>
              <option value="last_round_multiple_ratchet">last_round_multiple_ratchet</option>
              <option value="scorecard">scorecard</option>
              <option value="berkus">berkus</option>
            </select>
          </div>
        </div>

      <section id="inputs-last_round_market_adjusted" class="method-card active">
        <h3 class="method-title">Last Round Market Adjusted</h3>
        <div class="grid">
          <div class="field">
            <label>Last Post-Money ($)</label>
            <input id="lr_valuation" type="number" value="100000000">
          </div>
          <div class="field">
            <label>Last Round Date</label>
            <input id="lr_round_date" type="date">
          </div>
          <div class="field">
            <label>Public Index</label>
            <select id="lr_index">
              <option value="NASDAQ_COMPOSITE">NASDAQ_COMPOSITE</option>
              <option value="RUSSELL_2000">RUSSELL_2000</option>
              <option value="SP500">SP500</option>
            </select>
          </div>
        </div>
      </section>

      <section id="inputs-comparable_companies" class="method-card">
        <h3 class="method-title">Comparable Companies</h3>
        <div class="grid">
          <div class="field">
            <label>Sector</label>
            <select id="cc_sector">
              <option value="enterprise_software">enterprise_software</option>
              <option value="cybersecurity">cybersecurity</option>
              <option value="infrastructure_software">infrastructure_software</option>
              <option value="semiconductors">semiconductors</option>
            </select>
          </div>
          <div class="field">
            <label>LTM Revenue ($)</label>
            <input id="cc_revenue" type="number" value="10000000">
          </div>
          <div class="field">
            <label>Statistic</label>
            <select id="cc_statistic">
              <option value="median">median</option>
              <option value="mean">mean</option>
            </select>
          </div>
          <div class="field">
            <label>Private Discount (%)</label>
            <input id="cc_discount" type="number" value="20" min="0" max="100">
          </div>
          <div class="field full">
            <label>Target Description (optional)</label>
            <textarea id="cc_target_description" placeholder="Cloud-native workflow automation platform for enterprise legal ops teams"></textarea>
          </div>
        </div>
      </section>

      <section id="inputs-last_round_multiple_ratchet" class="method-card">
        <h3 class="method-title">Last Round Multiple Ratchet</h3>
        <div class="grid">
          <div class="field">
            <label>Last Post-Money ($)</label>
            <input id="mr_post_money" type="number" value="100000000">
          </div>
          <div class="field">
            <label>Revenue at Last Round ($)</label>
            <input id="mr_revenue_last" type="number" value="10000000">
          </div>
          <div class="field">
            <label>Current Revenue ($)</label>
            <input id="mr_revenue_current" type="number" value="12000000">
          </div>
          <div class="field">
            <label>Sector</label>
            <select id="mr_sector">
              <option value="enterprise_software">enterprise_software</option>
              <option value="cybersecurity">cybersecurity</option>
              <option value="infrastructure_software">infrastructure_software</option>
              <option value="semiconductors">semiconductors</option>
            </select>
          </div>
          <div class="field">
            <label>Statistic</label>
            <select id="mr_statistic">
              <option value="median">median</option>
              <option value="mean">mean</option>
            </select>
          </div>
          <div class="field">
            <label>Private Discount (%)</label>
            <input id="mr_discount" type="number" value="20" min="0" max="100">
          </div>
          <div class="field full">
            <label>Target Description (optional)</label>
            <textarea id="mr_target_description" placeholder="B2B platform for security posture automation in regulated industries"></textarea>
          </div>
        </div>
      </section>

      <section id="inputs-scorecard" class="method-card">
        <h3 class="method-title">Scorecard</h3>
        <div class="grid">
          <div class="field">
            <label>Regional Median Pre-Money ($)</label>
            <input id="sc_median" type="number" value="6000000">
          </div>
          <div class="field"><label>strength_of_team</label><input id="sc_team" type="number" value="1.2" step="0.1" min="0" max="2"></div>
          <div class="field"><label>size_of_opportunity</label><input id="sc_opp" type="number" value="1.1" step="0.1" min="0" max="2"></div>
          <div class="field"><label>product_technology</label><input id="sc_prod" type="number" value="1.0" step="0.1" min="0" max="2"></div>
          <div class="field"><label>competitive_environment</label><input id="sc_comp" type="number" value="0.9" step="0.1" min="0" max="2"></div>
          <div class="field"><label>marketing_sales_channels</label><input id="sc_mkt" type="number" value="1.0" step="0.1" min="0" max="2"></div>
          <div class="field"><label>need_for_additional_investment</label><input id="sc_need" type="number" value="1.0" step="0.1" min="0" max="2"></div>
          <div class="field"><label>other</label><input id="sc_other" type="number" value="1.0" step="0.1" min="0" max="2"></div>
        </div>
      </section>

      <section id="inputs-berkus" class="method-card">
        <h3 class="method-title">Berkus</h3>
        <div class="grid">
          <div class="field">
            <label>Max Pre-Money ($)</label>
            <input id="bk_max" type="number" value="2500000">
          </div>
          <div class="field"><label>sound_idea</label><input id="bk_sound" type="number" value="1" step="0.1" min="0" max="1"></div>
          <div class="field"><label>prototype</label><input id="bk_proto" type="number" value="0.8" step="0.1" min="0" max="1"></div>
          <div class="field"><label>quality_management</label><input id="bk_mgmt" type="number" value="1" step="0.1" min="0" max="1"></div>
          <div class="field"><label>strategic_relationships</label><input id="bk_rel" type="number" value="0.6" step="0.1" min="0" max="1"></div>
          <div class="field"><label>product_rollout</label><input id="bk_rollout" type="number" value="0.5" step="0.1" min="0" max="1"></div>
        </div>
      </section>

      <div class="actions">
        <button class="btn btn-secondary" id="run-value">Run manual /api/value</button>
      </div>
      </details>

      <div id="report"></div>
    </main>
  </div>

<script>
(function(){
  var $ = function(sel){ return document.querySelector(sel); };
  var methodSelect = $('#methodology');
  var researchOverrideEnabled = $('#research_override_enabled');
  var researchMethodology = $('#research_methodology');
  var report = $('#report');
  var errorBanner = $('#error-banner');

  function todayISO(){
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function setDefaults(){
    var today = todayISO();
    $('#as_of_date').value = today;
    $('#lr_round_date').value = '2024-06-30';
    researchOverrideEnabled.checked = false;
    researchMethodology.value = '';
    researchMethodology.disabled = true;
  }

  function setLoading(btn, isLoading, text){
    btn.disabled = isLoading;
    btn.textContent = text;
  }

  function showError(msg){
    errorBanner.textContent = msg;
    errorBanner.style.display = 'block';
  }

  function clearError(){
    errorBanner.style.display = 'none';
    errorBanner.textContent = '';
  }

  function toggleMethodCard(){
    var selected = methodSelect.value;
    document.querySelectorAll('.method-card').forEach(function(el){
      el.classList.toggle('active', el.id === 'inputs-' + selected);
    });
  }

  function toggleResearchOverride(){
    researchMethodology.disabled = !researchOverrideEnabled.checked;
    if (!researchOverrideEnabled.checked){
      researchMethodology.value = '';
    }
  }

  function val(id){ return $(id).value; }
  function num(id){ return Number($(id).value); }

  function buildValuePayload(){
    var m = val('#methodology');
    var payload = {
      company_name: val('#company_name'),
      methodology: m,
      as_of_date: val('#as_of_date'),
      inputs: {}
    };

    if (m === 'last_round_market_adjusted'){
      payload.inputs = {
        last_post_money_valuation: num('#lr_valuation'),
        last_round_date: val('#lr_round_date'),
        public_index: val('#lr_index')
      };
      return payload;
    }

    if (m === 'comparable_companies'){
      payload.inputs = {
        sector: val('#cc_sector'),
        revenue_ltm: num('#cc_revenue'),
        statistic: val('#cc_statistic'),
        private_company_discount_pct: num('#cc_discount')
      };
      if (val('#cc_target_description').trim()){
        payload.inputs.target_description = val('#cc_target_description').trim();
      }
      return payload;
    }

    if (m === 'last_round_multiple_ratchet'){
      payload.inputs = {
        last_post_money_valuation: num('#mr_post_money'),
        revenue_at_last_round: num('#mr_revenue_last'),
        current_revenue: num('#mr_revenue_current'),
        sector: val('#mr_sector'),
        statistic: val('#mr_statistic'),
        private_company_discount_pct: num('#mr_discount')
      };
      if (val('#mr_target_description').trim()){
        payload.inputs.target_description = val('#mr_target_description').trim();
      }
      return payload;
    }

    if (m === 'scorecard'){
      payload.inputs = {
        regional_median_pre_money: num('#sc_median'),
        factors: {
          strength_of_team: num('#sc_team'),
          size_of_opportunity: num('#sc_opp'),
          product_technology: num('#sc_prod'),
          competitive_environment: num('#sc_comp'),
          marketing_sales_channels: num('#sc_mkt'),
          need_for_additional_investment: num('#sc_need'),
          other: num('#sc_other')
        }
      };
      return payload;
    }

    payload.inputs = {
      max_pre_money_valuation: num('#bk_max'),
      factors: {
        sound_idea: num('#bk_sound'),
        prototype: num('#bk_proto'),
        quality_management: num('#bk_mgmt'),
        strategic_relationships: num('#bk_rel'),
        product_rollout: num('#bk_rollout')
      }
    };
    return payload;
  }

  function fmtMoney(n){
    return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }

  function renderValuation(env, sourceLabel){
    var r = env.valuation_result || {};
    var meta = env.audit_metadata || {};
    var fv = (r.estimated_fair_value || {});
    var html = '<div class="report">';
    html += '<h2>' + (r.company_name || 'Unknown') + ' - ' + String(r.methodology || '').replaceAll('_', ' ');
    html += '<span class="source-badge">' + (sourceLabel || 'Response') + '</span></h2>';
    if (typeof fv.amount !== 'undefined'){
      html += '<div class="value">' + fmtMoney(fv.amount) + ' ' + (fv.currency || 'USD') + '</div>';
    }
    if (r.confidence_indicators){
      html += '<div class="section"><h3>Confidence Indicators</h3><div class="pills">';
      Object.entries(r.confidence_indicators).forEach(function(entry){
        html += '<span class="pill">' + entry[0] + ': ' + entry[1] + '</span>';
      });
      html += '</div></div>';
    }
    if (Array.isArray(r.derivation_steps) && r.derivation_steps.length){
      html += '<div class="section"><h3>Derivation Steps</h3>';
      r.derivation_steps.forEach(function(step, idx){
        html += '<div class="item">' + (idx + 1) + '. ' + step + '</div>';
      });
      html += '</div>';
    }
    if (Array.isArray(r.assumptions) && r.assumptions.length){
      html += '<div class="section"><h3>Assumptions</h3>';
      r.assumptions.forEach(function(item){
        html += '<div class="item">' + item + '</div>';
      });
      html += '</div>';
    }
    if (Array.isArray(r.citations) && r.citations.length){
      html += '<div class="section"><h3>Citations</h3>';
      r.citations.forEach(function(c){
        html += '<div class="item"><strong>' + (c.label || '') + '</strong>: ' + (c.detail || '');
        if (c.dataset_version){ html += '<div class="mono">v=' + c.dataset_version + '</div>'; }
        html += '</div>';
      });
      html += '</div>';
    }
    if (meta.request_id){
      html += '<div class="section mono">request_id=' + meta.request_id + '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderReconciled(env, sourceLabel){
    var cv = env.concluded_value || {};
    var recon = env.reconciliation || {};
    var html = '<div class="report">';
    html += '<h2>Reconciled Valuation<span class="source-badge">' + (sourceLabel || 'Response') + '</span></h2>';
    if (typeof cv.point_estimate !== 'undefined'){
      html += '<div class="value">' + fmtMoney(cv.point_estimate) + ' ' + (cv.currency || 'USD') + '</div>';
      html += '<div class="mono">Range: ' + fmtMoney(cv.range_low) + ' to ' + fmtMoney(cv.range_high) + '</div>';
    }
    if (Array.isArray(recon.methodology_weights) && recon.methodology_weights.length){
      html += '<div class="section"><h3>Methodology Weights</h3>';
      recon.methodology_weights.forEach(function(w){
        html += '<div class="item">' + w.methodology + ' -> ' + w.weight + '</div>';
      });
      html += '</div>';
    }
    if (recon.reconciliation_rationale){
      html += '<div class="section"><h3>Rationale</h3><div class="item">' + recon.reconciliation_rationale + '</div></div>';
    }
    html += '</div>';
    return html;
  }

  function renderEnvelope(env, sourceLabel){
    if (env.valuation_result){ return renderValuation(env, sourceLabel); }
    if (env.concluded_value){ return renderReconciled(env, sourceLabel); }
    return '<div class="report"><h2>Response</h2><pre>' + JSON.stringify(env, null, 2) + '</pre></div>';
  }

  async function postJSON(url, payload){
    var resp = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    var data = await resp.json();
    if (!resp.ok){
      throw new Error(data.error || ('Request failed (' + resp.status + ')'));
    }
    return data;
  }

  function buildResearchPayload(includeMethodology){
    var payload = {
      company_name: val('#company_name'),
      as_of_date: val('#as_of_date'),
      description_hint: val('#description_hint')
    };
    if (includeMethodology && researchOverrideEnabled.checked && val('#research_methodology')){
      payload.methodology = val('#research_methodology');
    }
    return payload;
  }

  $('#run-value').addEventListener('click', async function(){
    clearError();
    var btn = this;
    setLoading(btn, true, 'Running /api/value...');
    try{
      var data = await postJSON('/api/value', buildValuePayload());
      report.innerHTML = renderEnvelope(data, 'Manual');
      await loadRuns();
    }catch(err){
      showError(err.message || String(err));
    }finally{
      setLoading(btn, false, 'Run manual /api/value');
    }
  });

  $('#run-research').addEventListener('click', async function(){
    clearError();
    var btn = this;
    setLoading(btn, true, 'Running /research...');
    try{
      var data = await postJSON('/research', buildResearchPayload(true));
      report.innerHTML = renderEnvelope(data, 'Research');
    }catch(err){
      showError(err.message || String(err));
    }finally{
      setLoading(btn, false, 'Run /research');
    }
  });

  $('#run-reconcile').addEventListener('click', async function(){
    clearError();
    var btn = this;
    setLoading(btn, true, 'Running /reconcile...');
    try{
      var data = await postJSON('/reconcile', buildResearchPayload(false));
      report.innerHTML = renderEnvelope(data, 'Reconcile');
    }catch(err){
      showError(err.message || String(err));
    }finally{
      setLoading(btn, false, 'Run /reconcile');
    }
  });

  async function loadRuns(){
    try{
      var res = await fetch('/api/runs');
      if (!res.ok){ return; }
      var runs = await res.json();
      var list = $('#runs-list');
      if (!Array.isArray(runs) || runs.length === 0){
        list.innerHTML = '<div class="empty">No runs yet.</div>';
        return;
      }
      list.innerHTML = runs.map(function(r){
        return '<div class="run-item" data-id="' + r.request_id + '">' +
          '<div class="company">' + r.company_name + '</div>' +
          '<div class="meta">' + String(r.methodology || '').replaceAll('_', ' ') + ' | ' + r.as_of_date + '</div>' +
          '<div class="meta">' + fmtMoney(r.fair_value) + '</div>' +
        '</div>';
      }).join('');
      list.querySelectorAll('.run-item').forEach(function(el){
        el.addEventListener('click', async function(){
          var response = await fetch('/api/runs/' + el.dataset.id);
          if (!response.ok){ return; }
          var data = await response.json();
          report.innerHTML = renderEnvelope(data, 'Manual');
        });
      });
    }catch(_err){}
  }

  methodSelect.addEventListener('change', toggleMethodCard);
  researchOverrideEnabled.addEventListener('change', toggleResearchOverride);
  setDefaults();
  toggleResearchOverride();
  toggleMethodCard();
  loadRuns();
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
