"""Valuation routes: /health, /value, /api/value, /api/runs, /api/runs/{id}."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vc_audit_tool.logging_config import get_request_id
from vc_audit_tool.services.valuation_service import read_json, run_valuation

logger = logging.getLogger("vc_audit_tool.routers.valuation")

router = APIRouter()


def _detect_llm_provider() -> str:
    """Detect which LLM provider is configured."""
    for name, env in [
        ("google", "GOOGLE_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("ollama", "OLLAMA_MODEL"),
    ]:
        if os.getenv(env):
            return name
    return "regex"


def _detect_store() -> str:
    """Detect which valuation store is active."""
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
        return "supabase"
    return "sqlite_wal"


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe with extended status information."""
    try:
        version = importlib.metadata.version("vc-audit-tool")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"

    pinecone_index = "disabled"
    if os.getenv("PINECONE_API_KEY"):
        pinecone_index = os.getenv("PINECONE_INDEX_NAME", "disabled")

    return {
        "status": "ok",
        "version": version,
        "store": _detect_store(),
        "llm_provider": _detect_llm_provider(),
        "pinecone_index": pinecone_index,
        "request_id": get_request_id(),
    }


@router.get("/")
def web_root() -> JSONResponse:
    """API root — UI is served by the Next.js frontend on port 3000."""
    return JSONResponse({"message": "VC Audit Tool API. UI: http://localhost:3000"})


@router.post("/value")
async def post_value(request: Request) -> JSONResponse:
    """Run a valuation and return the auditable envelope."""
    try:
        payload = await read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)
    return await run_valuation(payload, request.app.state.engine, request.app.state.store)


@router.post("/api/value")
async def api_value(request: Request) -> JSONResponse:
    """Run a valuation, persist to SQLite, return JSON (used by the web UI)."""
    try:
        payload = await read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)
    return await run_valuation(
        payload, request.app.state.engine, request.app.state.store, persist=True
    )


@router.get("/api/runs")
def api_runs(request: Request) -> Any:
    """List recent valuation runs (summary only)."""
    return request.app.state.store.list_runs()


@router.get("/api/runs/{run_id}")
def api_run_detail(run_id: str, request: Request) -> JSONResponse:
    """Return the full payload for a single run."""
    run = request.app.state.store.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return JSONResponse(run, status_code=200)
