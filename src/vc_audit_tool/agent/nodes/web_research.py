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

from vc_audit_tool.agent.cost_tracker import CostTracker
from vc_audit_tool.agent.llm_adapter import (
    HumanMessage,
    SystemMessage,
    _get_llm,
    _llm_extract_structured,
    _llm_judge_valuation,
    _needs_judgment,
)
from vc_audit_tool.agent.state import ResearchState
from vc_audit_tool.data_sources.evidence_collector import (
    EvidencePackage,
    ValuationEvidence,
    extract_evidence,
)

logger = logging.getLogger(__name__)

# ── Per-process search cache ──────────────────────────────────────────────
# Keyed by (company_name, iso_date, canonical_query_string).
# Prevents non-determinism when the same company is researched multiple times
# in one server session: DDGS results can vary per call; caching ensures the
# same snippets feed the evidence extractor on every run.
_SEARCH_CACHE: dict[str, tuple[list[str], list[str], list[str | None]]] = {}

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


def _make_queries(as_of: date | None = None) -> list[str]:
    """Return search query templates with current year range substituted.

    Replaces the hard-coded "2024 OR 2025" token with a dynamic range based on
    the valuation as_of date so that queries remain accurate across years.
    """
    aod = as_of or date.today()
    year_range = f"{aod.year - 1} OR {aod.year}"
    return [q.replace("2024 OR 2025", year_range) for q in _SEARCH_QUERIES]


