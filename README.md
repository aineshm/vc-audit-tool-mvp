# VC Audit Tool

A Python engine that produces **auditable, deterministic valuation output** for venture-backed private companies. Every result includes a full derivation trail — assumptions, citations with dataset versions, step-by-step math, and confidence indicators — so an auditor can independently reproduce the number.

The tool can operate in two modes:

| Mode | Data Source | Use case |
|------|------------|----------|
| **Live** (default) | SEC EDGAR + Yahoo Finance + sentence-transformer embeddings | Real valuations of actual private companies |
| **Mock** (`VC_AUDIT_MOCK=1`) | Built-in curated datasets | Development, demos, tests |

> See **[ARCHITECTURE.md](ARCHITECTURE.md)** for detailed system design, data-flow diagrams, and component descriptions.

---

## Quick Start

```bash
git clone https://github.com/aineshm/vc-audit-tool-mvp.git
cd vc-audit-tool-mvp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

> **Python ≥ 3.10** required. All commands below must be run from the **`vc-audit-tool/` project root** (the directory containing `pyproject.toml`). The `sentence-transformers` dependency will download the `all-MiniLM-L6-v2` model (~80 MB) on first use.

---

## Running the Tool

### CLI (primary interface)

> Run all commands from the **project root** (`vc-audit-tool/`).

```bash
# Run a valuation from a JSON file
python3 -m vc_audit_tool.cli value --request-file examples/comps_request.json --pretty
python3 -m vc_audit_tool.cli value --request-file examples/last_round_request.json --pretty
python3 -m vc_audit_tool.cli value --request-file examples/techco_ratchet_request.json --pretty

# Cache management (Epic 5)
python3 -m vc_audit_tool.cli cache list                     # show all cached datasets
python3 -m vc_audit_tool.cli cache clear --older-than 30d   # remove stale cache files
python3 -m vc_audit_tool.cli cache clear --all              # wipe everything

# Confidence report for a stored run (Epic 5)
python3 -m vc_audit_tool.cli confidence <request-id>

# Research-first valuation from company name (requires VC_AUDIT_SEC_USER_AGENT + an LLM key)
python3 -m vc_audit_tool.cli research "Stripe" --pretty
python3 -m vc_audit_tool.cli research "Anthropic" --pretty
python3 -m vc_audit_tool.cli research "Databricks" --methodology comparable_companies --pretty
```

### FastAPI Server

```bash
python3 -m vc_audit_tool.server          # starts on http://127.0.0.1:8080
python3 -m vc_audit_tool.server --mode mock   # force mock sources
python3 -m vc_audit_tool.server --mode live   # explicit live mode (default)

# In another terminal:
curl http://127.0.0.1:8080/health       # → {"status":"ok"}
curl -X POST http://127.0.0.1:8080/value -H 'Content-Type: application/json' \
     -d @examples/comps_request.json

# Automated research — just a company name:
curl -X POST http://127.0.0.1:8080/research \
     -H 'Content-Type: application/json' \
     -d '{"company_name": "Anthropic"}'

# Multi-methodology reconciled valuation (Phase 2):
curl -X POST http://127.0.0.1:8080/reconcile \
     -H 'Content-Type: application/json' \
     -d '{"company_name": "Anthropic", "description_hint": "AI safety lab"}'

open http://127.0.0.1:8080              # Web UI with run history
open http://127.0.0.1:8080/docs         # Auto-generated OpenAPI docs
```

The web UI is **research-first** by default (`/research` primary action). Manual `/api/value`
is available under **Advanced Manual Mode** in the UI.

UI flow:
- **Research Mode (default)**: `company_name`, optional `as_of_date`, optional `description_hint`, optional methodology override.
- **Reconcile action**: runs `POST /reconcile` from the same research inputs.
- **Advanced Manual Mode**: runs `POST /api/value` with full structured inputs; manual `comparable_companies` still requires `sector`.

### Using Live Data (Real Valuations)

The live data providers can be wired into the engine programmatically. This is how you'd value a real private company using public market data:

```python
from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.data_sources import (
    EdgarYFinanceComparableCompanySource,
    YFinanceMetricsFetcher,
    EdgarCompanyUniverse,
    EmbeddingCompsRanker,
)

