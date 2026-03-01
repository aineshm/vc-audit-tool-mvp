"""Regex patterns and pure helper functions for evidence extraction.

Separated from evidence_collector to keep each module under the 400-line limit.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
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
            r"\$([\d,.]+)\s*(trillion|billion|T|B)\b"
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
            r"(?:raised|closed|completed)[^.]{0,120}?\bat\s+(?:a\s+)?\$?([\d,.]+)\s*(billion|B)\b"
            r"[^.]{0,60}?(?:valuation|post.money|post_money|value)\b",
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

_RELATIVE_DATE_PATTERN = re.compile(
    r"(\d+)\s+(day|week|month|year)s?\s+ago"
    r"|yesterday"
    r"|last\s+(week|month|year)",
    re.IGNORECASE,
)

# ── Delta/increment context patterns ─────────────────────────────────────
# Used by _is_delta_context() to suppress false-positive signals like
# "boost Stripe's valuation by $15B" where $15B is an increment, not a valuation.

_DELTA_CONTEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bboost(?:ed|s)?\s+(?:\S+\s+){0,5}?by\b", re.IGNORECASE),
    re.compile(r"\bincrease[ds]?\s+(?:\S+\s+){0,5}?by\b", re.IGNORECASE),
    re.compile(r"\bup\s+by\b", re.IGNORECASE),
    re.compile(r"\bdown\s+by\b", re.IGNORECASE),
    re.compile(r"\badd(?:ed|s)?\s+(?:\S+\s+){0,5}?by\b", re.IGNORECASE),
    re.compile(r"\bgrew?\s+by\b", re.IGNORECASE),
    re.compile(r"\brose?\s+by\b", re.IGNORECASE),
    re.compile(r"\bfell?\s+by\b", re.IGNORECASE),
    re.compile(r"\bcut\s+(?:\S+\s+){0,5}?by\b", re.IGNORECASE),
    re.compile(r"\bgain(?:ed|s)?\s+(?:\S+\s+){0,5}?by\b", re.IGNORECASE),
]

# ── Pure helpers ─────────────────────────────────────────────────────────


def _parse_amount(num_str: str, unit: str) -> float:
    raw = float(num_str.replace(",", ""))
    mult = _MULTIPLIERS.get(unit.lower(), 1)
    return raw * mult


def _parse_relative_date(text: str, as_of: date) -> str | None:
    """Resolve relative date phrases to absolute ISO date strings.

    Examples (with as_of=2026-02-28):
      "4 days ago"  -> "2026-02-24"
      "yesterday"   -> "2026-02-27"
      "last week"   -> "2026-02-21"
      "2 months ago" -> approx 60 days before as_of
    """
    m = _RELATIVE_DATE_PATTERN.search(text)
    if not m:
        return None

    matched = m.group(0).lower().strip()

    if "yesterday" in matched:
        return (as_of - timedelta(days=1)).isoformat()
    if "last week" in matched:
        return (as_of - timedelta(days=7)).isoformat()
    if "last month" in matched:
        return (as_of - timedelta(days=30)).isoformat()
    if "last year" in matched:
        return (as_of - timedelta(days=365)).isoformat()

    # "N days/weeks/months/years ago" — group(1) and group(2) are always set here.
    group1 = m.group(1)
    group2 = m.group(2)
    if group1 is None or group2 is None:
        return None
    n = int(group1)
    unit = group2.lower()
    if unit == "day":
        delta = timedelta(days=n)
    elif unit == "week":
        delta = timedelta(days=n * 7)
    elif unit == "month":
        delta = timedelta(days=n * 30)
    else:  # year
        delta = timedelta(days=n * 365)
    return (as_of - delta).isoformat()


def _find_nearby_date(
    text: str,
    pos: int,
    window: int = 300,
    as_of: date | None = None,
) -> str | None:
    """Look for a date string near a match position in text.

    First tries absolute date patterns (YYYY-MM-DD, "January 2024", "2024").
    Falls back to relative date parsing ("4 days ago") when as_of is provided.
    """
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    excerpt = text[start:end]
    m = _DATE_NEAR_SIGNAL.search(excerpt)
    if m:
        return m.group(0)
    # Fallback: resolve relative dates when as_of anchor is available
    if as_of is not None:
        return _parse_relative_date(excerpt, as_of)
    return None


def _is_delta_context(snippet: str, match_start: int, lookback: int = 70) -> bool:
    """Return True if the dollar amount at match_start is a delta/increment, not a valuation.

    Checks the ``lookback`` chars BEFORE match_start for increment verbs
    ("boost by", "increase by", "up by", etc.). Only checks the prefix —
    verbs appearing *after* the amount do NOT trigger this.

    Examples:
      "could boost Stripe's valuation by $15B"  -> True  (delta, skip it)
      "Stripe is valued at $159B"               -> False (valid signal)
      "grew 15% to $159B"                       -> False (verb is after $)
    """
    prefix = snippet[max(0, match_start - lookback) : match_start]
    return any(pat.search(prefix) for pat in _DELTA_CONTEXT_PATTERNS)


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


def _recency_multiplier(
    date_str: str | None,
    as_of: date | None = None,
    evidence_type: str | None = None,
) -> float:
    """Age-based decay factor applied on top of evidence-type base confidence.

    Returns a multiplier in [0.30, 1.00]:
      < 6 months  → 1.00 (no decay)
      < 12 months → 0.92
      < 24 months → 0.75
      < 36 months → 0.55
      >= 36 months → 0.30
      unknown date (secondary_market) → 0.70 (stronger penalty for unverified recency)
      unknown date (other types)      → 0.85 (moderate penalty)
    """
    if not date_str:
        # Secondary market signals with unknown dates get a stronger penalty —
        # they should not anchor the average as firmly as confirmed-fresh ones.
        if evidence_type == "secondary_market":
            return 0.70
        return 0.85
    age = _rough_age_months(date_str, as_of)
    if age is None:
        return 0.85
    if age < 6:
        return 1.00
    if age < 12:
        return 0.92
    if age < 24:
        return 0.75
    if age < 36:
        return 0.55
    return 0.30


# ── Source reliability tiers ──────────────────────────────────────────────
# Maps source title keywords (lowercased substring match) to (multiplier, tier_label).
# Checked in order; first match wins.

SOURCE_RELIABILITY_TIERS: list[tuple[str, float, str]] = [
    # Tier 1 (0.95): Premier financial press — factual, editor-reviewed
    ("bloomberg", 0.95, "tier_1_premier_financial"),
    ("reuters", 0.95, "tier_1_premier_financial"),
    ("wall street journal", 0.95, "tier_1_premier_financial"),
    ("wsj.com", 0.95, "tier_1_premier_financial"),
    ("financial times", 0.95, "tier_1_premier_financial"),
    ("ft.com", 0.95, "tier_1_premier_financial"),
    ("new york times", 0.95, "tier_1_premier_financial"),
    ("nytimes.com", 0.95, "tier_1_premier_financial"),
    ("cnbc", 0.95, "tier_1_premier_financial"),
    ("forbes", 0.95, "tier_1_premier_financial"),
    ("sec.gov", 0.95, "tier_1_premier_financial"),
    # Tier 2 (0.85): Specialist tech/VC press — strong editorial
    ("techcrunch", 0.85, "tier_2_specialist_tech"),
    ("axios", 0.85, "tier_2_specialist_tech"),
    ("the information", 0.85, "tier_2_specialist_tech"),
    ("theinformation.com", 0.85, "tier_2_specialist_tech"),
    ("crunchbase", 0.85, "tier_2_specialist_tech"),
    ("pitchbook", 0.85, "tier_2_specialist_tech"),
    ("fortune", 0.85, "tier_2_specialist_tech"),
    ("the verge", 0.85, "tier_2_specialist_tech"),
    # Tier 3 (0.75): General tech/business press
    ("venturebeat", 0.75, "tier_3_general_press"),
    ("business insider", 0.75, "tier_3_general_press"),
    ("insider.com", 0.75, "tier_3_general_press"),
    ("yahoo finance", 0.75, "tier_3_general_press"),
    ("marketwatch", 0.75, "tier_3_general_press"),
    ("seeking alpha", 0.75, "tier_3_general_press"),
    ("wired", 0.75, "tier_3_general_press"),
    ("fast company", 0.75, "tier_3_general_press"),
    # Tier 5 (0.50): Known low-quality / SEO / aggregator
    ("medium.com", 0.50, "tier_5_low_quality"),
    ("substack.com", 0.50, "tier_5_low_quality"),
    ("quora.com", 0.50, "tier_5_low_quality"),
    ("reddit.com", 0.50, "tier_5_low_quality"),
    ("wikipedia.org", 0.50, "tier_5_low_quality"),
]

_DEFAULT_SOURCE_RELIABILITY: tuple[float, str] = (0.65, "tier_4_unrecognized")
_LLM_EXTRACTION_RELIABILITY: tuple[float, str] = (0.80, "tier_llm_synthetic")


def _source_reliability_multiplier(source_title: str | None) -> tuple[float, str]:
    """Return (multiplier, tier_label) based on source publisher identity.

    Checks SOURCE_RELIABILITY_TIERS via case-insensitive substring match on
    source_title. Returns Tier 4 default (0.65) for unrecognized sources and
    None/empty titles.

    Special case: "LLM extraction" returns 0.80 (synthetic signal, no domain trust).
    """
    if not source_title:
        return _DEFAULT_SOURCE_RELIABILITY
    lower = source_title.lower()
    if "llm extraction" in lower or "llm-extracted" in lower:
        return _LLM_EXTRACTION_RELIABILITY
    for keyword, multiplier, label in SOURCE_RELIABILITY_TIERS:
        if keyword in lower:
            return (multiplier, label)
    return _DEFAULT_SOURCE_RELIABILITY


def _classify_evidence_type(
    pattern_label: str,
    amount: float,
    snippet: str,
    date_str: str | None,
    as_of: date | None = None,
    source_title: str | None = None,
) -> tuple[str, float, str]:
    """Return (evidence_type, confidence, source_reliability_tier) for a match.

    Confidence = base_type_confidence × recency_multiplier × source_reliability_multiplier.
    """
    snippet_lower = snippet.lower()

    # Determine base evidence type and confidence
    if pattern_label == "secondary" or any(
        kw in snippet_lower for kw in ("secondary", "tender offer", "buyback", "private share")
    ):
        ev_type = "secondary_market"
        base_conf = EVIDENCE_TYPES["secondary_market"]

    elif pattern_label == "analyst" or any(
        kw in snippet_lower for kw in ("analyst", "estimate", "projection", "forecast")
    ):
        ev_type = "analyst_consensus"
        base_conf = EVIDENCE_TYPES["analyst_consensus"]

    elif pattern_label == "round":
        if date_str:
            age = _rough_age_months(date_str, as_of)
            if age is not None and age < 12:
                ev_type = "post_money_fresh"
                base_conf = EVIDENCE_TYPES["post_money_fresh"]
            elif age is not None and age < 36:
                ev_type = "post_money_stale"
                base_conf = EVIDENCE_TYPES["post_money_stale"]
            else:
                ev_type = "post_money_fresh"
                base_conf = EVIDENCE_TYPES["post_money_fresh"] * 0.8
        else:
            ev_type = "post_money_fresh"
            base_conf = EVIDENCE_TYPES["post_money_fresh"] * 0.8

    elif date_str:
        age = _rough_age_months(date_str, as_of)
        if age is not None and age < 6:
            ev_type = "post_money_fresh"
            base_conf = EVIDENCE_TYPES["post_money_fresh"]
        elif age is not None and age < 18:
            ev_type = "analyst_consensus"
            base_conf = EVIDENCE_TYPES["analyst_consensus"]
        else:
            ev_type = "analyst_consensus"
            base_conf = EVIDENCE_TYPES["analyst_consensus"] * 0.85
    else:
        ev_type = "analyst_consensus"
        base_conf = EVIDENCE_TYPES["analyst_consensus"] * 0.85

    # Apply recency decay — penalises old signals regardless of type
    rec_mult = _recency_multiplier(date_str, as_of, evidence_type=ev_type)
    src_mult, src_tier = _source_reliability_multiplier(source_title)
    return ev_type, round(base_conf * rec_mult * src_mult, 4), src_tier
