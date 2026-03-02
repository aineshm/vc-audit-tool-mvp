# VC Audit Tool — Codemap Index

**Last Updated:** 2026-03-01

This directory contains architectural codemaps for the VC Audit Tool, a Python engine that produces auditable, deterministic valuations for venture-backed private companies.

## Overview

VC Audit Tool is a multi-phase project with:
- **Backend**: Python FastAPI engine with pluggable data sources
- **Frontend**: Next.js 16 + React 19 + Tailwind v4 web UI
- **Storage**: SQLite WAL (default) or Supabase PostgreSQL (Phase 4)
- **LLM**: Multi-provider fallback chain (Gemini > OpenAI > Claude > Ollama > Regex)
- **Data Sources**: SEC EDGAR, Yahoo Finance, DuckDuckGo, USASpending.gov, Pinecone vectors
- **Test Suite**: 557+ unit tests, integration tests, and E2E tests

## Codemaps

| Codemap | Purpose | Key Modules |
|---------|---------|-----------|
| **[backend.md](./backend.md)** | Core valuation engine, methodologies, data sources | `engine.py`, `methodologies/`, `data_sources/`, `store_factory.py` |
| **[frontend.md](./frontend.md)** | Next.js UI, pages, components, API integration | `frontend/src/`, `next.config.ts`, `globals.css` |
| **[data-sources.md](./data-sources.md)** | Protocol-based data fetching and caching | `yfinance_metrics.py`, `edgar_universe.py`, `form_d.py`, `embedding_ranker.py`, `pinecone_ranker.py` |
| **[agent.md](./agent.md)** | LangGraph research agent, web search, evidence extraction | `agent/state.py`, `agent/nodes/`, `evidence_patterns.py` |
| **[storage.md](./storage.md)** | Valuation run persistence (SQLite + Supabase) | `store.py`, `store_supabase.py`, `store_factory.py` |
| **[reconciliation.md](./reconciliation.md)** | Multi-methodology selection and weighting | `reconciliation/profiler.py`, `selector.py`, `reconciler.py` |

## Key Architecture Patterns

### 1. Protocol-Based Data Sources

All data sources implement a Python `typing.Protocol`, enabling:
- **Structural subtyping** — mock/live swapping without inheritance
- **Interface consistency** — same methods across data providers
- **Easy testing** — inject mock implementations

Example:
```python
class ComparableCompanySource(Protocol):
    def fetch_peers(self, sector: str, target_description: str) -> list[Comparable]: ...
```

### 2. Deterministic Output

Every valuation result includes:
- **Assumptions** — input parameters and derived assumptions
- **Derivation steps** — step-by-step calculations (readable walkthrough)
- **Confidence indicators** — source reliability, peer count, data freshness
- **Citations** — dataset versions, snapshot dates, data source attribution

Uses `decimal.Decimal` for all monetary calculations (no floating-point arithmetic).

### 3. Multi-Provider Fallback Chain

LLM selection priority (first available wins):
1. **Google Gemini 2.5 Flash** — `GOOGLE_API_KEY` (~$0.001/request)
2. **OpenAI GPT-4o-mini** — `OPENAI_API_KEY` (~$0.002/request)
3. **Anthropic Claude 3.5 Haiku** — `ANTHROPIC_API_KEY` (~$0.003/request)
4. **Ollama (local)** — `OLLAMA_MODEL` ($0 cost)
5. **Regex extraction** — fallback (no API key needed)

Configured in `config/llm_providers.yaml` (with hardcoded defaults if YAML is missing).

### 4. Configurable Illiquidity Discounts

Per-methodology private-company discounts defined in `config/methodology_rules_v1.yaml`:
- `comparable_companies`: 25% (public comps anchor → higher discount)
- `last_round_market_adjusted`: 10% (real transaction anchor → lower)
- `last_round_multiple_ratchet`: 25% (derived from public comps)
- `direct_valuation`: 10–20% (depends on secondary market evidence)

Applied and disclosed explicitly in `derivation_steps` — auditor can see the discount multiplier applied at each step.

### 5. Source Reliability Scoring

**Score = base_type_confidence × recency_multiplier × source_tier_multiplier**

3-factor scoring with 35-entry domain mapping:
- **Tier 1**: `bloomberg.com`, `reuters.com`, `wsj.com` (0.95 multiplier)
- **Tier 2**: `techcrunch.com`, `forbes.com`, `cnbc.com` (0.90 multiplier)
- **Tier 3**: General news (0.80 multiplier)
- **Tier 4**: Aggregators like Crunchbase (0.70 multiplier)
- **Tier 5**: Blogs, social media (0.50–0.60 multiplier)

See `src/vc_audit_tool/data_sources/evidence_patterns.py` → `SOURCE_RELIABILITY_TIERS`.

### 6. Store Abstraction (Phase 4)

`store_factory.get_store()` returns:
- **SupabaseValuationStore** — if `SUPABASE_URL` + `SUPABASE_KEY` set
- **ValuationStore** — default SQLite WAL fallback