# Wire up live data providers
comps_source = EdgarYFinanceComparableCompanySource(
    target_description=(
        "Cloud-native data analytics platform providing real-time "
        "business intelligence for enterprise customers"
    ),
    top_k=5,
)

engine = ValuationEngine(comps_source=comps_source)

result = engine.evaluate_from_dict({
    "company_name": "Acme Analytics",
    "methodology": "comparable_companies",
    "as_of_date": "2026-02-22",
    "inputs": {
        "revenue_ltm": 50_000_000,
        "sector": "enterprise_software",
        "private_company_discount_pct": 25,
    },
})

import json
print(json.dumps(result.to_dict(), indent=2))
```

**What happens under the hood:**
1. **EDGAR** finds all public companies with SIC code 7372 (prepackaged software)
2. **Yahoo Finance** fetches EV/Revenue multiples for each ticker
3. **Sentence-transformer embeddings** rank those companies by semantic similarity to your `target_description`
4. The top-k are used as the comparable set; the engine applies the median multiple to your revenue, then applies the private-company discount

The `target_description` is the key input for finding relevant comps. You can:
- **Write it manually** — describe what the target company does in 1–2 sentences
- **Use company marketing copy** — paste from the company's website or pitch deck
- **Automate it** — a future enhancement could scrape the company's website or use an LLM to generate the description

---

## Running Tests

> Run from the **project root** (`vc-audit-tool/`).

```bash
# Unit tests only (default, no network needed)
python3 -m pytest tests/ -q

# Include integration tests (hits SEC EDGAR + Yahoo Finance APIs)
python3 -m pytest tests/ -q -m 'integration or not integration'

# Run a specific test file
python3 -m pytest tests/test_epic2.py -v

