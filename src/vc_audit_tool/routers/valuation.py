"""Valuation routes: /health, /value, /api/value, /api/runs, /api/runs/{id}."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from vc_audit_tool.services.valuation_service import read_json, run_valuation

logger = logging.getLogger("vc_audit_tool.routers.valuation")

router = APIRouter()

_STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/")
def web_root() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(_STATIC_DIR / "index.html")


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
