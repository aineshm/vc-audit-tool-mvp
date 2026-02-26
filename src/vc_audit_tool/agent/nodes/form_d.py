"""Node 2: form_d -- fetch SEC Form D filings for funding-round data."""

from __future__ import annotations

import logging

from vc_audit_tool.agent.state import ResearchState
from vc_audit_tool.data_sources.form_d import FormDSource
from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)


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