# Run with coverage
python3 -m pytest tests/ --cov=vc_audit_tool --cov-report=term-missing
```

### Quality Gates

All four must pass before committing:

```bash
ruff check src/ tests/               # linter (pyflakes, isort, bugbear, etc.)
ruff format --check src/ tests/      # formatter
mypy src/                            # strict type checking
python3 -m pytest tests/ -q          # ~471 unit tests
```

Current status:
```text
ruff check:   ✅ All checks passed
ruff format:  ✅ All files already formatted
mypy:         ✅ Success: no issues found
pytest:       ✅ 508 passed, 11 deselected (integration)
```

---

## Supported Methodologies

### 1. Comparable Companies (`comparable_companies`)

Applies a peer-set EV/Revenue multiple to LTM revenue, then discounts for illiquidity.

```json
{
  "company_name": "Inflo",
  "methodology": "comparable_companies",
  "as_of_date": "2026-02-22",
  "inputs": {
    "sector": "enterprise_software",
    "revenue_ltm": 10000000,
    "statistic": "median",
    "private_company_discount_pct": 20,
    "target_description": "Cloud security platform for enterprise SOC teams"
  }
}
```

You can also pass explicit tickers instead of a sector:
```json
"inputs": {
  "peer_tickers": ["CRM", "NOW", "DDOG"],
  "revenue_ltm": 10000000,
  "sector": "enterprise_software",
  "private_company_discount_pct": 25
}
```

`target_description` is optional and only used for sector-based peer selection.

### 2. Last-Round Market-Adjusted (`last_round_market_adjusted`)

Scales the most recent post-money valuation by public-market index movement.

```json
{
  "company_name": "Basis AI",
  "methodology": "last_round_market_adjusted",
  "as_of_date": "2026-02-22",
  "inputs": {
    "last_post_money_valuation": 100000000,
    "last_round_date": "2024-06-30",
    "public_index": "NASDAQ_COMPOSITE"
  }
}
```

### 3. Last-Round Multiple-Ratchet (`last_round_multiple_ratchet`)

Re-rates a prior-round valuation by comparing the **implied revenue multiple** at the time of the last round against the **current market multiple** for the same sector. Unlike the market-adjusted method (which tracks a broad index), this captures **sector-specific multiple compression or expansion** and **company-specific revenue performance**.

```json
{
  "company_name": "TechCo",
  "methodology": "last_round_multiple_ratchet",
  "as_of_date": "2026-02-22",
  "inputs": {
    "last_post_money_valuation": 100000000,
    "revenue_at_last_round": 10000000,
    "current_revenue": 12000000,
    "sector": "enterprise_software",
    "statistic": "median",
    "private_company_discount_pct": 20
  }
}
```

**Derivation:**
1. Implied multiple = last post-money ÷ revenue at last round = 10.0×
2. Current market median from peer set (e.g. 7.0×)
3. Multiple ratchet = current ÷ implied = 0.70 (30% compression)
4. Re-rated value = current revenue × current market multiple = $12M × 7.0 = $84M
5. Apply private-company discount: $84M × 0.80 = **$67.2M**

This methodology is ideal for scenarios where sector multiples have contracted (or expanded) significantly since the last funding round.

### 4. Scorecard (`scorecard`) — *Phase 2*

The **Payne Scorecard Method** benchmarks a startup against 7 qualitative factors (strength of team, size of opportunity, product/technology, competitive environment, marketing/sales, need for additional funding, other) relative to a regional median pre-money valuation.

Each factor is scored 0.0 – 2.0 (1.0 = average), weighted, and the weighted-average factor is applied to the regional median.

```json
{
  "company_name": "NovaBio",
  "methodology": "scorecard",
  "as_of_date": "2026-03-01",
  "inputs": {
    "regional_median_pre_money": 6000000,
    "scorecard_factors": {
      "team": 1.5,
      "opportunity": 1.2,
      "product": 1.0,
      "competitive_env": 0.8,
      "marketing": 1.1,
      "need_for_funding": 0.9,
      "other": 1.0
    }
  }
}
```

### 5. Berkus (`berkus`) — *Phase 2*

The **Berkus Method** assigns up to a maximum value across 5 risk dimensions (sound idea, prototype, quality management, strategic relationships, product rollout / sales). Each factor is scored as a boolean (present/absent) or a float 0.0 – 1.0 for partial credit.

```json
{
  "company_name": "NovaBio",
  "methodology": "berkus",
  "as_of_date": "2026-03-01",
  "inputs": {
    "max_pre_money_valuation": 2500000,
    "factors": {
      "sound_idea": true,
      "prototype": 0.7,
      "quality_management": true,
      "strategic_relationships": false,
      "product_rollout": 0.3
    }
  }
}
```

Backward compatibility: legacy aliases (`working_prototype`, `product_rollout_or_sales`) and
`berkus_factors` are still accepted.

---

## Multi-Methodology Reconciliation (Phase 2)

The reconciliation layer automatically selects, weights, and reconciles multiple methodologies into a single concluded valuation.

### How It Works

1. **CompanyProfiler** — classifies the company into a lifecycle stage (`pre_seed`, `seed`, `early`, `growth`, `late`) based on age, revenue, round history, and headcount
2. **MethodologySelector** — loads a versioned YAML rules config (`config/methodology_rules_v1.yaml`) and applies stage exclusions, data-availability rules, and base weights to produce a `MethodologyPlan`
3. **Reconciler** — runs each selected methodology, computes a weighted-average point estimate, derives a range (±10% or from min/max results), and flags divergence when any pair of results differs by > 40%

### `POST /reconcile` Endpoint

```bash
curl -X POST http://127.0.0.1:8080/reconcile \
  -H "Content-Type: application/json" \
  -d '{
    "company_name": "Anthropic",
    "as_of_date": "2026-03-01",
    "description_hint": "AI safety research lab"
  }'
