# VC Audit Tool

A Python engine that produces **auditable, deterministic valuation output** for venture-backed private companies. Every result includes a full derivation trail — assumptions, citations with dataset versions, step-by-step math, and confidence indicators — so an auditor can independently reproduce the number.

The tool can operate in two modes:

| Mode | Data Source | Use case |
|------|------------|----------|
| **Mock** (default) | Built-in curated datasets | Development, demos, tests |
| **Live** | SEC EDGAR + Yahoo Finance + sentence-transformer embeddings | Real valuations of actual private companies |

> See **[ARCHITECTURE.md](ARCHITECTURE.md)** for detailed system design, data-flow diagrams, and component descriptions.

---

## Quick Start

```bash
git clone https://github.com/aineshm/vc-audit-tool-mvp.git
cd vc-audit-tool-mvp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

> **Python ≥ 3.10** required. The `sentence-transformers` dependency will download the `all-MiniLM-L6-v2` model (~80 MB) on first use.

---

## Running the Tool

### CLI (primary interface)

```bash
# Mock data — works out of the box, no API calls
python -m vc_audit_tool.cli --request-file examples/comps_request.json --pretty
python -m vc_audit_tool.cli --request-file examples/last_round_request.json --pretty
python -m vc_audit_tool.cli --request-file examples/techco_ratchet_request.json --pretty
```

### FastAPI Server

```bash
python -m vc_audit_tool.server          # starts on http://127.0.0.1:8080

# In another terminal:
curl http://127.0.0.1:8080/health       # → {"status":"ok"}
curl -X POST http://127.0.0.1:8080/value -H 'Content-Type: application/json' \
     -d @examples/comps_request.json

open http://127.0.0.1:8080              # Web UI with run history
open http://127.0.0.1:8080/docs         # Auto-generated OpenAPI docs
```

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

```bash
# Unit tests only (default, no network needed) — ~220 tests
python -m pytest tests/ -q

# Include integration tests (hits SEC EDGAR + Yahoo Finance APIs)
python -m pytest tests/ -q -m 'integration or not integration'

# Run a specific test file
python -m pytest tests/test_epic2.py -v

# Run with coverage
python -m pytest tests/ --cov=vc_audit_tool --cov-report=term-missing
```

### Quality Gates

All four must pass before committing:

```bash
ruff check src/ tests/               # linter (pyflakes, isort, bugbear, etc.)
ruff format --check src/ tests/      # formatter
mypy src/                            # strict type checking (21 source files)
python -m pytest tests/ -q           # 220 unit tests
```

Current status:
```
ruff check:   ✅ All checks passed
ruff format:  ✅ 34 files already formatted
mypy:         ✅ Success: no issues found in 21 source files
pytest:       ✅ 220 passed, 11 deselected (integration)
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
    "private_company_discount_pct": 20
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

| Epic | Feature | Status |
|------|---------|--------|
| **MVP** | Valuation engine, 3 methodologies, CLI, FastAPI server, Web UI, SQLite persistence | ✅ Complete |
| **Epic 1** | `YFinanceMarketIndexSource` — live NASDAQ/Russell 2000 levels via Yahoo Finance | ✅ Complete |
| **Epic 2** | Real Comparable Companies — EDGAR universe + yfinance metrics + embedding ranker | ✅ Complete |
| **Epic 3** | Multiple-Ratchet methodology — sector multiple compression / expansion + revenue performance | ✅ Complete |

### Can It Value Real Companies Today?

**Yes, with one manual input.** The engine can produce real valuations for private companies using live public market data. The only thing a user needs to provide is:

1. **Revenue (LTM)** — the target company's last-twelve-months revenue
2. **Sector** — maps to SIC codes for finding public peers
3. **Target description** (recommended) — a 1–2 sentence description of what the company does, used for semantic matching of the most relevant public comparables

The system will then automatically:
- Pull the EDGAR universe of ~10,000+ public companies
- Fetch real EV/Revenue multiples from Yahoo Finance
- Rank peers by semantic similarity using sentence-transformer embeddings
- Compute a fully auditable valuation with derivation steps and citations

### What's Next

| Epic | Feature | Description |
|------|---------|-------------|
| **Epic 4** | CLI + Server integration | Wire live providers into CLI flags (`--live`) and server config |
| **Epic 5** | Auto-description | Scrape company website or use LLM to generate `target_description` automatically |
| **Epic 6** | Additional methodologies | DCF, weighted average of methods, multi-currency support |

---

## License

MIT
