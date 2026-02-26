"""Node 1: parse_company -- normalise name, infer sector / SIC."""

from __future__ import annotations

import logging

from vc_audit_tool.agent.state import _KEYWORD_SECTORS, ResearchState

logger = logging.getLogger(__name__)


def _parse_company_node(state: ResearchState) -> ResearchState:
    name = state.get("company_name", "")
    normalised = name.strip()
    if not normalised:
        # Return only the new keys — LangGraph merges with existing state.
        return {"error": "company_name is required."}  # type: ignore[return-value]

    hint = state.get("description_hint", normalised).lower()
    sector = "enterprise_software"
    sorted_keywords = sorted(
        _KEYWORD_SECTORS.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    for keyword, mapped in sorted_keywords:
        if keyword in hint or keyword in normalised.lower():
            sector = mapped
            break

    from vc_audit_tool.data_sources.edgar_universe import SIC_SECTOR_MAP

    sic = "7372"
    for code, sec in SIC_SECTOR_MAP.items():
        if sec == sector:
            sic = code
            break

    logger.info("parse_company: name=%s sector=%s sic=%s", normalised, sector, sic)
    # Return only the keys this node sets — LangGraph merges with existing state.
    return {  # type: ignore[return-value]
        "normalised_name": normalised,
        "inferred_sector": sector,
        "inferred_sic": sic,
    }