```

**Request body:**

| Field | Required | Description |
|-------|----------|-------------|
| `company_name` | ✅ | Name of the company to value |
| `as_of_date` | ❌ | Valuation date (defaults to today) |
| `description_hint` | ❌ | Short description to improve semantic comp matching |

**What happens under the hood:**

1. **Research** — the agent searches SEC EDGAR, DuckDuckGo, and USASpending.gov for the company
2. **Profile** — the profiler classifies the company's lifecycle stage
3. **Select** — the selector picks applicable methodologies and assigns weights from the YAML rules
4. **Execute** — each selected methodology runs independently through the valuation engine
5. **Reconcile** — results are combined into a weighted-average point estimate with range and divergence analysis

<details>
<summary>Sample Response (Reconciled Valuation)</summary>

```json
{
  "concluded_value": {
    "point_estimate": 120000000.0,
    "range_low": 108000000.0,
    "range_high": 132000000.0,
    "currency": "USD",
    "as_of_date": "2026-03-01"
  },
  "reconciliation": {
    "methodology_weights": [
      {
        "methodology": "comparable_companies",
        "weight": 0.60,
        "rationale": "Growth-stage company with strong revenue — comps method primary anchor",
        "data_requirements_met": true,
        "point_estimate": 130000000.0
      },
      {
        "methodology": "last_round_market_adjusted",
        "weight": 0.40,
        "rationale": "Recent round within 18 months — reliable secondary anchor",
        "data_requirements_met": true,
        "point_estimate": 105000000.0
      }
    ],
    "divergence_flag": false,
    "divergence_note": null,
    "reconciliation_rationale": "Weighted average of 2 applicable methodologies for growth-stage company.",
    "selector_version": "v1.0"
  },
  "methodology_results": { "...": "full ValuationResult per method" },
  "company_profile": {
    "name": "Anthropic",
    "stage": "growth",
    "has_revenue": true,
    "sector": "enterprise_software",
    "...": "..."
  },
  "audit_metadata": { "request_id": "...", "generated_at_utc": "..." }
}
```
</details>

### Stage-Based Methodology Weights

The YAML rules config defines base weights per lifecycle stage:

| Stage | Scorecard | Berkus | Comps | Last-Round Market-Adj | Last-Round Ratchet |
|-------|-----------|--------|-------|-----------------------|--------------------|
| `pre_seed` | 50% | 50% | ❌ excluded | ❌ excluded | ❌ excluded |
| `seed` | 35% | 30% | — | 35% | ❌ excluded |
| `early` | — | — | 50% | 50% | — |
| `growth` | — | — | 60% | 40% | — |
| `late` | — | — | 70% | 30% | — |

Weights are dynamically adjusted based on data availability (e.g., round staleness, peer-set quality, revenue presence). If a methodology is excluded at runtime, remaining weights are renormalised.

---

## Sample Output

<details>
<summary>Comparable Companies (mock data)</summary>

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
  "audit_metadata": {
    "request_id": "...",
    "generated_at_utc": "...",
    "engine_version": "0.1.0"
  }
}
```
</details>

