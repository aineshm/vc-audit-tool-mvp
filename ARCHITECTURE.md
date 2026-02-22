# Architecture — VC Audit Tool

Detailed design documentation for the VC Audit Tool valuation engine.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Directory Structure](#directory-structure)
3. [Core Concepts](#core-concepts)
4. [Data Flow](#data-flow)
5. [Component Reference](#component-reference)
6. [Data Sources (Protocol-Based)](#data-sources-protocol-based)
7. [Methodologies](#methodologies)
8. [Caching Strategy](#caching-strategy)
9. [Semantic Comp Selection](#semantic-comp-selection)
10. [Audit Trail Design](#audit-trail-design)
11. [Research Agent (Epic 3)](#research-agent-epic-3)
12. [Testing Architecture](#testing-architecture)
13. [Dependency Map](#dependency-map)
14. [Extension Points](#extension-points)

---

## System Overview

```
                  ┌──────────────────────┐
                  │    CLI / API         │  (cli.py, server.py)
                  │  POST /value         │  ← structured inputs
                  │  POST /research      │  ← company name only
                  └──────┬──────┬────────┘
                         │      │
          ┌──────────────┘      └──────────────┐
          │ /value                             │ /research
          ▼                                    ▼
┌─────────────────┐              ┌─────────────────────────┐
│ ValuationEngine │              │ CompanyResearchAgent     │
└────────┬────────┘              │  (LangGraph StateGraph)  │
         │                       │                         │
         │  routes to            │  ┌─ parse_company       │
         │  methodology          │  ├─ form_d (SEC EDGAR)  │
         │                       │  ├─ web_research (DDG   │
         │                       │  │   + LLM extraction)  │
         │                       │  ├─ contracts (USASpend) │
         │                       │  └─ assemble            │
         │                       └──────────┬──────────────┘
         │                                  │ assembled inputs
         │                                  ▼
         │                       ┌─────────────────┐
         │                       │ ValuationEngine │
         │                       └────────┬────────┘
         │                                │
  ┌──────┼──────────────┬─────────────────┘
  │      │              │
┌─▼──────▼──┐ ┌────▼─────┐  ┌──────▼──────┐
│ Last-Round │ │  Comps   │  │  Multiple   │  (methodologies/)
│ Market-Adj │ │Companies │  │  Ratchet    │
└─────┬──────┘ └────┬─────┘  └──────┬──────┘
      │              │               │
 ┌────▼────────┐  ┌──▼────────────────▼──────┐
 │ IndexSource │  │ ComparableCompanySource   │  (interfaces.py)
 │  Protocol   │  │       Protocol            │
 └──────┬──────┘  └──────┬───────────────────┘
        │                │
┌───────┴────────┐    ┌──┴──────────────────────┐
│  Mock   │ Live │    │  Mock  │  EDGAR+YFin    │  (data_sources/)
│  Index  │ YFin │    │  Comps │  +Embeddings   │
       └─────────┴───────┘    └────────┴────────────────┘
```

The engine is **data-source agnostic**. It accepts any object satisfying the `Protocol` interfaces — the same engine code runs identically with mock data (for tests and demos) or live data (for production valuations).

---

## Directory Structure

```
src/vc_audit_tool/
├── __init__.py                    # Package version
├── cli.py                         # CLI entry point — value, cache, confidence subcommands
├── server.py                      # FastAPI server + Web UI + SQLite persistence
├── engine.py                      # ValuationEngine — routes requests to methodologies
├── models.py                      # ValuationRequest, ValuationResult, Citation, MonetaryAmount
├── interfaces.py                  # Protocol definitions (MarketIndexSource, ComparableCompanySource)
├── validation.py                  # Input parsing & validation helpers
├── exceptions.py                  # ValidationError, DataSourceError
├── store.py                       # SQLite-backed ValuationStore (run history)
├── cache.py                       # Epic 5.1: Cache list/clear utilities
├── confidence.py                  # Epic 5.2: Confidence-indicator report formatter
│
├── data_sources/
│   ├── __init__.py                # Re-exports + lazy imports for heavy modules
│   ├── mock.py                    # MockMarketIndexSource, MockComparableCompanySource
│   ├── yfinance_market_index.py   # Epic 1: Live NASDAQ/Russell via yfinance
│   ├── edgar_universe.py          # Epic 2.1: EDGAR company universe by SIC
│   ├── yfinance_metrics.py        # Epic 2.3: EV/Revenue/marketCap via yfinance
│   ├── embedding_ranker.py        # Epic 2.2: Sentence-transformer ranking
│   └── edgar_comps.py             # Epic 2.4: Composite source (wires EDGAR+YFin+Embeddings)
│
├── methodologies/
│   ├── __init__.py
│   ├── base.py                    # MethodologyContext, ValuationMethodology ABC
│   ├── comps.py                   # Comparable Companies methodology
│   ├── last_round.py             # Last-Round Market-Adjusted methodology
│   └── multiple_ratchet.py       # Last-Round Multiple-Ratchet methodology
│
├── agent/
│   ├── __init__.py                # Re-exports CompanyResearchAgent, ResearchResult
│   └── research.py                # Epic 3: LangGraph research agent (5 nodes)
│                                  #   + DuckDuckGo search + multi-provider LLM extraction
│                                  #   + regex fallback + _get_llm() provider chain
│
├── data_sources/
│   ├── ...                        # (existing mock + live sources from Epic 1-2)
│   ├── form_d.py                  # Epic 3.1: SEC Form D filings via EDGAR EFTS
│   └── usaspending.py             # Epic 3.3: Federal contracts via USASpending.gov
│
tests/
├── test_engine.py                 # 138 tests — engine, methodologies, server, CLI, validation
├── test_yfinance.py               # Epic 1 tests — YFinanceMarketIndexSource
├── test_epic2.py                  # 43 tests — EDGAR, metrics, embeddings, composite source
├── test_multiple_ratchet.py       # 39 tests — Multiple-Ratchet methodology
├── test_epic3.py                  # 77 tests — FormD, USASpending, agent nodes, /research
├── test_epic5.py                  # 38 tests — cache list/clear, confidence reports, CLI subcommands
│
examples/
├── comps_request.json             # Sample Comparable Companies request
├── last_round_request.json        # Sample Last-Round request
└── techco_ratchet_request.json    # Sample Multiple-Ratchet request (TechCo scenario)
```

**27 source files, ~3,800 lines of production code, 335 total tests.**

---

## Core Concepts

### Determinism

Every valuation must be **reproducible**. Given the same inputs and the same dataset version, the output is byte-identical. This is achieved by:

- **`dataset_version`** — stamped on every data source (e.g., `"mock-comps-v2"`, `"yfinance-metrics-2026-02-22"`, `"edgar-sic-7372-2026-02-22"`)
- **Daily disk caching** — live data is cached by `{ticker}_{date}.json` so re-runs on the same day return identical values
- **Decimal arithmetic** — all monetary and multiple calculations use `decimal.Decimal` to avoid floating-point drift
- **Isolated non-determinism** — `request_id` and `generated_at_utc` live in `audit_metadata`, never in `valuation_result`

### Protocol Interfaces

Data sources are defined as `typing.Protocol` (PEP 544) — structural subtyping with no inheritance coupling:

```python
@runtime_checkable
class ComparableCompanySource(Protocol):
    def list_by_sector(self, sector: str) -> list[ComparableCompany]: ...
    def list_by_tickers(self, tickers: Iterable[str]) -> list[ComparableCompany]: ...
    @staticmethod
    def aggregate_multiple(comps: list[ComparableCompany], statistic: str) -> Decimal: ...

@runtime_checkable
class MarketIndexSource(Protocol):
    def get_level(self, index_name: str, as_of_date: date) -> MarketIndexPoint: ...
```

Any object with these methods satisfies the contract. The engine doesn't know or care whether it's talking to a mock, a cache, or a live API.

### Audit Trail

Every `ValuationResult` contains:

| Field | Purpose |
|-------|---------|
| `assumptions` | Plain-English statements about what the model assumes |
| `derivation_steps` | Step-by-step math showing how the final number was derived |
| `citations` | Data source labels, dataset versions, and resolved data points |
| `confidence_indicators` | Risk flags (staleness, peer count, data source type) |
| `inputs_used` | Echoed inputs so a reviewer can see exactly what went in |

---

## Data Flow

### Comparable Companies — Full Pipeline

```
User Request
    │
    ├── company_name: "Acme Analytics"
    ├── sector: "enterprise_software"
    ├── revenue_ltm: 50,000,000
    └── private_company_discount_pct: 25
         │
         ▼
┌──────────────────────────────────┐
│ EdgarYFinanceComparableCompanySource │
│                                      │
│  1. Resolve sector → SIC codes       │
│     enterprise_software → [7372,     │
│      7371, 7374, 7379]               │
│                                      │
│  2. EdgarCompanyUniverse             │
│     ├── GET company_tickers.json     │
│     ├── GET browse-edgar?SIC=7372    │
│     └── Cross-reference → 50+ cos   │
│                                      │
│  3. YFinanceMetricsFetcher           │
│     ├── yfinance.Ticker("SNOW").info │
│     ├── EV, Revenue, EV/Rev, desc   │
│     └── Filter: has_valid_multiple   │
│                                      │
│  4. EmbeddingCompsRanker             │
│     ├── Encode target_description    │
│     ├── Encode all candidate descs   │
│     ├── Cosine similarity ranking    │
│     └── Return top-k (default: 5)   │
│                                      │
│  5. Build ComparableCompany objects  │
│     ticker, company_name, sector,    │
│     ev_to_revenue                    │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────┐
│ ComparableCompaniesMethodology │
│                                │
│  multiple = median(ev/rev)     │
│  gross = revenue × multiple    │
│  discount = (100 - 25%) / 100  │
│  fair_value = gross × discount │
│  = 50M × 13.2 × 0.75          │
│  = $495,000,000                │
└────────────┬─────────────────┘
             │
             ▼
       ValuationResult
       (with full audit trail)
```

### Last-Round Market-Adjusted — Pipeline

```
User Request
    │
    ├── last_post_money_valuation: 100,000,000
    ├── last_round_date: "2024-06-30"
    └── public_index: "NASDAQ_COMPOSITE"
         │
         ▼
┌─────────────────────────────────┐
│ MarketIndexSource               │
│  get_level("NASDAQ", 2024-06-30)│ → 17,637.12
│  get_level("NASDAQ", 2026-02-22)│ → 21,311.12
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ LastRoundMarketAdjustedMethodology  │
│                                     │
│  pct_change = (21311 / 17637) - 1   │
│            = +20.83%                │
│  multiplier = 1.2083                │
│  fair_value = 100M × 1.2083         │
│            = $120,831,065.39        │
└─────────────────────────────────────┘
```

### Last-Round Multiple-Ratchet — Pipeline

```
User Request
    │
    ├── last_post_money_valuation: 100,000,000
    ├── revenue_at_last_round: 10,000,000
    ├── current_revenue: 12,000,000
    ├── sector: "enterprise_software"
    └── private_company_discount_pct: 20
         │
         ▼
┌────────────────────────────────────────┐
│ Step 1: Implied Multiple at Last Round │
│  100M / 10M = 10.0×                   │
└────────────┬───────────────────────────┘
             │
             ▼
┌────────────────────────────────────────┐
│ ComparableCompanySource                │
│  list_by_sector("enterprise_software") │
│  aggregate_multiple(comps, "median")   │
│  → 11.80× (mock) or e.g. 7.0× (live) │
└────────────┬───────────────────────────┘
             │
             ▼
┌────────────────────────────────────────┐
│ Step 3: Multiple Ratchet               │
│  11.8 / 10.0 = 1.18 (expansion)       │
│  OR: 7.0 / 10.0 = 0.70 (compression) │
├────────────────────────────────────────┤
│ Step 5: Re-rated Value                 │
│  12M × 11.8 = 141.6M (mock)           │
│  OR: 12M × 7.0 = 84.0M (live)        │
├────────────────────────────────────────┤
│ Step 7: Apply Discount                 │
│  141.6M × 0.80 = $113,280,000 (mock)  │
│  OR: 84.0M × 0.80 = $67,200,000 (live)│
└────────────────────────────────────────┘

Key insight: unlike Last-Round Market-Adjusted (which tracks
a broad index), this method captures sector-specific multiple
compression and company-specific revenue performance.
```

---

## Component Reference

### `ValuationEngine` (`engine.py`)

The central router. Receives a `ValuationRequest`, dispatches to the correct `ValuationMethodology`, and returns a `ValuationResult`.

- **Constructor**: accepts optional `index_source` and `comps_source` kwargs. Defaults to mock sources.
- **Key invariant**: the engine itself contains zero business logic — all valuation math lives in methodology classes.
- **Adding a new methodology**: create a `ValuationMethodology` subclass with a unique `name`, register it in the `_methodologies` dict.

### `ValuationRequest` / `ValuationResult` (`models.py`)

Immutable dataclasses. `ValuationRequest.from_dict()` handles parsing and validation. `ValuationResult.to_dict()` produces the JSON envelope with separate `valuation_result` and `audit_metadata` sections.

### `MethodologyContext` (`methodologies/base.py`)

A simple dataclass carrying the provider implementations:

```python
@dataclass
class MethodologyContext:
    index_source: MarketIndexSource
    comps_source: ComparableCompanySource
```

Passed into every `valuate()` call so methodologies can access data sources without importing them directly.

---

## Data Sources (Protocol-Based)

### Mock Sources (`data_sources/mock.py`)

| Class | Protocol | Description |
|-------|----------|-------------|
| `MockMarketIndexSource` | `MarketIndexSource` | Curated NASDAQ/Russell 2000 levels (2020–2026) |
| `MockComparableCompanySource` | `ComparableCompanySource` | 7 enterprise_software + 5 fintech companies with EV/Revenue multiples |

### Live Sources (Epic 1 + Epic 2)

| Class | File | Protocol | External API |
|-------|------|----------|-------------|
| `YFinanceMarketIndexSource` | `yfinance_market_index.py` | `MarketIndexSource` | Yahoo Finance (index levels) |
| `EdgarCompanyUniverse` | `edgar_universe.py` | — (sub-component) | SEC EDGAR (company search) |
| `YFinanceMetricsFetcher` | `yfinance_metrics.py` | — (sub-component) | Yahoo Finance (company financials) |
| `EmbeddingCompsRanker` | `embedding_ranker.py` | — (sub-component) | Local `all-MiniLM-L6-v2` model |
| `EdgarYFinanceComparableCompanySource` | `edgar_comps.py` | `ComparableCompanySource` | Composite of above three |

### `EdgarCompanyUniverse` — Detail

Builds a universe of public companies by SIC code:

1. **`company_tickers.json`** — Downloads from SEC.gov, maps ~10,000+ filers to CIK + ticker
2. **`browse-edgar?SIC=...&output=atom`** — Atom XML feed returning CIKs for a given SIC code
3. **Cross-references** CIKs with the tickers map to produce `EdgarCompany` objects

**SIC → Sector mapping** (configured in `SIC_SECTOR_MAP`):

| SIC Codes | Internal Sector |
|-----------|----------------|
| 7372, 7371, 7374, 7379 | `enterprise_software` |
| 3674 | `semiconductors` |
| 5045 | `technology_distribution` |
| 6282 | `investment_advice` |
| 4813 | `telecommunications` |
| 3812 | `defense_electronics` |
| 5961 | `ecommerce` |
| 3669 | `communications_equipment` |

### `YFinanceMetricsFetcher` — Detail

For each ticker, pulls from `yfinance.Ticker.info`:

| yfinance key | Maps to |
|-------------|---------|
| `enterpriseValue` | `enterprise_value` |
| `totalRevenue` | `total_revenue` |
| `enterpriseToRevenue` | `ev_to_revenue` (or computed if missing) |
| `marketCap` | `market_cap` |
| `longBusinessSummary` | `business_summary` (used for embedding ranking) |
| `sector`, `industry` | metadata fields |

Returns a `TickerMetrics` frozen dataclass. The `has_valid_multiple` property returns `True` only when EV, Revenue, and EV/Revenue are all present and positive.

### `EmbeddingCompsRanker` — Detail

Uses the `all-MiniLM-L6-v2` sentence-transformer model (384-dimensional embeddings, ~80 MB):

1. Encodes the target company's description as an embedding vector
2. Encodes all candidate descriptions in a single batch
3. Computes cosine similarity between target and each candidate
4. Returns top-k `RankedCompany` objects sorted by descending similarity

**Peer-set quality thresholds:**

| Mean Similarity | Quality Label |
|----------------|---------------|
| > 0.75 | `HIGH` |
| 0.50 – 0.75 | `MEDIUM` |
| < 0.50 | `LOW` |

### `EdgarYFinanceComparableCompanySource` — Composite

Wires the three sub-components together and satisfies the `ComparableCompanySource` Protocol:

```
list_by_sector("enterprise_software")
    │
    ├── _SECTOR_TO_SIC: enterprise_software → [7372, 7371, 7374, 7379]
    ├── EdgarCompanyUniverse.list_by_sic("7372") → [SNOW, MDB, CRM, ...]
    ├── YFinanceMetricsFetcher.fetch_many([...])  → metrics with business_summary
    ├── filter: has_valid_multiple == True
    ├── EmbeddingCompsRanker.rank(target_desc, candidates, top_k=5)
    └── → [ComparableCompany(ticker, name, sector, ev_to_revenue), ...]
```

**Fallback**: if no `target_description` is provided, selects top-k by market cap instead of embedding similarity.

---

## Caching Strategy

All live data sources use a **two-tier cache** (memory + disk) with deterministic keys:

| Source | Cache Key | TTL | Location |
|--------|-----------|-----|----------|
| `YFinanceMarketIndexSource` | `{INDEX}_{date}.json` | 1 day | `data/yfinance_cache/` |
| `YFinanceMetricsFetcher` | `{TICKER}_{date}.json` | 1 day | `data/yfinance_metrics_cache/` |
| `EdgarCompanyUniverse` (tickers) | `company_tickers.json` | 30 days | `data/edgar_cache/` |
| `EdgarCompanyUniverse` (SIC) | `sic_{code}.json` | 30 days | `data/edgar_cache/` |

**Why daily caching?** Financial multiples change daily. Caching by date ensures:
- Same-day re-runs are **deterministic** (auditor can reproduce)
- Next-day runs pick up fresh data
- Corrupt cache files are detected (JSON parse failure) and silently re-fetched

The `data/` directory is `.gitignore`d. Cache directories are created automatically on first use.

---

## Semantic Comp Selection

The `EmbeddingCompsRanker` solves a key problem in comparable-company analysis: **which public companies are actually comparable to this private company?**

### How It Works

Traditional approach: pick companies in the same SIC code. Problem: SIC 7372 ("Prepackaged Software") contains Snowflake, Oracle, Adobe, and hundreds of unrelated software companies.

Our approach: **semantic similarity** using the company's business description.

```
Target: "Cloud-native data analytics platform providing real-time
         business intelligence for enterprise customers"

SNOW (0.92): "Snowflake provides cloud data platform..."         ✅ Very similar
DDOG (0.85): "Datadog provides monitoring and analytics..."      ✅ Similar
ORCL (0.41): "Oracle provides database and cloud services..."    ❌ Too generic
ADBE (0.28): "Adobe provides creative and document software..."  ❌ Different domain
```

### Input Options for `target_description`

| Source | Quality | Effort |
|--------|---------|--------|
| Manual (analyst writes 1–2 sentences) | High — domain expert knows the company | Low |
| Company website / pitch deck copy | Medium — marketing language may be too broad | Low |
| LLM-generated from company name + sector | Medium — good starting point | Very low |
| Web scraping + summarization (future) | Potentially high | Automated |

### Current Recommendation

For best results, provide a `target_description` that describes:
- **What** the company does (product/service)
- **Who** the customers are (enterprise, consumer, SMB)
- **How** they deliver (cloud, on-prem, marketplace)

Example: *"B2B SaaS platform for automated compliance monitoring in financial services, serving mid-market banks and credit unions."*

---

## Audit Trail Design

The output envelope separates deterministic from non-deterministic content:

```json
{
  "valuation_result": {
    "company_name": "...",
    "methodology": "...",
    "as_of_date": "...",
    "estimated_fair_value": { "amount": 495000000.0, "currency": "USD" },
    "assumptions": ["..."],
    "inputs_used": { "...": "..." },
    "citations": [
      {
        "label": "EDGAR + yfinance + embedding ranker",
        "dataset_version": "edgar+yfinance+embeddings-edgar-sic-7372-2026-02-22",
        "resolved_data_points": ["SNOW=13.2x", "DDOG=12.0x", "..."]
      }
    ],
    "derivation_steps": ["..."],
    "confidence_indicators": {
      "peer_count": 5,
      "multiple_spread": 3.2,
      "peer_set_quality": "HIGH - 5+ comparable companies",
      "data_source_type": "live"
    }
  },
  "audit_metadata": {
    "request_id": "uuid-...",
    "generated_at_utc": "2026-02-22T10:30:00Z",
    "engine_version": "0.1.0"
  }
}
```

**`data_source_type`** flips between `"mock"` and `"live"` based on the dataset version string, so reviewers immediately know whether the valuation used real market data.

---

## Testing Architecture

### Test Pyramid

```
335 total tests
├── 318 unit tests (run offline, <2 seconds)
│   ├── test_engine.py    — 138 tests (engine, methodologies, server, CLI, validation)
│   ├── test_yfinance.py  — Epic 1 offline tests
│   ├── test_epic2.py     — 43 tests (EDGAR, metrics, embeddings, composite)
│   ├── test_epic3.py     — 77 tests (Form D, USASpending, agent pipeline, /research)
│   └── test_epic5.py     — 38 tests (cache management, confidence reports, CLI subcommands)
│
└── 17 integration tests (marked @pytest.mark.integration, require network)
    ├── test_yfinance.py  — live Yahoo Finance index tests
    ├── test_epic2.py     — live EDGAR, yfinance, embedding tests
    └── test_epic3.py     — live Form D, USASpending, agent tests
```

### Mocking Strategy

All unit tests run fully offline. The mocking targets match the lazy-import patterns:

| Module | Mock Target | Why |
|--------|------------|-----|
| `yfinance_metrics.py` | `vc_audit_tool.data_sources.yfinance_metrics.yf.Ticker` | Module-level lazy `yf` attribute |
| `yfinance_market_index.py` | `vc_audit_tool.data_sources.yfinance_market_index.yf.download` | Same pattern |
| `edgar_universe.py` | `httpx.get` | `httpx` imported inside method bodies (not module-level) |
| `embedding_ranker.py` | Direct `_model` attribute injection | Avoids loading the 80 MB model in tests |
| `edgar_comps.py` | `MagicMock(spec=...)` for all three sub-components | Tested as pure orchestration |
| `form_d.py` | `httpx.get` | Lazy `import httpx` inside methods |
| `usaspending.py` | `httpx.post` | Lazy `import httpx` inside methods |
| `research.py` | `vc_audit_tool.agent.research.DDGS` + env clearing | Must clear all API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OLLAMA_MODEL`) and mock `DDGS` to prevent live searches |

### Quality Gate Commands

```bash
ruff check src/ tests/          # Lint: pyflakes, isort, bugbear, simplify, builtins, print
ruff format --check src/ tests/ # Formatter: black-compatible
mypy src/                       # Strict type checking with no Any leakage
python -m pytest tests/ -q      # Unit tests only (integration deselected by default)
```

---

## Dependency Map

### Runtime Dependencies

| Package | Version | Used By |
|---------|---------|---------|
| `fastapi` | ≥ 0.115 | `server.py` — HTTP API + Web UI |
| `uvicorn` | ≥ 0.30 | `server.py` — ASGI server |
| `yfinance` | ≥ 0.2.31 | `yfinance_market_index.py`, `yfinance_metrics.py` |
| `httpx` | ≥ 0.27 | `edgar_universe.py`, `form_d.py`, `usaspending.py` |
| `sentence-transformers` | ≥ 2.2 | `embedding_ranker.py` — semantic ranking |
| `langgraph` | ≥ 1.0 | `research.py` — agent state graph |
| `langchain-core` | ≥ 0.3 | `research.py` — message types |
| `langchain-ollama` | ≥ 0.3 | `research.py` — Ollama LLM provider |
| `duckduckgo-search` | ≥ 7.0 | `research.py` — free web search |

### Optional LLM Dependencies (`pip install -e ".[llm]"`)

| Package | Version | Provider |
|---------|---------|----------|
| `langchain-openai` | ≥ 0.3 | OpenAI GPT-4o-mini |
| `langchain-anthropic` | ≥ 0.3 | Anthropic Claude 3.5 Haiku |
| `langchain-google-genai` | ≥ 2.0 | Google Gemini 2.0 Flash |

### Dev Dependencies

| Package | Used For |
|---------|----------|
| `pytest` | Test runner |
| `mypy` | Static type checking |
| `ruff` | Linting + formatting |
| `httpx` | `TestClient` for FastAPI tests |

### Lazy Import Strategy

Heavy dependencies (`yfinance`, `sentence-transformers`, `httpx`) are **never imported at module top level**. Instead:

```python
yf: ModuleType | None = None

def _ensure_yf() -> ModuleType:
    global yf
    if yf is None:
        import yfinance as _yf
        yf = _yf
    return yf
```

This means:
- `import vc_audit_tool` is fast (~10 ms)
- The 80 MB sentence-transformer model only loads when someone actually calls the embedding ranker
- Mock-only usage never touches `yfinance` or `httpx`

---

## Research Agent (Epic 3)

### Overview

The `CompanyResearchAgent` is a LangGraph `StateGraph` that takes **only a company name** and automatically assembles the structured inputs needed for valuation. It does NOT call the valuation engine — it only produces a `ValuationRequest`-shaped dict.

### Pipeline

```
company_name
    │
    ▼
┌─────────────────┐
│  parse_company   │  Normalise name, infer sector from keywords
└────────┬────────┘
         ▼
┌─────────────────┐
│    form_d        │  Search SEC EDGAR EFTS for Form D filings
└────────┬────────┘    → funding rounds (date, issuer, filing URL)
         ▼
┌─────────────────┐
│  web_research    │  4 DuckDuckGo queries × 6 results
│                  │  → regex extraction (always)
│                  │  → LLM extraction (if provider available)
└────────┬────────┘
         ▼
┌─────────────────┐
│   contracts      │  USASpending.gov federal contract lookup
└────────┬────────┘    → award amounts, agencies, descriptions
         ▼
┌─────────────────┐
│    assemble      │  Auto-select methodology, validate completeness,
│                  │  build ValuationRequest dict
└─────────────────┘
```

### LLM Provider Chain (`_get_llm()`)

The web research node uses a **multi-provider fallback chain**:

```
OPENAI_API_KEY set?   → ChatOpenAI(gpt-4o-mini)
    ↓ no
ANTHROPIC_API_KEY set? → ChatAnthropic(claude-3-5-haiku)
    ↓ no
GOOGLE_API_KEY set?    → ChatGoogleGenerativeAI(gemini-2.0-flash)
    ↓ no
OLLAMA_MODEL set?      → ChatOllama(local model)
    ↓ no
Regex-only mode        → still extracts valuations, revenue, dates
```

Each provider is wrapped in `try/except` — if init fails, the next provider is tried. The system always works because regex extraction runs unconditionally before LLM extraction.

### Data Sources

| Source | Module | API | Data Extracted |
|--------|--------|-----|---------------|
| SEC Form D | `form_d.py` | EDGAR EFTS full-text search | Funding dates, issuer names, filing URLs |
| USASpending.gov | `usaspending.py` | REST API | Contract amounts, agencies, descriptions |
| DuckDuckGo | `research.py` | `duckduckgo-search` | Revenue, valuations, round dates, descriptions |

All sources use a 7-day disk cache with the same pattern as Epic 1-2 sources.

### Web Research Strategy

1. **Search phase**: 4 targeted queries run through DuckDuckGo:
   - `"{name} latest funding round valuation post-money"`
   - `"{name} annual revenue ARR"`
   - `"{name} Series A B C D funding raised investors"`
   - `"{name} company overview private valuation"`

2. **Regex extraction** (always runs): patterns for `$X billion valuation`, `raised $X million`, `$X million in revenue`, and dates near funding keywords.

3. **LLM extraction** (when available): sends first 4,000 chars of search snippets with a system prompt requesting structured JSON with `revenue_ltm`, `last_round_date`, `last_round_amount_raised`, `last_post_money_valuation`, `company_description`, and `sources`.

LLM results **override** regex results (they're more accurate), but regex provides a safety net when no LLM is configured.

---

## Extension Points

### Adding a New Methodology

1. Create `src/vc_audit_tool/methodologies/dcf.py`
2. Subclass `ValuationMethodology`, set `name = "dcf"`
3. Implement `valuate()` returning a `ValuationResult`
4. Register in `engine.py`'s `_methodologies` dict

### Adding a New Data Source

1. Implement the `MarketIndexSource` or `ComparableCompanySource` Protocol
2. No base class needed — just match the method signatures
3. Pass the new source to `ValuationEngine(comps_source=my_source)`

### Adding a New Sector

Add the SIC code → sector mapping in `edgar_universe.py`'s `SIC_SECTOR_MAP`:

```python
SIC_SECTOR_MAP["5912"] = "healthcare"
```

The reverse map `_SECTOR_TO_SIC` in `edgar_comps.py` is built automatically.

### Adding a CLI `--live` Flag

Wire `EdgarYFinanceComparableCompanySource` and `YFinanceMarketIndexSource` into `cli.py`:

```python
if args.live:
    from vc_audit_tool.data_sources import (
        EdgarYFinanceComparableCompanySource,
        YFinanceMarketIndexSource,
    )
    engine = ValuationEngine(
        index_source=YFinanceMarketIndexSource(),
        comps_source=EdgarYFinanceComparableCompanySource(
            target_description=args.description or "",
        ),
    )
```
