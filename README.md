# VC Audit Tool

A Python CLI and FastAPI service that produces **auditable, deterministic valuation output** for venture-backed companies. Every result includes a full derivation trail — assumptions, citations with dataset versions, step-by-step math, and confidence indicators — so an auditor can independently reproduce the number.

## Approach & Methodology

The tool ships two valuation models behind a common `ValuationEngine` router:

| Methodology | Core idea | Key inputs |
|---|---|---|
| **Last-Round Market-Adjusted** | Scale the most recent post-money valuation by a public-market index movement | Last round valuation + date, public index (NASDAQ / Russell 2000) |
| **Comparable Companies** | Apply a peer-set EV/Revenue multiple to LTM revenue, then discount for illiquidity | Revenue, sector or explicit tickers, private-company discount % |

Both models are backed by **mock data sources** that implement `typing.Protocol` interfaces — swapping in a live API (e.g. Bloomberg, PitchBook) requires only a new adapter, no engine changes.

## Design Decisions & Trade-offs

- **Determinism by contract.** The `valuation_result` envelope is byte-identical across repeated runs for the same inputs. Non-deterministic fields (`request_id`, `generated_at_utc`) are isolated in `audit_metadata`.
- **Citation traces.** Every result embeds `dataset_version` and `resolved_data_points` so a reviewer can pin the exact data that produced the output.
- **Protocol-based data sources.** `MarketIndexSource` and `ComparableCompanySource` are `typing.Protocol` interfaces — no base-class coupling, and mock-to-live swap is a one-line config change.
- **FastAPI.** Provides async support, automatic OpenAPI docs at `/docs`, and clean testability via `TestClient`.

## Setup

```bash
git clone https://github.com/aineshm/vc-audit-tool-mvp.git
cd vc-audit-tool-mvp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

**CLI** (primary interface):
```bash
python -m vc_audit_tool.cli --request-file examples/last_round_request.json --pretty
python -m vc_audit_tool.cli --request-file examples/comps_request.json --pretty
```

**FastAPI server** (API + Web UI):
```bash
python -m vc_audit_tool.server          # starts on :8080
curl -X POST http://localhost:8080/value -d @examples/comps_request.json
open http://localhost:8080              # web UI
open http://localhost:8080/docs         # auto-generated OpenAPI docs
```

> *The Web UI was built after completing the core deliverable (CLI + HTTP API + audit output). It demonstrates how auditors would interact with historical runs — the SQLite persistence and run-history browser are backend features; the HTML is a thin rendering layer.*

## Sample Output

<details>
<summary>Last-Round Market-Adjusted</summary>

```json
{
  "valuation_result": {
    "company_name": "Basis AI",
    "methodology": "last_round_market_adjusted",
    "as_of_date": "2026-02-18",
    "estimated_fair_value": { "amount": 120831065.39, "currency": "USD" },
    "assumptions": [
      "Method assumes valuation moves proportionally with NASDAQ_COMPOSITE.",
      "Used index level on 2024-06-30 for last round and 2026-02-18 for as-of date."
    ],
    "inputs_used": {
      "last_post_money_valuation": 100000000.0,
      "last_round_date": "2024-06-30",
      "public_index": "NASDAQ_COMPOSITE",
      "index_level_last_round": 17637.12,
      "index_level_as_of_date": 21311.12
    },
    "citations": [{
      "label": "Mock market index dataset",
      "dataset_version": "mock-market-index-v2",
      "resolved_data_points": ["NASDAQ_COMPOSITE@2024-06-30=17637.12", "NASDAQ_COMPOSITE@2026-02-18=21311.12"]
    }],
    "derivation_steps": [
      "Start with last post-money valuation: 100,000,000.00 USD.",
      "Compute index change: (21311.12 / 17637.12) - 1 = 20.8311%.",
      "Compute adjustment multiplier: 1 + 0.208311 = 1.208311.",
      "Apply multiplier: 100,000,000.00 * 1.208311 = 120,831,065.39 USD."
    ],
    "confidence_indicators": {
      "days_since_last_round": 598,
      "staleness_risk": "HIGH - last round >12 months ago",
      "data_source_type": "mock"
    }
  },
  "audit_metadata": { "request_id": "...", "generated_at_utc": "...", "engine_version": "0.1.0" }
}
```
</details>

<details>
<summary>Comparable Companies</summary>

```json
{
  "valuation_result": {
    "company_name": "Inflo",
    "methodology": "comparable_companies",
    "as_of_date": "2026-02-18",
    "estimated_fair_value": { "amount": 94400000.0, "currency": "USD" },
    "assumptions": [
      "Comparable universe based on sector peer set 'enterprise_software'.",
      "Applied median EV/Revenue multiple of 11.80x.",
      "Applied private-company discount of 20.00%."
    ],
    "derivation_steps": [
      "Select peer multiple (median): 11.80x.",
      "Apply multiple to LTM revenue: 10,000,000.00 * 11.80 = 118,000,000.00 USD.",
      "Compute discount multiplier: (100 - 20.00) / 100 = 0.8000.",
      "Apply private-company discount: 118,000,000.00 * 0.8000 = 94,400,000.00 USD."
    ],
    "confidence_indicators": {
      "peer_count": 7,
      "multiple_spread": 5.6,
      "peer_set_quality": "HIGH - 5+ comparable companies",
      "data_source_type": "mock"
    }
  },
  "audit_metadata": { "request_id": "...", "generated_at_utc": "...", "engine_version": "0.1.0" }
}
```
</details>

## Quality Gates

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
PYTHONPATH=src python -m pytest tests/ -q
```

## Potential Improvements

- **Live data adapters** — plug in Bloomberg/PitchBook behind the existing Protocol interfaces.
- **DCF / weighted-blend methodology** — the engine router makes adding models a one-class change.
- **Time-series index interpolation** — currently uses previous-business-day fallback; linear interpolation would improve mid-month accuracy.
- **Multi-currency support** — all amounts are currently USD; `MonetaryAmount` already carries a `currency` field.
