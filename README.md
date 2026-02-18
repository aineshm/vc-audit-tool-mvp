# VC Audit Tool# VC Audit Tool (Take-Home Assignment)



A zero-dependency Python CLI and HTTP API that produces **auditable, deterministic valuation output** for venture-backed companies. Every result includes a full derivation trail — assumptions, citations with dataset versions, step-by-step math, and confidence indicators — so an auditor can independently reproduce the number.## Overview

This project implements an auditable backend workflow for valuing private VC portfolio companies.

## Approach & Methodology

It focuses on consistency, traceability, and maintainability rather than financial-model precision.

The tool ships two valuation models behind a common `ValuationEngine` router:Each valuation response includes:

- Estimated fair value

| Methodology | Core idea | Key inputs |- Inputs used

|---|---|---|- Assumptions

| **Last-Round Market-Adjusted** | Scale the most recent post-money valuation by a public-market index movement | Last round valuation + date, public index (NASDAQ / Russell 2000) |- Data citations

| **Comparable Companies** | Apply a peer-set EV/Revenue multiple to LTM revenue, then discount for illiquidity | Revenue, sector or explicit tickers, private-company discount % |- Step-by-step derivation

- Confidence / risk indicators (data freshness, peer-set quality, staleness risk)

Both models are backed by **mock data sources** that implement `typing.Protocol` interfaces — swapping in a live API (e.g. Bloomberg, PitchBook) requires only a new adapter, no engine changes.- Audit metadata (request ID, timestamp, engine version)



## Design Decisions & Trade-offs## Chosen Methodologies

The tool supports two methodologies behind a shared valuation engine:

- **Zero runtime dependencies.** The entire stack (CLI, HTTP server, Web UI) runs on Python ≥ 3.10 stdlib. This keeps the audit surface minimal and the setup instant.

- **Determinism by contract.** The `valuation_result` envelope is byte-identical across repeated runs for the same inputs. Non-deterministic fields (`request_id`, `generated_at_utc`) are isolated in `audit_metadata`.1. Last Round (Market-Adjusted)

- **Citation traces.** Every result embeds `dataset_version` and `resolved_data_points` so a reviewer can pin the exact data that produced the output.- Starts from `last_post_money_valuation`

- **Protocol-based data sources.** `MarketIndexSource` and `ComparableCompanySource` are `typing.Protocol` interfaces — no base-class coupling, and mock ↔ live swap is a one-line config change.- Applies change in a selected public index between `last_round_date` and `as_of_date`

- Uses mocked index history (in-memory dataset)

## Setup- Reports staleness risk and index data freshness



```bash2. Comparable Company Analysis (Comps)

git clone https://github.com/aineshm/vc-audit-tool-mvp.git- Selects public comps by sector or explicit ticker list

cd vc-audit-tool-mvp- Computes selected EV/Revenue statistic (`median` or `mean`)

python -m venv .venv && source .venv/bin/activate- Applies multiple to private company LTM revenue

pip install -e ".[dev]"       # installs ruff, mypy, etc.- Optionally applies private-company discount

```- Uses mocked comps data (in-memory dataset)

- Reports peer-set quality and multiple spread

## Usage

## Why this approach

**CLI** (primary interface):Design decisions and tradeoffs:

```bash- **Standard library only**: avoids setup friction, but uses a minimal `http.server` API instead of a richer framework.

python -m vc_audit_tool.cli --request-file examples/last_round_request.json --pretty- **Mocked data sources with explicit citations**: deterministic and reproducible for audit/testing, but not live market-connected.

python -m vc_audit_tool.cli --request-file examples/comps_request.json --pretty- **Provider Protocols (PEP 544)**: data sources are typed to `Protocol` interfaces (`MarketIndexSource`, `ComparableCompanySource`), so mock and real providers satisfy the same contract. Swapping in a live Yahoo Finance adapter requires zero changes to business logic.

```- **Pluggable methodologies via ABC**: `ValuationMethodology` is an abstract base class with `@abstractmethod`. Adding DCF or custom methods is a one-file addition plus a registration line in `engine.py`.

- **Strict validation and typed models**: catches bad inputs early and keeps outputs structured for downstream audit storage.

**HTTP API:**- **Confidence indicators**: every valuation includes risk signals (staleness, peer-set quality, data source type) so auditors can assess estimate reliability.

```bash- **Structured logging**: the HTTP server uses Python's `logging` module with configurable verbosity (`--log-level`) and audit-friendly request/response logging.

python -m vc_audit_tool.server          # starts on :8000

curl -X POST http://localhost:8000/value -d @examples/comps_request.json## Architecture

```Workflow: input ingestion → validation → methodology execution → auditable output



