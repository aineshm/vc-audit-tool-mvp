"""Research route: POST /research."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vc_audit_tool.agent.nodes.assemble import _assemble_last_round
from vc_audit_tool.exceptions import DataSourceError, ValidationError
from vc_audit_tool.methodologies._discount_config import get_discount_default
from vc_audit_tool.routers.error_utils import sanitize_error
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
        return JSONResponse({"error": sanitize_error(exc)}, status_code=500)

    if not research.is_complete:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "research_incomplete company=%s missing=%s error=%s elapsed_ms=%.1f",
            company_name,
            research.missing_fields,
            research.error,
            elapsed_ms,
        )
        return JSONResponse(
            {
                "error": research.error or "Research incomplete — insufficient data found.",
                "assembled_request": None,
                "best_available_methodology": research.best_available_methodology,
                "missing_for_best_available": (
                    research.missing_for_best_available or research.missing_fields
                ),
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
                "web_facts": research.web_facts or {},
            },
            status_code=422,
        )

    engine = request.app.state.engine
    try:
        assembled_request = research.assembled_request
        if assembled_request is None:
            return JSONResponse(
                {
                    "error": "Research returned no assembled request.",
                    "research_metadata": research.research_metadata,
                },
                status_code=400,
            )
        result = engine.evaluate_from_dict(assembled_request)
        result_dict = result.to_dict()
        result_dict["research_metadata"] = research.research_metadata
        try:
            store = request.app.state.store
            store.save(result_dict)
        except Exception as save_exc:
            logger.warning("store_save_failed error=%s — returning result anyway", save_exc)
            result_dict["_store_warning"] = f"Result not persisted: {save_exc}"
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
        ev_pkg = research.research_metadata.get("evidence_package", {})
        fallback_req = None

        # Fallback 1: last_round_market_adjusted when comps-based method failed
        # and web_facts has post-money + round date.
        if assembled_request is not None and assembled_request.get("methodology") in (
            "comparable_companies",
            "last_round_multiple_ratchet",
        ):
            lr_req, lr_miss = _assemble_last_round(
                company_name,
                assembled_request.get("as_of_date", ""),
                research.web_facts or {},
                [],
            )
            if lr_req and not lr_miss:
                fallback_req = lr_req
                logger.info(
                    "last_round_fallback company=%s original_error=%s",
                    company_name,
                    exc,
                )

        # Fallback 2: direct_valuation when MODERATE/STRONG evidence exists
        if fallback_req is None and (
            ev_pkg.get("consensus_strength") in ("STRONG", "MODERATE")
            and assembled_request is not None
            and assembled_request.get("methodology") != "direct_valuation"
        ):
            fallback_req = _build_direct_valuation_request(assembled_request, ev_pkg)

        if fallback_req:
            try:
                logger.info(
                    "direct_valuation_fallback company=%s original_error=%s",
                    company_name,
                    exc,
                )
                result = engine.evaluate_from_dict(fallback_req)
                result_dict = result.to_dict()
                result_dict["research_metadata"] = research.research_metadata
                orig_method = (
                    assembled_request.get("methodology") if assembled_request else "unknown"
                )
                result_dict["research_metadata"]["fallback_note"] = (
                    f"Fell back to {fallback_req.get('methodology')}; "
                    f"original {orig_method} error: {exc}"
                )
                try:
                    store = request.app.state.store
                    store.save(result_dict)
                except Exception as save_exc:
                    logger.warning("store_save_failed error=%s", save_exc)
                    result_dict["_store_warning"] = f"Result not persisted: {save_exc}"
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "research_ok_via_fallback company=%s request_id=%s elapsed_ms=%.1f",
                    result.company_name,
                    result.request_id,
                    elapsed_ms,
                )
                return JSONResponse(result_dict, status_code=200)
            except Exception as fallback_exc:
                logger.warning("direct_valuation_fallback_failed error=%s", fallback_exc)

        return JSONResponse(
            {
                "error": str(exc),
                "research_metadata": research.research_metadata,
            },
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("research_unhandled_error error=%s", exc)
        return JSONResponse({"error": sanitize_error(exc)}, status_code=500)


def _build_direct_valuation_request(
    original_request: dict[str, Any],
    evidence_package: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a direct_valuation request from the evidence_package dict.

    Returns None if there are insufficient evidence signals to proceed.
    """
    evidence_signals = evidence_package.get("evidence", [])
    if not evidence_signals:
        return None
    consensus_strength = evidence_package.get("consensus_strength", "NONE")
    if consensus_strength == "NONE":
        return None
    has_secondary = any(
        e.get("evidence_type") in ("secondary_market", "post_money_fresh") for e in evidence_signals
    )
    return {
        "company_name": original_request.get("company_name", ""),
        "methodology": "direct_valuation",
        "as_of_date": original_request.get("as_of_date", ""),
        "inputs": {
            "evidence_signals": evidence_signals[:8],
            "consensus_strength": consensus_strength,
            "private_company_discount_pct": get_discount_default(
                "direct_valuation",
                has_secondary_evidence=has_secondary,
            ),
        },
    }