def _make_targeted_queries(name: str, as_of: date | None = None) -> dict[str, list[str]]:
    """Return targeted follow-up queries with current year range applied."""
    aod = as_of or date.today()
    year_range = f"{aod.year - 1} OR {aod.year}"
    old_round_date_range = "2022 OR 2023 OR 2024"
    new_round_date_range = f"{aod.year - 2} OR {aod.year - 1} OR {aod.year}"
    result: dict[str, list[str]] = {}
    for key, templates in _TARGETED_QUERIES.items():
        updated = [
            t.replace("2024 OR 2025", year_range).replace(
                old_round_date_range, new_round_date_range
            )
            for t in templates
        ]
        result[key] = [q.format(name=name) for q in updated]
    return result


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
    raw_snippets, source_titles, source_dates = _ddg_search(name, as_of=as_of)

    # Route to lite model for small batches; full model for large ones.
    llm, model_label, provider_cfg = _get_llm(snippet_count=len(raw_snippets))
    cost_tracker = CostTracker()
    llm_facts: dict[str, Any] = {}
    if raw_snippets and llm is not None and HumanMessage is not None:
        llm_facts = _llm_extract_structured(
            llm,
            model_label,
            name,
            raw_snippets,
            tracker=cost_tracker,
            provider_cfg=provider_cfg,
            as_of_date=as_of.isoformat(),
            sector_hint=state.get("inferred_sector", ""),
        )

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

        targeted = _make_targeted_queries(name, as_of)
        for field in still_missing:
            for q in targeted.get(field, []):
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
            new_llm = _llm_extract_structured(
                llm,
                model_label,
                name,
                new_snippets,
                tracker=cost_tracker,
                provider_cfg=provider_cfg,
                as_of_date=as_of.isoformat(),
                sector_hint=state.get("inferred_sector", ""),
            )
            if new_llm:
                llm_facts.update({k: v for k, v in new_llm.items() if v})
                _merge_llm_into_package(llm_facts, pkg, as_of)

        web_facts = _build_web_facts(pkg, llm_facts, raw_snippets)

    # ── Generate company description if still missing ─────────────────────
    if not web_facts.get("company_description") and raw_snippets and llm is not None:
        description = _generate_company_description(llm, name, raw_snippets[:10])
        if description:
            web_facts["company_description"] = description

    # ── LLM judge: resolve conflicting valuation signals ──────────────────
    # When the evidence package contains 2+ candidates that differ by >20%
    # (e.g. $1B raise amount vs $5B post-money), the regex layer may not
    # have fully resolved the ambiguity.  Ask the LLM to look at all
    # candidates + raw context and confirm which is the real post-money val.
    _POINT_IN_TIME = {"post_money_fresh", "post_money_stale", "secondary_market"}
    judge_candidates = [e for e in pkg.evidence if e.evidence_type in _POINT_IN_TIME][:5]
    if llm is not None and raw_snippets and _needs_judgment(judge_candidates):
        judge_snippets = _relevant_snippets_for_judge(judge_candidates, raw_snippets)
        judged_val, judge_reason = _llm_judge_valuation(
            llm,
            model_label,
            name,
            judge_candidates,
            judge_snippets,
            tracker=cost_tracker,
            provider_cfg=provider_cfg,
        )
        if judged_val is not None:
            prev = web_facts.get("last_post_money_valuation")
            prev_str = f"${prev / 1e9:.2f}B" if prev else "none"
            logger.info(
                "web_research: llm_judge override post_money=$%.2fB (was %s)",
                judged_val / 1e9,
                prev_str,
            )
            web_facts["last_post_money_valuation"] = judged_val
            if judge_reason:
                web_facts["llm_judge_reason"] = judge_reason

    logger.info(
        "web_research: done company=%s snippets=%d evidence=%d "
        "has_post_money=%s has_round_date=%s has_revenue=%s "
        "llm_calls=%d total_cost_usd=%.6f",
        name,
        len(raw_snippets),
        len(pkg.evidence),
        bool(web_facts.get("last_post_money_valuation")),
        bool(web_facts.get("last_round_date")),
        bool(web_facts.get("revenue_ltm")),
        cost_tracker.call_count,
        cost_tracker.total_cost,
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
    """Build the web_facts dict from current evidence package + LLM facts.

    Post-money valuation precedence (highest wins):
      1. Highest-confidence post_money_fresh/stale from evidence package
         — regex-extracted from snippets, suppresses raise-amount confusion
      2. LLM-extracted value (now prompted to distinguish raise vs valuation)
      3. Regex fallback from _extract_last_post_money_valuation
      4. Evidence package best_post_money
    When evidence package has a high-confidence fresh value significantly
    above the LLM value, the evidence package wins — this handles cases
    like "raised $1B at a $5B valuation" where the LLM returns $1B.
    """
    inferred_last_round_amount = _extract_last_round_amount_raised(raw_snippets)
    inferred_last_post_money = _extract_last_post_money_valuation(
        raw_snippets
    ) or _extract_best_post_money_from_package(pkg)

    # Build candidate post-money values from evidence package (raise-safe).
    _POINT_IN_TIME = {"post_money_fresh", "post_money_stale", "secondary_market"}
    pkg_post_money_candidates = [e for e in pkg.evidence if e.evidence_type in _POINT_IN_TIME]

    llm_post_money: float | None = llm_facts.get("last_post_money_valuation")

    # Select post-money by recency rather than magnitude.
    # _select_valuation_by_recency prefers the candidate with the more recent
    # round date; falls back to evidence-package value (raise-suppressed regex)
    # when dates are unavailable.  This correctly handles down-rounds where
    # the older higher valuation should NOT win.
    chosen_post_money: float | None = (
        _select_valuation_by_recency(
            pkg_post_money_candidates,
            llm_post_money,
            llm_facts.get("last_round_date"),
        )
        or inferred_last_post_money
    )

    best = pkg.best_evidence
    return {
        "revenue_ltm": pkg.best_revenue,
        "revenue_at_last_round": llm_facts.get("revenue_at_last_round"),
        "last_round_date": _most_recent_date(pkg.best_round_date, llm_facts.get("last_round_date")),
        "last_round_amount_raised": (
            llm_facts.get("last_round_amount_raised") or inferred_last_round_amount
        ),
        "last_post_money_valuation": chosen_post_money
        or (
            best.amount_usd
            if best and best.evidence_type in ("post_money_fresh", "post_money_stale")
            else None
        ),
        "company_description": llm_facts.get("company_description"),
        "llm_inferred_sector": llm_facts.get("sector"),
        "sources": [],  # populated by caller who has source_titles
        "llm_model_version": llm_facts.get("_model_label"),
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
    as_of: date | None = None,
) -> tuple[list[str], list[str], list[str | None]]:
    queries = _make_queries(as_of)
    return _ddg_search_queries(company_name, queries, max_results_per_query)


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

    # Return cached results to prevent non-determinism across repeated runs.
    # Cache key includes the query text (which embeds the year range), so
    # year boundaries correctly invalidate old cache entries.
    _today = date.today().isoformat()
    _cache_key = f"{company_name}|{_today}|{'|'.join(query_templates)}"
    if _cache_key in _SEARCH_CACHE:
        logger.debug("web_research: cache hit for %r", company_name)
        return _SEARCH_CACHE[_cache_key]

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

    result: tuple[list[str], list[str], list[str | None]] = (
        raw_snippets,
        source_titles,
        source_dates,
    )
    _SEARCH_CACHE[_cache_key] = result
    return result


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


def _relevant_snippets_for_judge(
    candidates: list[ValuationEvidence],
    raw_snippets: list[str],
    max_snippets: int = 8,
) -> list[str]:
    """Return snippets most likely to contain evidence about candidate values.

    Scores each snippet by how many candidate amount strings it contains.
    Snippets that mention at least one candidate amount are preferred over
    generic snippets — this prevents the judge from seeing only unrelated
    articles that happened to appear first in search results.
    """
    needles: list[str] = []
    for ev in candidates:
        b = ev.amount_usd / 1e9
        needles += [f"{b:.0f}B", f"{b:.1f}B", f"{b:.0f} billion", f"${b:.0f}B"]

    scored: list[tuple[int, str]] = []
    for snippet in raw_snippets:
        sl = snippet.lower()
        score = sum(1 for n in needles if n.lower() in sl)
        if score > 0:
            scored.append((score, snippet))
    scored.sort(key=lambda x: x[0], reverse=True)
    result = [s for _, s in scored[:max_snippets]]
    # Always include at least the first 3 snippets as fallback context
    for s in raw_snippets[:3]:
        if len(result) >= max_snippets:
            break
        if s not in result:
            result.append(s)
    return result[:max_snippets]


def _select_valuation_by_recency(
    pkg_candidates: list[ValuationEvidence],
    llm_post_money: float | None,
    llm_round_date: str | None,
) -> float | None:
    """Return the valuation amount whose associated date is most recent.

    Priority:
    1. If only one source has a value, use it.
    2. If both have a value and a round date, prefer the more recent one.
    3. If dates are missing or equal, prefer the evidence-package value
       (regex patterns are raise-suppressed, more reliable than a raw LLM number
       when both lack date context).

    This replaces the previous ``max(pkg_val, llm_val)`` call which incorrectly
    preferred high-but-stale valuations over more-recent lower ones (down-rounds).
    """
    from vc_audit_tool.data_sources.evidence_collector import _date_sortable

    pkg_top = max(pkg_candidates, key=lambda e: e.confidence) if pkg_candidates else None
    pkg_val: float | None = None
    pkg_date: str | None = None
    if pkg_top and pkg_top.confidence >= 0.60:
        pkg_val = pkg_top.amount_usd
        pkg_date = pkg_top.date_mentioned

    if pkg_val and llm_post_money:
        pkg_sortable = _date_sortable(pkg_date) if pkg_date else ""
        llm_sortable = _date_sortable(llm_round_date) if llm_round_date else ""
        if pkg_sortable and llm_sortable:
            # Both have dates — pick the more recent one
            return pkg_val if pkg_sortable >= llm_sortable else llm_post_money
        # One or both lack dates — prefer evidence package (raise-safe regex)
        return pkg_val

    return pkg_val or llm_post_money


def _most_recent_date(a: str | None, b: str | None) -> str | None:
    """Return whichever of *a* or *b* represents the more recent date.

    Falls back to whichever is non-None; returns None if both are None.
    Uses a simple string sort on the normalised ISO form (YYYY-MM-DD prefix).
    """
    if not a:
        return b
    if not b:
        return a
    # Normalise to YYYY-MM-DD for comparison; fall back to lexicographic order.
    from vc_audit_tool.data_sources.evidence_collector import _date_sortable

    return a if _date_sortable(a) >= _date_sortable(b) else b


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
    "_make_queries",
    "_make_targeted_queries",
    "_merge_llm_into_package",
    "_relevant_snippets_for_judge",
    "_missing_fields",
    "_extract_best_post_money_from_package",
    "_extract_last_post_money_valuation",
    "_extract_last_round_amount_raised",
    "_most_recent_date",
    "_select_valuation_by_recency",
    "_web_research_node",
]