**Web UI:**```mermaid

```bashflowchart LR

python -m vc_audit_tool.web             # starts on :8080, open browser  A["CLI / HTTP Request"] --> B["ValuationEngine"]

```  B --> C["Methodology Router"]

> *The Web UI was built after completing the core deliverable (CLI + HTTP API + audit output). It demonstrates how auditors would interact with historical runs — the SQLite persistence and run-history browser are backend features; the HTML is a thin rendering layer.*  C --> D["Last Round Model"]

  C --> E["Comps Model"]

## Sample Output  D --> F["MarketIndexSource (Protocol)"]

  E --> G["ComparableCompanySource (Protocol)"]

<details>  F --> F1["MockMarketIndexSource"]

<summary>Last-Round Market-Adjusted (click to expand)</summary>  G --> G1["MockComparableCompanySource"]

  D --> H["ValuationResult (auditable JSON)"]

```json  E --> H["ValuationResult (auditable JSON)"]

{```

  "valuation_result": {

    "company_name": "Basis AI",## Project Structure

    "methodology": "last_round_market_adjusted",- `src/vc_audit_tool/engine.py`: request orchestration and methodology routing

    "as_of_date": "2026-02-18",- `src/vc_audit_tool/models.py`: request/result schema and audit metadata

    "estimated_fair_value": {- `src/vc_audit_tool/interfaces.py`: `Protocol` definitions for data source contracts

      "amount": 120831065.39,- `src/vc_audit_tool/validation.py`: shared parsing + validation helpers

      "currency": "USD"- `src/vc_audit_tool/data_sources.py`: mocked market/comps datasets (implement Protocols)

    },- `src/vc_audit_tool/exceptions.py`: domain-specific exception hierarchy

    "assumptions": [- `src/vc_audit_tool/methodologies/base.py`: `ValuationMethodology` ABC + `MethodologyContext`

      "Method assumes valuation moves proportionally with NASDAQ_COMPOSITE.",- `src/vc_audit_tool/methodologies/last_round.py`: last-round market-adjusted model

      "Used index level on 2024-06-30 for last round and 2026-02-18 for as-of date."- `src/vc_audit_tool/methodologies/comps.py`: comparable-company model

    ],- `src/vc_audit_tool/cli.py`: command-line entry point

    "inputs_used": {- `src/vc_audit_tool/server.py`: HTTP JSON API (`POST /value`, `GET /health`) with structured logging

      "last_post_money_valuation": 100000000.0,- `src/vc_audit_tool/web.py`: minimal web UI (single-page app + SQLite persistence)

      "last_round_date": "2024-06-30",- `src/vc_audit_tool/store.py`: SQLite audit-trail store for past valuation runs

      "public_index": "NASDAQ_COMPOSITE",- `tests/test_engine.py`: core engine integration tests

      "index_level_last_round": 17637.12,- `tests/test_validation.py`: parser edge-case unit tests

      "index_level_as_of_date": 21311.12- `tests/test_methodologies.py`: boundary + negative-path tests for both methodologies

    },- `tests/test_serialization.py`: output schema contract tests

    "citations": [- `tests/test_server.py`: HTTP endpoint integration tests (live server)

      {- `tests/test_cli.py`: CLI subprocess tests (exit codes, error output)

        "label": "Mock market index dataset",- `tests/test_interfaces.py`: Protocol conformance verification

        "detail": "In-memory quarterly index levels for NASDAQ Composite and Russell 2000.",- `tests/test_determinism.py`: determinism guarantees and raw-request replay tests

        "dataset_version": "mock-market-index-v1",- `tests/test_store.py`: SQLite store unit tests

        "resolved_data_points": [- `tests/test_web.py`: web UI HTTP integration tests

          "NASDAQ_COMPOSITE@2024-06-30=17637.12",- `examples/*.json`: sample valuation requests

          "NASDAQ_COMPOSITE@2026-02-18=21311.12"- `.github/workflows/ci.yml`: CI pipeline (lint + type-check + test)

        ]

      }## Setup

    ],### Prerequisites

    "derivation_steps": [- Python 3.10+

      "Start with last post-money valuation: 100,000,000.00 USD.",

      "Compute index change: (21311.12 / 17637.12) - 1 = 20.8311%.",### Install (editable, with dev tools)

      "Compute adjustment multiplier: 1 + 0.208311 = 1.208311.",```bash

      "Apply multiplier to last valuation: 100,000,000.00 * 1.208311 = 120,831,065.39 USD."python3 -m pip install -e ".[dev]"

    ],```

    "confidence_indicators": {

      "days_since_last_round": 598,## Usage

      "index_data_freshness_gap_days": 0,### CLI example: last-round valuation

      "absolute_index_change_pct": 20.8311,```bash

      "staleness_risk": "HIGH – last round >12 months ago",PYTHONPATH=src python3 -m vc_audit_tool.cli \

      "data_source_type": "mock"  --request-file examples/last_round_request.json \

    }  --pretty

  },```

  "audit_metadata": {

    "request_id": "...",### CLI example: comps valuation

    "generated_at_utc": "2026-02-18T...",```bash

    "engine_version": "0.1.0"PYTHONPATH=src python3 -m vc_audit_tool.cli \

  }  --request-file examples/comps_request.json \

}  --pretty

``````

</details>

### API mode

<details>Start server:

<summary>Comparable Companies (click to expand)</summary>```bash

PYTHONPATH=src python3 -m vc_audit_tool.server --host 127.0.0.1 --port 8080

```json```

{

  "valuation_result": {With verbose logging:

    "company_name": "Inflo",```bash

    "methodology": "comparable_companies",PYTHONPATH=src python3 -m vc_audit_tool.server --host 127.0.0.1 --port 8080 --log-level DEBUG

    "as_of_date": "2026-02-18",```

    "estimated_fair_value": {

      "amount": 94400000.0,Health check:

      "currency": "USD"```bash

    },curl http://127.0.0.1:8080/health

    "assumptions": [```

      "Comparable universe based on sector peer set 'enterprise_software'.",

      "Applied median EV/Revenue multiple of 11.80x.",Valuation request:

      "Applied private-company discount of 20.00%."```bash

    ],curl -X POST http://127.0.0.1:8080/value \

    "inputs_used": {  -H "Content-Type: application/json" \

      "revenue_ltm": 10000000.0,  -d @examples/last_round_request.json

      "sector": "enterprise_software",```

      "statistic": "median",

      "peer_companies": [### Web UI (interactive)

        {"ticker": "SNOW", "company_name": "Snowflake", "ev_to_revenue": 13.1},Start the web UI (includes SQLite persistence for past runs):

        {"ticker": "DDOG", "company_name": "Datadog", "ev_to_revenue": 12.4},```bash

        {"ticker": "MDB", "company_name": "MongoDB", "ev_to_revenue": 9.2},PYTHONPATH=src python3 -m vc_audit_tool.web --port 8090

        {"ticker": "ZS", "company_name": "Zscaler", "ev_to_revenue": 11.8},```

        {"ticker": "HUBS", "company_name": "HubSpot", "ev_to_revenue": 10.5},Then open **http://127.0.0.1:8090** in your browser.

        {"ticker": "NOW", "company_name": "ServiceNow", "ev_to_revenue": 14.8},

        {"ticker": "TEAM", "company_name": "Atlassian", "ev_to_revenue": 11.2}Features:

      ],- **Editable request form** — select methodology, edit every input field, submit

      "private_company_discount_pct": 20.0- **Human-readable report** — fair value, derivation steps, assumptions, citations, confidence indicators

    },- **Past runs sidebar** — all historical valuations stored in SQLite, click to reload any past report

    "citations": [- **API endpoints** — `POST /api/value` (run + persist), `GET /api/runs` (list), `GET /api/runs/{id}` (detail)

      {

        "label": "Mock public comp dataset",## Input Schema (request)

        "detail": "In-memory EV/Revenue multiples by ticker and sector.",```json

        "dataset_version": "mock-comps-v1",{

        "resolved_data_points": [  "company_name": "Basis AI",

          "SNOW:ev_rev=13.1", "DDOG:ev_rev=12.4", "MDB:ev_rev=9.2",  "methodology": "last_round_market_adjusted | comparable_companies",

          "ZS:ev_rev=11.8", "HUBS:ev_rev=10.5", "NOW:ev_rev=14.8", "TEAM:ev_rev=11.2"  "as_of_date": "YYYY-MM-DD",

        ]  "inputs": { "methodology-specific fields": "..." }

      }}

    ],```

    "derivation_steps": [

      "Select peer multiple (median): 11.80x.",Last-round required inputs:

      "Apply multiple to LTM revenue: 10,000,000.00 * 11.80 = 118,000,000.00 USD.",- `last_post_money_valuation`

      "Compute discount multiplier: (100 - 20.00) / 100 = 0.8000.",- `last_round_date`

      "Apply private-company discount: 118,000,000.00 * 0.8000 = 94,400,000.00 USD."- Optional: `public_index` (default `NASDAQ_COMPOSITE`)

    ],

    "confidence_indicators": {Comps required inputs:

      "peer_count": 7,- `sector`

      "multiple_spread": 5.6,- `revenue_ltm`

      "peer_set_quality": "HIGH – 5+ comparable companies",- Optional: `peer_tickers` (array)

      "data_source_type": "mock"- Optional: `statistic` (`median` default, or `mean`)

    }- Optional: `private_company_discount_pct` (default `0`)

  },

  "audit_metadata": {## Output Schema (high-level)

    "request_id": "...",Every response is an envelope with two top-level keys:

    "generated_at_utc": "2026-02-18T...",

    "engine_version": "0.1.0"```json

  }{

}  "valuation_result": {

```    "company_name": "...",

</details>    "methodology": "...",

    "as_of_date": "YYYY-MM-DD",

## Quality Gates    "estimated_fair_value": { "amount": 0.0, "currency": "USD" },

    "assumptions": ["..."],

```bash    "inputs_used": { "...": "..." },

ruff check src/ tests/          # lint    "citations": [

ruff format --check src/ tests/ # format      {

mypy                            # strict type-checking        "label": "...",

python -m unittest discover -s tests -v   # 124 tests        "detail": "...",

```        "dataset_version": "mock-market-index-v1",

        "resolved_data_points": ["NASDAQ_COMPOSITE@2024-06-30=17637.12"]

## Potential Improvements      }

    ],

- **Live data adapters** — plug in Bloomberg/PitchBook behind the existing `Protocol` interfaces.    "derivation_steps": ["..."],

- **DCF / weighted-blend methodology** — the engine router makes adding models a one-class change.    "confidence_indicators": { "...": "..." }

- **FastAPI migration** — replace `http.server` with FastAPI for async, OpenAPI docs, and middleware.  },

- **Time-series index interpolation** — currently uses previous-business-day fallback; spline or linear interpolation would improve accuracy for mid-month as-of dates.  "audit_metadata": {

- **Multi-currency support** — all amounts are currently USD; `MonetaryAmount` already carries a `currency` field.    "request_id": "uuid",

    "generated_at_utc": "ISO-8601",
    "engine_version": "0.1.0"
  }
}
```

**Determinism guarantee:** Repeated identical requests produce byte-identical
`valuation_result`. Only `audit_metadata` (request_id, timestamp) varies
between runs.

**Citation trace:** Each citation includes `dataset_version` (snapshot key)
and `resolved_data_points` (the exact data points consumed), making every
valuation fully replay-capable.

This makes each valuation transparent and easy to review in an audit file.

## Quality Gates
The project enforces three quality gates, locally and in CI:

```bash
# Lint (ruff)
ruff check src/ tests/

# Format (ruff)
ruff format --check src/ tests/

# Type check (mypy --strict)
mypy

# Tests (124 tests)
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

CI runs all four gates on Python 3.10, 3.12, and 3.13 via GitHub Actions.

## Testing
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

**124 tests** across 11 test modules covering:
- Validation parser edge cases (None, wrong types, malformed dates, empty strings, bool rejection)
- Boundary behavior (0 values, 100% discounts, same-day round/as-of)
- Negative paths for every methodology input field (including bool-as-numeric rejection)
- Server integration (bad JSON, wrong routes, empty bodies, correct status codes)
- CLI behavior (missing files, malformed JSON, exit codes, pretty-print)
- Serialization contract (envelope structure, key stability, type guarantees, version consistency)
- Protocol conformance (mock sources satisfy interface contracts)
- Determinism (byte-identical `valuation_result` on repeat, unique `audit_metadata`, raw-request replay)
- Citation tracing (dataset_version, resolved_data_points present in output)
- SQLite store (save, list, get, ordering, limits)
- Web UI HTTP layer (routes, round-trip persist, error handling)

## Potential Improvements (if more time)
- Add a third methodology (DCF) with projection file ingestion and scenario support.
- Implement real data-source adapters behind the existing Protocol interfaces (e.g., Yahoo Finance, Bloomberg) with caching and retry logic.
- Add user/authn and role-based controls for audit teams.
- Add hash-chain or signed-payload support for tamper-evident valuation records.
- Methodology versioning to guarantee reproducibility of historical valuations.
- Side-by-side comparison view in the web UI (run multiple methodologies for the same company).
