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
| `src/vc_audit_tool/reconciliation/` | Phase 2 multi-methodology reconciliation (CompanyProfiler, MethodologySelector, Reconciler) |
| `src/vc_audit_tool/agent/` | LangGraph-based company research agent |
| `config/` | YAML configuration for methodology rules |
| `src/vc_audit_tool/logging_config.py` | JSON logging + `contextvars` correlation IDs |
| `src/vc_audit_tool/data_sources/pinecone_ranker.py` | Pinecone-backed comps ranker |
| `src/vc_audit_tool/data_sources/ranker_factory.py` | `get_ranker()` — Pinecone if key set, else local |
| `tests/` | Test suite (~508 tests) |
| `examples/` | Sample JSON request files |

### Core Patterns

1. **Protocol-based data sources**: Uses Python `typing.Protocol` for structural subtyping - allows mock/live swapping
2. **Deterministic outputs**: Uses `decimal.Decimal` for monetary calculations, dataset versioning, daily caching
3. **Audit trail**: Every result includes assumptions, derivation steps, confidence indicators, citations
4. **Multi-provider LLM fallback**: Gemini > OpenAI > Claude > Ollama > Regex fallback

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

- `POST /value` - Run a valuation with structured inputs
- `POST /research` - Automated research + valuation from company name
- `POST /reconcile` - Multi-methodology reconciled valuation
- `GET /health` - Health check (returns `version`, `store`, `llm_provider`, `pinecone_index`, `request_id`)

## Development Notes

- **SQLite WAL mode enabled** - Required for concurrent reads/writes during async operations; `wal_checkpoint` on startup
- **LLM configuration in YAML** - Provider settings, model routing, and cost limits defined in `config/llm_providers.yaml`
- **Test markers** - `integration` for external API tests, `agent` for LangGraph agent tests, `epic` for milestone tests
- **Async patterns** - Use `asyncio.to_thread()` for sync I/O (yfinance), native async for HTTP clients
- **Plan files** - `.claude/plan/stack-rethink.md` and `stack-rethink-v2.md` contain full phase roadmap and status
- **Request correlation** - `logging_config._request_id_var` (contextvars) propagates `X-Request-ID` through each request
