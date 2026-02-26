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

Backward-compatibility note
---------------------------
All symbols that were previously defined in this module are re-exported here so
that existing ``from vc_audit_tool.agent.research import ...`` statements
continue to work without changes.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

# ── Re-exports (backward compat) ─────────────────────────────────────────
from vc_audit_tool.agent.llm_adapter import (  # noqa: F401
    _LLM_SYSTEM_PROMPT,
    _get_llm,
    _llm_extract_structured,
)
from vc_audit_tool.agent.nodes.assemble import (  # noqa: F401
    _assemble_comps,
    _assemble_direct_valuation,
    _assemble_last_round,
    _assemble_node,
    _has_last_round_data,
    _normalize_round_date,
    _try_assemble,
)
from vc_audit_tool.agent.nodes.contracts import _contracts_node  # noqa: F401
from vc_audit_tool.agent.nodes.form_d import _form_d_node  # noqa: F401
from vc_audit_tool.agent.nodes.parse import _parse_company_node  # noqa: F401
from vc_audit_tool.agent.nodes.web_research import (  # noqa: F401
    _DDGS_BACKEND,
    _SEARCH_QUERIES,
    DDGS,
    _ddg_search,
    _extract_best_post_money_from_package,
    _extract_last_post_money_valuation,
    _extract_last_round_amount_raised,
    _merge_llm_into_package,
    _web_research_node,
)
from vc_audit_tool.agent.state import (  # noqa: F401
    _KEYWORD_SECTORS,
    ResearchResult,
    ResearchState,
)

logger = logging.getLogger(__name__)

# ── LangGraph lazy-load guard ─────────────────────────────────────────────

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


# ── Agent ─────────────────────────────────────────────────────────────────


class CompanyResearchAgent:
    """LangGraph-based agent that assembles valuation inputs from public data.

    The agent uses an evidence-first approach:
    1. Collect all valuation signals from the web
    2. Score them by recency, type, and source agreement
    3. Pick the methodology that best fits the evidence
    4. For companies with strong public valuation signals (SpaceX, Stripe, etc.)
       the ``direct_valuation`` methodology is automatically selected.

    Graph topology (fan-out / fan-in for parallel I/O):

        parse_company
        ┌─────┬──────────────┐
       form_d  web_research  contracts   ← run concurrently
        └─────┴──────────────┘
              assemble
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

        # Fan-out: parse_company → {form_d, web_research, contracts} in parallel.
        # LangGraph runs all nodes that become ready in the same super-step
        # concurrently (in separate threads for sync nodes).  The three I/O
        # nodes write to disjoint state keys so their outputs merge cleanly.
        graph.set_entry_point("parse_company")
        graph.add_edge("parse_company", "form_d")
        graph.add_edge("parse_company", "web_research")
        graph.add_edge("parse_company", "contracts")
        # Fan-in: assemble waits for all three to complete.
        graph.add_edge("form_d", "assemble")
        graph.add_edge("web_research", "assemble")
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


__all__ = [
    "CompanyResearchAgent",
    "ResearchResult",
    "ResearchState",
    "_KEYWORD_SECTORS",
    "_LLM_SYSTEM_PROMPT",
    "_get_llm",
    "_llm_extract_structured",
    "_assemble_node",
    "_assemble_direct_valuation",
    "_assemble_last_round",
    "_assemble_comps",
    "_try_assemble",
    "_normalize_round_date",
    "_has_last_round_data",
    "_parse_company_node",
    "_form_d_node",
    "_contracts_node",
    "_web_research_node",
    "_DDGS_BACKEND",
    "_SEARCH_QUERIES",
    "DDGS",
    "_ddg_search",
    "_merge_llm_into_package",
    "_extract_best_post_money_from_package",
    "_extract_last_post_money_valuation",
    "_extract_last_round_amount_raised",
]
