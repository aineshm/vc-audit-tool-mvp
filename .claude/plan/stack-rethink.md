# Implementation Plan: VC Audit Tool Stack Rethink

## Task Type
- [x] Backend (Python/FastAPI/LangGraph)
- [ ] Frontend
- [ ] Fullstack

## Technical Solution

Five-phase incremental refactor. Phases 2–5 parallelizable after Phase 1 completes.
External services: Google Gemini (LLM), Pinecone (vector search), Supabase (PostgreSQL).

---

## Phase 1: Emergency Refactor (PREREQUISITE — do first)

### Goal
Break three oversized files into single-responsibility modules. Zero behavior changes. All 392 tests remain green.

### Implementation Steps

#### Step 1 — Create `evidence_patterns.py`
- **File**: `src/vc_audit_tool/data_sources/evidence_patterns.py` (NEW, ~200 lines)
- **Move from** `evidence_collector.py`: `EVIDENCE_TYPES`, `_DIRECT_VALUATION_PATTERNS`, `_MULTIPLIERS`, `_DATE_NEAR_SIGNAL`, `_parse_amount`, `_find_nearby_date`, `_classify_evidence_type`, `_rough_age_months`, `_extract_revenue_signals`, `_extract_round_date_signals`, `_deduplicate`
- Use `from __future__ import annotations` + `TYPE_CHECKING` guard to avoid circular imports
- **Verify**: `python -c "from vc_audit_tool.data_sources.evidence_patterns import _classify_evidence_type; print('OK')"`

#### Step 2 — Slim `evidence_collector.py`
- Remove moved definitions; re-export from `evidence_patterns` for backward compat
- `_classify_evidence_type` must remain importable from `evidence_collector` (used in `research.py`)
- **Verify**: `python -m pytest tests/test_epic3.py -q`

#### Step 3 — Create `agent/state.py`
- **File**: `src/vc_audit_tool/agent/state.py` (NEW, ~50 lines)
- Move: `ResearchState` TypedDict, `ResearchResult` dataclass
- **Verify**: `python -c "from vc_audit_tool.agent.state import ResearchState, ResearchResult; print('OK')"`

#### Step 4 — Create `agent/llm_provider.py`
- **File**: `src/vc_audit_tool/agent/llm_provider.py` (NEW, ~130 lines)
- Move: all `Chat*` conditional import blocks, `HumanMessage`/`SystemMessage`, `_get_llm`, `_LLM_SYSTEM_PROMPT`, `_llm_extract_structured`
- **Verify**: `python -c "from vc_audit_tool.agent.llm_provider import _get_llm; print('OK')"`

#### Step 5 — Create `agent/nodes.py`
- **File**: `src/vc_audit_tool/agent/nodes.py` (NEW, ~250 lines)
- Move: `_KEYWORD_SECTORS`, `_SEARCH_QUERIES`, `DDGS` conditional imports, `_ensure_langgraph`, `_parse_company_node`, `_form_d_node`, `_web_research_node`, `_ddg_search`, `_contracts_node`, `_merge_llm_into_package`, `_extract_*` helpers
- Import `FormDSource`, `USASpendingSource` at module level (patch targets)
- **Verify**: `python -c "from vc_audit_tool.agent.nodes import _parse_company_node; print('OK')"`

#### Step 6 — Create `agent/assembly.py`
- **File**: `src/vc_audit_tool/agent/assembly.py` (NEW, ~190 lines)
- Move: `_assemble_node`, `_assemble_direct_valuation`, `_assemble_last_round`, `_assemble_comps`, `_try_assemble`, `_normalize_round_date`, `_has_last_round_data`
- **Verify**: `python -c "from vc_audit_tool.agent.assembly import _assemble_node; print('OK')"`

#### Step 7 — Slim `agent/research.py` + Update test patch targets
- Reduce `research.py` to ~100 lines: re-exports + `CompanyResearchAgent` class + `_build_graph`
- Re-export from all new modules for backward compat
- **Update 34 `@patch` targets in `tests/test_epic3.py`**:

| Old | New |
|-----|-----|
| `vc_audit_tool.agent.research.DDGS` | `vc_audit_tool.agent.nodes.DDGS` |
| `vc_audit_tool.agent.research.FormDSource` | `vc_audit_tool.agent.nodes.FormDSource` |
| `vc_audit_tool.agent.research.USASpendingSource` | `vc_audit_tool.agent.nodes.USASpendingSource` |
| `vc_audit_tool.agent.research.ChatGoogleGenerativeAI` | `vc_audit_tool.agent.llm_provider.ChatGoogleGenerativeAI` |
| `vc_audit_tool.agent.research.ChatOpenAI` | `vc_audit_tool.agent.llm_provider.ChatOpenAI` |
| `vc_audit_tool.agent.research.ChatAnthropic` | `vc_audit_tool.agent.llm_provider.ChatAnthropic` |
| `vc_audit_tool.agent.research._web_research_node` | **unchanged** (referenced in `_build_graph` in `research.py`) |

- **Verify**: `python -m pytest tests/test_epic3.py -q -v`

#### Step 8 — Extract HTML template
- Create `src/vc_audit_tool/templates/index.html` with content of `HTML_PAGE` string
- Path resolution: `Path(__file__).resolve().parent.parent / "templates" / "index.html"`

#### Step 9 — Create `routes/` package
- `routes/__init__.py`, `routes/core.py`, `routes/research.py`, `routes/reconcile.py`, `routes/web_ui.py`
- Route handlers access `engine`/`store` via **deferred imports** inside function bodies (avoids circular imports):
  ```python
  async def post_value(request):
      from vc_audit_tool.server import engine, store  # deferred — no circular
      ...
  ```

#### Step 10 — Slim `server.py` + Update `pyproject.toml`
- Reduce to ~80 lines: `engine`, `store`, `app` globals + `app.include_router()` × 4 + `build_parser()` + `main()`
- Remove `per-file-ignores = {"src/vc_audit_tool/server.py" = ["E501"]}` from `pyproject.toml`
- Add `[tool.setuptools.package-data] vc_audit_tool = ["templates/*.html"]`
- **Verify**: `python -m pytest tests/ -q` (ALL 392 tests)

