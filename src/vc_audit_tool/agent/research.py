"""Company research agent -- assembles valuation inputs from public data.

Uses a LangGraph StateGraph with five nodes:

1. **parse_company** -- Normalise name, infer sector / SIC
2. **form_d**        -- Fetch SEC Form D filings for funding-round data
3. **web_research**  -- Multi-query DuckDuckGo search + evidence extraction
4. **contracts**     -- USASpending.gov federal contract lookup
5. **assemble**      -- Evidence-first assembly: picks the best methodology
                       based on evidence quality, not a fixed priority list

Evidence hierarchy (highest wins):
  STRONG/MODERATE direct evidence → direct_valuation methodology
  Fresh post-money round          → last_round_market_adjusted
  Revenue + sector data           → comparable_companies
  Stale round                     → last_round_market_adjusted (with staleness note)

LLM provider priority:
  Google Gemini Flash → OpenAI GPT-4o-mini → Anthropic Haiku → Ollama → regex-only
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, TypedDict

from vc_audit_tool.data_sources.evidence_collector import (
    EvidencePackage,
    ValuationEvidence,
    extract_evidence,
)
from vc_audit_tool.data_sources.form_d import FormDSource
from vc_audit_tool.data_sources.usaspending import USASpendingSource
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# ── Optional search backend ──────────────────────────────────────────────
_DDGS_BACKEND = ""
try:
    from ddgs import DDGS  # type: ignore[import-not-found]
    _DDGS_BACKEND = "ddgs"
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]
        _DDGS_BACKEND = "duckduckgo_search"
    except ImportError:
        DDGS = None  # type: ignore[assignment,misc]

try:
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:
    HumanMessage = None  # type: ignore[assignment,misc]
    SystemMessage = None  # type: ignore[assignment,misc]

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_ollama import ChatOllama
except ImportError:
    ChatOllama = None  # type: ignore[assignment,misc,unused-ignore]

_LG_LOADED = False


def _ensure_langgraph() -> None:
    global _LG_LOADED  # noqa: PLW0603
    if _LG_LOADED:
        return
    try:
        import langgraph  # noqa: F401
        _LG_LOADED = True
    except ImportError as exc:
        raise ImportError(
            "langgraph is required for the research agent. "
            "Install with: pip install langgraph langchain-core"
        ) from exc


# ── Sector inference ────────────────────────────────────────────────────

_KEYWORD_SECTORS: dict[str, str] = {
    "artificial intelligence": "enterprise_software",
    "machine learning": "enterprise_software",
    "cybersecurity": "cybersecurity",
    "semiconductor": "semiconductors",
    "aerospace": "defense_electronics",
    "defense": "defense_electronics",
    "rocket": "defense_electronics",
    "space": "defense_electronics",
    "ecommerce": "ecommerce",
    "e-commerce": "ecommerce",
    "telecom": "telecommunications",
    "fintech": "enterprise_software",
    "saas": "enterprise_software",
    "cloud": "enterprise_software",
    "software": "enterprise_software",
    "ai": "enterprise_software",
    "data": "enterprise_software",
    "chip": "semiconductors",
    "security": "cybersecurity",
}

# ── State ────────────────────────────────────────────────────────────────


class ResearchState(TypedDict, total=False):
    # Input
    company_name: str
    as_of_date: str
    methodology: str
    description_hint: str

    # Intermediate
    normalised_name: str
    inferred_sector: str
    inferred_sic: str

    # Raw data
    form_d_rounds: list[dict[str, Any]]
    government_contracts: list[dict[str, Any]]
    government_contracts_usd: float | None
    raw_snippets: list[str]
    source_titles: list[str]

    # Structured evidence (NEW — replaces unstructured web_facts)
    evidence_package: dict[str, Any]    # EvidencePackage.to_dict()
    web_facts: dict[str, Any]           # kept for backward compat

    # Final output
    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    best_available_methodology: str | None
    missing_for_best_available: list[str]
    error: str | None


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass
class ResearchResult:
    """Return type of :meth:`CompanyResearchAgent.run`."""

    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    best_available_methodology: str | None = None
    missing_for_best_available: list[str] | None = None
    web_facts: dict[str, Any] | None = None
    error: str | None = None
    company_profile: Any | None = None

    @property
    def is_complete(self) -> bool:
        return self.assembled_request is not None and not self.missing_fields


# ── Node 1: parse_company ───────────────────────────────────────────────


def _parse_company_node(state: ResearchState) -> ResearchState:
    name = state.get("company_name", "")
    normalised = name.strip()
    if not normalised:
        return {**state, "error": "company_name is required."}

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
    return {**state, "normalised_name": normalised, "inferred_sector": sector, "inferred_sic": sic}


# ── Node 2: form_d ──────────────────────────────────────────────────────


def _form_d_node(state: ResearchState) -> ResearchState:
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        return state
    try:
        source = FormDSource()
        rounds = source.search(name)
        rounds_dicts = [r.to_dict() for r in rounds]
    except DataSourceError as exc:
        logger.warning("form_d_node error: %s", exc)
        rounds_dicts = []
    return {**state, "form_d_rounds": rounds_dicts}


# ── Node 3: web_research (redesigned) ──────────────────────────────────

# Smarter query set — explicitly targets valuation signals at multiple scales
_SEARCH_QUERIES = [
    '"{name}" valuation 2024 OR 2025 billion',
    '"{name}" funding round raised billion 2024 OR 2025',
    '"{name}" secondary market valuation tender offer',
    '"{name}" post-money valuation series',
    '"{name}" annual revenue ARR 2024 OR 2025',
    '"{name}" company worth how much valued',
    '"{name}" acquisition merger latest news',
]


def _web_research_node(state: ResearchState) -> ResearchState:
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        return state

    as_of_raw = state.get("as_of_date", "")
    try:
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else date.today()
    except ValueError:
        as_of = date.today()

    # Step 1: collect raw snippets
    raw_snippets, source_titles = _ddg_search(name)

    # Step 2: LLM structured extraction (if available) — runs in parallel with regex
    llm_facts: dict[str, Any] = {}
    if raw_snippets:
        llm, model_label = _get_llm()
        if llm is not None and HumanMessage is not None and SystemMessage is not None:
            llm_facts = _llm_extract_structured(llm, model_label, name, raw_snippets)

    # Step 3: Evidence extraction (NEW — the core improvement)
    # This replaces the old flat web_facts dict with a structured evidence package
    pkg: EvidencePackage = extract_evidence(raw_snippets, source_titles, name, as_of)

    # Merge LLM facts into the evidence package if they add new signals
    if llm_facts:
        _merge_llm_into_package(llm_facts, pkg, as_of)

    inferred_last_round_amount = _extract_last_round_amount_raised(raw_snippets)
    inferred_last_post_money = (
        _extract_last_post_money_valuation(raw_snippets)
        or _extract_best_post_money_from_package(pkg)
    )

    # Build backward-compatible web_facts dict from the package
    best = pkg.best_evidence
    web_facts: dict[str, Any] = {
        "revenue_ltm": pkg.best_revenue,
        "last_round_date": pkg.best_round_date or (
            llm_facts.get("last_round_date")
        ),
        "last_round_amount_raised": (
            llm_facts.get("last_round_amount_raised") or inferred_last_round_amount
        ),
        "last_post_money_valuation": (
            llm_facts.get("last_post_money_valuation")
            or inferred_last_post_money
            or (
                best.amount_usd
                if best
                and best.evidence_type in ("post_money_fresh", "post_money_stale")
                else None
            )
        ),
        "company_description": llm_facts.get("company_description"),
        "sources": source_titles[:10],
        "llm_model_version": llm_facts.get("_model_label"),
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        **state,
        "raw_snippets": raw_snippets,
        "source_titles": source_titles,
        "evidence_package": pkg.to_dict(),
        "web_facts": web_facts,
    }


def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
) -> tuple[list[str], list[str]]:
    if DDGS is None:
        logger.info("web_research: duckduckgo not installed — skipping")
        return [], []

    raw_snippets: list[str] = []
    source_titles: list[str] = []
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
    except Exception as exc:
        logger.warning("web_research: DuckDuckGo search failed: %s", exc)

    return raw_snippets, source_titles


def _merge_llm_into_package(
    llm_facts: dict[str, Any],
    pkg: EvidencePackage,
    as_of: date,
) -> None:
    """If LLM found a post-money valuation not already in pkg, add it."""
    from vc_audit_tool.data_sources.evidence_collector import _classify_evidence_type

    pm = llm_facts.get("last_post_money_valuation")
    if pm and isinstance(pm, (int, float)) and pm > 1_000_000:
        date_str = llm_facts.get("last_round_date")
        ev_type, conf = _classify_evidence_type("round", pm, "", date_str, as_of)
        # Only add if not already captured by regex
        if not any(abs(e.amount_usd - pm) / max(pm, 1) < 0.15 for e in pkg.evidence):
            pkg.evidence.append(
                ValuationEvidence(
                    amount_usd=float(pm),
                    evidence_type=ev_type,
                    source_snippet=f"LLM-extracted: ${pm / 1e9:.2f}B post-money",
                    date_mentioned=str(date_str) if date_str else None,
                    source_title="LLM extraction",
                    confidence=conf * 0.9,  # slight haircut for LLM extraction
                )
            )

    # Revenue from LLM
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


# ── LLM helpers ─────────────────────────────────────────────────────────


def _get_llm() -> tuple[Any, str]:
    if os.environ.get("GOOGLE_API_KEY") and ChatGoogleGenerativeAI is not None:
        try:
            model = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")
            llm: Any = ChatGoogleGenerativeAI(model=model, temperature=0, max_output_tokens=1024)
            return llm, f"google/{model}"
        except Exception as exc:
            logger.warning("Google Gemini init failed (%s)", exc)

    if os.environ.get("OPENAI_API_KEY") and ChatOpenAI is not None:
        try:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            llm = ChatOpenAI(model=model, temperature=0)
            return llm, f"openai/{model}"
        except Exception as exc:
            logger.warning("OpenAI init failed (%s)", exc)

    if os.environ.get("ANTHROPIC_API_KEY") and ChatAnthropic is not None:
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
            llm = ChatAnthropic(model=model, temperature=0, max_tokens=1024)
            return llm, f"anthropic/{model}"
        except Exception as exc:
            logger.warning("Anthropic init failed (%s)", exc)

    ollama_model = os.environ.get("OLLAMA_MODEL", "")
    if ollama_model and ChatOllama is not None:
        try:
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            llm = ChatOllama(model=ollama_model, base_url=base_url, temperature=0, num_predict=512)
            return llm, f"ollama/{ollama_model}"
        except Exception as exc:
            logger.warning("Ollama init failed (%s)", exc)

    return None, ""


_LLM_SYSTEM_PROMPT = (
    "You are a financial analyst. From the search snippets, extract ONLY confirmed facts.\n"
    "Return ONLY a JSON object with these keys:\n"
    "- last_post_money_valuation: number or null (USD, most recent valuation)\n"
    "- last_round_date: string or null (ISO date YYYY-MM-DD or 'Month YYYY')\n"
    "- last_round_amount_raised: number or null (USD raised in last round)\n"
    "- revenue_ltm: number or null (USD annual revenue or ARR)\n"
    "- company_description: string or null (1-2 sentences)\n"
    "- valuation_signals: list of objects [{amount_usd, source, date, type}] "
    "  where type is one of: post_money, secondary_market, analyst_estimate\n"
    "NEVER guess. Return null if uncertain. JSON only, no markdown."
)


def _llm_extract_structured(
    llm: Any,
    model_label: str,
    company_name: str,
    snippets: list[str],
) -> dict[str, Any]:
    """Call LLM and return extracted facts dict."""
    try:
        combined = "\n".join(snippets[:40])[:5000]
        response = llm.invoke([
            SystemMessage(content=_LLM_SYSTEM_PROMPT),
            HumanMessage(content=f"Company: {company_name}\n\nSnippets:\n{combined}"),
        ])
        content = response.content
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            parsed: dict[str, Any] = json.loads(text)
            parsed["_model_label"] = model_label
            return parsed
    except Exception as exc:
        logger.warning("LLM extraction failed: %s", exc)
    return {}


# ── Node 4: contracts ───────────────────────────────────────────────────


def _contracts_node(state: ResearchState) -> ResearchState:
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        return state
    try:
        source = USASpendingSource()
        contracts = source.search(name)
        total = sum(c.award_amount for c in contracts) if contracts else None
        contracts_dicts = [c.to_dict() for c in contracts]
    except DataSourceError:
        contracts_dicts = []
        total = None
    return {**state, "government_contracts": contracts_dicts, "government_contracts_usd": total}


# ── Node 5: assemble (evidence-first logic) ─────────────────────────────


def _assemble_node(state: ResearchState) -> ResearchState:
    if state.get("error"):
        return state

    name = state.get("normalised_name", state.get("company_name", ""))
    as_of_date = state.get("as_of_date", date.today().isoformat())
    requested_methodology = state.get("methodology", "")
    sector = state.get("inferred_sector", "enterprise_software")
    web_facts = state.get("web_facts", {})
    form_d_rounds = state.get("form_d_rounds", [])
    evidence_pkg_dict = state.get("evidence_package", {})
    description_hint = (state.get("description_hint") or "").strip()

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
        research_metadata["sources_consulted"].append(
            f"LLM ({web_facts['llm_model_version']})"
        )
        research_metadata["llm_model_version"] = web_facts["llm_model_version"]
    if state.get("raw_snippets"):
        research_metadata["sources_consulted"].append(
            f"Web search ({len(state.get('raw_snippets', []))} snippets)"
        )

    research_metadata["extracted_facts"] = {
        "form_d_rounds_found": len(form_d_rounds),
        "government_contracts_found": len(state.get("government_contracts", [])),
        "government_contracts_usd": state.get("government_contracts_usd"),
        "evidence_count": evidence_pkg_dict.get("evidence_count", 0),
        "consensus_strength": evidence_pkg_dict.get("consensus_strength", "NONE"),
        "consensus_valuation_usd": evidence_pkg_dict.get("consensus_valuation"),
        "web_sources": web_facts.get("sources", [])[:5],
    }

    # ── Evidence-first methodology selection ────────────────────────────
    # Re-build the EvidencePackage from the dict to use its methods
    raw_snippets = state.get("raw_snippets", [])
    source_titles = state.get("source_titles", [])
    try:
        as_of = date.fromisoformat(as_of_date[:10])
    except ValueError:
        as_of = date.today()

    # Re-extract fresh package (if we have snippets)
    if raw_snippets:
        pkg = extract_evidence(raw_snippets, source_titles, name, as_of)
    else:
        from vc_audit_tool.data_sources.evidence_collector import EvidencePackage
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

    # Override methodology selection if caller specified one
    chosen_methodology = requested_methodology or pkg.recommended_methodology()

    logger.info(
        "assemble: company=%s chosen_method=%s consensus=%s strength=%s",
        name, chosen_methodology,
        f"${pkg.consensus_valuation / 1e9:.1f}B" if pkg.consensus_valuation else "none",
        pkg.consensus_strength,
    )

    # ── Build the request dict ──────────────────────────────────────────
    assembled: dict[str, Any] | None = None
    missing_fields: list[str] = []

    if chosen_methodology == "direct_valuation":
        assembled, missing_fields = _assemble_direct_valuation(
            name, as_of_date, pkg
        )

    if assembled is None and chosen_methodology == "last_round_market_adjusted":
        assembled, missing_fields = _assemble_last_round(
            name, as_of_date, web_facts, form_d_rounds
        )

    if assembled is None and chosen_methodology in (
        "comparable_companies", "last_round_multiple_ratchet"
    ):
        assembled, missing_fields = _assemble_comps(
            name, as_of_date, chosen_methodology, sector, web_facts,
            description_hint=description_hint,
        )

    # If caller requested a specific method, return strict missing fields
    if requested_methodology:
        best_methodology = (
            assembled.get("methodology")
            if assembled
            else requested_methodology
        )
        return {
            **state,
            "assembled_request": assembled,
            "research_metadata": research_metadata,
            "missing_fields": missing_fields,
            "best_available_methodology": best_methodology,
            "missing_for_best_available": missing_fields,
        }

    # If still nothing, try methods and pick least-incomplete.
    if assembled is None:
        attempts = []
        for method in [
            "last_round_market_adjusted",
            "comparable_companies",
        ]:
            req, miss = _try_assemble(
                name, as_of_date, method, sector, web_facts,
                form_d_rounds, pkg, description_hint=description_hint,
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


# ── Assembly helpers ────────────────────────────────────────────────────


def _assemble_direct_valuation(
    name: str,
    as_of_date: str,
    pkg: EvidencePackage,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build a direct_valuation request from an EvidencePackage."""
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
            "private_company_discount_pct": (
                10.0 if any(
                    e["evidence_type"] in ("secondary_market", "post_money_fresh")
                    for e in evidence_signals
                ) else 20.0
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
                "private_company_discount_pct": 25,
            },
        }
        if description_hint:
            payload["inputs"]["target_description"] = description_hint
        return payload, []

    # last_round_multiple_ratchet
    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        return None, ["last_post_money_valuation", "revenue_at_last_round"]

    payload = {
        "company_name": name,
        "methodology": "last_round_multiple_ratchet",
        "as_of_date": as_of_date,
        "inputs": {
            "last_post_money_valuation": post_money,
            "revenue_at_last_round": revenue,
            "current_revenue": revenue,
            "sector": sector,
            "statistic": "median",
            "private_company_discount_pct": 25,
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
    return _assemble_comps(name, as_of_date, method, sector, web_facts,
                           description_hint=description_hint)


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


def _has_last_round_data(web_facts: dict[str, Any], form_d_rounds: list[dict[str, Any]]) -> bool:
    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        return False
    round_date = web_facts.get("last_round_date")
    if not round_date and form_d_rounds:
        round_date = form_d_rounds[0].get("filing_date")
    return _normalize_round_date(round_date) is not None


# ── Agent ───────────────────────────────────────────────────────────────


class CompanyResearchAgent:
    """LangGraph-based agent that assembles valuation inputs from public data.

    The agent now uses an evidence-first approach:
    1. Collect all valuation signals from the web
    2. Score them by recency, type, and source agreement
    3. Pick the methodology that best fits the evidence
    4. For companies with strong public valuation signals (SpaceX, Stripe, etc.)
       the ``direct_valuation`` methodology is automatically selected.
    """

    def __init__(self) -> None:
        _ensure_langgraph()
        self._graph = self._build_graph()

    @staticmethod
    def _build_graph() -> Any:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(ResearchState)
        graph.add_node("parse_company", _parse_company_node)
        graph.add_node("form_d", _form_d_node)
        graph.add_node("web_research", _web_research_node)
        graph.add_node("contracts", _contracts_node)
        graph.add_node("assemble", _assemble_node)

        graph.set_entry_point("parse_company")
        graph.add_edge("parse_company", "form_d")
        graph.add_edge("form_d", "web_research")
        graph.add_edge("web_research", "contracts")
        graph.add_edge("contracts", "assemble")
        graph.add_edge("assemble", END)

        return graph.compile()

    def run(
        self,
        company_name: str,
        *,
        methodology: str = "",
        as_of_date: str = "",
        description_hint: str = "",
    ) -> ResearchResult:
        if not as_of_date:
            as_of_date = date.today().isoformat()

        initial_state: ResearchState = {
            "company_name": company_name,
            "as_of_date": as_of_date,
            "methodology": methodology,
            "description_hint": description_hint,
        }

        try:
            final_state = self._graph.invoke(initial_state)
        except Exception as exc:
            logger.exception("research agent failed for '%s'", company_name)
            return ResearchResult(
                assembled_request=None,
                research_metadata={"error": str(exc)},
                missing_fields=[],
                error=str(exc),
            )

        company_profile = None
        assembled = final_state.get("assembled_request")
        if assembled is not None:
            try:
                from vc_audit_tool.reconciliation.profiler import CompanyProfiler
                company_profile = CompanyProfiler.build_from_dict(
                    assembled,
                    final_state.get("research_metadata"),
                    date.fromisoformat(as_of_date) if as_of_date else None,
                )
            except Exception as exc:
                logger.warning("profiler failed: %s", exc)

        return ResearchResult(
            assembled_request=assembled,
            research_metadata=final_state.get("research_metadata", {}),
            missing_fields=final_state.get("missing_fields", []),
            best_available_methodology=final_state.get("best_available_methodology"),
            missing_for_best_available=final_state.get("missing_for_best_available"),
            web_facts=final_state.get("web_facts"),
            error=final_state.get("error"),
            company_profile=company_profile,
        )