<details>
<summary>Last-Round Market-Adjusted (mock data)</summary>

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
  }
}
```
</details>

<details>
<summary>Last-Round Multiple-Ratchet (mock data — TechCo scenario)</summary>

```json
{
  "valuation_result": {
    "company_name": "TechCo",
    "methodology": "last_round_multiple_ratchet",
    "as_of_date": "2026-02-22",
    "estimated_fair_value": { "amount": 113280000.0, "currency": "USD" },
    "assumptions": [
      "Last-round implied revenue multiple: 10.00x (100,000,000 / 10,000,000).",
      "Current median market multiple from sector peer set 'enterprise_software': 11.80x.",
      "Multiple ratchet (current / implied): 1.1800 (expansion: -18.0%).",
      "Company revenue grew 20.0% (10,000,000 → 12,000,000).",
      "Applied private-company discount of 20.00%."
    ],
    "derivation_steps": [
      "Step 1: Implied multiple at last round = 100,000,000.00 / 10,000,000.00 = 10.00x.",
      "Step 2: Current market median EV/Revenue multiple = 11.80x.",
      "Step 3: Multiple ratchet = 11.80 / 10.00 = 1.1800 (↑ 18.0%).",
      "Step 4: Revenue performance = 12,000,000.00 (+20.0% vs last round).",
      "Step 5: Re-rated value = current revenue × market multiple = 12,000,000.00 × 11.80 = 141,600,000.00 USD.",
      "Step 6: Discount multiplier = (100 - 20.00) / 100 = 0.8000.",
      "Step 7: Final value = 141,600,000.00 × 0.8000 = 113,280,000.00 USD."
    ],
    "confidence_indicators": {
      "peer_count": 7,
      "multiple_spread": 5.6,
      "peer_set_quality": "HIGH – 5+ comparable companies",
      "implied_multiple_at_last_round": 10.0,
      "current_market_multiple": 11.8,
      "multiple_ratchet": 1.18,
      "ratchet_severity": "EXPANSION – multiples increased",
      "revenue_growth_pct": 20.0,
      "data_source_type": "mock"
    }
  }
}
```

> **Note:** With mock data the enterprise_software median is 11.8×, so this shows _expansion_. With live data where sector multiples have dropped (e.g. to 7×), the same methodology would produce $12M × 7 × 0.80 = **$67.2M** — the Investopedia down-round scenario.

</details>

---

## Project Status

### What's Built

| Phase | Feature | Status |
|-------|---------|--------|
| **MVP** | Valuation engine, 3 methodologies, CLI, FastAPI server, Web UI, SQLite persistence | ✅ Complete |
| **Epic 1** | `YFinanceMarketIndexSource` — live NASDAQ/Russell 2000 levels via Yahoo Finance | ✅ Complete |
| **Epic 2** | Real Comparable Companies — EDGAR universe + yfinance metrics + embedding ranker | ✅ Complete |
| **Epic 3** | Private Company Data Agent — LangGraph research agent, Form D, USASpending, DuckDuckGo + multi-provider LLM extraction | ✅ Complete |
| **Epic 4** | `POST /research` endpoint — one-call company valuation from just a name | ✅ Complete |
| **Epic 5** | Observability — `vc-audit cache list/clear` CLI + `vc-audit confidence <id>` report | ✅ Complete |
| **Phase 2** | Multi-methodology reconciliation — Scorecard & Berkus methods, CompanyProfiler, MethodologySelector (YAML rules), Reconciler, `POST /reconcile` endpoint | ✅ Complete |

### Automated Research Agent (`POST /research`)

The research agent can produce a **full valuation from just a company name**:

```bash
curl -X POST http://127.0.0.1:8080/research \
  -H "Content-Type: application/json" \
  -d '{"company_name": "Stripe"}'
```

**What happens under the hood:**

1. **Parse** — normalise company name, infer sector from keywords
2. **SEC Form D** — search EDGAR EFTS for Regulation D filings (funding rounds)
3. **Web research** — 7 DuckDuckGo/DDGS queries × 6 results, then LLM-structured extraction
4. **Federal contracts** — query USASpending.gov for government revenue
5. **Assemble** — auto-select methodology, build a complete `ValuationRequest`
6. **Engine** — run the valuation and return an auditable result with full derivation trail

### LLM Provider Configuration

The agent uses a **multi-provider fallback chain**. Set one or more API keys as environment variables:

| Priority | Provider | Env Var | Cost/Request |
|----------|----------|---------|-------------|
| 1 | **Google Gemini 2.5 Flash** | `GOOGLE_API_KEY` | ~$0.001 |
| 2 | **OpenAI GPT-4o-mini** | `OPENAI_API_KEY` | ~$0.002 |
| 3 | **Anthropic Claude 3.5 Haiku** | `ANTHROPIC_API_KEY` | ~$0.003 |
| 4 | **Ollama (local)** | `OLLAMA_MODEL` | $0 |
| 5 | **Regex-only fallback** | *(none needed)* | $0 |

```bash
# Example: use Google Gemini (highest priority when set)
export GOOGLE_API_KEY="..."

