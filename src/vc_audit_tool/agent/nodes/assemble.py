"""Node 5: assemble -- evidence-first methodology selection and request building."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from vc_audit_tool.agent.state import ResearchState
from vc_audit_tool.data_sources.evidence_collector import (
    EvidencePackage,
    extract_evidence,
)
from vc_audit_tool.methodologies._discount_config import get_discount_default

logger = logging.getLogger(__name__)


# ── Node implementation ──────────────────────────────────────────────────


def _assemble_node(state: ResearchState) -> ResearchState:
    if state.get("error"):
        return state

    name = state.get("normalised_name", state.get("company_name", ""))
    as_of_date = state.get("as_of_date", date.today().isoformat())
    requested_methodology = state.get("methodology", "")
    web_facts = state.get("web_facts", {})
    sector = web_facts.get("llm_inferred_sector") or state.get(
        "inferred_sector", "enterprise_software"
    )
    form_d_rounds = state.get("form_d_rounds", [])
    evidence_pkg_dict = state.get("evidence_package", {})
    # Prefer explicit caller hint; fall back to LLM-generated description from web_facts.
    description_hint = (state.get("description_hint") or "").strip()
    if not description_hint:
        description_hint = (web_facts.get("company_description") or "").strip()

    research_metadata: dict[str, Any] = {
        "sources_consulted": [],
        "extracted_facts": {},
        "evidence_package": evidence_pkg_dict,
        "research_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if form_d_rounds:
        research_metadata["sources_consulted"].append("SEC EDGAR Form D")
    if state.get("government_contracts"):
        research_metadata["sources_consulted"].append("USASpending.gov")
    if web_facts.get("llm_model_version"):
        research_metadata["sources_consulted"].append(f"LLM ({web_facts['llm_model_version']})")
        research_metadata["llm_model_version"] = web_facts["llm_model_version"]
    if state.get("raw_snippets"):
        research_metadata["sources_consulted"].append(
            f"Web search ({len(state.get('raw_snippets', []))} snippets)"
        )

    # Build a deduplicated, readable source list from all search result titles.
    all_source_titles: list[str] = state.get("source_titles", [])
    web_sources = [t for t in all_source_titles if t][:20]

    # Include evidence signal summaries (amount + type + date + source title).
    evidence_list: list[dict[str, Any]] = evidence_pkg_dict.get("evidence", [])
    evidence_summary = [
        {
            "amount_usd": e.get("amount_usd"),
            "evidence_type": e.get("evidence_type"),
            "confidence": round(e.get("confidence", 0), 3),
            "date_mentioned": e.get("date_mentioned"),
            "source_title": e.get("source_title"),
            "source_reliability_tier": e.get("source_reliability_tier"),
        }
        for e in evidence_list[:10]
    ]

    research_metadata["extracted_facts"] = {
        "form_d_rounds_found": len(form_d_rounds),
        "government_contracts_found": len(state.get("government_contracts", [])),
        "government_contracts_usd": state.get("government_contracts_usd"),
        "evidence_count": evidence_pkg_dict.get("evidence_count", 0),
        "consensus_strength": evidence_pkg_dict.get("consensus_strength", "NONE"),
        "consensus_valuation_usd": evidence_pkg_dict.get("consensus_valuation"),
        "web_sources": web_sources,
        "evidence_summary": evidence_summary,
        "company_description": web_facts.get("company_description"),
        "last_post_money_valuation": web_facts.get("last_post_money_valuation"),
        "last_round_date": web_facts.get("last_round_date"),
        "revenue_ltm": web_facts.get("revenue_ltm"),
        "llm_judge_reason": web_facts.get("llm_judge_reason"),
    }

    raw_snippets = state.get("raw_snippets", [])
    source_titles = state.get("source_titles", [])
    try:
        as_of = date.fromisoformat(as_of_date[:10])
    except ValueError:
        as_of = date.today()

    # Prefer the already-computed evidence package from _web_research_node.
    # This avoids re-running extract_evidence (expensive) and preserves any
    # LLM-judge overrides that were baked into the package at search time.
    if evidence_pkg_dict and evidence_pkg_dict.get("evidence"):
        pkg = EvidencePackage.from_dict(evidence_pkg_dict)
    elif raw_snippets:
        pkg = extract_evidence(raw_snippets, source_titles, name, as_of)
    else:
        pkg = EvidencePackage(company_name=name)

    allowed_methods = {
        "direct_valuation",
        "last_round_market_adjusted",
        "comparable_companies",
        "last_round_multiple_ratchet",
    }
    if requested_methodology and requested_methodology not in allowed_methods:
        return {
            **state,
            "assembled_request": None,
            "research_metadata": research_metadata,
            "missing_fields": [f"Unsupported methodology: {requested_methodology}"],
            "best_available_methodology": None,
            "missing_for_best_available": [f"Unsupported methodology: {requested_methodology}"],
        }

    chosen_methodology = requested_methodology or pkg.recommended_methodology()

    logger.info(
        "assemble: company=%s chosen_method=%s consensus=%s strength=%s",
        name,
        chosen_methodology,
        f"${pkg.consensus_valuation / 1e9:.1f}B" if pkg.consensus_valuation else "none",
        pkg.consensus_strength,
    )

    assembled: dict[str, Any] | None = None
    missing_fields: list[str] = []

    if chosen_methodology == "last_round_market_adjusted":
        assembled, missing_fields = _assemble_last_round(name, as_of_date, web_facts, form_d_rounds)

    if assembled is None and chosen_methodology in (
        "comparable_companies",
        "last_round_multiple_ratchet",
    ):
        assembled, missing_fields = _assemble_comps(
            name,
            as_of_date,
            chosen_methodology,
            sector,
            web_facts,
            description_hint=description_hint,
        )

    if assembled is None and chosen_methodology == "direct_valuation":
        assembled, missing_fields = _assemble_direct_valuation(name, as_of_date, pkg)

    if requested_methodology:
        best_methodology = assembled.get("methodology") if assembled else requested_methodology
        return {
            **state,
            "assembled_request": assembled,
            "research_metadata": research_metadata,
            "missing_fields": missing_fields,
            "best_available_methodology": best_methodology,
            "missing_for_best_available": missing_fields,
        }

    if assembled is None:
        attempts = []
        # Try round-based and revenue-based methods first; direct_valuation is last resort.
        fallback_methods: list[str] = [
            "last_round_market_adjusted",
            "last_round_multiple_ratchet",
            "comparable_companies",
            "direct_valuation",
        ]
        for method in fallback_methods:
            req, miss = _try_assemble(
                name,
                as_of_date,
                method,
                sector,
                web_facts,
                form_d_rounds,
                pkg,
                description_hint=description_hint,
            )
            attempts.append((method, req, miss))
            if req and not miss:
                assembled = req
                missing_fields = []
                break

        if assembled is None and attempts:
            best_method, _, best_missing = min(attempts, key=lambda x: len(x[2]))
            missing_fields = best_missing
            chosen_methodology = best_method

    best_methodology = assembled.get("methodology") if assembled else chosen_methodology

    return {
        **state,
        "assembled_request": assembled,
        "research_metadata": research_metadata,
        "missing_fields": missing_fields,
        "best_available_methodology": best_methodology,
        "missing_for_best_available": missing_fields,
    }


# ── Assembly helpers ─────────────────────────────────────────────────────


def _assemble_direct_valuation(
    name: str,
    as_of_date: str,
    pkg: EvidencePackage,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not pkg.evidence:
        return None, ["evidence_signals (no valuation signals found in web search)"]

    if pkg.consensus_strength == "NONE":
        return None, ["evidence_signals (insufficient evidence quality)"]

    evidence_signals = [e.to_dict() for e in pkg.evidence[:8]]
    return {
        "company_name": name,
        "methodology": "direct_valuation",
        "as_of_date": as_of_date,
        "inputs": {
            "evidence_signals": evidence_signals,
            "consensus_strength": pkg.consensus_strength,
            "private_company_discount_pct": get_discount_default(
                "direct_valuation",
                has_secondary_evidence=any(
                    e["evidence_type"] in ("secondary_market", "post_money_fresh")
                    for e in evidence_signals
                ),
            ),
        },
    }, []


def _assemble_last_round(
    name: str,
    as_of_date: str,
    web_facts: dict[str, Any],
    form_d_rounds: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    missing: list[str] = []

    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        missing.append("last_post_money_valuation")

    round_date = web_facts.get("last_round_date")
    if not round_date and form_d_rounds:
        round_date = form_d_rounds[0].get("filing_date")
    round_date_iso = _normalize_round_date(round_date)
    if not round_date_iso:
        missing.append("last_round_date")

    if missing:
        return None, missing

    return {
        "company_name": name,
        "methodology": "last_round_market_adjusted",
        "as_of_date": as_of_date,
        "inputs": {
            "last_post_money_valuation": post_money,
            "last_round_date": round_date_iso,
            "public_index": "NASDAQ_COMPOSITE",
        },
    }, []


def _assemble_comps(
    name: str,
    as_of_date: str,
    methodology: str,
    sector: str,
    web_facts: dict[str, Any],
    *,
    description_hint: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    revenue = web_facts.get("revenue_ltm")
    if not revenue:
        return None, ["revenue_ltm"]

    if methodology == "comparable_companies":
        payload: dict[str, Any] = {
            "company_name": name,
            "methodology": "comparable_companies",
            "as_of_date": as_of_date,
            "inputs": {
                "sector": sector,
                "revenue_ltm": revenue,
                "statistic": "median",
                "private_company_discount_pct": get_discount_default("comparable_companies"),
            },
        }
        if description_hint:
            payload["inputs"]["target_description"] = description_hint
        return payload, []

    # last_round_multiple_ratchet
    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        return None, ["last_post_money_valuation", "revenue_at_last_round"]

    # Use historical revenue at round time when available; fall back to current revenue.
    # When both equal current revenue, the ratchet growth rate is 0% (explicit, intentional).
    rev_at_round = web_facts.get("revenue_at_last_round") or revenue

    payload = {
        "company_name": name,
        "methodology": "last_round_multiple_ratchet",
        "as_of_date": as_of_date,
        "inputs": {
            "last_post_money_valuation": post_money,
            "revenue_at_last_round": rev_at_round,
            "current_revenue": revenue,
            "sector": sector,
            "statistic": "median",
            "private_company_discount_pct": get_discount_default("last_round_multiple_ratchet"),
        },
    }
    if description_hint:
        payload["inputs"]["target_description"] = description_hint
    return payload, []


def _try_assemble(
    name: str,
    as_of_date: str,
    method: str,
    sector: str,
    web_facts: dict[str, Any],
    form_d_rounds: list[dict[str, Any]],
    pkg: EvidencePackage,
    *,
    description_hint: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    if method == "direct_valuation":
        return _assemble_direct_valuation(name, as_of_date, pkg)
    if method == "last_round_market_adjusted":
        return _assemble_last_round(name, as_of_date, web_facts, form_d_rounds)
    return _assemble_comps(
        name, as_of_date, method, sector, web_facts, description_hint=description_hint
    )


def _normalize_round_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().strip("()[]{}.,;:!\"' ")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return f"{dt.year:04d}-{dt.month:02d}-01"
        except ValueError:
            pass
    for fmt in ("%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    if raw.isdigit() and len(raw) == 4:
        return f"{raw}-01-01"
    return None


def _has_last_round_data(
    web_facts: dict[str, Any],
    form_d_rounds: list[dict[str, Any]],
) -> bool:
    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        return False
    round_date = web_facts.get("last_round_date")
    if not round_date and form_d_rounds:
        round_date = form_d_rounds[0].get("filing_date")
    return _normalize_round_date(round_date) is not None
