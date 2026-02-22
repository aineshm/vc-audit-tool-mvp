"""Confidence-indicator report for a stored valuation run.

Reads a valuation result (either from the SQLite store by request-id
or from a raw dict) and produces a human-readable summary of every
confidence warning, colour-coded for terminal output.

Story 5.2 of the Production Upgrade Plan.
"""

from __future__ import annotations

import sys
from typing import Any

from vc_audit_tool.store import ValuationStore

# ── ANSI colour helpers ──

_RED = "\033[91m"
_YELLOW = "\033[93m"
_GREEN = "\033[92m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _supports_colour() -> bool:
    """Return *True* when stdout is an interactive terminal."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _colour(text: str, code: str) -> str:
    if _supports_colour():
        return f"{code}{text}{_RESET}"
    return text


# ── Classification ──

_HIGH_KEYWORDS = {"high", "stale"}
_MEDIUM_KEYWORDS = {"medium", "moderate"}


def _classify(value: Any) -> str:
    """Return ``'HIGH'``, ``'MEDIUM'``, or ``'LOW'``."""
    s = str(value).lower()
    for kw in _HIGH_KEYWORDS:
        if kw in s:
            return "HIGH"
    for kw in _MEDIUM_KEYWORDS:
        if kw in s:
            return "MEDIUM"
    # Numeric thresholds (e.g. gap_days > 365 → HIGH)
    if isinstance(value, int | float):
        if value > 365:
            return "HIGH"
        if value > 180:
            return "MEDIUM"
    return "LOW"


# ── Report formatting ──

_SEVERITY_COLOUR = {
    "HIGH": _RED,
    "MEDIUM": _YELLOW,
    "LOW": _GREEN,
}


def format_confidence_report(result_dict: dict[str, Any]) -> str:
    """Build a multi-line human-readable confidence report.

    Parameters
    ----------
    result_dict:
        A full valuation output dict (as returned by
        ``ValuationResult.to_dict()`` or the ``/value`` endpoint).

    Returns
    -------
    str
        The report text, with optional ANSI colour codes.
    """
    vr = result_dict.get("valuation_result", {})
    am = result_dict.get("audit_metadata", {})
    ci: dict[str, Any] = vr.get("confidence_indicators", {})

    lines: list[str] = []
    lines.append(
        _colour("━━━ Confidence Report ━━━", _BOLD),
    )
    lines.append(f"Company:     {vr.get('company_name', 'N/A')}")
    lines.append(f"Methodology: {vr.get('methodology', 'N/A')}")
    lines.append(f"As-of date:  {vr.get('as_of_date', 'N/A')}")
    lines.append(f"Request ID:  {am.get('request_id', 'N/A')}")
    lines.append("")

    if not ci:
        lines.append("  No confidence indicators recorded for this run.")
        return "\n".join(lines)

    for key, value in ci.items():
        severity = _classify(value)
        colour_code = _SEVERITY_COLOUR[severity]
        label = key.replace("_", " ").title()
        badge = _colour(f"[{severity}]", colour_code)
        lines.append(f"  {badge}  {label}: {value}")

    lines.append("")
    return "\n".join(lines)


def confidence_report_for_request_id(
    request_id: str,
    store: ValuationStore | None = None,
) -> str:
    """Look up a run by *request_id* and return a formatted report.

    Raises
    ------
    KeyError
        If the request-id is not found in the store.
    """
    if store is None:
        store = ValuationStore()
    run = store.get_run(request_id)
    if run is None:
        raise KeyError(f"Run not found: {request_id}")
    return format_confidence_report(run)
