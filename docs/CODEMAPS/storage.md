# Storage Layer Codemap (Phase 4)

**Last Updated:** 2026-03-01

Valuation run persistence with automatic SQLite ↔ Supabase selection.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                  store_factory.get_store()                 │
│            (auto-detect based on env vars)                 │
└────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┴────────────────────┐
        │                                        │
        v                                        v
  ┌─────────────────┐            ┌──────────────────────────┐
  │  ValuationStore │            │ SupabaseValuationStore   │
  │  (SQLite WAL)   │            │  (PostgreSQL via SDK)    │
  │                 │            │                          │
  │ Default         │            │ Phase 4 — Auto-detect    │
  │ (no setup)      │            │ if SUPABASE_URL+KEY set  │
  └─────────────────┘            └──────────────────────────┘
        │                                │
        │                                │
        v                                v
   valuation_runs.db             Supabase Project
   (SQLite WAL mode)             drykfbevdfyivyhnkyfc
                                 valuation_runs table
```

## Store Selection Logic

**Priority:**
1. If `SUPABASE_URL` AND `SUPABASE_KEY` both set → `SupabaseValuationStore`
2. Otherwise → `ValuationStore` (SQLite WAL, default path `valuation_runs.db`)

```python
# store_factory.py
def get_store(db_path: Path = Path("valuation_runs.db")) -> ValuationStoreProtocol:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if url and key:
        return SupabaseValuationStore(url=url, key=key)
    return ValuationStore(db_path)
```

## Option 1: SQLite WAL (Default)

**File:** `src/vc_audit_tool/store.py`

**Class:** `ValuationStore`

**Activation:** No environment variables needed (automatic default)

### Features

- **SQLite WAL mode** — Write-Ahead Logging for concurrent reads/writes during async operations
- **Auto-checkpoint** — `wal_checkpoint()` on server startup
- **No setup** — Single file `valuation_runs.db` in project root
- **Local-only** — No network latency, instant reads
- **Perfect for development** — Works out of the box

### Schema

```sql
CREATE TABLE IF NOT EXISTS valuation_runs (
  request_id TEXT PRIMARY KEY,
  company_name TEXT,
  methodology TEXT,
  as_of_date TEXT,
  fair_value REAL,
  generated_at_utc TEXT,
  payload TEXT  -- JSON string
);

