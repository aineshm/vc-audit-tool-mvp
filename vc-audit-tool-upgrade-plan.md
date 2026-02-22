# VC Audit Tool — Production Upgrade Plan

## Overview

This document describes the full set of features to add to the existing `vc-audit-tool-mvp` to evolve it from a mock-data demo into a production-grade agentic valuation workflow that can value real private companies using publicly available data.

The existing codebase already has:
- A working valuation engine (`engine.py`)
- Two methodology implementations: Last Round Market-Adjusted, Comparable Companies
- Protocol-based data source abstraction (`interfaces.py`)
- Structured audit output with citations and confidence indicators
- FastAPI HTTP API + CLI
- Full test suite with determinism guarantees

**We are NOT rewriting anything. We are adding new concrete implementations of existing interfaces and a new agentic orchestration layer on top.**

---

## Guiding Principles

1. **The engine stays untouched.** New real-data providers plug into existing Protocol interfaces. If a new data source requires changing `engine.py` or `methodologies/`, something is wrong.
2. **Every data point must be cited.** Source URL, retrieval timestamp, and raw value observed — logged in the existing `Citation` model.
3. **Fail loudly, never silently.** If data is unavailable or ambiguous, surface a warning in `confidence_indicators`. Never guess.
4. **Determinism is preserved for the valuation result.** Any AI-generated content (summaries, narrative) lives in a separate top-level key, never inside `valuation_result`.
5. **Dataset versions are pinned.** Every external data pull is stamped with a version so outputs are reproducible.

---

## Epic 1: Real Market Data Provider (replaces `MockMarketIndexSource`)

### Context
The existing `MockMarketIndexSource` returns hardcoded NASDAQ values. We need a real implementation that fetches historical index data via `yfinance`.

---

### Story 1.1 — `YFinanceMarketIndexSource`

**As a** valuation engineer,
**I want** the Last Round Market-Adjusted methodology to pull real NASDAQ index values from yfinance,
**So that** the index drift applied to a funding round valuation reflects actual market movement.

**Implementation notes:**
- Create `data_sources/yfinance_market_index.py`
- Implement `MarketIndexSource` Protocol
- Use `yfinance` to fetch daily closing prices for `^IXIC` (NASDAQ Composite)
- Cache results locally (JSON file keyed by ticker + date range) to avoid redundant API calls
- `dataset_version` should be `"yfinance-nasdaq-{date_of_retrieval}"`

**Acceptance criteria:**
- [x] `YFinanceMarketIndexSource` passes all existing `MarketIndexSource` Protocol type checks
- [x] Given a date range, returns closing prices for each trading day in that range
- [x] If a requested date falls on a weekend/holiday, returns the nearest prior trading day's value and notes the fallback in the citation
- [x] Cache file is written on first fetch; subsequent calls within the same session read from cache
- [x] `resolved_data_points` in the citation logs the exact ticker symbol, date, and closing value used (e.g. `"^IXIC@2024-03-15=16832.92"`)
- [x] Unit test: fetch NASDAQ values for a known historical date range and assert values are within expected range
- [x] `test_determinism.py` continues to pass (same input → same output when cache is warm)

---

### Story 1.2 — Index Data Staleness Warning

**As an** auditor reviewing output,
**I want** a warning when the market index data used is older than 30 days,
**So that** I know when the valuation may not reflect current market conditions.

**Acceptance criteria:**
- [x] `confidence_indicators.index_data_freshness_gap_days` is populated with the number of days between the most recent index value used and today's date
- [x] If gap > 30 days, `staleness_risk` is set to `"HIGH"`
- [x] If gap is 7–30 days, `staleness_risk` is `"MEDIUM"`
- [x] If gap < 7 days, `staleness_risk` is `"LOW"`

---

## Epic 2: Real Comparable Companies Provider (replaces `MockComparableCompanySource`)

### Context
The existing mock returns hardcoded tickers. We need a real implementation that: (1) queries SEC EDGAR for companies by sector, (2) uses embedding similarity to rank relevance to the target company's description, (3) fetches financial metrics via yfinance.

---

