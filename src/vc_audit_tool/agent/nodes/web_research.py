"""Node 3: web_research -- multi-query DuckDuckGo search + evidence extraction.

Adaptive loop: after the initial pass the node checks which minimum fields are
still missing and runs targeted follow-up queries until all fields are found or
the iteration cap (_MAX_SEARCH_ROUNDS) is reached.

Minimum required fields (at least one set must be complete):
  A) post_money_valuation + round_date  →  last_round_market_adjusted
  B) post_money_valuation + revenue     →  last_round_multiple_ratchet
  C) evidence_signals (2+ items)        →  direct_valuation
  D) revenue_ltm                        →  comparable_companies (last resort)

The node also generates a compact company_description string via the LLM so
that the comps ranker can do a semantic Pinecone search against it.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any

from vc_audit_tool.agent.llm_adapter import (
    HumanMessage,
    SystemMessage,
    _get_llm,
    _llm_extract_structured,
)
from vc_audit_tool.agent.state import ResearchState
from vc_audit_tool.data_sources.evidence_collector import (
    EvidencePackage,
    ValuationEvidence,
    extract_evidence,
)

logger = logging.getLogger(__name__)

# ── Optional search backend ───────────────────────────────────────────────

_DDGS_BACKEND = ""
DDGS: Any = None
try:
    from ddgs import DDGS as DDGSNew

    DDGS = DDGSNew
    _DDGS_BACKEND = "ddgs"
except ImportError:
    try:
        from duckduckgo_search import DDGS as DDGSOld

        DDGS = DDGSOld
        _DDGS_BACKEND = "duckduckgo_search"
    except ImportError:
        DDGS = None

# ── Search query templates ────────────────────────────────────────────────

# Initial broad pass — covers valuation, funding, revenue, and sector context.
_SEARCH_QUERIES = [
    '"{name}" valuation 2024 OR 2025 billion',
    '"{name}" funding round raised billion 2024 OR 2025',
    '"{name}" secondary market valuation tender offer',
    '"{name}" post-money valuation series',
    '"{name}" annual revenue ARR 2024 OR 2025',
    '"{name}" company worth how much valued',
    '"{name}" acquisition merger latest news',
]

# Targeted follow-up query sets, keyed by what's missing.
_TARGETED_QUERIES: dict[str, list[str]] = {
    "post_money": [
        '"{name}" post-money valuation fundraise',
        '"{name}" series A B C D E funding valuation',
        '"{name}" last round valuation investors',
        '"{name}" unicorn valuation billion startup',
    ],
    "round_date": [
        '"{name}" funding date closed announced 2022 OR 2023 OR 2024',
        '"{name}" series closed round date investment',
        '"{name}" raised funding when date',
    ],
    "revenue": [
        '"{name}" revenue ARR annual recurring 2023 OR 2024',
        '"{name}" sales revenue run rate millions billions',
        '"{name}" financials revenue growth',
    ],
    "description": [
        '"{name}" what does company do business model',
        '"{name}" products services sector industry',
        '"{name}" overview company profile',
    ],
}

# Stop after this many adaptive rounds to prevent unbounded searches.
_MAX_SEARCH_ROUNDS = 3


# ── Coverage check ───────────────────────────────────────────────────────


def _missing_fields(
    pkg: EvidencePackage,
    web_facts: dict[str, Any],
) -> list[str]:
    """Return list of field keys that are still missing after a search pass.

    Returns an empty list when at least one complete methodology set is found.
    """
    missing: list[str] = []

    has_post_money = bool(web_facts.get("last_post_money_valuation"))
    has_round_date = bool(web_facts.get("last_round_date"))
    has_revenue = bool(web_facts.get("revenue_ltm"))
    has_evidence = len(pkg.evidence) >= 2
    has_description = bool(web_facts.get("company_description"))

    # Set A: best path
    if has_post_money and has_round_date:
        missing_now = []
        if not has_description:
            missing_now.append("description")
        return missing_now  # Good enough — description is nice-to-have

    # Set B: ratchet
    if has_post_money and has_revenue:
        missing_now = []
        if not has_description:
            missing_now.append("description")
        return missing_now

    # Set C: direct
    if has_evidence:
        missing_now = []
        if not has_description:
            missing_now.append("description")
        return missing_now

    # Set D: comps — but we still want more
    if not has_post_money:
        missing.append("post_money")
    if not has_round_date:
        missing.append("round_date")
    if not has_revenue:
        missing.append("revenue")
    if not has_description:
        missing.append("description")
    return missing


# ── Node implementation ──────────────────────────────────────────────────


def _web_research_node(state: ResearchState) -> ResearchState:
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        # Return empty dict (no-op update) — safe for parallel fan-out.
        return {}

    as_of_raw = state.get("as_of_date", "")
    try:
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else date.today()
    except ValueError:
        as_of = date.today()

    # ── Round 1: initial broad search ────────────────────────────────────
    raw_snippets, source_titles, source_dates = _ddg_search(name)

    llm, model_label = _get_llm()
    llm_facts: dict[str, Any] = {}
    if raw_snippets and llm is not None and HumanMessage is not None:
        llm_facts = _llm_extract_structured(llm, model_label, name, raw_snippets)

    pkg: EvidencePackage = extract_evidence(
        raw_snippets, source_titles, name, as_of, source_dates=source_dates
    )
    if llm_facts:
        _merge_llm_into_package(llm_facts, pkg, as_of)

    web_facts = _build_web_facts(pkg, llm_facts, raw_snippets)

    # ── Adaptive follow-up rounds ─────────────────────────────────────────
    for round_num in range(2, _MAX_SEARCH_ROUNDS + 1):
        still_missing = _missing_fields(pkg, web_facts)
        if not still_missing:
            break

        logger.info("web_research: round %d — still missing: %s", round_num, still_missing)

        new_snippets: list[str] = []
        new_titles: list[str] = []
        new_dates: list[str | None] = []

        for field in still_missing:
            queries = _TARGETED_QUERIES.get(field, [])
            for q_template in queries:
                q = q_template.format(name=name)
                s, t, d = _ddg_search_queries(name, [q])
                new_snippets.extend(s)
                new_titles.extend(t)
                new_dates.extend(d)

        if not new_snippets:
            break

        raw_snippets.extend(new_snippets)
        for title in new_titles:
            if title not in source_titles:
                source_titles.append(title)
        source_dates.extend(new_dates)

        # Re-extract evidence from the expanded corpus
        pkg = extract_evidence(raw_snippets, source_titles, name, as_of, source_dates=source_dates)
        if llm is not None and HumanMessage is not None:
            new_llm = _llm_extract_structured(llm, model_label, name, new_snippets)
            if new_llm:
                llm_facts.update({k: v for k, v in new_llm.items() if v})
                _merge_llm_into_package(llm_facts, pkg, as_of)

        web_facts = _build_web_facts(pkg, llm_facts, raw_snippets)

    # ── Generate company description if still missing ─────────────────────
    if not web_facts.get("company_description") and raw_snippets and llm is not None:
        description = _generate_company_description(llm, name, raw_snippets[:10])
        if description:
            web_facts["company_description"] = description

    logger.info(
        "web_research: done company=%s snippets=%d evidence=%d "
        "has_post_money=%s has_round_date=%s has_revenue=%s",
        name,
        len(raw_snippets),
        len(pkg.evidence),
        bool(web_facts.get("last_post_money_valuation")),
        bool(web_facts.get("last_round_date")),
        bool(web_facts.get("revenue_ltm")),
    )

    # Stamp source_titles into web_facts now that the full list is known.
    web_facts["sources"] = source_titles[:10]

    # Return only the keys this node produces so parallel siblings can merge cleanly.
    return {
        "raw_snippets": raw_snippets,
        "source_titles": source_titles,
        "evidence_package": pkg.to_dict(),
        "web_facts": web_facts,
    }


# ── Helpers ───────────────────────────────────────────────────────────────


def _build_web_facts(
    pkg: EvidencePackage,
    llm_facts: dict[str, Any],
    raw_snippets: list[str],
) -> dict[str, Any]:
    """Build the web_facts dict from current evidence package + LLM facts."""
    inferred_last_round_amount = _extract_last_round_amount_raised(raw_snippets)
    inferred_last_post_money = _extract_last_post_money_valuation(
        raw_snippets
    ) or _extract_best_post_money_from_package(pkg)

    best = pkg.best_evidence
    return {
        "revenue_ltm": pkg.best_revenue,
        "last_round_date": pkg.best_round_date or llm_facts.get("last_round_date"),
        "last_round_amount_raised": (
            llm_facts.get("last_round_amount_raised") or inferred_last_round_amount
        ),
        "last_post_money_valuation": (
            llm_facts.get("last_post_money_valuation")
            or inferred_last_post_money
            or (
                best.amount_usd
                if best and best.evidence_type in ("post_money_fresh", "post_money_stale")
                else None
            )
        ),
        "company_description": llm_facts.get("company_description"),
        "sources": [],  # populated by caller who has source_titles
        "llm_model_version": llm_facts.get("_model_label"),
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
) -> tuple[list[str], list[str], list[str | None]]:
    return _ddg_search_queries(company_name, _SEARCH_QUERIES, max_results_per_query)


def _ddg_search_queries(
    company_name: str,
    query_templates: list[str],
    max_results_per_query: int = 5,
) -> tuple[list[str], list[str], list[str | None]]:
    if os.getenv("VC_AUDIT_DISABLE_WEB_SEARCH", "").lower() in {"1", "true", "yes"}:
        logger.info("web_research: web search disabled by VC_AUDIT_DISABLE_WEB_SEARCH")
        return [], [], []

    if DDGS is None:
        logger.info("web_research: duckduckgo not installed — skipping")
        return [], [], []

    raw_snippets: list[str] = []
    source_titles: list[str] = []
    source_dates: list[str | None] = []
    try:
        with DDGS() as ddgs:
            for q_template in query_templates:
                q = q_template.format(name=company_name)
                results = list(ddgs.text(q, max_results=max_results_per_query))
                for r in results:
                    title = str(r.get("title") or r.get("heading") or "").strip()
                    body = str(
                        r.get("body") or r.get("snippet") or r.get("description") or ""
                    ).strip()
                    if not title and not body:
                        continue
                    snippet = f"{title} -- {body}"
                    raw_snippets.append(snippet)
                    if title and title not in source_titles:
                        source_titles.append(title)
                    result_date = str(r.get("date") or "").strip() or None
                    source_dates.append(result_date)
    except Exception as exc:
        logger.warning("web_research: DuckDuckGo search failed: %s", exc)

    return raw_snippets, source_titles, source_dates


def _generate_company_description(
    llm: Any,
    company_name: str,
    snippets: list[str],
) -> str | None:
    """Ask the LLM to produce a compact 1-2 sentence business description."""
    if HumanMessage is None or SystemMessage is None:
        return None
    context = "\n".join(snippets[:8])
    try:
        messages = [
            SystemMessage(
                content=(
                    "You are a financial analyst. Given web search snippets about a company, "
                    "write a single concise sentence (max 30 words) describing the company's "
                    "core business, target market, and product/service category. "
                    "Example: 'B2B SaaS platform for mid-market sales automation targeting SMBs.' "
                    "Return only the description sentence, nothing else."
                )
            ),
            HumanMessage(
                content=(
                    f"Company: {company_name}\n\nWeb snippets:\n{context}\n\n"
                    "Write a compact business description:"
                )
            ),
        ]
        response = llm.invoke(messages)
        text = (response.content if hasattr(response, "content") else str(response)).strip()
        # Sanity: must be non-empty and not too long
        if 10 < len(text) < 300:
            return text
    except Exception as exc:
        logger.warning("web_research: description generation failed: %s", exc)
    return None


def _merge_llm_into_package(
    llm_facts: dict[str, Any],
    pkg: EvidencePackage,
    as_of: date,
) -> None:
    """If LLM found a post-money valuation not already in pkg, add it."""
    from vc_audit_tool.data_sources.evidence_patterns import _classify_evidence_type

    pm = llm_facts.get("last_post_money_valuation")
    if pm and isinstance(pm, (int, float)) and pm > 1_000_000:
        date_str = llm_facts.get("last_round_date")
        ev_type, conf, src_tier = _classify_evidence_type(
            "round", pm, "", date_str, as_of, source_title="LLM extraction"
        )
        if not any(abs(e.amount_usd - pm) / max(pm, 1) < 0.15 for e in pkg.evidence):
            pkg.evidence.append(
                ValuationEvidence(
                    amount_usd=float(pm),
                    evidence_type=ev_type,
                    source_snippet=f"LLM-extracted: ${pm / 1e9:.2f}B post-money",
                    date_mentioned=str(date_str) if date_str else None,
                    source_title="LLM extraction",
                    confidence=conf * 0.9,
                    source_reliability_tier=src_tier,
                )
            )

    rev = llm_facts.get("revenue_ltm")
    if rev and isinstance(rev, (int, float)) and rev > 0 and rev not in pkg.revenue_signals:
        pkg.revenue_signals.append(float(rev))


def _extract_best_post_money_from_package(pkg: EvidencePackage) -> float | None:
    valuations = [
        e.amount_usd
        for e in pkg.evidence
        if e.evidence_type in {"post_money_fresh", "post_money_stale", "secondary_market"}
    ]
    return max(valuations) if valuations else None


def _extract_last_post_money_valuation(snippets: list[str]) -> float | None:
    pattern = re.compile(
        r"\$([\d,.]+)\s*(trillion|billion|million|T|B|M)\b[^.$]{0,60}\b"
        r"(valuation|valued|worth|post-money)\b",
        flags=re.IGNORECASE,
    )
    best_value: float | None = None
    for snippet in snippets:
        for match in pattern.finditer(snippet):
            num = float(match.group(1).replace(",", ""))
            unit = match.group(2).lower()
            if unit in {"trillion", "t"}:
                value = num * 1_000_000_000_000
            elif unit in {"billion", "b"}:
                value = num * 1_000_000_000
            else:
                value = num * 1_000_000
            value = float(int(round(value)))
            if best_value is None or value > best_value:
                best_value = value
    return best_value


def _extract_last_round_amount_raised(snippets: list[str]) -> float | None:
    for snippet in snippets:
        match = re.search(
            r"\braised\s+\$([\d,.]+)\s*(trillion|billion|million|T|B|M)\b",
            snippet,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        num = float(match.group(1).replace(",", ""))
        unit = match.group(2).lower()
        if unit in {"trillion", "t"}:
            return num * 1_000_000_000_000
        if unit in {"billion", "b"}:
            return num * 1_000_000_000
        return num * 1_000_000
    return None


__all__ = [
    "_DDGS_BACKEND",
    "_MAX_SEARCH_ROUNDS",
    "_SEARCH_QUERIES",
    "_TARGETED_QUERIES",
    "DDGS",
    "_build_web_facts",
    "_ddg_search",
    "_ddg_search_queries",
    "_generate_company_description",
    "_merge_llm_into_package",
    "_missing_fields",
    "_extract_best_post_money_from_package",
    "_extract_last_post_money_valuation",
    "_extract_last_round_amount_raised",
    "_web_research_node",
]
