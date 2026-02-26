"""Reconcile route: POST /reconcile."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.services.valuation_service import read_json

logger = logging.getLogger("vc_audit_tool.routers.reconcile")

router = APIRouter()


@router.post("/reconcile")
async def post_reconcile(request: Request) -> JSONResponse:
    """Run the research agent, profile the company, then reconcile.

    Uses all applicable methodologies (weighted by stage / data rules)
    and returns a single reconciled valuation with divergence analysis.

    Request body::

        {
          "company_name": "Anthropic",
          "as_of_date": "2026-02-22",          // optional
          "description_hint": "AI safety lab"   // optional
        }

    Response: ``ReconciledValuation.to_dict()`` envelope.
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

    as_of_date_str = payload.get("as_of_date", "")
    description_hint = payload.get("description_hint", "")

    start = time.monotonic()

    # ── Step 1: Research ───────────────────────────────────────────────
    try:
        from vc_audit_tool.agent.research import CompanyResearchAgent

        agent = CompanyResearchAgent()
        research = agent.run(
            company_name,
            methodology="",  # let reconciliation engine pick
            as_of_date=as_of_date_str or "",
            description_hint=description_hint or "",
        )
    except ImportError as exc:
        return JSONResponse(
            {"error": f"Research agent dependencies not installed: {exc}"},
            status_code=500,
        )
    except Exception as exc:
        logger.exception("reconcile_research_error company=%s error=%s", company_name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not research.is_complete:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "reconcile_incomplete company=%s missing=%s elapsed_ms=%.1f",
            company_name,
            research.missing_fields,
            elapsed_ms,
        )
        return JSONResponse(
            {
                "error": "Could not assemble complete valuation inputs.",
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
            },
            status_code=422,
        )

    # ── Step 2: Build profile + data package ──────────────────────────
    try:
        from datetime import date as _date

        from vc_audit_tool.reconciliation.engine import ReconciliationEngine
        from vc_audit_tool.reconciliation.models import DataPackage
        from vc_audit_tool.reconciliation.profiler import CompanyProfiler

        assembled = research.assembled_request
        assert assembled is not None  # guarded by is_complete

        aod = _date.fromisoformat(as_of_date_str) if as_of_date_str else _date.today()

        profile = (
            research.company_profile
            if research.company_profile is not None
            else CompanyProfiler.build_from_dict(
                assembled,
                research.research_metadata,
                aod,
            )
        )

        data_package = DataPackage.from_assembled_request(assembled, aod)

        recon_engine = ReconciliationEngine()
        result = recon_engine.value(
            profile=profile,
            data_package=data_package,
            as_of_date=aod,
            company_name=company_name,
            research_metadata=research.research_metadata,
        )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "reconcile_ok company=%s methods=%d elapsed_ms=%.1f",
            company_name,
            len(result.methodology_results),
            elapsed_ms,
        )
        return JSONResponse(result.to_dict(), status_code=200)

    except (ValidationError, DataSourceError) as exc:
        logger.warning("reconcile_valuation_error error=%s", exc)
        return JSONResponse(
            {
                "error": str(exc),
                "research_metadata": research.research_metadata,
            },
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("reconcile_unhandled_error error=%s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