### Story 2.1 — EDGAR Company Universe Builder

**As a** valuation engineer,
**I want** to pull a list of public companies in a given sector from SEC EDGAR,
**So that** comparable company selection is based on a real, comprehensive universe rather than hardcoded tickers.

**Implementation notes:**
- Use the SEC EDGAR full-text search API: `https://efts.sec.gov/LATEST/search-index?q=%22{sector}%22&dateRange=custom&startdt=2023-01-01&forms=10-K`
- Parse company name, CIK, SIC code, and business description from 10-K filings
- Store results as a local JSON cache keyed by SIC code (refresh monthly)
- Target SIC codes to support: 7372 (software), 7374 (data processing), 6282 (investment advice), and others as needed

**Acceptance criteria:**
- [x] `EdgarCompanyUniverse` class fetches and caches a list of companies for a given SIC code
- [x] Each company record includes: ticker, CIK, company name, SIC code, business description (extracted from Item 1 of 10-K)
- [x] Cache is written to `data/edgar_cache/{sic_code}.json` with a `retrieved_at` timestamp
- [x] If EDGAR is unreachable, raises `DataSourceError` with a clear message — does not return stale data silently
- [x] Unit test: fetch companies for SIC 7372 and assert at least 20 results with non-empty business descriptions

---

### Story 2.2 — Embedding-Based Comps Ranker

**As a** valuation engineer,
**I want** to rank comparable companies by semantic similarity to the target company's business description,
**So that** selected comps reflect actual business model similarity, not just sector classification.

**Implementation notes:**
- Use `sentence-transformers` with model `all-MiniLM-L6-v2` (lightweight, no API key required)
- Embed the target company description and all candidate company descriptions
- Rank by cosine similarity
- Top-k is configurable (default: 5)
- Pin the embedding model version in `dataset_version`

**Acceptance criteria:**
- [x] `EmbeddingCompsRanker` takes a target description and list of candidate companies, returns ranked list with similarity scores
- [x] `dataset_version` includes the embedding model name and version (e.g. `"all-MiniLM-L6-v2-v1.0"`)
- [x] Each returned comp includes its similarity score, which maps to `confidence_indicators.peer_set_quality`:
  - Mean similarity > 0.75 → `"HIGH"`
  - Mean similarity 0.5–0.75 → `"MEDIUM"`
  - Mean similarity < 0.5 → `"LOW"`
- [x] Unit test: rank a set of known companies against "enterprise AI software for business analytics" and assert that clearly relevant companies rank above clearly irrelevant ones
- [x] Embeddings are cached locally to avoid recomputing on repeated runs

---

### Story 2.3 — yfinance Financial Metrics Fetcher

**As a** valuation engineer,
**I want** to fetch real EV/Revenue multiples for comparable public companies via yfinance,
**So that** the Comparable Companies methodology uses actual market-derived multiples.

**Implementation notes:**
- Fetch: market cap, enterprise value, trailing twelve months revenue, EV/Revenue multiple
- Use `yfinance` Ticker object
- Handle missing data gracefully (some small-cap companies have incomplete financials)

**Acceptance criteria:**
- [x] `YFinanceMetricsFetcher` returns EV, Revenue, and EV/Revenue for a given ticker
- [x] If EV or Revenue is unavailable for a ticker, that company is excluded from the comp set and a warning is added to `confidence_indicators`
- [x] `resolved_data_points` logs the exact values fetched: ticker, metric name, value, and retrieval date
- [x] Unit test: fetch metrics for `MSFT` and `GOOGL` and assert EV/Revenue > 0
- [x] Determinism: metrics are cached by ticker + date so repeated runs on the same day return identical values

---

### Story 2.4 — `EdgarYFinanceComparableCompanySource`

**As a** valuation engineer,
**I want** a single `ComparableCompanySource` implementation that wires together EDGAR, embedding ranking, and yfinance,
**So that** the Comparable Companies methodology can use it as a drop-in replacement for the mock source.

