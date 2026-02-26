"""Node 4: contracts -- USASpending.gov federal contract lookup."""

from __future__ import annotations

import logging

from vc_audit_tool.agent.state import ResearchState
from vc_audit_tool.data_sources.usaspending import USASpendingSource
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)


def _contracts_node(state: ResearchState) -> ResearchState:
    name = state.get("normalised_name", state.get("company_name", ""))
    if not name:
        # Return empty dict (no-op update) — safe for parallel fan-out.
        return {}
    try:
        source = USASpendingSource()
        contracts = source.search(name)
        total = sum(c.award_amount for c in contracts) if contracts else None
        contracts_dicts = [c.to_dict() for c in contracts]
    except DataSourceError:
        contracts_dicts = []
        total = None
    # Return only the keys this node produces so parallel siblings can merge cleanly.
    return {
        "government_contracts": contracts_dicts,
        "government_contracts_usd": total,
    }