CREATE INDEX IF NOT EXISTS idx_valuation_runs_date ON valuation_runs (generated_at_utc DESC);
```

### Implementation

```python
class ValuationStore:
    def __init__(self, db_path: Path = Path("valuation_runs.db")):
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create table and WAL checkpoint."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS valuation_runs (
                  request_id TEXT PRIMARY KEY,
                  company_name TEXT,
                  methodology TEXT,
                  as_of_date TEXT,
                  fair_value REAL,
                  generated_at_utc TEXT,
                  payload TEXT
                )
            """)
            conn.execute("PRAGMA optimize")
            # WAL checkpoint ensures all data is flushed
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def save(self, result_dict: dict[str, Any]) -> str:
        """Upsert a valuation result."""
        vr = result_dict["valuation_result"]
        am = result_dict["audit_metadata"]
        request_id = am["request_id"]

        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO valuation_runs
                  (request_id, company_name, methodology, as_of_date, fair_value, generated_at_utc, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                request_id,
                vr["company_name"],
                vr["methodology"],
                vr["as_of_date"],
                float(vr["estimated_fair_value"]["amount"]),
                am["generated_at_utc"],
                json.dumps(result_dict),
            ))
        return request_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs (summary only)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT request_id, company_name, methodology, as_of_date, fair_value, generated_at_utc
                FROM valuation_runs
                ORDER BY generated_at_utc DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_run(self, request_id: str) -> dict[str, Any] | None:
        """Return the full payload for a single run."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT payload FROM valuation_runs WHERE request_id = ?",
                (request_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def close(self) -> None:
        """Cleanup (no-op for SQLite)."""
        pass
```

## Option 2: Supabase PostgreSQL (Phase 4)

**File:** `src/vc_audit_tool/store_supabase.py`

**Class:** `SupabaseValuationStore`

**Activation:** Set both `SUPABASE_URL` and `SUPABASE_KEY` environment variables

### Project Details

| Attribute | Value |
|-----------|-------|
| **Project ID** | `drykfbevdfyivyhnkyfc` |
| **Region** | `us-west-2` |
| **Database** | PostgreSQL 15 |
| **Table** | `valuation_runs` (request_id PK) |
| **RLS** | Disabled (private backend use) |

### Why Supabase?

1. **Managed PostgreSQL** — No infrastructure management
2. **Stateless client** — Each operation opens a new connection (SDK handles pooling)
3. **Direct frontend reads** — Frontend can query Supabase directly (bypassing FastAPI) for read-only operations
4. **Automatic backups** — Daily snapshots
5. **JSON support** — JSONB column for storing full payloads

### Environment Variables

```bash
export SUPABASE_URL="https://drykfbevdfyivyhnkyfc.supabase.co"
export SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...."  # anon or service-role key
```

### Schema

```sql
CREATE TABLE valuation_runs (
  request_id TEXT PRIMARY KEY,
  company_name TEXT,
  methodology TEXT,
  as_of_date DATE,
  fair_value DECIMAL,
  generated_at_utc TIMESTAMP WITH TIME ZONE,
  payload JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_valuation_runs_date ON valuation_runs (generated_at_utc DESC);
```

### Implementation

```python
class SupabaseValuationStore:
    """Persist valuation runs in Supabase (PostgreSQL)."""

    _TABLE = "valuation_runs"

    def __init__(self, url: str, key: str) -> None:
        try:
            from supabase import create_client
            self._client = create_client(url, key)
        except ImportError as exc:
            raise RuntimeError(
                "supabase-py is required for SupabaseValuationStore. "
                "Install it with: pip install 'vc-audit-tool[supabase]'"
            ) from exc

    def save(self, result_dict: dict[str, Any]) -> str:
        """Upsert a valuation result."""
        vr = result_dict["valuation_result"]
        am = result_dict["audit_metadata"]
        request_id = am["request_id"]

        row = {
            "request_id": request_id,
            "company_name": vr["company_name"],
            "methodology": vr["methodology"],
            "as_of_date": vr["as_of_date"],
            "fair_value": vr["estimated_fair_value"]["amount"],
            "generated_at_utc": am["generated_at_utc"],
            "payload": json.dumps(result_dict),
        }

        try:
            self._client.table(self._TABLE).upsert(row).execute()
        except Exception as exc:
            raise RuntimeError(f"Supabase save failed: {exc}") from exc

        return request_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs (summary only — no payload)."""
        try:
            response = (
                self._client.table(self._TABLE)
                .select(
                    "request_id,company_name,methodology,as_of_date,fair_value,generated_at_utc"
                )
                .order("generated_at_utc", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as exc:
            raise RuntimeError(f"Supabase list_runs failed: {exc}") from exc

        return response.data or []

    def get_run(self, request_id: str) -> dict[str, Any] | None:
        """Return the full payload for a single run."""
        try:
            response = (
                self._client.table(self._TABLE)
                .select("payload")
                .eq("request_id", request_id)
                .maybe_single()
                .execute()
            )
        except Exception as exc:
            raise RuntimeError(f"Supabase get_run failed: {exc}") from exc

        if response.data is None:
            return None

        return json.loads(response.data["payload"])

    def close(self) -> None:
        """No-op — Supabase client is stateless."""
        pass
```

### Key Design Decisions

1. **Stateless client** — Each operation creates a new connection (SDK manages pooling internally)
   - Pros: Scales with serverless functions, no connection pool exhaustion
   - Cons: Slight per-operation overhead (mitigated by SDK pooling)

2. **JSONB payload column** — Full result stored as JSON
   - Enables: direct Supabase queries, future indexing on nested fields
   - Example query: `SELECT * FROM valuation_runs WHERE payload->>'company_name' = 'Anthropic'`

3. **RLS disabled** — Private backend use only
   - If frontend needs access, use anon key with RLS policies

4. **Upsert semantics** — `INSERT OR REPLACE` behavior
   - Allows re-running same request to update result

## Store Factory

**File:** `src/vc_audit_tool/store_factory.py`

```python
class ValuationStoreProtocol(Protocol):
    """Structural interface satisfied by both ValuationStore and SupabaseValuationStore."""

    def save(self, result_dict: dict[str, Any]) -> str: ...
    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]: ...
    def get_run(self, request_id: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


def get_store(db_path: Path = Path("valuation_runs.db")) -> ValuationStoreProtocol:
    """Return the best available valuation store.

    Priority:
      1. SupabaseValuationStore — if SUPABASE_URL and SUPABASE_KEY are set
      2. ValuationStore (SQLite WAL) — fallback default
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if url and key:
        logger.info("using SupabaseValuationStore (SUPABASE_URL set)")
        from vc_audit_tool.store_supabase import SupabaseValuationStore
        return SupabaseValuationStore(url=url, key=key)

    logger.info("using SQLite ValuationStore at %s", db_path)
    return ValuationStore(db_path)
```

## Integration Points

### Server Initialization (`server.py`)

```python
from vc_audit_tool.store_factory import get_store

# Module-level singleton (created once at startup)
store = get_store()

# Attached to FastAPI app state for router access
app.state.store = store
```

### Health Check Detection (`routers/valuation.py`)

```python
def _detect_store() -> str:
    """Detect which valuation store is active."""
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
        return "supabase"
    return "sqlite_wal"

@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": version,
        "store": _detect_store(),  # Detects and returns active store
        "llm_provider": _detect_llm_provider(),
        "pinecone_index": pinecone_index,
        "request_id": get_request_id(),
    }
```

### Valuation Service (`services/valuation_service.py`)

```python
async def run_valuation(
    payload: dict,
    engine: ValuationEngine,
    store: ValuationStoreProtocol,
    persist: bool = False
) -> JSONResponse:
    """Run valuation and optionally persist."""
    result = engine.evaluate_from_dict(payload)

    if persist:
        result_dict = result.to_dict()
        request_id = store.save(result_dict)
        result_dict["audit_metadata"]["request_id"] = request_id

    return JSONResponse(result.to_dict())
```

### Routers (`routers/valuation.py`)

```python
@router.get("/api/runs")
def api_runs(request: Request) -> Any:
    """List recent valuation runs."""
    return request.app.state.store.list_runs()

@router.get("/api/runs/{run_id}")
def api_run_detail(run_id: str, request: Request) -> JSONResponse:
    """Return full payload for a run."""
    run = request.app.state.store.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(run)
```

## Migration & Testing

### Running with SQLite (Default)

```bash
# No setup needed
python3 -m vc_audit_tool.server
# Creates valuation_runs.db automatically
```

### Running with Supabase

```bash
# 1. Install optional dependency
pip install -e ".[supabase]"

# 2. Set environment variables
export SUPABASE_URL="https://drykfbevdfyivyhnkyfc.supabase.co"
export SUPABASE_KEY="your-anon-or-service-role-key"

# 3. Start server
python3 -m vc_audit_tool.server

# 4. Verify with health check
curl http://127.0.0.1:8080/health
# Should return: "store": "supabase"
```

### Testing Store Abstraction

```python
# tests/test_server.py
def test_store_auto_selection(monkeypatch):
    """Verify store_factory selects Supabase when vars are set."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    from vc_audit_tool.store_factory import get_store
    store = get_store()

    assert store.__class__.__name__ == "SupabaseValuationStore"

def test_store_fallback_to_sqlite(monkeypatch):
    """Verify store_factory falls back to SQLite."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    from vc_audit_tool.store_factory import get_store
    store = get_store()

    assert store.__class__.__name__ == "ValuationStore"
```

### Test Fixtures

```python
# conftest.py
@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    """Fresh SQLite store per test (prevent cross-test leakage)."""
    db_path = tmp_path / "test.db"
    import vc_audit_tool.server
    vc_audit_tool.server.store = ValuationStore(db_path)
    yield
    # Cleanup handled by tmp_path fixture
```

## Frontend Integration (Phase 4)

**File:** `frontend/src/lib/supabase-data-service.ts`

Frontend can read directly from Supabase (bypassing FastAPI):

```typescript
// When NEXT_PUBLIC_SUPABASE_URL + ANON_KEY are set:
class SupabaseDataService implements DataService {
    async listRuns(): Promise<RunSummary[]> {
        // Direct query to Supabase (no FastAPI roundtrip)
        return await supabase
            .from("valuation_runs")
            .select("request_id,company_name,methodology,fair_value,generated_at_utc")
            .order("generated_at_utc", { ascending: false })
            .limit(50);
    }

    async getRun(runId: string): Promise<ValuationResult> {
        // Direct query, parse payload
        const { data } = await supabase
            .from("valuation_runs")
            .select("payload")
            .eq("request_id", runId)
            .single();

        return JSON.parse(data.payload);
    }

    // Write operations still go through FastAPI
    async createRun(request: ValuationRequest): Promise<ValuationResult> {
        return await fastapi.post("/api/value", request);
    }
}
```

## Performance Considerations

| Metric | SQLite WAL | Supabase |
|--------|-----------|----------|
| **Write latency** | <1ms (local) | 50–200ms (network) |
| **Read latency** | <1ms (local) | 50–200ms (network) |
| **Scalability** | Single-process | Unlimited (managed) |
| **Concurrency** | Good (WAL) | Excellent (PostgreSQL) |
| **Cost** | Free (self-hosted) | Pay-as-you-go |
| **Backup** | Manual | Automatic (daily) |
| **Multi-region** | Not supported | Supported |
| **Ideal for** | Development, single-server | Production, multi-region |

## Related Codemaps

- **[backend.md](./backend.md)** — Valuation engine, data sources, methodologies
- **[frontend.md](./frontend.md)** — Next.js UI, frontend data service selection
