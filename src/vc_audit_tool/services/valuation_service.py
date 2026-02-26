"""Valuation orchestration service -- business logic layer between HTTP routes and engine."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from vc_audit_tool.exceptions import DataSourceError, ValidationError

logger = logging.getLogger("vc_audit_tool.services.valuation")


async def read_json(request: Request) -> dict[str, Any]:
    """Read and parse the JSON body, raising JSONDecodeError on failure."""
    body = await request.body()
    result: dict[str, Any] = json.loads(body)
    return result


def run_valuation(
    payload: dict[str, Any],
    engine: Any,
    store: Any,
    *,
    persist: bool = False,
) -> JSONResponse:
    """Run the engine and optionally persist to the store."""
    start = time.monotonic()
    try:
        result = engine.evaluate_from_dict(payload)
        result_dict = result.to_dict()
        if persist:
            store.save(result_dict)
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "valuation_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
            result.company_name,
            result.methodology,
            result.request_id,
            elapsed_ms,
        )
        return JSONResponse(result_dict, status_code=200)
    except (ValidationError, DataSourceError) as exc:
        logger.warning("validation_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # pragma: no cover
        logger.exception("unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
