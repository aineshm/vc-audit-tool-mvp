"""Shared helpers for store implementations.

Handles both regular ValuationResult envelopes and ReconciledValuation
envelopes so the store layer doesn't need per-format branches.
"""

from __future__ import annotations

from typing import Any


def extract_run_fields(
    result_dict: dict[str, Any],
) -> tuple[str, str, str, str, float, str]:
    """Return (request_id, company_name, methodology, as_of_date, fair_value, generated_at_utc).

    Supports two envelope shapes:

    * **ValuationResult** (``POST /api/value``, ``POST /research``) —
      has a ``valuation_result`` top-level key.
    * **ReconciledValuation** (``POST /reconcile``) —
      has a ``concluded_value`` top-level key.
    """
    am = result_dict["audit_metadata"]
    request_id: str = am["request_id"]
    generated_at_utc: str = am["generated_at_utc"]

    if "valuation_result" in result_dict:
        vr = result_dict["valuation_result"]
        return (
            request_id,
            str(vr["company_name"]),
            str(vr["methodology"]),
            str(vr["as_of_date"]),
            float(vr["estimated_fair_value"]["amount"]),
            generated_at_utc,
        )

    # ReconciledValuation format
    cv = result_dict["concluded_value"]
    profile = result_dict.get("company_profile", {})
    return (
        request_id,
        str(profile.get("name", "unknown")),
        "reconciled",
        str(cv["as_of_date"]),
        float(cv["point_estimate"]),
        generated_at_utc,
    )
