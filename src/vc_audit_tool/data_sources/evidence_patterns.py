"""Regex patterns and pure helper functions for evidence extraction.

Separated from evidence_collector to keep each module under the 400-line limit.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── Evidence type confidence weights ─────────────────────────────────────

EVIDENCE_TYPES = {
    "secondary_market": 0.90,  # secondary trades, tender offers
    "post_money_fresh": 0.85,  # post-money < 12 months old
    "analyst_consensus": 0.70,  # analyst / press estimates, repeated
    "post_money_stale": 0.50,  # post-money 12-36 months old
    "revenue_implied": 0.30,  # inferred from revenue × sector multiple
}

# ── Pattern library ──────────────────────────────────────────────────────

# Captures: "$X trillion/billion/million [valuation/valued/worth/value]"
_DIRECT_VALUATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\$([\d,.]+)\s*(trillion|billion|trillion|T|B)\b"
            r"[^.]{0,80}?(?:valuation|valued|worth|value|price)",
            re.IGNORECASE,
        ),
        "direct",
    ),
    (
        re.compile(
            r"(?:valuation|valued at|worth|priced at)\s+\$?([\d,.]+)\s*(trillion|billion|T|B)\b",
            re.IGNORECASE,
        ),
        "direct",
    ),
    (
        re.compile(
            r"secondary\s+market[^.]{0,60}?\$([\d,.]+)\s*(trillion|billion|T|B)",
            re.IGNORECASE,
        ),
        "secondary",
    ),
    (
        re.compile(
            r"\$([\d,.]+)\s*(trillion|billion|T|B)\b[^.]{0,80}?"
            r"(?:tender\s*offer|secondary|buyback|share\s*sale)",
            re.IGNORECASE,
        ),
        "secondary",
    ),
    (
        re.compile(
            r"(?:raised|closed|completed)\s+[^$]{0,20}?\$([\d,.]+)\s*(billion|million|B|M)\b",
            re.IGNORECASE,
        ),
        "round",
    ),
    (
        re.compile(
            r"post.money\s+(?:valuation\s+of\s+)?\$([\d,.]+)\s*(billion|million|B|M)\b",
            re.IGNORECASE,
        ),
        "round",
    ),
    (
        re.compile(
            r"analyst[^.]{0,60}?\$([\d,.]+)\s*(trillion|billion|T|B)",
            re.IGNORECASE,
        ),
        "analyst",
    ),
]

_MULTIPLIERS: dict[str, float] = {
    "trillion": 1_000_000_000_000,
    "t": 1_000_000_000_000,
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
    "million": 1_000_000,
    "m": 1_000_000,
}

_DATE_NEAR_SIGNAL = re.compile(
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})",
    re.IGNORECASE,
)

# ── Pure helpers ─────────────────────────────────────────────────────────


def _parse_amount(num_str: str, unit: str) -> float:
    raw = float(num_str.replace(",", ""))
    mult = _MULTIPLIERS.get(unit.lower(), 1)
    return raw * mult


def _find_nearby_date(text: str, pos: int, window: int = 300) -> str | None:
    """Look for a date string near a match position in text."""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    excerpt = text[start:end]
    m = _DATE_NEAR_SIGNAL.search(excerpt)
    return m.group(0) if m else None


def _rough_age_months(date_str: str, as_of: date | None = None) -> float | None:
    aod = as_of or date.today()
    cleaned = date_str.strip()
    for fmt in ("%Y-%m-%d", "%B %Y", "%b %Y", "%Y"):
        try:
            d = datetime.strptime(cleaned[: len(fmt) + 2], fmt).date()
            return (aod - d).days / 30.44
        except ValueError:
            continue
    return None


def _classify_evidence_type(
    pattern_label: str,
    amount: float,
    snippet: str,
    date_str: str | None,
    as_of: date | None = None,
) -> tuple[str, float]:
    """Return (evidence_type, confidence) for a match."""
    snippet_lower = snippet.lower()

    if pattern_label == "secondary" or any(
        kw in snippet_lower for kw in ("secondary", "tender offer", "buyback", "private share")
    ):
        return "secondary_market", EVIDENCE_TYPES["secondary_market"]

    if pattern_label == "analyst" or any(
        kw in snippet_lower for kw in ("analyst", "estimate", "projection", "forecast")
    ):
        return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"]

    if pattern_label == "round":
        if date_str:
            age = _rough_age_months(date_str, as_of)
            if age is not None and age < 12:
                return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"]
            if age is not None and age < 36:
                return "post_money_stale", EVIDENCE_TYPES["post_money_stale"]
        return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"] * 0.8

    if date_str:
        age = _rough_age_months(date_str, as_of)
        if age is not None and age < 6:
            return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"]
        if age is not None and age < 18:
            return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"]

    return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"] * 0.85
