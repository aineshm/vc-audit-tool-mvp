# Backend Codemap

**Last Updated:** 2026-03-01

Core valuation engine, methodologies, data sources, and FastAPI server.

## Entry Points

| File | Purpose | Key Exports |
|------|---------|-----------|
| `src/vc_audit_tool/server.py` | FastAPI app + CLI entry-point | `app`, `engine`, `store`, `main()` |
| `src/vc_audit_tool/cli.py` | CLI interface | `main()` |
| `src/vc_audit_tool/engine.py` | Valuation engine | `ValuationEngine` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI Server                           │
│  POST /value, /research, /reconcile, /api/value                 │
│  GET /health, /api/runs, /api/runs/{id}                         │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ├─> valuation_service.py (request validation, persist)
               │   └─> run_valuation() → engine.evaluate_from_dict()
               │
      ┌────────┴─────────┬──────────────┬──────────────┐
      │                  │              │              │
   ENGINE         RESEARCH AGENT    RECONCILIATION  STORE
┌─────────┐   ┌─────────────────┐  ┌──────────────┐  ┌──────────┐
│ValEngine│   │LangGraph Agent  │  │CompanyProfile│  │SQLite WAL│
│         │   │  (research.py)  │  │ Selector     │  │or Supabase
│Methods:│   │  (web_research) │  │  Reconciler  │  └──────────┘
│.evaluate│   │ (evidence extrc)│  └──────────────┘
│.mock()  │   │ (form_d, edgar) │
└─────────┘   │ (usaspending)   │
              │ (ddgs)          │
              │ (llm extractor) │
              └─────────────────┘
```

## Key Modules

### Engine (`src/vc_audit_tool/engine.py`)

**Class:** `ValuationEngine`

**Methods:**
- `evaluate_from_dict(request: dict) -> ValuationResult` — primary API
- `evaluate(request: ValuationRequest) -> ValuationResult` — typed interface
- `mock()` → ValuationEngine — factory for testing

**Responsibility:** Orchestrates valuation flow
1. Validate request
2. Select appropriate methodology
3. Run methodology (comps, last-round, ratchet, scorecard, berkus, direct)
4. Assemble auditable result with assumptions, derivation steps, confidence indicators

**Dependencies:**
- All methodologies (`comps.py`, `last_round.py`, `multiple_ratchet.py`, `scorecard.py`, `berkus.py`, `direct_valuation.py`)
- Data sources (`yfinance_metrics.py`, `edgar_universe.py`, `embedding_ranker.py`, `evidence_collector.py`)
- Models (`models.py`, `validation.py`)

### Methodologies (`src/vc_audit_tool/methodologies/`)

| File | Methodology | Phase | Status |
|------|-------------|-------|--------|
| `comps.py` | Comparable Companies (EV/Revenue multiple) | MVP | Complete |
| `last_round.py` | Last-Round Market-Adjusted (index scaling) | Epic 1 | Complete |
| `multiple_ratchet.py` | Last-Round Multiple-Ratchet (sector multiple re-rating) | MVP | Complete |
| `scorecard.py` | Payne Scorecard (7-factor qualitative) | Phase 2 | Complete |
| `berkus.py` | Berkus Method (5-factor risk scoring) | Phase 2 | Complete |
| `direct_valuation.py` | Evidence-signal based (from research agent) | Epic 3 | Complete |

**Base class:** `base.py` → `ValuationMethodology` (abstract)

**Common pattern:**
```python
class SomeMethodology(ValuationMethodology):
    def apply(
        self,
        request: ValuationRequest,
        sources: DataSources  # protocol-based container
    ) -> ValuationResult:
        # Compute and return result with full derivation trail
        pass
