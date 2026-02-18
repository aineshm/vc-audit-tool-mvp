"""Minimal web UI for the VC Audit Tool.

Serves a single-page application (embedded HTML/CSS/JS) and a thin JSON
API backed by the valuation engine + SQLite persistence.  Zero external
runtime dependencies — stdlib only.

Routes
------
GET  /              → HTML UI
POST /api/value     → run valuation, persist, return JSON
GET  /api/runs      → list recent runs (summary)
GET  /api/runs/{id} → full payload for a single run
GET  /health        → health-check
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.store import ValuationStore

logger = logging.getLogger("vc_audit_tool.web")

# ─────────────────────────────────────────────────────────────────────
# HTML template – a single self-contained page (no build step)
# ─────────────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VC Audit Tool</title>
<style>
  :root {
    --bg: #fafbfc; --card: #fff; --border: #e1e4e8;
    --text: #24292e; --muted: #586069; --accent: #0366d6;
    --green: #28a745; --red: #d73a49; --radius: 6px;
  }
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); margin: 0;
    display: flex; min-height: 100vh;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* ── Layout ── */
  .sidebar {
    width: 280px; background: var(--card); border-right: 1px solid var(--border);
    padding: 1rem; overflow-y: auto; flex-shrink: 0;
  }
  .main { flex: 1; padding: 2rem; max-width: 860px; margin: 0 auto; overflow-y: auto; }

  /* ── Sidebar ── */
  .sidebar h2 { font-size: .95rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 .75rem; }
  .run-item {
    padding: .55rem .5rem; border-radius: var(--radius); cursor: pointer;
    border: 1px solid transparent; margin-bottom: .35rem; font-size: .85rem;
  }
  .run-item:hover { background: var(--bg); border-color: var(--border); }
  .run-item .company { font-weight: 600; }
  .run-item .meta { color: var(--muted); font-size: .78rem; }

  /* ── Form ── */
  h1 { font-size: 1.35rem; margin: 0 0 1.25rem; }
  .form-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1.25rem; margin-bottom: 1.5rem;
  }
  .form-row { display: flex; gap: 1rem; margin-bottom: .85rem; flex-wrap: wrap; }
  .form-group { display: flex; flex-direction: column; flex: 1; min-width: 200px; }
  .form-group label { font-size: .82rem; font-weight: 600; margin-bottom: .25rem; color: var(--muted); }
  .form-group input, .form-group select {
    padding: .45rem .55rem; border: 1px solid var(--border); border-radius: var(--radius);
    font-size: .9rem; background: var(--bg);
  }
  .form-group input:focus, .form-group select:focus { outline: none; border-color: var(--accent); }
  .btn {
    background: var(--accent); color: #fff; border: none; padding: .55rem 1.4rem;
    border-radius: var(--radius); font-size: .9rem; font-weight: 600; cursor: pointer;
  }
  .btn:hover { opacity: .9; }
  .btn:disabled { opacity: .5; cursor: not-allowed; }

  /* ── Report ── */
  .report { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem; }
  .report h2 { margin: 0 0 .6rem; font-size: 1.1rem; }
  .report-section { margin-bottom: 1rem; }
  .report-section h3 { font-size: .9rem; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; margin: 0 0 .35rem; }
  .fair-value { font-size: 1.7rem; font-weight: 700; color: var(--green); }
  .step, .assumption { padding: .3rem 0; font-size: .88rem; border-bottom: 1px solid var(--bg); }
  .citation { font-size: .84rem; color: var(--muted); padding: .2rem 0; }
  .badge {
    display: inline-block; padding: .15rem .5rem; border-radius: 99px;
    font-size: .75rem; font-weight: 600; margin-right: .3rem; margin-bottom: .3rem;
  }
  .badge-green { background: #dcffe4; color: #22863a; }
  .badge-yellow { background: #fff5b1; color: #735c0f; }
  .badge-red { background: #ffdce0; color: #cb2431; }
  .meta-row { font-size: .82rem; color: var(--muted); margin-top: .5rem; }

  .empty-state { color: var(--muted); text-align: center; padding: 3rem 1rem; font-size: .95rem; }

  #error-banner {
    background: var(--red); color: #fff; padding: .6rem 1rem; border-radius: var(--radius);
    margin-bottom: 1rem; display: none; font-size: .9rem;
  }
</style>
</head>
<body>

<!-- Sidebar: past runs -->
<aside class="sidebar">
  <h2>Past Runs</h2>
  <div id="runs-list"><div class="empty-state">No runs yet</div></div>
</aside>

<!-- Main area -->
<div class="main">
  <h1>🔍 VC Audit Tool</h1>

  <div id="error-banner"></div>

  <!-- Request form -->
  <div class="form-card">
    <div class="form-row">
      <div class="form-group">
        <label>Company Name</label>
        <input id="company_name" value="Basis AI">
      </div>
      <div class="form-group">
        <label>Methodology</label>
        <select id="methodology">
          <option value="last_round_market_adjusted">Last Round (Market Adjusted)</option>
          <option value="comparable_companies">Comparable Companies</option>
        </select>
      </div>
      <div class="form-group">
        <label>As-of Date</label>
        <input id="as_of_date" type="date" value="2026-02-18">
      </div>
    </div>

    <!-- Dynamic inputs for last_round_market_adjusted -->
    <div id="inputs-last_round_market_adjusted">
      <div class="form-row">
        <div class="form-group">
          <label>Last Post-Money Valuation ($)</label>
          <input id="lr_valuation" type="number" value="100000000">
        </div>
        <div class="form-group">
          <label>Last Round Date</label>
          <input id="lr_round_date" type="date" value="2024-06-30">
        </div>
        <div class="form-group">
          <label>Public Index</label>
          <select id="lr_index">
            <option value="NASDAQ_COMPOSITE">NASDAQ Composite</option>
            <option value="RUSSELL_2000">Russell 2000</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Dynamic inputs for comparable_companies -->
    <div id="inputs-comparable_companies" style="display:none">
      <div class="form-row">
        <div class="form-group">
          <label>Sector</label>
          <select id="cc_sector">
            <option value="enterprise_software">Enterprise Software</option>
            <option value="cybersecurity">Cybersecurity</option>
            <option value="infrastructure_software">Infrastructure Software</option>
          </select>
        </div>
        <div class="form-group">
          <label>LTM Revenue ($)</label>
          <input id="cc_revenue" type="number" value="10000000">
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Statistic</label>
          <select id="cc_statistic">
            <option value="median">Median</option>
            <option value="mean">Mean</option>
          </select>
        </div>
        <div class="form-group">
          <label>Private Company Discount (%)</label>
          <input id="cc_discount" type="number" value="20" min="0" max="100">
        </div>
      </div>
    </div>

    <button class="btn" id="run-btn">Run Valuation</button>
  </div>

  <!-- Report output -->
  <div id="report"></div>
</div>

<script>
(function() {
  const $ = (sel) => document.querySelector(sel);

  // ── Methodology toggle ──
  $('#methodology').addEventListener('change', function() {
    document.querySelectorAll('[id^="inputs-"]').forEach(el => el.style.display = 'none');
    const target = document.getElementById('inputs-' + this.value);
    if (target) target.style.display = '';
  });

  // ── Build request payload ──
  function buildPayload() {
    const meth = $('#methodology').value;
    const base = {
      company_name: $('#company_name').value,
      methodology: meth,
      as_of_date: $('#as_of_date').value,
      inputs: {}
    };
    if (meth === 'last_round_market_adjusted') {
      base.inputs = {
        last_post_money_valuation: Number($('#lr_valuation').value),
        last_round_date: $('#lr_round_date').value,
        public_index: $('#lr_index').value,
      };
    } else {
      base.inputs = {
        sector: $('#cc_sector').value,
        revenue_ltm: Number($('#cc_revenue').value),
        statistic: $('#cc_statistic').value,
        private_company_discount_pct: Number($('#cc_discount').value),
      };
    }
    return base;
  }

  // ── Render human-readable report ──
  function renderReport(envelope) {
    const r = envelope.valuation_result;
    const meta = envelope.audit_metadata;
    const fv = r.estimated_fair_value;
    const fmt = (n) => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});

    // Confidence badges
    let badges = '';
    if (r.confidence_indicators) {
      for (const [k, v] of Object.entries(r.confidence_indicators)) {
        let cls = 'badge-green';
        const s = String(v).toLowerCase();
        if (s === 'high' || s.includes('stale') || s === 'narrow') cls = 'badge-red';
        else if (s === 'medium' || s === 'moderate') cls = 'badge-yellow';
        else if (s === 'low' || s === 'wide' || s === 'good') cls = 'badge-green';
        if (typeof v === 'number' && v > 365) cls = 'badge-red';
        else if (typeof v === 'number' && v > 180) cls = 'badge-yellow';
        badges += `<span class="badge ${cls}">${k.replace(/_/g, ' ')}: ${v}</span> `;
      }
    }

    let html = `<div class="report">`;
    html += `<h2>${r.company_name} — ${r.methodology.replace(/_/g, ' ')}</h2>`;
    html += `<div class="report-section"><h3>Fair Value Estimate</h3><div class="fair-value">${fmt(fv.amount)} ${fv.currency}</div></div>`;

    if (badges) html += `<div class="report-section"><h3>Confidence Indicators</h3>${badges}</div>`;

    html += `<div class="report-section"><h3>Derivation Steps</h3>`;
    r.derivation_steps.forEach((s, i) => html += `<div class="step">${i+1}. ${s}</div>`);
    html += `</div>`;

    html += `<div class="report-section"><h3>Key Assumptions</h3>`;
    r.assumptions.forEach(a => html += `<div class="assumption">• ${a}</div>`);
    html += `</div>`;

    html += `<div class="report-section"><h3>Citations</h3>`;
    r.citations.forEach(c => {
      html += `<div class="citation"><strong>${c.label}</strong>: ${c.detail}`;
      if (c.dataset_version) html += ` <span class="badge badge-green">v: ${c.dataset_version}</span>`;
      if (c.resolved_data_points) html += `<br><small>Data: ${c.resolved_data_points.join(', ')}</small>`;
      html += `</div>`;
    });
    html += `</div>`;

    html += `<div class="meta-row">Request ID: ${meta.request_id} · Generated: ${meta.generated_at_utc} · Engine v${meta.engine_version}</div>`;
    html += `</div>`;
    return html;
  }

  // ── Run valuation ──
  $('#run-btn').addEventListener('click', async function() {
    const btn = this;
    btn.disabled = true;
    btn.textContent = 'Running…';
    $('#error-banner').style.display = 'none';
    try {
      const res = await fetch('/api/value', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(buildPayload()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unknown error');
      $('#report').innerHTML = renderReport(data);
      loadRuns();
    } catch (e) {
      $('#error-banner').textContent = e.message;
      $('#error-banner').style.display = '';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run Valuation';
    }
  });

  // ── Sidebar: load past runs ──
  async function loadRuns() {
    try {
      const res = await fetch('/api/runs');
      const runs = await res.json();
      const list = $('#runs-list');
      if (!runs.length) { list.innerHTML = '<div class="empty-state">No runs yet</div>'; return; }
      list.innerHTML = runs.map(r => `
        <div class="run-item" data-id="${r.request_id}">
          <div class="company">${r.company_name}</div>
          <div class="meta">${r.methodology.replace(/_/g, ' ')} · ${r.as_of_date}</div>
          <div class="meta">$${Number(r.fair_value).toLocaleString()}</div>
        </div>
      `).join('');
      // click to load
      list.querySelectorAll('.run-item').forEach(el => {
        el.addEventListener('click', async () => {
          const res = await fetch('/api/runs/' + el.dataset.id);
          if (res.ok) {
            const data = await res.json();
            $('#report').innerHTML = renderReport(data);
          }
        });
      });
    } catch (e) { /* silent */ }
  }

  // initial load
  loadRuns();
})();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────
# HTTP handler
# ─────────────────────────────────────────────────────────────────────


class WebHandler(BaseHTTPRequestHandler):
    engine = ValuationEngine()
    store: ValuationStore  # attached at startup

    # ── routing ──

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._write_html(HTML_PAGE)
        elif self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
        elif self.path == "/api/runs":
            runs = self.store.list_runs()
            self._write_json(HTTPStatus.OK, runs)
        elif self.path.startswith("/api/runs/"):
            run_id = self.path[len("/api/runs/") :]
            run = self.store.get_run(run_id)
            if run is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
            else:
                self._write_json(HTTPStatus.OK, run)
        else:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/value":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})
            return

        start = time.monotonic()
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            result = self.engine.evaluate_from_dict(payload)
            result_dict = result.to_dict()

            self.store.save(result_dict)

            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "valuation_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
                result.company_name,
                result.methodology,
                result.request_id,
                elapsed_ms,
            )
            self._write_json(HTTPStatus.OK, result_dict)
        except json.JSONDecodeError as exc:
            logger.warning("bad_json error=%s", exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc}"})
        except (ValidationError, DataSourceError) as exc:
            logger.warning("validation_error error=%s", exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover
            logger.exception("unhandled_error error=%s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    # ── helpers ──

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug(fmt, *args)

    def _write_json(self, status: HTTPStatus, payload: Any) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _write_html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


# ─────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VC Audit Tool web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8090, type=int)
    parser.add_argument("--db", default="valuation_runs.db", help="SQLite database path.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    store = ValuationStore(Path(args.db))
    WebHandler.store = store

    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    logger.info("web ui listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
