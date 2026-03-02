# Implementation Plan: Stack Rethink — Next Steps

## Status Summary

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Emergency Refactor | ✅ DONE | server.py=108L, agent/nodes/, routers/ |
| Phase 2: Async + Pinecone | 70% — **Pinecone missing** | httpx async ✅, WAL ✅, LangGraph parallel ✅ |
| Phase 3: Configurable LLM | ✅ DONE | llm_config.py + llm_providers.yaml |
| Phase 4: Supabase | ❌ NOT DONE | requires external service |
| Phase 5: Observability | ❌ NOT DONE | pure code, high value |

---

## Execution Plan: Two parallel tracks

### Track A — Pinecone Comps Ranker (Phase 2 completion)

**Goal:** Drop-in alternative to `EmbeddingCompsRanker` using Pinecone hosted inference.
Uses same `rank(target_description, candidates, top_k) -> list[RankedCompany]` interface.

#### A1 — `src/vc_audit_tool/data_sources/pinecone_ranker.py` (NEW, ~120 lines)

```python
class PineconeCompsRanker:
    dataset_version: str = "pinecone-multilingual-e5-large-v1"
    source_label: str = "Pinecone hosted-inference ranker"

    def __init__(self, index_name: str, embedding_model: str) -> None: ...

    def rank(
        self,
        target_description: str,
        candidates: list[dict[str, str]],
        top_k: int = 5,
    ) -> list[RankedCompany]: ...
```

**Implementation notes:**
- Lazy-import `pinecone` (not in base deps)
- Upsert candidate vectors to Pinecone index on first call (batch upsert)
- Query with target description embedding
- Map returned IDs back to `RankedCompany` objects
- Raise `DataSourceError` if `pinecone` not installed or API call fails

#### A2 — `src/vc_audit_tool/data_sources/ranker_factory.py` (NEW, ~40 lines)

```python
def get_ranker(cache_dir: Path = ...) -> EmbeddingCompsRanker | PineconeCompsRanker:
    """Return PineconeCompsRanker if PINECONE_API_KEY set, else local fallback."""
    if os.getenv("PINECONE_API_KEY"):
        return PineconeCompsRanker(
            index_name=os.getenv("PINECONE_INDEX_NAME", "vc-audit-edgar-comps"),
            embedding_model=os.getenv("PINECONE_EMBEDDING_MODEL", "multilingual-e5-large"),
        )
    return EmbeddingCompsRanker(cache_dir=cache_dir)
```

#### A3 — Modify `edgar_comps.py` (1 line)

```python
# Before:
self._ranker = ranker or EmbeddingCompsRanker(cache_dir=cache_root / "embedding_cache")
# After:
self._ranker = ranker or get_ranker(cache_dir=cache_root / "embedding_cache")
```

#### A4 — Tests: `tests/test_pinecone_ranker.py` (~60 lines)

- Test `get_ranker()` returns `EmbeddingCompsRanker` when `PINECONE_API_KEY` unset
- Test `get_ranker()` returns `PineconeCompsRanker` when `PINECONE_API_KEY` set
- Test `PineconeCompsRanker.rank()` with mocked `pinecone` client
- Test graceful `DataSourceError` when pinecone not installed

---

### Track B — Observability / Phase 5

**Goal:** Structured JSON logging, per-request correlation IDs, extended `/health`.

#### B1 — `src/vc_audit_tool/logging_config.py` (NEW, ~80 lines)

```python
import contextvars, logging, json
from datetime import datetime, timezone

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

class JsonFormatter(logging.Formatter):
    def format(self, record) -> str:
        return json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _request_id_var.get(),
        })

def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(level)
```

#### B2 — FastAPI middleware in `server.py` (~15 lines added)

```python
import uuid
from vc_audit_tool.logging_config import _request_id_var

@app.middleware("http")
async def request_id_middleware(request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    token = _request_id_var.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    _request_id_var.reset(token)
    return response
```

#### B3 — Extended `/health` in `routers/valuation.py`

```python
# Current: {"status": "ok"}
# New:
{
    "status": "ok",
    "version": "0.1.0",
    "store": "sqlite_wal",
    "llm_provider": "google|openai|anthropic|ollama|regex",
    "pinecone_index": "vc-audit-edgar-comps|disabled"
}
```

#### B4 — Tests: additions to `test_server.py` (~30 lines)

- Test `X-Request-ID` header is echoed back
- Test `/health` returns extended fields
- Test `configure_logging()` sets JSON formatter

---

### Key Files

| File | Op | Description |
|------|----|-------------|
| `src/vc_audit_tool/data_sources/pinecone_ranker.py` | CREATE | Pinecone-backed comps ranker |
| `src/vc_audit_tool/data_sources/ranker_factory.py` | CREATE | `get_ranker()` factory |
| `src/vc_audit_tool/data_sources/edgar_comps.py` | MODIFY | Use `get_ranker()` |
| `src/vc_audit_tool/logging_config.py` | CREATE | JSON logging + correlation ID |
| `src/vc_audit_tool/server.py` | MODIFY | Add request-ID middleware |
| `src/vc_audit_tool/routers/valuation.py` | MODIFY | Extend /health |
| `tests/test_pinecone_ranker.py` | CREATE | Pinecone ranker tests |
| `tests/test_server.py` | MODIFY | Add observability tests |

### Quality Gate (after implementation)
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python3 -m pytest tests/ -q
```
