"""FastAPI server -- app wiring, CLI entry-point, and startup.

Routes are split across sub-modules:
  routers/valuation.py  -- GET /health, POST /value, POST /api/value, GET /api/runs*
  routers/research.py   -- POST /research
  routers/reconcile.py  -- POST /reconcile

The single-page web UI is served from static/index.html.
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.logging_config import configure_logging, reset_request_id, set_request_id
from vc_audit_tool.routers import reconcile as reconcile_router
from vc_audit_tool.routers import research as research_router
from vc_audit_tool.routers import valuation as valuation_router
from vc_audit_tool.store import ValuationStore

logger = logging.getLogger("vc_audit_tool.server")

# Configure structured logging at module level
configure_logging()

# ---------------------------------------------------------------------------
# Module-level singletons (kept here for backward compatibility with tests
# that patch vc_audit_tool.server.ValuationEngine / .ValuationStore or
# directly assign server_module.engine).
# ---------------------------------------------------------------------------

engine = ValuationEngine()
store = ValuationStore()

app = FastAPI(
    title="VC Audit Tool",
    description="Auditable valuation engine for private VC portfolio companies.",
    version="0.1.0",
)

# Eagerly attach singletons to app.state so routers can access them via
# request.app.state.engine / request.app.state.store without circular imports.
app.state.engine = engine
app.state.store = store


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    token = set_request_id(rid)
    try:
        response: Response = await call_next(request)
    finally:
        reset_request_id(token)
    response.headers["X-Request-ID"] = rid
    return response


app.include_router(valuation_router.router)
app.include_router(research_router.router)
app.include_router(reconcile_router.router)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VC Audit Tool FastAPI service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--db", default="valuation_runs.db", help="SQLite database path.")
    parser.add_argument(
        "--mode",
        choices=["live", "mock"],
        default="live",
        help="Data-source mode for valuation engine (default: live).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO).",
    )
    return parser


def main() -> int:
    import uvicorn

    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # ``--mode`` is authoritative and overrides ``VC_AUDIT_MOCK``.
    os.environ["VC_AUDIT_MOCK"] = "1" if args.mode == "mock" else "0"

    global engine  # noqa: PLW0603
    engine = ValuationEngine()

    # Re-initialise the module-level store with the user-chosen DB path.
    global store  # noqa: PLW0603
    store = ValuationStore(Path(args.db))

    # Keep app.state in sync with the re-initialised singletons.
    app.state.engine = engine
    app.state.store = store

    logger.info("starting FastAPI server on http://%s:%d mode=%s", args.host, args.port, args.mode)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