Both implement `ValuationStoreProtocol` for transparent swapping.

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **API** | FastAPI 0.115+ | REST endpoints, OpenAPI docs |
| **Web UI** | Next.js 16, React 19, Tailwind v4 | Dashboard, forms, run history |
| **Backend** | Python 3.10+ | Valuation engine, CLI |
| **Graphs** | LangGraph 1.0+ | Research agent, state management |
| **Storage** | SQLite WAL (default) | Default valuation run persistence |
| **Storage (opt)** | Supabase 2.10+ (Phase 4) | PostgreSQL persistence, direct frontend reads |
| **Vectors** | sentence-transformers (local) | Embedding ranker (default) |
| **Vectors (opt)** | Pinecone 2.1+ | Hosted vector inference (Phase 4) |
| **Data** | yfinance | Yahoo Finance equity multiples, index levels |
| **Data** | EDGAR via requests | SEC company filings, Form D |
| **Data** | DuckDuckGo Search API | Web research, company intelligence |
| **Data** | USASpending.gov | Federal contract revenue signals |
| **Testing** | pytest 8.0+ | Unit, integration, E2E tests |
| **Quality** | ruff, mypy | Linting, type checking, formatting |

## Supported Methodologies

| Methodology | Use Case | Phase | Status |
|-------------|----------|-------|--------|
| **Comparable Companies** | Market-based valuation via peer EV/Revenue multiples | MVP | Complete |
| **Last-Round Market-Adjusted** | Index-adjusted historical valuation | Epic 1 | Complete |
| **Last-Round Multiple-Ratchet** | Sector multiple re-rating from prior round | MVP | Complete |
| **Scorecard** | Payne Scorecard (7-factor qualitative) | Phase 2 | Complete |
| **Berkus** | Berkus Method (5-factor risk scoring) | Phase 2 | Complete |
| **Direct Valuation** | Evidence-based with confidence weighting | Epic 3 | Complete |

## Environment Variables (Activation)

### Recommended (Live Data)
```bash
# LLM (one or more for fallback chain)
export GOOGLE_API_KEY="..."          # Gemini Flash (priority 1, recommended)
export OPENAI_API_KEY="..."          # GPT-4o-mini (priority 2)
export ANTHROPIC_API_KEY="..."       # Claude 3.5 Haiku (priority 3)

# SEC access
export VC_AUDIT_SEC_USER_AGENT="Your Name your-email@company.com"
```

### Optional (Cloud Services)
```bash
# Supabase (Phase 4) — PostgreSQL store instead of SQLite
export SUPABASE_URL="https://drykfbevdfyivyhnkyfc.supabase.co"
export SUPABASE_KEY="..."            # anon or service-role key

# Pinecone (Phase 4) — hosted vector embeddings for comps ranking
export PINECONE_API_KEY="..."
export PINECONE_INDEX_NAME="vc-audit-edgar-comps"  # default
# PINECONE_EMBEDDING_MODEL="multilingual-e5-large"  # default
```

### Testing/Local Development
```bash
# Force mock data sources (no API calls needed)
export VC_AUDIT_MOCK=1

# Local Ollama LLM (no API key)
export OLLAMA_MODEL="llama3.2"
```

## Phase Roadmap

| Phase | Focus | Completion |
|-------|-------|-----------|
| **MVP** | Comps valuation, CLI, FastAPI, SQLite | ✅ 2025-12 |
| **Epic 1** | YFinance index levels, market-adjusted method | ✅ 2025-12 |
| **Epic 2** | Live EDGAR + YFinance comps | ✅ 2026-01 |
| **Epic 3** | Research agent, Form D, USASpending, DDGS | ✅ 2026-01 |
| **Epic 4** | `/research` endpoint (one-call valuation) | ✅ 2026-01 |
| **Epic 5** | Observability: cache CLI + confidence report | ✅ 2026-02 |
| **Phase 2** | Multi-method reconciliation, Scorecard, Berkus, profiler | ✅ 2026-02 |
| **Phase 4** | Supabase store + frontend direct reads | ✅ 2026-03-01 |
| **Pinecone** | Hosted vector ranker | ✅ 2026-03-01 |
| **Source Reliability** | 3-factor confidence scoring, 35-entry domain tier map | ✅ 2026-03-01 |
| **Discount Transparency** | YAML-driven configurable discounts | ✅ 2026-03-01 |
| **Frontend** | Next.js UI with all 6 routes + dark mode | ✅ 2026-03-01 |

## Quick Links

- **[CLAUDE.md](../../CLAUDE.md)** — Developer guide with commands, env vars, patterns
- **[README.md](../../README.md)** — User guide with quick start, examples, methodologies
- **[ARCHITECTURE.md](../../ARCHITECTURE.md)** — Detailed system design (if exists)
- **[.claude/plan/stack-rethink.md](../../.claude/plan/)** — Phase roadmap and status
- **[config/llm_providers.yaml](../../config/llm_providers.yaml)** — LLM provider configuration
- **[config/methodology_rules_v1.yaml](../../config/methodology_rules_v1.yaml)** — Stage-based methodology weights and discounts
- **[pyproject.toml](../../pyproject.toml)** — Dependencies, optional extras (`[dev]`, `[llm]`, `[supabase]`)

## Test Coverage

- **557+ unit tests** (default, no network)
- **Integration tests** (optional, hits SEC EDGAR + Yahoo Finance)
- **Agent tests** (LangGraph research agent)
- **Epic tests** (milestone validation)

Run tests:
```bash
python3 -m pytest tests/ -q                    # Unit tests only
python3 -m pytest tests/ -q -m 'integration or not integration'  # Include integration
python3 -m pytest tests/ --cov=vc_audit_tool --cov-report=term-missing  # With coverage
```

Quality gates (all must pass before commit):
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python3 -m pytest tests/ -q
```
