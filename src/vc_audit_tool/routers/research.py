"""Research route: POST /research."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.services.valuation_service import read_json

logger = logging.getLogger("vc_audit_tool.routers.research")

router = APIRouter()


@router.post("/research")
async def post_research(request: Request) -> JSONResponse:
    """Run the research agent, then pass assembled inputs to the engine.

    Request body::

        {
          "company_name": "Anthropic",
          "methodology": "comparable_companies",  // optional
          "as_of_date": "2026-02-22"               // optional
        }

    Response extends the standard valuation envelope with a
    ``research_metadata`` key that is clearly separated from
    ``valuation_result``.
    """
    try:
        payload = await read_json(request)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("bad_json error=%s", exc)
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    company_name = payload.get("company_name")
    if not company_name or not isinstance(company_name, str):
        return JSONResponse(
            {"error": "Missing required field: 'company_name'."},
            status_code=400,
        )

    methodology = payload.get("methodology", "")
    as_of_date = payload.get("as_of_date", "")
    description_hint = payload.get("description_hint", "")

    start = time.monotonic()
    try:
        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        research = agent.run(
            company_name,
            methodology=methodology or "",
            as_of_date=as_of_date or "",
            description_hint=description_hint or "",
        )
    except ImportError as exc:
        return JSONResponse(
            {"error": f"Research agent dependencies not installed: {exc}"},
            status_code=500,
        )
    except Exception as exc:
        logger.exception("research_agent_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not research.is_complete:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "research_incomplete company=%s missing=%s elapsed_ms=%.1f",
            company_name,
            research.missing_fields,
            elapsed_ms,
        )
        return JSONResponse(
            {
                "assembled_request": None,
                "best_available_methodology": research.best_available_methodology,
                "missing_for_best_available": (
                    research.missing_for_best_available or research.missing_fields
                ),
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
                "web_facts": research.web_facts or {},
            },
            status_code=200,
        )

    engine = request.app.state.engine
    try:
        result = engine.evaluate_from_dict(research.assembled_request)  # type: ignore[arg-type]
        result_dict = result.to_dict()
        result_dict["research_metadata"] = research.research_metadata
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "research_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
            result.company_name,
            result.methodology,
            result.request_id,
            elapsed_ms,
        )
        return JSONResponse(result_dict, status_code=200)
    except (ValidationError, DataSourceError) as exc:
        logger.warning("research_valuation_error error=%s", exc)
        return JSONResponse(
            {
                "error": str(exc),
                "research_metadata": research.research_metadata,
            },
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("research_unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
