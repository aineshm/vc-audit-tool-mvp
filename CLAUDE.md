# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VC Audit Tool is a Python engine that produces auditable, deterministic valuation output for venture-backed private companies. Every result includes a full derivation trail with assumptions, citations, step-by-step math, and confidence indicators.

**Python:** >=3.10
**Main frameworks:** FastAPI, LangGraph, LangChain, sentence-transformers, yfinance

## Common Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"
pip install -e ".[llm]"        # With LLM provider packages

# Run tests
python3 -m pytest tests/ -q                    # Unit tests only
python3 -m pytest tests/ -q -m 'integration or not integration'  # Include integration tests
python3 -m pytest tests/test_epic2.py -v      # Specific test file
python3 -m pytest tests/ --cov=vc_audit_tool --cov-report=term-missing  # With coverage

# Quality gates (all must pass before committing)
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python3 -m pytest tests/ -q

# Run CLI
python3 -m vc_audit_tool.cli value --request-file examples/comps_request.json --pretty
python3 -m vc_audit_tool.cli research "Stripe" --pretty    # Research subcommand
python3 -m vc_audit_tool.cli cache list
python3 -m vc_audit_tool.cli confidence <request-id>

# Run FastAPI server
python3 -m vc_audit_tool.server                      # Default live mode
python3 -m vc_audit_tool.server --mode mock          # Force mock mode

# Frontend (requires Node 24)
cd frontend
source ~/.nvm/nvm.sh && nvm use 24 --silent
npm run dev                  # dev server on :3000
npm run build && npm start   # production
```

## Environment Variables

```bash
# Required for live data
export VC_AUDIT_SEC_USER_AGENT="Your Name your-email@company.com"

# Optional LLM providers (priority: Gemini > OpenAI > Claude > Ollama > Regex)
export GOOGLE_API_KEY="..."
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export OLLAMA_MODEL="llama3.2"

# Optional Pinecone (enables hosted-inference comps ranking over local embeddings)
export PINECONE_API_KEY="..."
export PINECONE_INDEX_NAME="vc-audit-edgar-comps"       # optional, default
export PINECONE_EMBEDDING_MODEL="multilingual-e5-large"  # optional, default

# Optional Supabase (enables PostgreSQL store instead of SQLite WAL)
export SUPABASE_URL="https://drykfbevdfyivyhnkyfc.supabase.co"
export SUPABASE_KEY="..."                                # anon or service-role key