**Acceptance criteria:**
- [x] `EdgarYFinanceComparableCompanySource` implements the `ComparableCompanySource` Protocol
- [x] `list_by_description(description, sector, k)` returns top-k comps ranked by embedding similarity with real financial metrics attached
- [x] All existing Comparable Companies methodology tests pass with the new source substituted in
- [x] Integration test: value a real public company against itself as a comp and assert the methodology produces a reasonable multiple

---

## Epic 3: Private Company Data Agent

### Context
Private companies don't have 10-K filings. We need an agentic layer that assembles structured company profiles from public sources: SEC Form D filings, press coverage, government contract databases, and disclosed financials.

---

### Story 3.1 — SEC Form D Ingestion

**As a** valuation engineer,
**I want** to pull funding round data for private companies from SEC Form D filings,
**So that** the Last Round Market-Adjusted methodology has real, citable last-round data.

**Implementation notes:**
- SEC EDGAR Form D full-text search: `https://efts.sec.gov/LATEST/search-index?q=%22{company_name}%22&forms=D`
- Form D XML contains: offering amount, date of first sale, issuer state, number of investors
- Parse into a `FundingRound` model: `{date, amount_raised, post_money_valuation (if disclosed), source_url}`
- Note: Form D discloses amount raised but not post-money valuation — that must come from press or other sources

**Acceptance criteria:**
- [x] `FormDSource` fetches Form D filings for a given company name from EDGAR
- [x] Returns list of `FundingRound` objects sorted by date descending
- [x] Each result includes the EDGAR filing URL as the citation source
- [x] If no Form D is found, returns empty list (not an error — many companies use exemptions)
- [x] Unit test: search for a known company (e.g. "Anthropic PBC") and assert at least one result

---

### Story 3.2 — Company Research Agent (LangGraph)

**As a** user of the valuation tool,
**I want** to provide only a company name and have the system automatically assemble the structured inputs needed for valuation,
**So that** I don't need to manually research funding rounds, revenue, and comparable companies.

**Implementation notes:**
- Build a LangGraph workflow with the following nodes:

  1. **`parse_company_node`** — Normalizes company name, identifies likely sector and SIC code
  2. **`form_d_node`** — Fetches funding rounds from SEC Form D
  3. **`web_research_node`** — Uses a search tool to find: most recent disclosed revenue, major contracts, post-money valuation from press coverage. Uses an LLM to extract structured facts from search results. Every extracted fact includes the source URL.
  4. **`validate_inputs_node`** — Checks completeness. If last round date or amount is missing, flags `DataSourceError` with specific missing fields. Does not proceed with incomplete required inputs.
  5. **`assemble_request_node`** — Constructs a `ValuationRequest` from assembled data, ready to pass to the existing engine

- The agent does NOT call the valuation engine — it only assembles inputs. The engine call happens outside the agent, using the existing API.

**Acceptance criteria:**
- [x] Given `"Anthropic"` as input, the agent returns a structured `ValuationRequest` with: last round date, amount raised, post-money valuation (or best available estimate with uncertainty flag), sector
- [x] Every fact in the assembled request has a citation with source URL and retrieval timestamp
- [x] If a required field cannot be found, the agent returns a structured error listing which fields are missing — it does not hallucinate values
- [x] `web_research_node` uses temperature=0 and pins the model version; both are logged in the output
- [x] LangGraph state is fully inspectable — each node's input and output can be logged for debugging
- [x] Any LLM-extracted content is placed in a separate `research_metadata` key in the final output, never inside `valuation_result`
- [x] Integration test: run the agent for a well-documented company (e.g. Stripe, Anthropic) and assert the assembled request passes `ValuationRequest` schema validation

---

### Story 3.3 — USASpending.gov Contract Lookup

**As a** valuation engineer,
**I want** to include federal contract commitments in the company profile,
**So that** companies with significant government revenue (e.g. AI companies with DoD/intelligence contracts) are valued with that context included.

**Implementation notes:**
- USASpending.gov has a public API: `https://api.usaspending.gov/api/v2/search/spending_by_award/`
- Query by recipient name, return total award amount and contract descriptions
- This is supplementary data — its absence is not a blocking error

