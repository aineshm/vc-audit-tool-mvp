"""Node 3: web_research -- multi-query DuckDuckGo search + evidence extraction."""

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

_SEARCH_QUERIES = [
    '"{name}" valuation 2024 OR 2025 billion',
    '"{name}" funding round raised billion 2024 OR 2025',
    '"{name}" secondary market valuation tender offer',
    '"{name}" post-money valuation series',
    '"{name}" annual revenue ARR 2024 OR 2025',
    '"{name}" company worth how much valued',
    '"{name}" acquisition merger latest news',
]


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

    raw_snippets, source_titles, source_dates = _ddg_search(name)

    llm_facts: dict[str, Any] = {}
    if raw_snippets:
        llm, model_label = _get_llm()
        if llm is not None and HumanMessage is not None and SystemMessage is not None:
            llm_facts = _llm_extract_structured(llm, model_label, name, raw_snippets)

    pkg: EvidencePackage = extract_evidence(
        raw_snippets, source_titles, name, as_of, source_dates=source_dates
    )

    if llm_facts:
        _merge_llm_into_package(llm_facts, pkg, as_of)

    inferred_last_round_amount = _extract_last_round_amount_raised(raw_snippets)
    inferred_last_post_money = _extract_last_post_money_valuation(
        raw_snippets
    ) or _extract_best_post_money_from_package(pkg)

    best = pkg.best_evidence
    web_facts: dict[str, Any] = {
        "revenue_ltm": pkg.best_revenue,
        "last_round_date": pkg.best_round_date or (llm_facts.get("last_round_date")),
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
        "sources": source_titles[:10],
        "llm_model_version": llm_facts.get("_model_label"),
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Return only the keys this node produces so parallel siblings can merge cleanly.
    return {
        "raw_snippets": raw_snippets,
        "source_titles": source_titles,
        "evidence_package": pkg.to_dict(),
        "web_facts": web_facts,
    }


# ── Helpers ───────────────────────────────────────────────────────────────


def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
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
            for q_template in _SEARCH_QUERIES:
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
        ev_type, conf = _classify_evidence_type("round", pm, "", date_str, as_of)
        if not any(abs(e.amount_usd - pm) / max(pm, 1) < 0.15 for e in pkg.evidence):
            pkg.evidence.append(
                ValuationEvidence(
                    amount_usd=float(pm),
                    evidence_type=ev_type,
                    source_snippet=f"LLM-extracted: ${pm / 1e9:.2f}B post-money",
                    date_mentioned=str(date_str) if date_str else None,
                    source_title="LLM extraction",
                    confidence=conf * 0.9,
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
    "_SEARCH_QUERIES",
    "DDGS",
    "_ddg_search",
    "_merge_llm_into_package",
    "_extract_best_post_money_from_package",
    "_extract_last_post_money_valuation",
    "_extract_last_round_amount_raised",
    "_web_research_node",
]