```

### Models & Validation (`src/vc_audit_tool/`)

| File | Purpose |
|------|---------|
| `models.py` | Pydantic models: `ValuationRequest`, `ValuationResult`, `Comparable`, `Evidence`, `Assumption`, `DerivationStep` |
| `validation.py` | Input validation and sanitization |
| `interfaces.py` | Protocol definitions for data sources |
| `exceptions.py` | Custom exceptions (`ValidationError`, `DataSourceError`, etc.) |

### Data Sources (`src/vc_audit_tool/data_sources/`)

All implement protocol-based structural interfaces. Auto-switch between mock/live via `ValuationEngine.mock()`.

| File | Protocol | Purpose | Live Provider |
|------|----------|---------|---|
| `yfinance_metrics.py` | `MetricsFetcher` | EV, Revenue, sector multiples | Yahoo Finance API |
| `yfinance_market_index.py` | `MarketIndexSource` | NASDAQ, Russell 2000 levels | Yahoo Finance |
| `edgar_universe.py` | `CompanyUniverse` | ~10K+ public companies by SIC code | SEC EDGAR |
| `embedding_ranker.py` | `CompsRanker` | Semantic ranking of peers (local embeddings) | sentence-transformers |
| `pinecone_ranker.py` | `CompsRanker` | Semantic ranking (Pinecone hosted) | Pinecone API |
| `ranker_factory.py` | — | Factory: Pinecone if API key set, else local | — |
| `form_d.py` | `FormDProvider` | Regulation D filings, funding rounds | SEC EDGAR (EFTS) |
| `usaspending.py` | `ContractProvider` | Federal contract revenue | USASpending.gov API |
| `evidence_patterns.py` | — | Regex patterns, source tiers, confidence scoring | — |
| `evidence_collector.py` | — | Extract evidence from research data | — |
| `mock.py` | All protocols | Mock implementations (curated datasets) | Built-in |

**Protocol example:**
```python
class MetricsFetcher(Protocol):
    def fetch_metrics(
        self, ticker: str, metric: str
    ) -> Decimal: ...

    def fetch_multiples(
        self, tickers: list[str], metric: str
    ) -> dict[str, Decimal]: ...
```

### Storage Layer (`src/vc_audit_tool/store*.py`)

| File | Class | Purpose | Activation |
|------|-------|---------|-----------|
| `store.py` | `ValuationStore` | SQLite WAL persistence | Default |
| `store_supabase.py` | `SupabaseValuationStore` | PostgreSQL (Supabase) | Phase 4 |
| `store_factory.py` | `get_store()` | Factory selecting active store | Both |

**Protocol:** `ValuationStoreProtocol`
```python
class ValuationStoreProtocol(Protocol):
    def save(self, result_dict: dict) -> str: ...        # upsert, return request_id
    def list_runs(self, limit: int = 50) -> list[dict]: ...  # recent summaries
    def get_run(self, request_id: str) -> dict | None: ...   # full payload
    def close(self) -> None: ...                          # cleanup
```

**Supabase table schema:**
```sql
CREATE TABLE valuation_runs (
  request_id TEXT PRIMARY KEY,
  company_name TEXT,
  methodology TEXT,
  as_of_date DATE,
  fair_value DECIMAL,
  generated_at_utc TIMESTAMP,
  payload JSONB
);
```

### FastAPI Routers (`src/vc_audit_tool/routers/`)

| File | Route | Method | Purpose |
|------|-------|--------|---------|
| `valuation.py` | `/health` | GET | Liveness probe (returns `store`, `llm_provider`, `pinecone_index`) |
| `` | `/` | GET | Serve single-page web UI |
| `` | `/value` | POST | Run valuation (no persist) |
| `` | `/api/value` | POST | Run valuation + persist to store |
| `` | `/api/runs` | GET | List recent runs (summary) |
| `` | `/api/runs/{run_id}` | GET | Get full payload for one run |
| `research.py` | `/research` | POST | Automated research + valuation from company name |
| `reconcile.py` | `/reconcile` | POST | Multi-methodology reconciled valuation |

### Services (`src/vc_audit_tool/services/`)

| File | Purpose |
|------|---------|
| `valuation_service.py` | Request validation, JSON parsing, engine orchestration, response formatting |

## Data Flow

### 1. Comparable Companies Flow

```
POST /api/value (with sector, revenue_ltm)
    ↓
request validation
    ↓
ValuationEngine.evaluate(request)
    ↓