# Mock mode
export VC_AUDIT_MOCK=1
```

## Architecture

The codebase follows a **protocol-based architecture** for data sources, allowing mock/live swapping via structural subtyping.

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `src/vc_audit_tool/` | Core package - CLI, server, engine, models, validation |
| `src/vc_audit_tool/data_sources/` | Pluggable data source implementations (mock, yfinance, EDGAR, embeddings) |
| `src/vc_audit_tool/methodologies/` | Valuation methodology implementations (comps, last_round, ratchet, scorecard, berkus) |
| `src/vc_audit_tool/methodologies/_discount_config.py` | Configurable illiquidity discount defaults — `get_discount_default()`, `clamp_discount()` |
| `src/vc_audit_tool/reconciliation/` | Phase 2 multi-methodology reconciliation (CompanyProfiler, MethodologySelector, Reconciler) |
| `src/vc_audit_tool/agent/` | LangGraph-based company research agent |
| `config/` | YAML configuration for methodology rules and discount defaults |
| `src/vc_audit_tool/logging_config.py` | JSON logging + `contextvars` correlation IDs |
| `src/vc_audit_tool/data_sources/pinecone_ranker.py` | Pinecone-backed comps ranker |
| `src/vc_audit_tool/data_sources/ranker_factory.py` | `get_ranker()` — Pinecone if key set, else local |
| `src/vc_audit_tool/data_sources/evidence_patterns.py` | `SOURCE_RELIABILITY_TIERS` (35-entry domain map), 3-factor confidence scoring |
| `src/vc_audit_tool/store.py` | SQLite WAL-backed valuation run storage |
| `src/vc_audit_tool/store_supabase.py` | Supabase (PostgreSQL) valuation run storage — Phase 4 |
| `src/vc_audit_tool/store_factory.py` | `get_store()` — Supabase if SUPABASE_URL+SUPABASE_KEY set, else SQLite |
| `frontend/` | Next.js 16 + React 19 + Tailwind v4 frontend |
| `tests/` | Test suite (~557 tests) |
| `examples/` | Sample JSON request files |

### Core Patterns

1. **Protocol-based data sources**: Uses Python `typing.Protocol` for structural subtyping - allows mock/live swapping
2. **Deterministic outputs**: Uses `decimal.Decimal` for monetary calculations, dataset versioning, daily caching
3. **Audit trail**: Every result includes assumptions, derivation steps, confidence indicators, citations
4. **Multi-provider LLM fallback**: Gemini > OpenAI > Claude > Ollama > Regex fallback
5. **Source reliability tiers**: 3-factor confidence = `base_type × recency_multiplier × source_tier_multiplier`; 35-entry domain mapping in `evidence_patterns.py`
6. **Configurable discounts**: Per-methodology illiquidity discounts loaded from `config/methodology_rules_v1.yaml`; applied via `_discount_config.py`; always disclosed in `derivation_steps`

### Configuration Patterns

- `config/llm_providers.yaml` - LLM provider chain with cost tracking (evaluated in order, first available wins)
- Hardcoded defaults in `llm_config.py` - Fallback if YAML is missing/malformed
- `config/methodology_rules_v1.yaml` - Versioned rules for methodology weighting by company stage

### LangGraph Patterns

- State: Use `TypedDict, total=False` for LangGraph state (see `agent/state.py`)
- ResearchState tracks: input → intermediate → raw data → structured evidence → output
- EvidencePackage replaces unstructured web_facts for audit trail

### Decimal Patterns

- Quantize with `Decimal("0.0001")` for weight calculations
- Normalize weights to sum to exactly 1.0 (fix rounding by adjusting first entry)
- Use `Decimal(str(v))` when loading floats from YAML/JSON to avoid precision loss

### Testing Patterns

- `conftest.py` swaps server engine to mock mode at import time (prevents live API calls)
- Use `isolated_store` fixture (autouse) to prevent test-to-test SQLite leakage
- Evidence tests use `_make_evidence()` and `_make_package()` factory helpers
- Mock engine: `ValuationEngine.mock()` for deterministic test data
- Web research tests mock DDGS (DuckDuckGo) — no live external search calls in unit tests

### Supported Methodologies

- `comparable_companies` - EV/Revenue multiple from peer set
- `last_round_market_adjusted` - Scales last valuation by public index movement
- `last_round_multiple_ratchet` - Re-rates by sector multiple compression/expansion
- `scorecard` - Payne Scorecard method (Phase 2)
- `berkus` - Berkus Method (Phase 2)
- `direct_valuation` - Evidence-signal based valuation with illiquidity discounts

### Key API Endpoints

- `POST /value` — Run a valuation with structured inputs
- `POST /api/value` — Alias for `/value` (used by frontend)
- `POST /research` — Automated research + valuation from company name
- `POST /reconcile` — Multi-methodology reconciled valuation
- `GET /health` — Health check (returns `version`, `store` (sqlite_wal/supabase), `llm_provider`, `pinecone_index`, `request_id`)
- `GET /api/runs` — List recent valuation runs (summary)
- `GET /api/runs/{run_id}` — Full payload for a single run

### Frontend Routes (Next.js)

- `GET /` — Dashboard (recent runs)
- `GET /research` — Research-first valuation form (POST goes to backend via rewrite)
- `GET /value` — Manual valuation form (POSTs to `/api/value`)
- `GET /reconcile` — Reconcile form (POST goes to backend via rewrite)
- `GET /runs` — Run history table
- `GET /runs/[id]` — Run detail with audit trail, evidence package, source reliability badges

## Development Notes

- **Store abstraction** - `store_factory.get_store()` returns either SQLite or Supabase based on env vars; both implement `ValuationStoreProtocol`
- **Supabase table** - `valuation_runs` (request_id PK, company_name, methodology, as_of_date, fair_value, generated_at_utc, payload JSONB); RLS disabled
- **SQLite WAL mode enabled** - Required for concurrent reads/writes during async operations; `wal_checkpoint` on startup
- **LLM configuration in YAML** - Provider settings, model routing, and cost limits defined in `config/llm_providers.yaml`
- **Test markers** - `integration` for external API tests, `agent` for LangGraph agent tests, `epic` for milestone tests
- **Async patterns** - Use `asyncio.to_thread()` for sync I/O (yfinance), native async for HTTP clients
- **Plan files** - `.claude/plan/stack-rethink.md` and `stack-rethink-v2.md` contain full phase roadmap and status
- **Request correlation** - `logging_config._request_id_var` (contextvars) propagates `X-Request-ID` through each request
- **Pinecone auto-creation** - `pinecone_ranker._ensure_index()` creates the index on first use if not present
