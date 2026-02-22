"""Company research agent — LangGraph-based agentic workflow.

Exposes :class:`CompanyResearchAgent` which, given only a company name,
automatically assembles the structured inputs needed for valuation.

The agent does NOT call the valuation engine.  It only assembles a
``ValuationRequest``-shaped dict.  The engine call happens outside.
"""

from vc_audit_tool.agent.research import CompanyResearchAgent, ResearchResult

__all__ = ["CompanyResearchAgent", "ResearchResult"]
