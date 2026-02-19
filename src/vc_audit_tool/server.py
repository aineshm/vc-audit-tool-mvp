"""FastAPI server -- JSON API + optional web UI + SQLite persistence.

Routes
------
GET  /health           -> liveness probe
POST /value            -> run valuation, return auditable JSON
GET  /                 -> HTML single-page UI
POST /api/value        -> run valuation, persist, return JSON
GET  /api/runs         -> list recent runs (summary)
GET  /api/runs/{id}    -> full payload for a single run
"""

from __future__ import annotations

import argparse
import json
import logging
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

    # Re-initialise the module-level store with the user-chosen DB path.
    global store  # noqa: PLW0603
    store = ValuationStore(Path(args.db))

    logger.info("starting FastAPI server on http://%s:%d", args.host, args.port)
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
  :root{--bg:#fafbfc;--card:#fff;--border:#e1e4e8;--text:#24292e;--muted:#586069;--accent:#0366d6;--green:#28a745;--red:#d73a49;--radius:6px}
  *,*::before,*::after{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0;display:flex;min-height:100vh}
  a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
  .sidebar{width:280px;background:var(--card);border-right:1px solid var(--border);padding:1rem;overflow-y:auto;flex-shrink:0}
  .main{flex:1;padding:2rem;max-width:860px;margin:0 auto;overflow-y:auto}
  .sidebar h2{font-size:.95rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 .75rem}
  .run-item{padding:.55rem .5rem;border-radius:var(--radius);cursor:pointer;border:1px solid transparent;margin-bottom:.35rem;font-size:.85rem}
  .run-item:hover{background:var(--bg);border-color:var(--border)}
  .run-item .company{font-weight:600}.run-item .meta{color:var(--muted);font-size:.78rem}
  h1{font-size:1.35rem;margin:0 0 1.25rem}
  .form-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1.25rem;margin-bottom:1.5rem}
  .form-row{display:flex;gap:1rem;margin-bottom:.85rem;flex-wrap:wrap}
  .form-group{display:flex;flex-direction:column;flex:1;min-width:200px}
  .form-group label{font-size:.82rem;font-weight:600;margin-bottom:.25rem;color:var(--muted)}
  .form-group input,.form-group select{padding:.45rem .55rem;border:1px solid var(--border);border-radius:var(--radius);font-size:.9rem;background:var(--bg)}
  .form-group input:focus,.form-group select:focus{outline:none;border-color:var(--accent)}
  .btn{background:var(--accent);color:#fff;border:none;padding:.55rem 1.4rem;border-radius:var(--radius);font-size:.9rem;font-weight:600;cursor:pointer}
  .btn:hover{opacity:.9}.btn:disabled{opacity:.5;cursor:not-allowed}
  .report{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1.25rem}
  .report h2{margin:0 0 .6rem;font-size:1.1rem}
  .report-section{margin-bottom:1rem}
  .report-section h3{font-size:.9rem;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;margin:0 0 .35rem}
  .fair-value{font-size:1.7rem;font-weight:700;color:var(--green)}
  .step,.assumption{padding:.3rem 0;font-size:.88rem;border-bottom:1px solid var(--bg)}
  .citation{font-size:.84rem;color:var(--muted);padding:.2rem 0}
  .badge{display:inline-block;padding:.15rem .5rem;border-radius:99px;font-size:.75rem;font-weight:600;margin-right:.3rem;margin-bottom:.3rem}
  .badge-green{background:#dcffe4;color:#22863a}.badge-yellow{background:#fff5b1;color:#735c0f}.badge-red{background:#ffdce0;color:#cb2431}
  .meta-row{font-size:.82rem;color:var(--muted);margin-top:.5rem}
  .empty-state{color:var(--muted);text-align:center;padding:3rem 1rem;font-size:.95rem}
  #error-banner{background:var(--red);color:#fff;padding:.6rem 1rem;border-radius:var(--radius);margin-bottom:1rem;display:none;font-size:.9rem}
</style>
</head>
<body>
<aside class="sidebar"><h2>Past Runs</h2><div id="runs-list"><div class="empty-state">No runs yet</div></div></aside>
<div class="main">
  <h1>VC Audit Tool</h1>
  <div id="error-banner"></div>
  <div class="form-card">
    <div class="form-row">
      <div class="form-group"><label>Company Name</label><input id="company_name" value="Basis AI"></div>
      <div class="form-group"><label>Methodology</label>
        <select id="methodology">
          <option value="last_round_market_adjusted">Last Round (Market Adjusted)</option>
          <option value="comparable_companies">Comparable Companies</option>
        </select>
      </div>
      <div class="form-group"><label>As-of Date</label><input id="as_of_date" type="date" value="2026-02-18"></div>
    </div>
    <div id="inputs-last_round_market_adjusted">
      <div class="form-row">
        <div class="form-group"><label>Last Post-Money Valuation ($)</label><input id="lr_valuation" type="number" value="100000000"></div>
        <div class="form-group"><label>Last Round Date</label><input id="lr_round_date" type="date" value="2024-06-30"></div>
        <div class="form-group"><label>Public Index</label><select id="lr_index"><option value="NASDAQ_COMPOSITE">NASDAQ Composite</option><option value="RUSSELL_2000">Russell 2000</option></select></div>
      </div>
    </div>
    <div id="inputs-comparable_companies" style="display:none">
      <div class="form-row">
        <div class="form-group"><label>Sector</label><select id="cc_sector"><option value="enterprise_software">Enterprise Software</option><option value="cybersecurity">Cybersecurity</option><option value="infrastructure_software">Infrastructure Software</option></select></div>
        <div class="form-group"><label>LTM Revenue ($)</label><input id="cc_revenue" type="number" value="10000000"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Statistic</label><select id="cc_statistic"><option value="median">Median</option><option value="mean">Mean</option></select></div>
        <div class="form-group"><label>Private Company Discount (%)</label><input id="cc_discount" type="number" value="20" min="0" max="100"></div>
      </div>
    </div>
    <button class="btn" id="run-btn">Run Valuation</button>
  </div>
  <div id="report"></div>
</div>
<script>
(function(){
  var $=function(s){return document.querySelector(s)};
  $('#methodology').addEventListener('change',function(){
    document.querySelectorAll('[id^="inputs-"]').forEach(function(el){el.style.display='none'});
    var t=document.getElementById('inputs-'+this.value);if(t)t.style.display='';
  });
  function buildPayload(){
    var m=$('#methodology').value;
    var b={company_name:$('#company_name').value,methodology:m,as_of_date:$('#as_of_date').value,inputs:{}};
    if(m==='last_round_market_adjusted'){b.inputs={last_post_money_valuation:Number($('#lr_valuation').value),last_round_date:$('#lr_round_date').value,public_index:$('#lr_index').value}}
    else{b.inputs={sector:$('#cc_sector').value,revenue_ltm:Number($('#cc_revenue').value),statistic:$('#cc_statistic').value,private_company_discount_pct:Number($('#cc_discount').value)}}
    return b;
  }
  function renderReport(env){
    var r=env.valuation_result,meta=env.audit_metadata,fv=r.estimated_fair_value;
    var fmt=function(n){return '$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})};
    var badges='';
    if(r.confidence_indicators){Object.entries(r.confidence_indicators).forEach(function(e){var k=e[0],v=e[1],cls='badge-green',s=String(v).toLowerCase();if(s==='high'||s.indexOf('stale')>=0)cls='badge-red';else if(s==='medium'||s==='moderate')cls='badge-yellow';if(typeof v==='number'&&v>365)cls='badge-red';else if(typeof v==='number'&&v>180)cls='badge-yellow';badges+='<span class="badge '+cls+'">'+k.replace(/_/g,' ')+': '+v+'</span> '})}
    var h='<div class="report"><h2>'+r.company_name+' - '+r.methodology.replace(/_/g,' ')+'</h2>';
    h+='<div class="report-section"><h3>Fair Value Estimate</h3><div class="fair-value">'+fmt(fv.amount)+' '+fv.currency+'</div></div>';
    if(badges)h+='<div class="report-section"><h3>Confidence Indicators</h3>'+badges+'</div>';
    h+='<div class="report-section"><h3>Derivation Steps</h3>';r.derivation_steps.forEach(function(s,i){h+='<div class="step">'+(i+1)+'. '+s+'</div>'});h+='</div>';
    h+='<div class="report-section"><h3>Key Assumptions</h3>';r.assumptions.forEach(function(a){h+='<div class="assumption">- '+a+'</div>'});h+='</div>';
    h+='<div class="report-section"><h3>Citations</h3>';r.citations.forEach(function(c){h+='<div class="citation"><strong>'+c.label+'</strong>: '+c.detail;if(c.dataset_version)h+=' <span class="badge badge-green">v: '+c.dataset_version+'</span>';if(c.resolved_data_points)h+='<br><small>Data: '+c.resolved_data_points.join(', ')+'</small>';h+='</div>'});h+='</div>';
    h+='<div class="meta-row">Request ID: '+meta.request_id+' | Generated: '+meta.generated_at_utc+' | Engine v'+meta.engine_version+'</div></div>';
    return h;
  }
  $('#run-btn').addEventListener('click',async function(){
    var btn=this;btn.disabled=true;btn.textContent='Running...';$('#error-banner').style.display='none';
    try{var res=await fetch('/api/value',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(buildPayload())});var data=await res.json();if(!res.ok)throw new Error(data.error||'Unknown error');$('#report').innerHTML=renderReport(data);loadRuns()}
    catch(e){$('#error-banner').textContent=e.message;$('#error-banner').style.display=''}
    finally{btn.disabled=false;btn.textContent='Run Valuation'}
  });
  async function loadRuns(){
    try{var res=await fetch('/api/runs');var runs=await res.json();var list=$('#runs-list');
    if(!runs.length){list.innerHTML='<div class="empty-state">No runs yet</div>';return}
    list.innerHTML=runs.map(function(r){return '<div class="run-item" data-id="'+r.request_id+'"><div class="company">'+r.company_name+'</div><div class="meta">'+r.methodology.replace(/_/g,' ')+' | '+r.as_of_date+'</div><div class="meta">$'+Number(r.fair_value).toLocaleString()+'</div></div>'}).join('');
    list.querySelectorAll('.run-item').forEach(function(el){el.addEventListener('click',async function(){var res=await fetch('/api/runs/'+el.dataset.id);if(res.ok){var data=await res.json();$('#report').innerHTML=renderReport(data)}})})}
    catch(e){}
  }
  loadRuns();
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