ComparableCompaniesMethodology.apply()
    ├─> EdgarCompanyUniverse.find_by_sector(sector)  [~100–1000 companies]
    ├─> YFinanceMetricsFetcher.fetch_multiples(tickers, "ev_revenue")
    ├─> EmbeddingCompsRanker.rank(peers, target_description)  [top 5–10]
    ├─> compute median EV/Revenue multiple
    ├─> apply to revenue_ltm
    ├─> apply private_company_discount (from config/methodology_rules_v1.yaml)
    └─> assemble ValuationResult with full derivation trail
    ↓
store.save(result)  [SQLite or Supabase]
    ↓
return JSON response
```

### 2. Research-First Flow

```
POST /research (with company_name only)
    ↓
ResearchAgent.run()
    ├─> parse company name, infer sector
    ├─> FormDProvider.find_filings(company_name)  [funding rounds]
    ├─> web_research node: 7 DDGS queries × 6 results  [42 snippets]
    ├─> USASpending.find_contracts(company_name)  [federal revenue]
    ├─> llm_extractor: LLM structured extraction (Gemini > OpenAI > ...)
    ├─> evidence_collector: classify evidence (type, recency, source_tier)
    └─> assemble ValuationRequest with auto-selected methodology
    ↓
engine.evaluate(assembled_request)
    ↓
store.save(result)
    ↓
return ValuationResult + audit_metadata
```

### 3. Reconciliation Flow

```
POST /reconcile (with company_name)
    ↓
research_agent.run()  [gathers company data]
    ↓
CompanyProfiler.profile()  [classify into lifecycle stage: pre_seed, seed, early, growth, late]
    ↓
MethodologySelector.select()  [load config/methodology_rules_v1.yaml, pick applicable methods]
    ↓
for each selected methodology:
    engine.evaluate() in parallel
    ↓
Reconciler.reconcile()
    ├─> compute weighted-average point estimate
    ├─> derive range (±10% or min/max from results)
    ├─> check divergence (flag if any pair differs >40%)
    └─> assemble ReconciliationResult
    ↓
store.save(result)
    ↓
return JSON
```

## Key Patterns

### 1. Protocol-Based Data Sources

All data sources use Python `typing.Protocol` for structural subtyping:

```python
# No inheritance, just match the interface
class MockMetricsFetcher:
    def fetch_metrics(self, ticker: str, metric: str) -> Decimal:
        # mock implementation
        pass

class YFinanceMetricsFetcher:
    def fetch_metrics(self, ticker: str, metric: str) -> Decimal:
        # live implementation
        pass

# Engine accepts either — no type checking needed
engine = ValuationEngine(sources=DataSources(
    metrics=MockMetricsFetcher() if mock else YFinanceMetricsFetcher()
))
```

### 2. Deterministic Output (Decimal-based)

All monetary calculations use `decimal.Decimal`:

```python
# BAD: floating-point rounding errors
multiple = 11.8
revenue = 10_000_000.0
value = revenue * multiple  # 118000000.0000002?

# GOOD: precise decimal arithmetic
multiple = Decimal("11.8")
revenue = Decimal("10000000")
value = revenue * multiple  # Decimal("118000000") exactly
```

Weights normalized to sum to exactly 1.0:
```python
weights = [Decimal("0.3"), Decimal("0.4"), Decimal("0.3")]
# Adjust first if rounding:
weights[0] = Decimal("1.0") - sum(weights[1:])
```

### 3. Derivation Trail (Audit Trail)

Every result includes:

```python
# assumptions — what assumptions were made
result.assumptions = [
    "Comparable universe based on sector peer set 'enterprise_software'.",
    "Applied median EV/Revenue multiple of 11.80x.",
    "Applied private-company discount of 20.00%.",
]

# derivation_steps — step-by-step calculations (readable walkthrough)
result.derivation_steps = [
    "Step 1: Find peer multiples (median): 11.80x.",
    "Step 2: Apply multiple to LTM revenue: 10,000,000 * 11.80 = 118,000,000 USD.",
    "Step 3: Compute discount (100 - 20.00) / 100 = 0.8000.",
    "Step 4: Apply discount: 118,000,000 * 0.8000 = 94,400,000 USD.",
]