#### Step 11 — Full quality gate
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python -m pytest tests/ -q
```

### Key Files (Phase 1)

| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/data_sources/evidence_patterns.py` | CREATE | Regex patterns + classifiers (~200 lines) |
| `src/vc_audit_tool/data_sources/evidence_collector.py` | MODIFY | Slim to ~180 lines, re-export moved symbols |
| `src/vc_audit_tool/agent/state.py` | CREATE | ResearchState, ResearchResult (~50 lines) |
| `src/vc_audit_tool/agent/llm_provider.py` | CREATE | _get_llm, Chat* imports (~130 lines) |
| `src/vc_audit_tool/agent/nodes.py` | CREATE | 5 node functions, DDGS (~250 lines) |
| `src/vc_audit_tool/agent/assembly.py` | CREATE | _assemble_node + 6 helpers (~190 lines) |
| `src/vc_audit_tool/agent/research.py` | MODIFY | Slim to ~100 lines, re-exports + CompanyResearchAgent |
| `src/vc_audit_tool/templates/index.html` | CREATE | Extracted HTML/CSS/JS (~800 lines) |
| `src/vc_audit_tool/routes/__init__.py` | CREATE | Package marker |
| `src/vc_audit_tool/routes/core.py` | CREATE | /health, /value (~65 lines) |
| `src/vc_audit_tool/routes/research.py` | CREATE | /research (~85 lines) |
| `src/vc_audit_tool/routes/reconcile.py` | CREATE | /reconcile (~105 lines) |
| `src/vc_audit_tool/routes/web_ui.py` | CREATE | /, /api/* (~55 lines) |
| `src/vc_audit_tool/server.py` | MODIFY | Slim to ~80 lines, composition root |
| `pyproject.toml` | MODIFY | Remove E501 ignore, add package-data |
| `tests/test_epic3.py` | MODIFY | Update 34 @patch target strings |

### Risks and Mitigation (Phase 1)

| Risk | Severity | Mitigation |
|------|----------|------------|
| @patch targets break silently | HIGH | Update per mapping table; verify assertion-based tests fail if wrong |
| Circular imports (routes ↔ server) | HIGH | Strict deferred import rule inside function bodies only |
| Template not found in install | MEDIUM | `Path(__file__).resolve()` + `package-data` in pyproject.toml |
| evidence_patterns circular import | MEDIUM | `from __future__ import annotations` + `TYPE_CHECKING` guard |

---

## Phase 2: Async Data Layer + Pinecone (parallel with Phases 4+5)

### Goal
Eliminate blocking sync I/O in async FastAPI server. Replace local sentence-transformers with Pinecone. Parallelize LangGraph research nodes (~3× speedup).

### Key Changes

| Source | Strategy | New method |
|--------|----------|------------|
| `form_d.py` | `httpx.AsyncClient` | `async_search()` |
| `usaspending.py` | `httpx.AsyncClient` | `async_search()` |
| `edgar_universe.py` | `httpx.AsyncClient` | `async_list_by_sic()` |
| `yfinance_market_index.py` | `asyncio.to_thread` | `async_get_level()` |
| `yfinance_metrics.py` | `asyncio.to_thread` + `asyncio.Semaphore(5)` | `async_fetch()`, `async_fetch_many()` |
| DuckDuckGo (`_ddg_search`) | `asyncio.to_thread` | `_async_ddg_search()` |

**LangGraph fan-out** (after async nodes exist):
```
parse_company → [form_d ‖ web_research ‖ contracts] → assemble
```
Each parallel node returns **partial dict** (only its output keys) — no merge conflicts.

**New files:**
- `src/vc_audit_tool/data_sources/pinecone_ranker.py` — `PineconeCompsRanker` using `multilingual-e5-large` hosted inference (dim=1024)
- `src/vc_audit_tool/data_sources/ranker_factory.py` — `get_ranker()`: Pinecone if `PINECONE_API_KEY` set, else local fallback

**`edgar_comps.py` change:** single line — `self._ranker = ranker or get_ranker(cache_dir=...)`

**New env vars:**
```bash
PINECONE_API_KEY="..."
PINECONE_INDEX_NAME="vc-audit-edgar-comps"       # optional, default
PINECONE_EMBEDDING_MODEL="multilingual-e5-large"  # optional, default
```

**New dependencies:**
```toml
dependencies = ["pinecone>=5.0"]
dev = ["pytest-asyncio>=0.23"]
```

**New CLI commands:**
```bash
vc-audit index build      # populate Pinecone from EDGAR universe
vc-audit index status     # show index stats
vc-audit index delete     # delete index (with confirmation)
```

**New tests (~51):** `test_async_data_sources.py`, `test_async_research_agent.py`, `test_pinecone_ranker.py`, `test_index_cli.py`

---

## Phase 3: Google Gemini Configurable LLM (after Phase 2)

### Goal
Replace hardcoded 35-line `_get_llm()` fallback chain with configurable, cost-aware provider system.

### New files
- `config/llm_providers.yaml`:
  ```yaml
  providers:
    - name: google
      model: gemini-2.0-flash
      env_key: GOOGLE_API_KEY
      cost_per_1k_tokens: 0.001
      timeout_seconds: 30
      max_retries: 2
      priority: 1
    - name: openai
      model: gpt-4o-mini
      env_key: OPENAI_API_KEY
      priority: 2
    # ... anthropic, ollama
  budget:
    max_cost_per_request_usd: 0.10
  fallback: regex
  ```
- `src/vc_audit_tool/llm/__init__.py`
- `src/vc_audit_tool/llm/config.py` — load + validate YAML
- `src/vc_audit_tool/llm/provider.py` — retry (exponential backoff), `asyncio.wait_for` timeout
- `src/vc_audit_tool/llm/fallback_chain.py` — `LLMFallbackChain.get_provider()`

**Integration:** Replace `_get_llm()` call in `agent/llm_provider.py` with `LLMFallbackChain.get_provider()` — single line change.

---

## Phase 4: Supabase PostgreSQL (parallel with Phase 2)

### Goal
Replace SQLite append-log with indexed, typed PostgreSQL via Supabase (free tier).

### Schema
```sql
CREATE TABLE valuation_runs (
    request_id   UUID PRIMARY KEY,
    company_name TEXT NOT NULL,
    methodology  TEXT NOT NULL,
    as_of_date   DATE,
    generated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence_score REAL,
    sector       TEXT,
    estimated_fair_value_usd REAL,
    payload      JSONB NOT NULL
);
CREATE INDEX idx_runs_company    ON valuation_runs(company_name);
CREATE INDEX idx_runs_methodology ON valuation_runs(methodology);
CREATE INDEX idx_runs_date       ON valuation_runs(as_of_date);
CREATE INDEX idx_runs_confidence ON valuation_runs(confidence_score);
```

### New files
- `src/vc_audit_tool/store_supabase.py` — `SupabaseValuationStore` (same Protocol interface as `ValuationStore`)
- Store factory in `store.py`: `get_store()` → Supabase if `SUPABASE_URL` set, else SQLite
- `scripts/migrate_sqlite_to_supabase.py` — one-time data migration

**New env vars:**
```bash
SUPABASE_URL="https://xxx.supabase.co"
SUPABASE_KEY="anon-key"
```

**New dependency:** `supabase>=2.0` (async support)

**New CLI:** `vc-audit db migrate`

**Free tier note:** Projects pause after 1 week of inactivity (auto-resumes in ~10s on next request).

---

## Phase 5: Observability + DX (parallel with Phase 2)

### Goal
Structured logging with correlation IDs, YAML validation at startup, TypedDict response envelopes, externalized mock data.

### New files
- `src/vc_audit_tool/logging_config.py` — JSON formatter, `X-Request-ID` → `contextvars.ContextVar`, FastAPI middleware
- `src/vc_audit_tool/config_schema.py` — validate `methodology_rules_v1.yaml` at startup
- `src/vc_audit_tool/response_types.py` — `ValuationResponse`, `ResearchResponse`, `ErrorResponse` TypedDicts
- `src/vc_audit_tool/data_sources/fixtures/mock_index_levels.json`
- `src/vc_audit_tool/data_sources/fixtures/mock_comps.json`

### Key changes
- `agent/nodes.py`: timing decorator per node → logs `{"event": "node_complete", "node": "web_research", "duration_ms": 4231}`
- `ResearchState`: add optional `timings: dict[str, float]` key
- `/health` endpoint: extended response with `database`, `llm_provider`, `pinecone_index`, `version`

---

## Execution Roadmap

```
Phase 1 ── (both engineers, ~2-3 days) ── prerequisite
    ├── Phase 2: Async + Pinecone    (~4-5 days) ─┐ parallel
    ├── Phase 4: Supabase            (~3 days)    ─┤
    └── Phase 5: Observability       (~2 days)    ─┘
              └── Phase 3: Gemini config (~3 days, after Phase 2)
```

---

## All New Environment Variables

| Variable | Phase | Required | Default | Purpose |
|----------|-------|----------|---------|---------|
| `GOOGLE_API_KEY` | 3 | No | — | Gemini LLM (priority 1) |
| `PINECONE_API_KEY` | 2 | No | — | Pinecone vector DB |
| `PINECONE_INDEX_NAME` | 2 | No | `vc-audit-edgar-comps` | Pinecone index |
| `PINECONE_EMBEDDING_MODEL` | 2 | No | `multilingual-e5-large` | Hosted inference model |
| `SUPABASE_URL` | 4 | No | — | Supabase project URL |
| `SUPABASE_KEY` | 4 | No | — | Supabase anon key |

All existing env vars (`VC_AUDIT_SEC_USER_AGENT`, `VC_AUDIT_MOCK`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_MODEL`) unchanged.

---

## All New Dependencies

```toml
[project.dependencies]
# Phase 2
"pinecone>=5.0"
# Phase 4
"supabase>=2.0"

[project.optional-dependencies]
dev = [
    # Phase 2
    "pytest-asyncio>=0.23",
]
llm = [
    # existing + no changes needed for Phase 3 (uses langchain-google-genai already)
]
```

---

## SESSION_ID (for /ccg:execute use)
- CODEX_SESSION: N/A (codeagent-wrapper not installed — fell back to manual planning)
- GEMINI_SESSION: N/A (codeagent-wrapper not installed — fell back to manual planning)
