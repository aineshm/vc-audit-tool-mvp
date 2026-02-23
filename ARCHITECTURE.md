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
8. [Reconciliation Layer (Phase 2)](#reconciliation-layer-phase-2)
9. [Caching Strategy](#caching-strategy)
10. [Semantic Comp Selection](#semantic-comp-selection)
11. [Audit Trail Design](#audit-trail-design)
12. [Research Agent (Epic 3)](#research-agent-epic-3)
13. [Testing Architecture](#testing-architecture)
14. [Dependency Map](#dependency-map)
15. [Extension Points](#extension-points)

---

## System Overview

```
                  +----------------------------+
                  |      CLI / API             |  (cli.py, server.py)
                  |  POST /value               |  <- structured inputs
                  |  POST /research            |  <- company name only
                  |  POST /reconcile           |  <- multi-methodology
                  +------+------+------+-------+
                         |      |      |
          +--------------+      |      +------------------+
          | /value              | /research               | /reconcile
          v                     v                         v
+-----------------+  +-----------------------+  +------------------------+
| ValuationEngine |  | CompanyResearchAgent  |  | ReconciliationEngine   |
+--------+--------+  | (LangGraph StateGraph)|  |  + CompanyProfiler     |
         |           |                       |  |  + MethodologySelector  |
         |  routes   |  +- parse_company     |  |  + Reconciler          |
         |  to       |  +- form_d (EDGAR)    |  |  + ValuationEngine     |
         |  method   |  +- web_research (DDG)|  +------------+-----------+
         |           |  +- contracts (USA$)  |               |
         |           |  +- assemble          |               | runs each
         |           +----------+------------+               | selected method
         |                      | assembled inputs           |
         |                      v                            |
         |           +-----------------+                     |
         |           | ValuationEngine |<--------------------+
         |           +--------+--------+
         |                    |
  +------+--------------------+-------------------+
  |      |         |          |            |      |
+-v------v-+ +--v------+ +---v------+ +---v-----+ +---v----+
| Last-Rnd | |  Comps  | | Multiple | |Scorecard| | Berkus |
| Mkt-Adj  | |Companies| | Ratchet  | |(Payne 7)| |(5 risk)|
+-----+----+ +---+-----+ +---+------+ +---------+ +--------+
      |           |           |
 +----v------+  +-v-----------v-----------+
 | IndexSrc  |  | ComparableCompanySrc    |  (interfaces.py)
 | Protocol  |  |       Protocol          |
 +-----+-----+  +-----+------------------+
       |               |
+------+--------+  +---+-------------------+
| Mock  | Live  |  | Mock | EDGAR+YFin     |  (data_sources/)
| Index | YFin  |  | Comps| +Embeddings    |
+-------+-------+  +------+---------------+
```

The engine is **data-source agnostic**. It accepts any object satisfying the `Protocol` interfaces -- the same engine code runs identically with mock data (for tests and demos) or live data (for production valuations).

The **reconciliation layer** (Phase 2) sits above the valuation engine: it profiles the company, selects applicable methodologies with stage-based weights from a YAML config, runs each through the engine, and reconciles the results into a single concluded valuation with divergence analysis.

### Endpoint Interaction Modes

| Endpoint | Input Style | Methodology Selection | Sector Handling |
|----------|-------------|-----------------------|-----------------|
| `POST /research` | Research-first (`company_name` + optional hints) | Auto-selected by research agent when omitted | Inferred by agent |
| `POST /reconcile` | Research-first (`company_name` + optional hints) | Selected by reconciliation selector/rules | Inferred from assembled research data |
| `POST /value` or `POST /api/value` | Manual structured payload | Explicitly provided by caller | Required for manual `comparable_companies` |

---

## Directory Structure

```
src/vc_audit_tool/
+-- __init__.py                    # Package version
+-- cli.py                         # CLI entry point -- value, cache, confidence subcommands
+-- server.py                      # FastAPI server + Web UI + SQLite persistence
+-- engine.py                      # ValuationEngine -- routes requests to methodologies
+-- models.py                      # ValuationRequest, ValuationResult, Citation, MonetaryAmount
+-- interfaces.py                  # Protocol definitions (MarketIndexSource, ComparableCompanySource)
+-- validation.py                  # Input parsing & validation helpers
+-- exceptions.py                  # ValidationError, DataSourceError
+-- store.py                       # SQLite-backed ValuationStore (run history)
+-- cache.py                       # Epic 5.1: Cache list/clear utilities
+-- confidence.py                  # Epic 5.2: Confidence-indicator report formatter
|
+-- data_sources/
|   +-- __init__.py                # Re-exports + lazy imports for heavy modules
|   +-- mock.py                    # MockMarketIndexSource, MockComparableCompanySource
|   +-- yfinance_market_index.py   # Epic 1: Live NASDAQ/Russell via yfinance
|   +-- edgar_universe.py          # Epic 2.1: EDGAR company universe by SIC
|   +-- yfinance_metrics.py        # Epic 2.3: EV/Revenue/marketCap via yfinance
|   +-- embedding_ranker.py        # Epic 2.2: Sentence-transformer ranking
|   +-- edgar_comps.py             # Epic 2.4: Composite source (wires EDGAR+YFin+Embeddings)
|   +-- form_d.py                  # Epic 3.1: SEC Form D filings via EDGAR EFTS
|   +-- usaspending.py             # Epic 3.3: Federal contracts via USASpending.gov
|
+-- methodologies/
|   +-- __init__.py
|   +-- base.py                    # MethodologyContext, ValuationMethodology ABC
|   +-- comps.py                   # Comparable Companies methodology
|   +-- last_round.py              # Last-Round Market-Adjusted methodology
|   +-- multiple_ratchet.py        # Last-Round Multiple-Ratchet methodology
|   +-- scorecard.py               # Phase 2: Payne Scorecard (7 qualitative factors)
|   +-- berkus.py                  # Phase 2: Berkus Method (5 risk dimensions)
|
+-- reconciliation/                # Phase 2: Multi-methodology reconciliation
|   +-- __init__.py                # Re-exports core types
|   +-- models.py                  # CompanyProfile, MethodologyPlan, DataPackage,
|   |                              #   ConcludedValue, ReconciliationSummary,
|   |                              #   ReconciledValuation
|   +-- profiler.py                # CompanyProfiler -- classifies lifecycle stage
|   +-- selector.py                # MethodologySelector -- YAML rules -> MethodologyPlan
|   +-- reconciler.py              # Reconciler -- weighted avg, range, divergence
|   +-- engine.py                  # ReconciliationEngine -- orchestrates the pipeline
|
+-- agent/
|   +-- __init__.py                # Re-exports CompanyResearchAgent, ResearchResult
|   +-- research.py                # Epic 3: LangGraph research agent (5 nodes)

config/
+-- methodology_rules_v1.yaml      # Phase 2: Versioned stage weights + exclusion rules

tests/
+-- conftest.py                    # Shared fixtures
+-- test_engine.py                 # 138 tests -- engine, methodologies, server, CLI, validation
+-- test_yfinance.py               # Epic 1 tests -- YFinanceMarketIndexSource
+-- test_epic2.py                  # 43 tests -- EDGAR, metrics, embeddings, composite source
+-- test_multiple_ratchet.py       # 39 tests -- Multiple-Ratchet methodology
+-- test_epic3.py                  # 77 tests -- FormD, USASpending, agent nodes, /research
+-- test_epic5.py                  # 38 tests -- cache list/clear, confidence reports
+-- test_scorecard.py              # Phase 2: 11 tests -- Scorecard methodology
+-- test_berkus.py                 # Phase 2:  9 tests -- Berkus methodology
+-- test_reconciliation.py         # Phase 2: 37 tests -- profiler, selector, reconciler, engine, /reconcile
+-- test_cli.py                    # CLI subcommand tests
+-- test_determinism.py            # Determinism + reproducibility tests
+-- test_methodologies.py          # Cross-methodology parametrized tests
+-- test_serialization.py          # JSON envelope / to_dict round-trip tests
+-- test_server.py                 # FastAPI endpoint tests
+-- test_store.py                  # SQLite store tests
+-- test_validation.py             # Input validation tests
+-- test_web.py                    # Web UI endpoint tests

examples/
+-- comps_request.json             # Sample Comparable Companies request
+-- last_round_request.json        # Sample Last-Round request
+-- techco_ratchet_request.json    # Sample Multiple-Ratchet request (TechCo scenario)
```

**35 source files, ~5,100 lines of production code, 392 total tests.**

---

## Core Concepts

### Determinism

Every valuation must be **reproducible**. Given the same inputs and the same dataset version, the output is byte-identical. This is achieved by:

- **`dataset_version`** -- stamped on every data source (e.g., `"mock-comps-v2"`, `"yfinance-metrics-2026-02-22"`, `"edgar-sic-7372-2026-02-22"`)
- **Daily disk caching** -- live data is cached by `{ticker}_{date}.json` so re-runs on the same day return identical values
- **Decimal arithmetic** -- all monetary and multiple calculations use `decimal.Decimal` to avoid floating-point drift
- **Isolated non-determinism** -- `request_id` and `generated_at_utc` live in `audit_metadata`, never in `valuation_result`

### Protocol Interfaces

Data sources are defined as `typing.Protocol` (PEP 544) -- structural subtyping with no inheritance coupling:

```python
@runtime_checkable
class ComparableCompanySource(Protocol):
    def list_by_sector(
        self, sector: str, *, target_description: str | None = None
    ) -> list[ComparableCompany]: ...
    def list_by_tickers(self, tickers: Iterable[str]) -> list[ComparableCompany]: ...
    @staticmethod
    def aggregate_multiple(comps: list[ComparableCompany], statistic: str) -> Decimal: ...

@runtime_checkable
class MarketIndexSource(Protocol):
    def get_level(self, index_name: str, as_of_date: date) -> MarketIndexPoint: ...
```

Any object with these methods satisfies the contract. The engine does not know or care whether it is talking to a mock, a cache, or a live API.

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

### Comparable Companies -- Full Pipeline

```
User Request
    |
    +-- company_name: "Acme Analytics"
    +-- sector: "enterprise_software"
    +-- revenue_ltm: 50,000,000
    +-- private_company_discount_pct: 25
         |
         v
+-------------------------------------------+
| EdgarYFinanceComparableCompanySource       |
|                                           |
|  1. Resolve sector -> SIC codes           |
|     enterprise_software -> [7372,         |
|      7371, 7374, 7379]                    |
|                                           |
|  2. EdgarCompanyUniverse                  |
|     +-- GET company_tickers.json          |
|     +-- GET browse-edgar?SIC=7372         |
|     +-- Cross-reference -> 50+ cos        |
|                                           |
|  3. YFinanceMetricsFetcher                |
|     +-- yfinance.Ticker("SNOW").info      |
|     +-- EV, Revenue, EV/Rev, desc         |
|     +-- Filter: has_valid_multiple        |
|                                           |
|  4. EmbeddingCompsRanker                  |
|     +-- Encode target_description         |
|     +-- Encode all candidate descs        |
|     +-- Cosine similarity ranking         |
|     +-- Return top-k (default: 5)         |
|                                           |
|  5. Build ComparableCompany objects       |
|     ticker, company_name, sector,         |
|     ev_to_revenue                         |
+-------------------+-----------------------+
                    |
                    v
+-----------------------------------+
| ComparableCompaniesMethodology    |
|                                   |
|  multiple = median(ev/rev)        |
|  gross = revenue * multiple       |
|  discount = (100 - 25%) / 100     |
|  fair_value = gross * discount    |
|  = 50M * 13.2 * 0.75             |
|  = $495,000,000                   |
+-----------------+-----------------+
                  |
                  v
            ValuationResult
            (with full audit trail)
```

### Last-Round Market-Adjusted -- Pipeline

```
User Request
    |
    +-- last_post_money_valuation: 100,000,000
    +-- last_round_date: "2024-06-30"
    +-- public_index: "NASDAQ_COMPOSITE"
         |
         v
+--------------------------------------+
| MarketIndexSource                    |
|  get_level("NASDAQ", 2024-06-30)     | -> 17,637.12
|  get_level("NASDAQ", 2026-02-22)     | -> 21,311.12
+---------------+----------------------+
                |
                v
+--------------------------------------+
| LastRoundMarketAdjustedMethodology   |
|                                      |
|  pct_change = (21311 / 17637) - 1    |
|            = +20.83%                 |
|  multiplier = 1.2083                 |
|  fair_value = 100M * 1.2083          |
|            = $120,831,065.39         |
+--------------------------------------+
```

### Last-Round Multiple-Ratchet -- Pipeline

```
User Request
    |
    +-- last_post_money_valuation: 100,000,000
    +-- revenue_at_last_round: 10,000,000
    +-- current_revenue: 12,000,000
    +-- sector: "enterprise_software"
    +-- private_company_discount_pct: 20
         |
         v
+------------------------------------------+
| Step 1: Implied Multiple at Last Round   |
|  100M / 10M = 10.0x                     |
+---------------+--------------------------+
                |
                v
+------------------------------------------+
| ComparableCompanySource                  |
|  list_by_sector("enterprise_software")   |
|  aggregate_multiple(comps, "median")     |
|  -> 11.80x (mock) or e.g. 7.0x (live)   |
+---------------+--------------------------+
                |
                v
+------------------------------------------+
| Step 3: Multiple Ratchet                 |
|  11.8 / 10.0 = 1.18 (expansion)         |
|  OR: 7.0 / 10.0 = 0.70 (compression)   |
+------------------------------------------+
| Step 5: Re-rated Value                   |
|  12M * 11.8 = 141.6M (mock)             |
|  OR: 12M * 7.0 = 84.0M (live)           |
+------------------------------------------+
| Step 7: Apply Discount                   |
|  141.6M * 0.80 = $113,280,000 (mock)    |
|  OR: 84.0M * 0.80 = $67,200,000 (live)  |
+------------------------------------------+

Key insight: unlike Last-Round Market-Adjusted (which tracks
a broad index), this method captures sector-specific multiple
compression and company-specific revenue performance.
```

### Reconciliation -- Full Pipeline (Phase 2)

```
POST /reconcile { "company_name": "Anthropic" }
    |
    v
+--------------------------------------+
| CompanyResearchAgent                 |
|  SEC Form D + DuckDuckGo + LLM      |
|  -> assembled_request dict           |
+------------------+-------------------+
                   |
                   v
+--------------------------------------+
| CompanyProfiler                      |
|  _classify_stage():                  |
|  +- age < 1y, no rev  -> pre_seed   |
|  +- age < 2y, low rev -> seed       |
|  +- age < 5y          -> early      |
|  +- age < 10y         -> growth     |
|  +- else              -> late       |
|                                      |
|  -> CompanyProfile(stage="growth")   |
+------------------+-------------------+
                   |
                   v
+--------------------------------------+
| MethodologySelector                  |
|  1. Load YAML rules (v1.0)          |
|  2. Stage exclusions                 |
|  3. Data-availability rules          |
|  4. Base weights from config         |
|  5. Adjust weights by data rules     |
|  6. Renormalise -> MethodologyPlan   |
|                                      |
|  growth -> {comps: 0.60,            |
|             last_round: 0.40}        |
+------------------+-------------------+
                   |
                   v
+--------------------------------------+
| Reconciler                           |
|  For each method in plan:            |
|    -> ValuationEngine.evaluate()     |
|                                      |
|  Weighted avg:                       |
|    0.60 * $130M + 0.40 * $105M      |
|    = $120M                           |
|                                      |
|  Range: +/-10% or min/max results    |
|  Divergence: >40% spread -> flag     |
|                                      |
|  -> ReconciledValuation              |
+--------------------------------------+
```

---

## Component Reference

### `ValuationEngine` (`engine.py`)

The central router. Receives a `ValuationRequest`, dispatches to the correct `ValuationMethodology`, and returns a `ValuationResult`.

- **Constructor**: accepts optional `index_source` and `comps_source` kwargs. Defaults to mock sources.
- **Key invariant**: the engine itself contains zero business logic -- all valuation math lives in methodology classes.
- **Registered methodologies** (5): `comparable_companies`, `last_round_market_adjusted`, `last_round_multiple_ratchet`, `scorecard`, `berkus`.
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

### `ReconciliationEngine` (`reconciliation/engine.py`) -- Phase 2

Top-level orchestrator for multi-methodology reconciliation. Wires together `CompanyProfiler`, `MethodologySelector`, and `Reconciler`.

- **`value(profile, data_package, as_of_date, ...)`** -- builds methodology requests, runs all selected methods through `ValuationEngine`, reconciles, and returns a `ReconciledValuation`.
- Uses mock data sources by default (same as `ValuationEngine`).

### `CompanyProfiler` (`reconciliation/profiler.py`) -- Phase 2

Classifies a company into a lifecycle stage based on:

| Signal | Stage Mapping |
|--------|--------------|
| Age < 1y, no revenue | `pre_seed` |
| Age < 2y, low revenue | `seed` |
| Age < 5y | `early` |
| Age < 10y | `growth` |
| Age >= 10y | `late` |

Also computes `has_revenue`, `last_round_age_months`, `estimated_arr`, and a human-readable `profile_summary`.

### `MethodologySelector` (`reconciliation/selector.py`) -- Phase 2

Loads `config/methodology_rules_v1.yaml` and applies a 6-step selection pipeline:

1. Load all registered methodologies
2. Apply stage exclusions (e.g., `pre_seed` excludes `comparable_companies`)
3. Apply data-availability rules (round staleness, peer-set quality, revenue presence)
4. Load base weights from config
5. Adjust weights based on data-quality modifiers
6. Renormalise remaining weights to sum to 1.0

Output: `MethodologyPlan` with weighted list of applicable methods.

### `Reconciler` (`reconciliation/reconciler.py`) -- Phase 2

Takes a `MethodologyPlan` + per-method `ValuationResult`s and produces a `ReconciliationSummary`:

- **Point estimate**: weighted average of individual fair values
- **Range**: derived from min/max results (+/-10% if only one method)
- **Divergence flag**: triggered when any pair of results differs by > 40%
- **Rationale**: auto-generated explanation of the weighting and reconciliation logic


---

## Data Sources (Protocol-Based)

### Mock Sources (`data_sources/mock.py`)

| Class | Protocol | Description |
|-------|----------|-------------|
| `MockMarketIndexSource` | `MarketIndexSource` | Curated NASDAQ/Russell 2000 levels (2020-2026) |
| `MockComparableCompanySource` | `ComparableCompanySource` | 7 enterprise_software + 5 fintech companies with EV/Revenue multiples |

### Live Sources (Epic 1 + Epic 2)

| Class | File | Protocol | External API |
|-------|------|----------|-------------|
| `YFinanceMarketIndexSource` | `yfinance_market_index.py` | `MarketIndexSource` | Yahoo Finance (index levels) |
| `EdgarCompanyUniverse` | `edgar_universe.py` | -- (sub-component) | SEC EDGAR (company search) |
| `YFinanceMetricsFetcher` | `yfinance_metrics.py` | -- (sub-component) | Yahoo Finance (company financials) |
| `EmbeddingCompsRanker` | `embedding_ranker.py` | -- (sub-component) | Local `all-MiniLM-L6-v2` model |
| `EdgarYFinanceComparableCompanySource` | `edgar_comps.py` | `ComparableCompanySource` | Composite of above three |

### `EdgarCompanyUniverse` -- Detail

Builds a universe of public companies by SIC code:

1. **`company_tickers.json`** -- Downloads from SEC.gov, maps ~10,000+ filers to CIK + ticker
2. **`browse-edgar?SIC=...&output=atom`** -- Atom XML feed returning CIKs for a given SIC code
3. **Cross-references** CIKs with the tickers map to produce `EdgarCompany` objects

**SIC -> Sector mapping** (configured in `SIC_SECTOR_MAP`):

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

### `YFinanceMetricsFetcher` -- Detail

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

### `EmbeddingCompsRanker` -- Detail

Uses the `all-MiniLM-L6-v2` sentence-transformer model (384-dimensional embeddings, ~80 MB):

1. Encodes the target company's description as an embedding vector
2. Encodes all candidate descriptions in a single batch
3. Computes cosine similarity between target and each candidate
4. Returns top-k `RankedCompany` objects sorted by descending similarity

**Peer-set quality thresholds:**

| Mean Similarity | Quality Label |
|----------------|---------------|
| > 0.75 | `HIGH` |
| 0.50 - 0.75 | `MEDIUM` |
| < 0.50 | `LOW` |

### `EdgarYFinanceComparableCompanySource` -- Composite

Wires the three sub-components together and satisfies the `ComparableCompanySource` Protocol:

```
list_by_sector("enterprise_software")
    |
    +-- _SECTOR_TO_SIC: enterprise_software -> [7372, 7371, 7374, 7379]
    +-- EdgarCompanyUniverse.list_by_sic("7372") -> [SNOW, MDB, CRM, ...]
    +-- YFinanceMetricsFetcher.fetch_many([...])  -> metrics with business_summary
    +-- filter: has_valid_multiple == True
    +-- EmbeddingCompsRanker.rank(target_desc, candidates, top_k=5)
    +-- -> [ComparableCompany(ticker, name, sector, ev_to_revenue), ...]
```

**Fallback**: if no `target_description` is provided, selects top-k by market cap instead of embedding similarity.

---

## Methodologies

### Phase 1 Methodologies

| # | Name | Class | Key Inputs |
|---|------|-------|-----------|
| 1 | `comparable_companies` | `ComparableCompaniesMethodology` | `revenue_ltm`, `sector`, `private_company_discount_pct`, optional `target_description` |
| 2 | `last_round_market_adjusted` | `LastRoundMarketAdjustedMethodology` | `last_post_money_valuation`, `last_round_date`, `public_index` |
| 3 | `last_round_multiple_ratchet` | `LastRoundMultipleRatchetMethodology` | `last_post_money_valuation`, `revenue_at_last_round`, `current_revenue`, `sector`, optional `target_description` |

### Phase 2 Methodologies

| # | Name | Class | Key Inputs |
|---|------|-------|-----------|
| 4 | `scorecard` | `ScorecardMethodology` | `regional_median_pre_money`, `scorecard_factors` (7 Payne factors) |
| 5 | `berkus` | `BerkusMethodology` | `max_pre_money_valuation`, `factors` (5 risk dimensions) |

**Scorecard -- Payne's 7 Factors:**

| Factor | Default Weight | Score Range |
|--------|---------------|-------------|
| `team` | 30% | 0.0 - 2.0 |
| `opportunity` | 25% | 0.0 - 2.0 |
| `product` | 15% | 0.0 - 2.0 |
| `competitive_env` | 10% | 0.0 - 2.0 |
| `marketing` | 10% | 0.0 - 2.0 |
| `need_for_funding` | 5% | 0.0 - 2.0 |
| `other` | 5% | 0.0 - 2.0 |

Valuation = `regional_median * weighted_avg_factor`

**Berkus -- 5 Risk Dimensions:**

| Factor | Max Contribution |
|--------|-----------------|
| `sound_idea` | 20% of max |
| `prototype` | 20% of max |
| `quality_management` | 20% of max |
| `strategic_relationships` | 20% of max |
| `product_rollout` | 20% of max |

Valuation = `max_pre_money * sum(factor_scores * 0.20)`


---

## Reconciliation Layer (Phase 2)

### Architecture

```
                    +----------------------+
                    | ReconciliationEngine |
                    +-----------+----------+
                                |
               +----------------+----------------+
               |                |                |
      +--------v------+  +-----v------+  +------v-----+
      |CompanyProfiler|  |Methodology |  | Reconciler |
      |               |  | Selector   |  |            |
      +---------------+  +-----+------+  +------------+
                              |
                    +---------v---------+
                    | YAML Rules Config |
                    | (v1.0)            |
                    +-------------------+
```

### Models (`reconciliation/models.py`)

| Type | Purpose |
|------|---------|
| `CompanyStage` | Literal type: `pre_seed`, `seed`, `early`, `growth`, `late` |
| `CompanyProfile` | Frozen dataclass with name, stage, age, revenue, round data, sector, headcount, etc. |
| `MethodologyWeight` | Single methodology + weight + rationale + data_requirements_met flag |
| `MethodologyPlan` | Tuple of `MethodologyWeight`s -- output of selector |
| `DataPackage` | Typed struct of available data for methodology execution |
| `ConcludedValue` | Final point estimate + range + currency + date |
| `ReconciliationSummary` | Concluded value + weights + divergence flag + rationale |
| `ReconciledValuation` | Top-level output envelope with `to_dict()` for JSON serialisation |

### YAML Rules Config (`config/methodology_rules_v1.yaml`)

The rules config is **versioned** (`v1.0`) and contains three sections:

1. **`stage_exclusions`** -- hard excludes (e.g., pre_seed cannot use comps/ratchet)
2. **`data_rules`** -- conditional weight modifiers based on data quality (round age, peer-set quality, revenue presence)
3. **`base_weights`** -- per-stage starting weights before data-availability adjustments

Weights are stored in the config, not in code, making the system easy to tune without code changes.

### Stage Weights

| Stage | Scorecard | Berkus | Comps | Last-Round Mkt-Adj | Ratchet |
|-------|-----------|--------|-------|--------------------|---------|
| `pre_seed` | 50% | 50% | excluded | excluded | excluded |
| `seed` | 35% | 30% | -- | 35% | excluded |
| `early` | -- | -- | 50% | 50% | -- |
| `growth` | -- | -- | 60% | 40% | -- |
| `late` | -- | -- | 70% | 30% | -- |

### Divergence Detection

The reconciler flags divergence when any pair of methodology results differs by more than 40%:

```python
divergence_threshold = Decimal("0.40")
# For each pair (A, B):
#   max(|A - B|) / min(A, B) > 0.40 -> flag
```

This alerts reviewers when methodologies are producing materially different estimates, suggesting the valuation carries higher uncertainty.


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

SNOW (0.92): "Snowflake provides cloud data platform..."         Very similar
DDOG (0.85): "Datadog provides monitoring and analytics..."      Similar
ORCL (0.41): "Oracle provides database and cloud services..."    Too generic
ADBE (0.28): "Adobe provides creative and document software..."  Different domain
```

### Input Options for `target_description`

| Source | Quality | Effort |
|--------|---------|--------|
| Manual (analyst writes 1-2 sentences) | High -- domain expert knows the company | Low |
| Company website / pitch deck copy | Medium -- marketing language may be too broad | Low |
| LLM-generated from company name + sector | Medium -- good starting point | Very low |
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

### Reconciled Audit Trail (Phase 2)

The `POST /reconcile` endpoint returns a `ReconciledValuation` envelope that wraps multiple per-methodology audit trails:

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
    "methodology_weights": [ "..." ],
    "divergence_flag": false,
    "reconciliation_rationale": "...",
    "selector_version": "v1.0"
  },
  "methodology_results": {
    "comparable_companies": { "valuation_result": "...", "audit_metadata": "..." },
    "last_round_market_adjusted": { "valuation_result": "...", "audit_metadata": "..." }
  },
  "company_profile": { "name": "...", "stage": "growth", "..." },
  "audit_metadata": { "request_id": "...", "generated_at_utc": "..." }
}
```

Every per-methodology result retains its full audit trail (assumptions, derivation steps, citations), and the top-level reconciliation envelope adds the weighting rationale and divergence analysis.


---

## Research Agent (Epic 3)

### Overview

The `CompanyResearchAgent` is a LangGraph `StateGraph` that takes **only a company name** and automatically assembles the structured inputs needed for valuation. It does NOT call the valuation engine -- it only produces a `ValuationRequest`-shaped dict.

### Pipeline

```
company_name
    |
    v
+-----------------+
|  parse_company   |  Normalise name, infer sector from keywords
+--------+--------+
         v
+-----------------+
|    form_d        |  Search SEC EDGAR EFTS for Form D filings
+--------+--------+    -> funding rounds (date, issuer, filing URL)
         v
+-----------------+
|  web_research    |  4 DuckDuckGo queries x 6 results
|                  |  -> regex extraction (always)
|                  |  -> LLM extraction (if provider available)
+--------+--------+
         v
+-----------------+
|   contracts      |  USASpending.gov federal contract lookup
+--------+--------+    -> award amounts, agencies, descriptions
         v
+-----------------+
|    assemble      |  Auto-select methodology, validate completeness,
|                  |  build ValuationRequest dict
+-----------------+
```

### LLM Provider Chain (`_get_llm()`)

The web research node uses a **multi-provider fallback chain**:

```
OPENAI_API_KEY set?    -> ChatOpenAI(gpt-4o-mini)
    | no
ANTHROPIC_API_KEY set? -> ChatAnthropic(claude-3-5-haiku)
    | no
GOOGLE_API_KEY set?    -> ChatGoogleGenerativeAI(gemini-2.0-flash)
    | no
OLLAMA_MODEL set?      -> ChatOllama(local model)
    | no
Regex-only mode        -> still extracts valuations, revenue, dates
```

Each provider is wrapped in `try/except` -- if init fails, the next provider is tried. The system always works because regex extraction runs unconditionally before LLM extraction.

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

LLM results **override** regex results (they are more accurate), but regex provides a safety net when no LLM is configured.


---

## Testing Architecture

### Test Pyramid

```
392 total tests
+-- 381 unit tests (run offline, <6 seconds)
|   +-- test_engine.py         -- 138 tests (engine, methodologies, server, CLI, validation)
|   +-- test_yfinance.py       -- Epic 1 offline tests
|   +-- test_epic2.py          -- 43 tests (EDGAR, metrics, embeddings, composite)
|   +-- test_multiple_ratchet.py -- 39 tests (Multiple-Ratchet methodology)
|   +-- test_epic3.py          -- 77 tests (Form D, USASpending, agent pipeline, /research)
|   +-- test_epic5.py          -- 38 tests (cache management, confidence reports)
|   +-- test_scorecard.py      -- 11 tests (Phase 2: Scorecard methodology)
|   +-- test_berkus.py         --  9 tests (Phase 2: Berkus methodology)
|   +-- test_reconciliation.py -- 37 tests (Phase 2: profiler, selector, reconciler, engine, /reconcile)
|   +-- test_cli.py            -- CLI subcommand tests
|   +-- test_determinism.py    -- Determinism + reproducibility tests
|   +-- test_methodologies.py  -- Cross-methodology parametrized tests
|   +-- test_serialization.py  -- JSON envelope round-trip tests
|   +-- test_server.py         -- FastAPI endpoint tests
|   +-- test_store.py          -- SQLite store tests
|   +-- test_validation.py     -- Input validation tests
|   +-- test_web.py            -- Web UI endpoint tests
|
+-- 11 integration tests (marked @pytest.mark.integration, require network)
    +-- test_yfinance.py  -- live Yahoo Finance index tests
    +-- test_epic2.py     -- live EDGAR, yfinance, embedding tests
    +-- test_epic3.py     -- live Form D, USASpending, agent tests
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
| `research.py` | `vc_audit_tool.agent.research.DDGS` + env clearing | Must clear all API keys and mock `DDGS` |
| `reconciliation/` | `ValuationEngine` + `CompanyProfiler` mocked | Reconciliation logic tested independently of data sources |

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
| `fastapi` | >= 0.115 | `server.py` -- HTTP API + Web UI |
| `uvicorn` | >= 0.30 | `server.py` -- ASGI server |
| `yfinance` | >= 0.2.31 | `yfinance_market_index.py`, `yfinance_metrics.py` |
| `httpx` | >= 0.27 | `edgar_universe.py`, `form_d.py`, `usaspending.py` |
| `sentence-transformers` | >= 2.2 | `embedding_ranker.py` -- semantic ranking |
| `langgraph` | >= 1.0 | `research.py` -- agent state graph |
| `langchain-core` | >= 0.3 | `research.py` -- message types |
| `langchain-ollama` | >= 0.3 | `research.py` -- Ollama LLM provider |
| `duckduckgo-search` | >= 7.0 | `research.py` -- free web search |
| `pyyaml` | >= 6.0 | `reconciliation/selector.py` -- YAML rules config (Phase 2) |

### Optional LLM Dependencies (`pip install -e ".[llm]"`)

| Package | Version | Provider |
|---------|---------|----------|
| `langchain-openai` | >= 0.3 | OpenAI GPT-4o-mini |
| `langchain-anthropic` | >= 0.3 | Anthropic Claude 3.5 Haiku |
| `langchain-google-genai` | >= 2.0 | Google Gemini 2.0 Flash |

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

## Extension Points

The engine is designed for **plug-and-play** extension. The most common extension scenarios:

### Adding a New Methodology

1. Create `src/vc_audit_tool/methodologies/my_method.py`
2. Implement `ValuationMethodology` protocol (must define `name` property + `value()` coroutine)
3. Register in `engine.py`:

```python
engine.register_methodology(MyMethodology())
```

4. Add test file `tests/test_my_method.py`
5. Register in `ReconciliationEngine.METHODOLOGY_MAP` if it should participate in reconciliation

### Adding a New Data Source

1. Create `src/vc_audit_tool/data_sources/my_source.py`
2. Implement `DataSource` protocol (`name`, `fetch()` coroutine)
3. Register in the live data-source bundle

### Adding a New Sector

Add an entry to `SECTOR_MULTIPLES` in the comparable-companies methodology:

```python
SECTOR_MULTIPLES = {
    "fintech": {"ev_revenue": 15.0, "ev_ebitda": 40.0},
    "healthtech": {"ev_revenue": 10.0, "ev_ebitda": 30.0},
    # ... add new sector here
}
```

### Runtime Mode Controls

Runtime mode defaults to **live** data sources.

Use `VC_AUDIT_MOCK=1` to force mock data sources globally:

```bash
VC_AUDIT_MOCK=1 python -m vc_audit_tool.cli value --request-file examples/comps_request.json
```

The server also supports explicit mode selection:

```bash
python -m vc_audit_tool.server --mode mock
python -m vc_audit_tool.server --mode live
```

### Phase 2 Extension Points

#### Adding a New Lifecycle Stage

1. Add the stage to `CompanyStage` enum in `reconciliation/models.py`:

```python
class CompanyStage(str, Enum):
    PRE_SEED = "pre_seed"
    SEED = "seed"
    SERIES_A = "series_a"
    # ... add new stage
```

2. Add a new rule block in `config/methodology_rules_v1.yaml`:

```yaml
stages:
  your_new_stage:
    weights:
      scorecard: 0.35
      berkus: 0.25
      comparable_companies: 0.20
      last_round_market_adjusted: 0.20
    exclude: []
```

3. Update `CompanyProfiler._classify_stage()` in `reconciliation/profiler.py` to classify companies into the new stage.

#### Customising Methodology Weights

All methodology weights live in `config/methodology_rules_v1.yaml`. To adjust weights for an existing stage:

```yaml
stages:
  seed:
    weights:
      scorecard: 0.40        # increase from 0.35
      berkus: 0.30            # increase from 0.25
      comparable_companies: 0.10  # decrease from 0.15
      last_round_market_adjusted: 0.20  # decrease from 0.25
    exclude: []
```

The YAML file is **versioned** (`config_version: "1.0"`) so breaking changes can be detected at load time.

#### Adding Exclusion Rules

To prevent a methodology from running for a given stage:

```yaml
stages:
  pre_seed:
    weights:
      scorecard: 0.50
      berkus: 0.50
    exclude:
      - comparable_companies       # no public comps for pre-seed
      - last_round_market_adjusted # no prior round exists
```

Excluded methodologies are removed from the plan by `MethodologySelector` and their weight is **redistributed proportionally** across remaining methods.

---

*Document auto-generated from codebase analysis. Last updated after Phase 2 (multi-methodology reconciliation) implementation.*