# confidence_indicators — basis for trust
result.confidence_indicators = {
    "peer_count": 7,
    "multiple_spread": 5.6,
    "peer_set_quality": "HIGH - 5+ comparable companies",
    "data_source_type": "live",
}
```

### 4. Evidence Confidence Scoring

**Score = base_type_confidence × recency_multiplier × source_tier_multiplier**

See `evidence_patterns.py`:
```python
SOURCE_RELIABILITY_TIERS = {
    "bloomberg.com": 0.95,
    "reuters.com": 0.95,
    "wsj.com": 0.95,
    "techcrunch.com": 0.90,
    # ... 35 entries total
}

def _classify_evidence_type(snippet: str) -> tuple[str, float, str]:
    # returns (type, base_confidence, domain)
    # type: "funding", "revenue", "acquisition", etc.
    # base_confidence: 0.3 – 0.9 depending on type
    # domain: extracted from source URL
    pass
```

### 5. Configurable Discounts

Per-methodology illiquidity discounts in `config/methodology_rules_v1.yaml`:

```yaml
private_company_discount:
  defaults:
    comparable_companies: 25
    last_round_market_adjusted: 10
    last_round_multiple_ratchet: 25
  max_allowed: 50
  exempt: [scorecard, berkus]
```

Applied in `_discount_config.py`:
```python
def get_discount_default(methodology: str) -> int:
    # Load from YAML (with fallback defaults)
    pass

def clamp_discount(discount: int, methodology: str) -> int:
    # Validate against max_allowed, return clamped value
    pass
```

Always disclosed in `derivation_steps`:
```
"Step 3: Apply private-company discount (25%): 118,000,000 * 0.75 = 88,500,000 USD."
```

## Dependency Map

```
ValuationEngine
├─> Methodologies (comps, last_round, ratchet, scorecard, berkus, direct)
│   ├─> DataSources (metrics, universe, ranker, index)
│   ├─> Models (ValuationRequest, ValuationResult, Comparable, Evidence)
│   └─> _discount_config (get_discount_default, clamp_discount)
│
├─> DataSources
│   ├─> yfinance_metrics (live) or mock.metrics
│   ├─> edgar_universe (live) or mock.universe
│   ├─> embedding_ranker (local) or pinecone_ranker (hosted)
│   ├─> yfinance_market_index (live) or mock.market_index
│   └─> cache (daily dataset caching)
│
└─> Models & Validation
    ├─> validation (input sanitization)
    └─> interfaces (protocol definitions)

FastAPI Server
├─> ValuationEngine
├─> Store (SQLite or Supabase)
├─> Research Agent (LangGraph)
├─> Reconciliation Engine (profiler, selector, reconciler)
└─> Routers (valuation, research, reconcile)

Research Agent
├─> FormDProvider (EDGAR EFTS)
├─> web_research node (DDGS)
├─> USASpending provider
├─> LLM provider (multi-provider fallback)
└─> Evidence collector & classifier
```

## Configuration Files

| File | Purpose |
|------|---------|
| `config/llm_providers.yaml` | LLM provider chain (order, models, cost tracking) |
| `config/methodology_rules_v1.yaml` | Methodology weights by company stage, discount defaults |
| `src/vc_audit_tool/.env.example` | Backend env vars reference |

## Testing

| Fixture | Purpose |
|---------|---------|
| `conftest.py` (autouse `isolated_store`) | Fresh SQLite per test (prevents leakage) |
| `conftest.py` (autouse `engine=mock`) | Server engine swaps to mock at import time |
| `mock.py` | Mock implementations of all data sources |

Test markers:
- `@pytest.mark.integration` — external API calls (SEC, Yahoo Finance)
- `@pytest.mark.agent` — LangGraph agent tests
- `@pytest.mark.epic` — milestone validation tests

## Related Codemaps

- **[frontend.md](./frontend.md)** — Next.js UI, API client
- **[data-sources.md](./data-sources.md)** — Detailed data source implementations
- **[agent.md](./agent.md)** — LangGraph research agent architecture
- **[storage.md](./storage.md)** — SQLite + Supabase store details
- **[reconciliation.md](./reconciliation.md)** — Multi-method selection and reconciliation