**Acceptance criteria:**
- [x] `USASpendingSource` queries the public API by company name and returns total federal contract value
- [x] Result is included in the assembled company profile as `government_contracts_usd` with citation
- [x] If no contracts found, field is `null` with a note — not an error
- [x] Unit test: query a known federal contractor and assert a non-zero result

---

## Epic 4: New HTTP Endpoint — `/research`

### Context
Currently `/value` requires fully structured inputs. We need a new endpoint that accepts just a company name, runs the research agent, and returns both the assembled inputs and the valuation.

---

### Story 4.1 — `POST /research` Endpoint

**As a** user of the API,
**I want** to POST just a company name and get back a full valuation with automatically assembled inputs,
**So that** I can value a company without manually researching it first.

**Request schema:**
```json
{
  "company_name": "Anthropic",
  "methodology": "last_round_market_adjusted" | "comparable_companies",
  "as_of_date": "2025-01-15"  // optional, defaults to today
}
```

**Response schema:** extends existing valuation response with an additional top-level key:
```json
{
  "valuation_result": { ... },       // existing schema, unchanged
  "audit_metadata": { ... },         // existing schema, unchanged
  "research_metadata": {             // NEW — AI-generated content lives here only
    "sources_consulted": [...],
    "extracted_facts": [...],
    "llm_model_version": "...",
    "research_timestamp": "..."
  }
}
```

**Acceptance criteria:**
- [x] `POST /research` accepts the request schema and returns the extended response schema
- [x] `valuation_result` is byte-identical on repeated calls with the same inputs and warm cache (determinism preserved)
- [x] `research_metadata` is clearly separated from `valuation_result`
- [x] If the research agent cannot assemble required inputs, returns a structured 422 with specific missing fields — not a 500
- [x] OpenAPI docs at `/docs` are auto-generated and accurate for the new endpoint
- [x] Integration test: call `/research` for a known company and assert the response passes schema validation

---

## Epic 5: Observability and Data Management

---

### Story 5.1 — Research Cache Management

**As a** developer,
**I want** a CLI command to inspect and clear the local data cache,
**So that** I can force a fresh data fetch when caches are stale.

**Acceptance criteria:**
- [x] `python -m vc_audit cache list` shows all cached datasets with their retrieval timestamps and size
- [x] `python -m vc_audit cache clear --older-than 30d` removes cache files older than the specified age
- [x] `python -m vc_audit cache clear --all` clears everything

---

### Story 5.2 — Confidence Report CLI

**As an** auditor,
**I want** a summary of all confidence warnings in a valuation output,
**So that** I can quickly assess how much manual review is needed.

**Acceptance criteria:**
- [x] `python -m vc_audit confidence <request_id>` prints a human-readable summary of all `confidence_indicators` for a stored valuation run
- [x] Warnings are color-coded: HIGH risk in red, MEDIUM in yellow, LOW in green (when outputting to a terminal that supports color)
- [x] Output includes the specific data points that triggered each warning

---

## Non-Goals (Explicit Scope Exclusions)

The following are **not** in scope for this upgrade:

- Real-time data streaming or websocket updates
- DCF (Discounted Cash Flow) methodology — this requires private financial projections not publicly available
- Multi-tenancy or authentication
- Paid data provider integrations (Bloomberg, PitchBook, Crunchbase Pro)
- Autonomous valuation without human review — the system surfaces data and computes; an analyst reviews and signs off

---

## Suggested Build Order

1. **Epic 2 first** (EDGAR + yfinance comps) — self-contained, no new dependencies on agent infrastructure, immediately demonstrates real data flowing through the existing engine
2. **Epic 1** (yfinance market index) — straightforward replacement of one mock, high confidence
3. **Epic 3, Story 3.1** (Form D ingestion) — no LLM required, pure API integration
4. **Epic 3, Story 3.2** (Research agent) — most complex, build last so the simpler data sources are already tested
5. **Epic 4** (new endpoint) — wire everything together
6. **Epic 5** (observability) — add last, low risk

---

## Key Dependencies

```
yfinance
sentence-transformers
langchain-core
langgraph
httpx         # for EDGAR and USASpending API calls
```

All existing dependencies remain unchanged. The new dependencies are additive only.