# Optional: override the default model
export GOOGLE_MODEL="gemini-2.5-flash"

# Or use a local Ollama model (no API key needed)
export OLLAMA_MODEL="llama3.2"
```

The first available provider wins. If no LLM is configured, the agent still works using regex extraction from search snippets.

Provider order/defaults are loaded from `config/llm_providers.yaml` (with hardcoded fallback defaults
if the file is missing or malformed). This lets you change provider priority/model defaults without
code changes.

To install the optional LLM provider packages:

```bash
pip install -e ".[llm]"    # installs langchain-google-genai, langchain-anthropic, langchain-openai
```

### SEC Access Configuration (Live EDGAR)

For live EDGAR access, set a contactable SEC user-agent string:

```bash
export VC_AUDIT_SEC_USER_AGENT="Your Name your-email@company.com"
```

SEC endpoints may return `403` with generic/default user agents.

### Can It Value Real Companies Today?

**Yes — fully automated or with manual input.**

| Endpoint | Primary Use | Methodology Selection | Sector Input |
|----------|-------------|-----------------------|--------------|
| `POST /research` | One-call valuation from company name | Auto-selected by research agent when `methodology` is omitted | Inferred by agent (no manual sector field needed in research UI) |
| `POST /reconcile` | Multi-method reconciled valuation | Selected by reconciliation selector/rules | Inferred from assembled research data |
| `POST /value` or `POST /api/value` | Manual structured valuation | Explicitly provided by caller | Required for manual `comparable_companies` payloads |

**Automated mode** (`POST /research`): provide only a company name. The agent searches SEC filings, the web, and government contracts to assemble inputs, then runs the valuation engine.

If no methodology can be fully assembled, `/research` returns HTTP `200` with a partial payload:
- `assembled_request: null`
- `best_available_methodology`
- `missing_for_best_available`
- `research_metadata`
- `web_facts`

**Reconciled mode** (`POST /reconcile`): provide a company name + optional description. The system researches the company, profiles its stage, selects applicable methodologies, runs all of them, and produces a single weighted-average valuation with divergence analysis.

**Manual mode** (`POST /value` or CLI): provide structured inputs directly. The engine can produce real valuations for private companies using live public market data. You need:

1. **Revenue (LTM)** — the target company's last-twelve-months revenue
2. **Sector** — required for manual `comparable_companies` payloads
3. **Target description** (recommended) — a 1–2 sentence description for semantic comp matching

The system will then automatically:
- Pull the EDGAR universe of ~10,000+ public companies
- Fetch real EV/Revenue multiples from Yahoo Finance
- Rank peers by semantic similarity using sentence-transformer embeddings
- Compute a fully auditable valuation with derivation steps and citations

### What's Next

### Redesign Status

The repository is following the incremental redesign baseline in `.claude/plan/stack-rethink.md`.
Current cycle scope is stabilization + modularization (agent node split, test stability, async service hardening),
not a full architecture rewrite.

| Epic | Feature | Description |
|------|---------|-------------|
| **Epic 6** | Auto-description | Scrape company website or use LLM to generate `target_description` automatically |
| **Epic 7** | Additional methodologies | DCF, multi-currency support |
| **Epic 8** | CLI `--live` flag | Wire live providers into CLI flags for command-line valuations |

---

## License

MIT
