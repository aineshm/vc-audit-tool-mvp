"""Company research agent -- assembles valuation inputs from public data.

Uses a LangGraph StateGraph with five nodes:

1. **parse_company** -- Normalise the company name, infer sector / SIC
2. **form_d**        -- Fetch SEC Form D filings for funding-round data
3. **web_research**  -- DuckDuckGo search + multi-provider LLM extraction
4. **contracts**     -- Look up federal contracts on USASpending.gov
5. **assemble**      -- Validate completeness and build a request dict

The agent never calls the valuation engine.  It only assembles the
structured ``inputs`` dict that a ``ValuationRequest`` needs.

Web research strategy
---------------------
DuckDuckGo search always runs first (free, no key).  Then an LLM
extracts structured facts from the snippets.  Provider priority:

  1. **OpenAI GPT-4o-mini**        (``OPENAI_API_KEY``)   ~$0.002/req
  2. **Anthropic Claude 3.5 Haiku** (``ANTHROPIC_API_KEY``)  ~$0.003/req
  3. **Google Gemini 2.0 Flash**   (``GOOGLE_API_KEY``)   ~$0.001/req
  4. **Ollama local model**        (``OLLAMA_MODEL`` env)  $0 (local GPU)
  5. **Regex-only fallback**       (no LLM needed)        $0

The first available provider wins.  If none is configured the agent
still returns results from the regex pass over search snippets.

Story 3.2 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, TypedDict

from vc_audit_tool.data_sources.form_d import FormDSource
from vc_audit_tool.data_sources.usaspending import USASpendingSource
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

# Optional deps — imported at module level so they can be mocked in tests.
# If not installed, the corresponding code paths degrade gracefully.
try:
    from duckduckgo_search import DDGS
except ImportError:  # pragma: no cover
    DDGS = None  # type: ignore[assignment,misc]

try:
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:  # pragma: no cover
    HumanMessage = None  # type: ignore[assignment,misc]
    SystemMessage = None  # type: ignore[assignment,misc]

# LLM providers — each is independently optional.
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover
    ChatGoogleGenerativeAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:  # pragma: no cover
    ChatAnthropic = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_ollama import ChatOllama
except ImportError:  # pragma: no cover
    ChatOllama = None  # type: ignore[assignment,misc,unused-ignore]

# ── LangGraph imports (lazy for light startup) ──
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
            "langgraph is required for the research agent.  "
            "Install with: pip install langgraph langchain-core "
            "langchain-ollama duckduckgo-search"
        ) from exc


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

# Sector inference table — simple heuristic, not LLM-dependent.
_KEYWORD_SECTORS: dict[str, str] = {
    "ai": "enterprise_software",
    "artificial intelligence": "enterprise_software",
    "machine learning": "enterprise_software",
    "saas": "enterprise_software",
    "software": "enterprise_software",
    "cloud": "enterprise_software",
    "data": "enterprise_software",
    "analytics": "enterprise_software",
    "platform": "enterprise_software",
    "cybersecurity": "cybersecurity",
    "security": "cybersecurity",
    "defense": "defense_electronics",
    "semiconductor": "semiconductors",
    "chip": "semiconductors",
    "ecommerce": "ecommerce",
    "e-commerce": "ecommerce",
    "retail": "ecommerce",
    "telecom": "telecommunications",
    "infrastructure": "infrastructure_software",
}


class ResearchState(TypedDict, total=False):
    """Mutable state threaded through the LangGraph nodes."""

    # Input
    company_name: str
    as_of_date: str
    methodology: str

    # Intermediate
    normalised_name: str
    inferred_sector: str
    inferred_sic: str
    description_hint: str

    # Data gathered
    form_d_rounds: list[dict[str, Any]]
    government_contracts: list[dict[str, Any]]
    government_contracts_usd: float | None
    web_facts: dict[str, Any]

    # Final output
    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    error: str | None


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ResearchResult:
    """Return type of :meth:`CompanyResearchAgent.run`."""

    assembled_request: dict[str, Any] | None
    """``ValuationRequest``-shaped dict, or ``None`` if incomplete."""

    research_metadata: dict[str, Any]
    """Sources consulted, extracted facts, LLM version, timestamps."""

    missing_fields: list[str]
    """Required fields that could not be found."""

    error: str | None = None
    """Top-level error message, if any."""

    company_profile: Any | None = None
    """Optional :class:`CompanyProfile` built by the profiler (Phase 2)."""

    @property
    def is_complete(self) -> bool:
        return self.assembled_request is not None and not self.missing_fields


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def _parse_company_node(state: ResearchState) -> ResearchState:
    """Normalise company name and infer sector."""
    name = state.get("company_name", "")
    normalised = name.strip()
    if not normalised:
        return {**state, "error": "company_name is required."}

    # Simple sector inference from company name / description hint
    hint = state.get("description_hint", normalised).lower()
    sector = "enterprise_software"  # default
    # Check longest keywords first so "cybersecurity" beats "security", etc.
    for keyword, mapped_sector in sorted(
        _KEYWORD_SECTORS.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if keyword in hint:
            sector = mapped_sector
            break

    # Reverse-lookup a SIC code
    from vc_audit_tool.data_sources.edgar_universe import SIC_SECTOR_MAP

    sic = "7372"  # default
    for code, sec in SIC_SECTOR_MAP.items():
        if sec == sector:
            sic = code
            break

    logger.info("parse_company: name=%s sector=%s sic=%s", normalised, sector, sic)

    return {
        **state,
        "normalised_name": normalised,
        "inferred_sector": sector,
        "inferred_sic": sic,
    }


def _form_d_node(state: ResearchState) -> ResearchState:
    """Fetch SEC Form D filings."""
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


def _contracts_node(state: ResearchState) -> ResearchState:
    """Fetch USASpending.gov federal contracts."""
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        return state

    try:
        source = USASpendingSource()
        contracts = source.search(name)
        contracts_dicts = [c.to_dict() for c in contracts]
        total = sum(c.award_amount for c in contracts) if contracts else None
    except DataSourceError:
        contracts_dicts = []
        total = None

    return {
        **state,
        "government_contracts": contracts_dicts,
        "government_contracts_usd": total,
    }


def _web_research_node(state: ResearchState) -> ResearchState:
    """Search the web for company facts, then use an LLM to extract them.

    Strategy:

    1. **DuckDuckGo search** — 4 targeted queries (funding, revenue,
       Series/valuation, company overview) for thorough 1-shot research.
    2. **Regex extraction** — pull dollar amounts and dates from snippets
       (always runs as a safety net).
    3. **LLM extraction** — the first available provider is used to
       extract structured facts from the raw search text.  Provider
       priority: Gemini Flash → Anthropic Haiku → OpenAI 4o-mini → Ollama.

    The node *always* produces results (even without an LLM) because the
    regex pass catches the most common patterns.  The LLM layer just
    improves accuracy when available.
    """
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        return state

    web_facts: dict[str, Any] = {
        "revenue_ltm": None,
        "last_round_date": None,
        "last_round_amount_raised": None,
        "last_post_money_valuation": None,
        "company_description": None,
        "sources": [],
        "llm_model_version": None,
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ── Step 1: DuckDuckGo search (deep, multi-query) ──────────────────
    raw_snippets, source_titles = _ddg_search(name)

    if not raw_snippets:
        return {**state, "web_facts": web_facts}

    combined_text = "\n".join(raw_snippets)
    web_facts["sources"] = source_titles[:10]

    # ── Step 2: Regex extraction (always runs as safety net) ───────────
    _regex_extract(combined_text, web_facts)

    # ── Step 3: LLM extraction (first available provider wins) ─────────
    llm, model_label = _get_llm()

    if llm is not None and HumanMessage is not None and SystemMessage is not None:
        _llm_extract(llm, model_label, name, combined_text, web_facts)

    return {**state, "web_facts": web_facts}


# ---------------------------------------------------------------------------
# DuckDuckGo search helper
# ---------------------------------------------------------------------------

_SEARCH_QUERIES = [
    "{name} latest funding round valuation post-money",
    "{name} annual revenue ARR",
    "{name} Series A B C D funding raised investors",
    "{name} company overview private valuation",
]


def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
) -> tuple[list[str], list[str]]:
    """Run DuckDuckGo text searches and return (snippets, source_titles)."""
    if DDGS is None:
        logger.info("web_research: duckduckgo-search not installed -- skipping")
        return [], []

    raw_snippets: list[str] = []
    source_titles: list[str] = []
    try:
        with DDGS() as ddgs:
            for q_template in _SEARCH_QUERIES:
                q = q_template.format(name=company_name)
                for r in ddgs.text(q, max_results=max_results_per_query):
                    snippet = f"{r.get('title', '')} -- {r.get('body', '')}"
                    raw_snippets.append(snippet)
                    title = r.get("title", "")
                    if title and title not in source_titles:
                        source_titles.append(title)
    except Exception as exc:
        logger.warning("web_research: DuckDuckGo search failed: %s", exc)

    return raw_snippets, source_titles


# ---------------------------------------------------------------------------
# LLM provider resolution
# ---------------------------------------------------------------------------


def _get_llm() -> tuple[Any, str]:
    """Return ``(llm_instance, model_label)`` for the first available provider.

    Priority: OpenAI 4o-mini → Anthropic Haiku → Gemini Flash → Ollama.
    Returns ``(None, "")`` when nothing is configured.
    """
    # 1. OpenAI GPT-4o-mini
    if os.environ.get("OPENAI_API_KEY") and ChatOpenAI is not None:
        try:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            llm: Any = ChatOpenAI(model=model, temperature=0)
            logger.info("web_research: using OpenAI %s", model)
            return llm, f"openai/{model}"
        except Exception as exc:
            logger.warning("OpenAI init failed (%s) -- trying next", exc)

    # 2. Anthropic Claude 3.5 Haiku
    if os.environ.get("ANTHROPIC_API_KEY") and ChatAnthropic is not None:
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
            llm = ChatAnthropic(model=model, temperature=0, max_tokens=1024)
            logger.info("web_research: using Anthropic %s", model)
            return llm, f"anthropic/{model}"
        except Exception as exc:
            logger.warning("Anthropic init failed (%s) -- trying next", exc)

    # 3. Google Gemini 2.0 Flash (cheapest API option ~$0.001 / req)
    if os.environ.get("GOOGLE_API_KEY") and ChatGoogleGenerativeAI is not None:
        try:
            model = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
            llm = ChatGoogleGenerativeAI(model=model, temperature=0, max_output_tokens=1024)
            logger.info("web_research: using Google %s", model)
            return llm, f"google/{model}"
        except Exception as exc:
            logger.warning("Google Gemini init failed (%s) -- trying next", exc)

    # 4. Ollama local model (free, no API key, must be running)
    ollama_model = os.environ.get("OLLAMA_MODEL", "")
    if ollama_model and ChatOllama is not None:
        try:
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            llm = ChatOllama(model=ollama_model, base_url=base_url, temperature=0, num_predict=512)
            logger.info("web_research: using Ollama %s at %s", ollama_model, base_url)
            return llm, f"ollama/{ollama_model}"
        except Exception as exc:
            logger.warning("Ollama init failed (%s) -- no LLM available", exc)

    logger.info("web_research: no LLM provider configured -- regex-only mode")
    return None, ""


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a financial research assistant.  Given search result snippets "
    "about a private company, extract ONLY verifiable facts.  "
    "For each fact, note the source (e.g. 'TechCrunch Jan 2025', 'SEC filing').  "
    "Return a JSON object with exactly these keys:\n"
    "- revenue_ltm: number or null (annual revenue in USD)\n"
    "- last_round_date: string or null (ISO date of most recent funding round)\n"
    "- last_round_amount_raised: number or null (USD amount raised)\n"
    "- last_post_money_valuation: number or null (USD post-money valuation)\n"
    "- company_description: string or null (1-2 sentence description)\n"
    "- sources: list of strings (source citations for each fact)\n\n"
    "NEVER guess.  If uncertain, set the field to null.  "
    "Return ONLY the JSON object, no explanation."
)

_LLM_EXTRACT_KEYS = (
    "revenue_ltm",
    "last_round_date",
    "last_round_amount_raised",
    "last_post_money_valuation",
    "company_description",
)


def _llm_extract(
    llm: Any,
    model_label: str,
    company_name: str,
    search_text: str,
    web_facts: dict[str, Any],
) -> None:
    """Call the LLM with search context and merge results into *web_facts*."""
    try:
        human_msg = f"Company: {company_name}\n\nSearch result snippets:\n{search_text[:4000]}"
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=human_msg),
            ]
        )

        content = response.content
        if isinstance(content, str):
            text = content.strip()
            # Handle markdown code fences
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            try:
                parsed = json.loads(text)
                for key in _LLM_EXTRACT_KEYS:
                    if parsed.get(key) is not None:
                        web_facts[key] = parsed[key]
                if parsed.get("sources"):
                    web_facts["sources"] = parsed["sources"]
                web_facts["llm_model_version"] = model_label
            except json.JSONDecodeError:
                logger.warning("web_research: LLM returned non-JSON: %s", text[:200])
    except Exception as exc:
        logger.warning("web_research: LLM extraction failed (%s) -- using regex results", exc)


# ---------------------------------------------------------------------------
# Regex extraction (always runs as safety net)
# ---------------------------------------------------------------------------


def _regex_extract(combined_text: str, web_facts: dict[str, Any]) -> None:
    """Extract financial facts from search snippets using regex patterns."""
    # Post-money valuation (e.g. "$4.1 billion valuation")
    val_pattern = re.compile(
        r"\$\s*([\d,.]+)\s*(billion|million|B|M)\s+(?:valuation|valued)",
        re.IGNORECASE,
    )
    for m in val_pattern.finditer(combined_text):
        raw_num = float(m.group(1).replace(",", ""))
        multiplier = m.group(2).lower()
        if multiplier in ("billion", "b"):
            raw_num = round(raw_num * 1_000_000_000)
        elif multiplier in ("million", "m"):
            raw_num = round(raw_num * 1_000_000)
        if (
            web_facts["last_post_money_valuation"] is None
            or raw_num > web_facts["last_post_money_valuation"]
        ):
            web_facts["last_post_money_valuation"] = raw_num
        break

    # Funding amount (e.g. "raised $500 million")
    raised_pattern = re.compile(
        r"raised\s+\$\s*([\d,.]+)\s*(billion|million|B|M)\b",
        re.IGNORECASE,
    )
    for m in raised_pattern.finditer(combined_text):
        raw_num = float(m.group(1).replace(",", ""))
        multiplier = m.group(2).lower()
        if multiplier in ("billion", "b"):
            raw_num = round(raw_num * 1_000_000_000)
        elif multiplier in ("million", "m"):
            raw_num = round(raw_num * 1_000_000)
        web_facts["last_round_amount_raised"] = raw_num
        break

    # Revenue (e.g. "$100 million in revenue")
    rev_pattern = re.compile(
        r"\$\s*([\d,.]+)\s*(billion|million|B|M)\b[^.]*?revenue",
        re.IGNORECASE,
    )
    for m in rev_pattern.finditer(combined_text):
        raw_num = float(m.group(1).replace(",", ""))
        multiplier = m.group(2).lower()
        if multiplier in ("billion", "b"):
            raw_num = round(raw_num * 1_000_000_000)
        elif multiplier in ("million", "m"):
            raw_num = round(raw_num * 1_000_000)
        web_facts["revenue_ltm"] = raw_num
        break

    # Date near "funding round" or "Series"
    round_context = re.compile(
        r"(?:series|round|funding|raised)[^.]{0,100}?"
        r"((?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )
    for m in round_context.finditer(combined_text):
        web_facts["last_round_date"] = m.group(1)
        break


def _assemble_node(state: ResearchState) -> ResearchState:
    """Validate completeness and build the ValuationRequest-shaped dict."""
    error = state.get("error")
    if error:
        return state

    name = state.get("normalised_name", state.get("company_name", ""))
    as_of_date = state.get("as_of_date", date.today().isoformat())
    methodology = state.get("methodology", "")
    sector = state.get("inferred_sector", "enterprise_software")
    web_facts = state.get("web_facts", {})
    form_d_rounds = state.get("form_d_rounds", [])

    # Build research_metadata (always populated, lives outside valuation_result)
    research_metadata: dict[str, Any] = {
        "sources_consulted": [],
        "extracted_facts": {},
        "research_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Collect sources
    if form_d_rounds:
        research_metadata["sources_consulted"].append("SEC EDGAR Form D")
    if state.get("government_contracts"):
        research_metadata["sources_consulted"].append("USASpending.gov")
    if web_facts.get("llm_model_version"):
        research_metadata["sources_consulted"].append(f"LLM ({web_facts['llm_model_version']})")
        research_metadata["llm_model_version"] = web_facts["llm_model_version"]

    research_metadata["extracted_facts"] = {
        "form_d_rounds_found": len(form_d_rounds),
        "government_contracts_found": len(state.get("government_contracts", [])),
        "government_contracts_usd": state.get("government_contracts_usd"),
        "web_facts": {
            k: v
            for k, v in web_facts.items()
            if k not in ("sources", "llm_model_version", "extraction_timestamp")
        },
    }

    if web_facts.get("sources"):
        research_metadata["extracted_facts"]["web_sources"] = web_facts["sources"]

    # Now try to assemble a complete request based on the chosen methodology
    missing: list[str] = []
    request_dict: dict[str, Any] | None = None

    if not methodology:
        # Auto-select: if we have Form D data, use last_round; otherwise, comps
        if _has_last_round_data(web_facts, form_d_rounds):
            methodology = "last_round_market_adjusted"
        else:
            methodology = "comparable_companies"

    description_hint_raw = state.get("description_hint", "")
    description_hint = (
        description_hint_raw.strip() if isinstance(description_hint_raw, str) else ""
    )

    if methodology == "last_round_market_adjusted":
        request_dict, missing = _assemble_last_round(name, as_of_date, web_facts, form_d_rounds)
    elif methodology in ("comparable_companies", "last_round_multiple_ratchet"):
        request_dict, missing = _assemble_comps(
            name,
            as_of_date,
            methodology,
            sector,
            web_facts,
            description_hint=description_hint,
        )
    else:
        missing.append(f"unsupported methodology: {methodology}")

    assembled = request_dict if request_dict and not missing else None

    return {
        **state,
        "assembled_request": assembled,
        "research_metadata": research_metadata,
        "missing_fields": missing,
    }


def _has_last_round_data(web_facts: dict[str, Any], form_d_rounds: list[dict[str, Any]]) -> bool:
    """Check if we have enough data for a last-round methodology."""
    has_date = bool(
        web_facts.get("last_round_date") or (form_d_rounds and form_d_rounds[0].get("filing_date"))
    )
    has_valuation = bool(web_facts.get("last_post_money_valuation"))
    return has_date and has_valuation


def _assemble_last_round(
    name: str,
    as_of_date: str,
    web_facts: dict[str, Any],
    form_d_rounds: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Try to build a last_round_market_adjusted request."""
    missing: list[str] = []

    post_money = web_facts.get("last_post_money_valuation")
    if not post_money:
        missing.append("last_post_money_valuation")

    round_date = web_facts.get("last_round_date")
    if not round_date and form_d_rounds:
        round_date = form_d_rounds[0].get("filing_date")
    if not round_date:
        missing.append("last_round_date")

    if missing:
        return None, missing

    return {
        "company_name": name,
        "methodology": "last_round_market_adjusted",
        "as_of_date": as_of_date,
        "inputs": {
            "last_post_money_valuation": post_money,
            "last_round_date": round_date,
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
    """Try to build a comparable_companies or multiple_ratchet request."""
    missing: list[str] = []
    revenue = web_facts.get("revenue_ltm")

    if methodology == "comparable_companies":
        if not revenue:
            missing.append("revenue_ltm")
        if missing:
            return None, missing
        payload = {
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
        missing.append("last_post_money_valuation")
    # For ratchet we need revenue_at_last_round AND current_revenue
    # We use revenue_ltm as current_revenue; revenue_at_last_round is harder to find
    if not revenue:
        missing.append("current_revenue (revenue_ltm)")
    # We'll estimate revenue_at_last_round from post_money and a reasonable multiple
    # if not explicitly available — flagged as an estimate
    if missing:
        return None, missing
    payload = {
        "company_name": name,
        "methodology": "last_round_multiple_ratchet",
        "as_of_date": as_of_date,
        "inputs": {
            "last_post_money_valuation": post_money,
            "revenue_at_last_round": revenue,  # Approximation — noted in metadata
            "current_revenue": revenue,
            "sector": sector,
            "statistic": "median",
            "private_company_discount_pct": 25,
        },
    }
    if description_hint:
        payload["inputs"]["target_description"] = description_hint
    return payload, []


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class CompanyResearchAgent:
    """LangGraph-based agent that assembles valuation inputs from public data.

    Usage::

        agent = CompanyResearchAgent()
        result = agent.run("Anthropic", methodology="comparable_companies")
        if result.is_complete:
            engine.evaluate_from_dict(result.assembled_request)
    """

    def __init__(self) -> None:
        _ensure_langgraph()
        self._graph = self._build_graph()

    @staticmethod
    def _build_graph() -> Any:
        """Construct the LangGraph StateGraph."""
        from langgraph.graph import END, StateGraph

        graph = StateGraph(ResearchState)

        # Add nodes
        graph.add_node("parse_company", _parse_company_node)
        graph.add_node("form_d", _form_d_node)
        graph.add_node("web_research", _web_research_node)
        graph.add_node("contracts", _contracts_node)
        graph.add_node("assemble", _assemble_node)

        # Define edges: parse → parallel data gathering → assemble
        graph.set_entry_point("parse_company")

        # After parsing, fan out to data sources
        # LangGraph processes edges sequentially in a single-process graph,
        # but the semantic intent is: all three data sources are independent.
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
        """Run the research agent and return assembled inputs.

        Parameters
        ----------
        company_name:
            The name of the private company to research.
        methodology:
            Override methodology selection.  If empty, the agent will
            auto-select based on data availability.
        as_of_date:
            Valuation date (ISO format).  Defaults to today.
        description_hint:
            Optional description of the company to help with sector
            inference and embedding-based comp selection.
        """
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

        # Build CompanyProfile if we have assembled data (Phase 2)
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
            error=final_state.get("error"),
            company_profile=company_profile,
        )
