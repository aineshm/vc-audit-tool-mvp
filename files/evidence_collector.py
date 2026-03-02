"""Evidence-first valuation signal collector.

Instead of rigidly assembling a methodology request, this module collects
*all* valuation signals found on the web and scores them by quality.

The resulting ``EvidencePackage`` is then used by ``_assemble_node`` to
pick the highest-quality methodology — or synthesise a direct estimate
when multiple credible sources agree on a number.

Evidence hierarchy (highest to lowest confidence):
  1. Direct secondary-market valuation   (e.g. "SpaceX secondary at $1.25T")
  2. Recent post-money (< 12 months)     (e.g. "closed Series E at $4B")
  3. Analyst consensus estimate          (e.g. "analysts put Stripe at $65B")
  4. Stale post-money (12-36 months)     (used with market-adjustment haircut)
  5. Revenue-implied (comps)             (only when no round data found)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Evidence types ──────────────────────────────────────────────────────

EVIDENCE_TYPES = {
    "secondary_market":  0.90,   # secondary trades, tender offers
    "post_money_fresh":  0.85,   # post-money < 12 months old
    "analyst_consensus": 0.70,   # analyst / press estimates, repeated
    "post_money_stale":  0.50,   # post-money 12-36 months old
    "revenue_implied":   0.30,   # inferred from revenue × sector multiple
}


@dataclass
class ValuationEvidence:
    """A single valuation data point scraped from the web."""

    amount_usd: float
    evidence_type: str
    source_snippet: str
    date_mentioned: str | None = None
    source_title: str | None = None
    confidence: float = 0.5

    def age_months(self, as_of: date | None = None) -> float | None:
        """Return approximate age in months, or None if no date available."""
        if not self.date_mentioned:
            return None
        aod = as_of or date.today()
        try:
            # Try ISO first
            d = date.fromisoformat(str(self.date_mentioned)[:10])
        except ValueError:
            # Try "Month YYYY"
            try:
                d = datetime.strptime(self.date_mentioned.strip()[:8], "%B %Y").date()
            except ValueError:
                return None
        return round((aod - d).days / 30.44, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_usd": self.amount_usd,
            "evidence_type": self.evidence_type,
            "confidence": self.confidence,
            "date_mentioned": self.date_mentioned,
            "source_title": self.source_title,
            "source_snippet": self.source_snippet[:200],
        }


@dataclass
class EvidencePackage:
    """All valuation signals found for a company, ranked by quality."""

    company_name: str
    evidence: list[ValuationEvidence] = field(default_factory=list)
    revenue_signals: list[float] = field(default_factory=list)
    round_date_signals: list[str] = field(default_factory=list)
    extraction_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Derived properties ──────────────────────────────────────────────

    @property
    def best_evidence(self) -> ValuationEvidence | None:
        """Highest-confidence single evidence item."""
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda e: e.confidence)

    @property
    def consensus_valuation(self) -> float | None:
        """Weighted average of all evidence (weighted by confidence).

        Returns None when no evidence exists.  When multiple high-
        confidence items agree within 40%, this becomes a strong signal.
        """
        if not self.evidence:
            return None
        total_w = sum(e.confidence for e in self.evidence)
        if total_w == 0:
            return None
        return sum(e.amount_usd * e.confidence for e in self.evidence) / total_w

    @property
    def consensus_strength(self) -> str:
        """STRONG / MODERATE / WEAK / NONE."""
        if not self.evidence:
            return "NONE"
        high = [e for e in self.evidence if e.confidence >= 0.70]
        if len(high) >= 3:
            vals = [e.amount_usd for e in high]
            spread = (max(vals) - min(vals)) / ((max(vals) + min(vals)) / 2)
            if spread < 0.30:
                return "STRONG"
            return "MODERATE"
        if len(self.evidence) >= 2:
            return "MODERATE"
        if self.evidence:
            return "WEAK"
        return "NONE"

    @property
    def best_revenue(self) -> float | None:
        return max(self.revenue_signals) if self.revenue_signals else None

    @property
    def best_round_date(self) -> str | None:
        return self.round_date_signals[0] if self.round_date_signals else None

    @property
    def best_post_money(self) -> float | None:
        """Best post-money from any evidence type (not secondary-market)."""
        non_secondary = [
            e for e in self.evidence
            if e.evidence_type not in ("secondary_market", "analyst_consensus")
        ]
        if not non_secondary:
            return None
        return max(non_secondary, key=lambda e: e.confidence).amount_usd

    def recommended_methodology(self) -> str:
        """Pick the methodology that best fits the available evidence."""
        strength = self.consensus_strength
        best = self.best_evidence

        # Strong direct evidence → use it directly
        if strength in ("STRONG", "MODERATE") and best is not None:
            if best.confidence >= 0.70:
                return "direct_valuation"

        # Have a recent post-money round
        if best and best.evidence_type == "post_money_fresh" and self.best_round_date:
            return "last_round_market_adjusted"

        # Have revenue + can compute comps
        if self.best_revenue:
            return "comparable_companies"

        # Stale round — still better than nothing
        if best and best.evidence_type == "post_money_stale" and self.best_round_date:
            return "last_round_market_adjusted"

        return "comparable_companies"

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "evidence_count": len(self.evidence),
            "consensus_valuation": self.consensus_valuation,
            "consensus_strength": self.consensus_strength,
            "recommended_methodology": self.recommended_methodology(),
            "best_revenue": self.best_revenue,
            "best_round_date": self.best_round_date,
            "evidence": [e.to_dict() for e in self.evidence[:5]],  # top 5
            "extraction_timestamp": self.extraction_timestamp,
        }


# ── Pattern library ─────────────────────────────────────────────────────

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

_MULTIPLIERS = {
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


def _classify_evidence_type(
    pattern_label: str,
    amount: float,
    snippet: str,
    date_str: str | None,
    as_of: date | None = None,
) -> tuple[str, float]:
    """Return (evidence_type, confidence) for a match."""
    snippet_lower = snippet.lower()

    # Secondary market signals
    if pattern_label == "secondary" or any(
        kw in snippet_lower for kw in ("secondary", "tender offer", "buyback", "private share")
    ):
        return "secondary_market", EVIDENCE_TYPES["secondary_market"]

    # Analyst estimate signals
    if pattern_label == "analyst" or any(
        kw in snippet_lower for kw in ("analyst", "estimate", "projection", "forecast")
    ):
        return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"]

    # Round signals — check staleness
    if pattern_label == "round":
        if date_str:
            # Rough age check
            age = _rough_age_months(date_str, as_of)
            if age is not None and age < 12:
                return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"]
            if age is not None and age < 36:
                return "post_money_stale", EVIDENCE_TYPES["post_money_stale"]
        return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"] * 0.8

    # Direct valuation statement — check if it's a fresh vs old claim
    if date_str:
        age = _rough_age_months(date_str, as_of)
        if age is not None and age < 6:
            return "post_money_fresh", EVIDENCE_TYPES["post_money_fresh"]
        if age is not None and age < 18:
            return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"]

    return "analyst_consensus", EVIDENCE_TYPES["analyst_consensus"] * 0.85


def _rough_age_months(date_str: str, as_of: date | None = None) -> float | None:
    aod = as_of or date.today()
    cleaned = date_str.strip()
    for fmt in ("%Y-%m-%d", "%B %Y", "%b %Y", "%Y"):
        try:
            d = datetime.strptime(cleaned[:len(fmt) + 2], fmt).date()
            return (aod - d).days / 30.44
        except ValueError:
            continue
    return None


def extract_evidence(
    snippets: list[str],
    source_titles: list[str],
    company_name: str,
    as_of: date | None = None,
) -> EvidencePackage:
    """Parse all snippets and return a ranked ``EvidencePackage``.

    This is the main entry point called from ``_web_research_node``.
    """
    pkg = EvidencePackage(company_name=company_name)

    for i, snippet in enumerate(snippets):
        title = source_titles[i] if i < len(source_titles) else None

        for pattern, label in _DIRECT_VALUATION_PATTERNS:
            for m in pattern.finditer(snippet):
                try:
                    # Group indices differ by pattern — try both orderings
                    try:
                        num_str, unit = m.group(1), m.group(2)
                    except IndexError:
                        continue

                    amount = _parse_amount(num_str, unit)

                    # Sanity filter: ignore amounts < $10M or > $10T
                    if amount < 10_000_000 or amount > 10_000_000_000_000:
                        continue

                    date_str = _find_nearby_date(snippet, m.start())
                    ev_type, confidence = _classify_evidence_type(
                        label, amount, snippet, date_str, as_of
                    )

                    pkg.evidence.append(
                        ValuationEvidence(
                            amount_usd=amount,
                            evidence_type=ev_type,
                            source_snippet=snippet[:300],
                            date_mentioned=date_str,
                            source_title=title,
                            confidence=confidence,
                        )
                    )
                except (ValueError, IndexError):
                    continue

        # Revenue signals (separate from valuation evidence)
        _extract_revenue_signals(snippet, pkg)
        _extract_round_date_signals(snippet, pkg)

    # Sort by confidence descending, deduplicate near-identical amounts
    pkg.evidence = _deduplicate(pkg.evidence)
    pkg.evidence.sort(key=lambda e: e.confidence, reverse=True)

    logger.info(
        "evidence_collector: company=%s signals=%d consensus=%s strength=%s",
        company_name,
        len(pkg.evidence),
        f"${pkg.consensus_valuation / 1e9:.1f}B" if pkg.consensus_valuation else "none",
        pkg.consensus_strength,
    )
    return pkg


def _extract_revenue_signals(snippet: str, pkg: EvidencePackage) -> None:
    rev_patterns = [
        re.compile(
            r"\$([\d,.]+)\s*(billion|million|B|M)\b[^.]{0,50}?(?:revenue|arr|mrr|run\s*rate)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:revenue|arr|run\s*rate)[^.]{0,50}?\$([\d,.]+)\s*(billion|million|B|M)\b",
            re.IGNORECASE,
        ),
    ]
    for pat in rev_patterns:
        m = pat.search(snippet)
        if m:
            try:
                amount = _parse_amount(m.group(1), m.group(2))
                if 100_000 < amount < 100_000_000_000:
                    pkg.revenue_signals.append(amount)
            except (ValueError, IndexError):
                pass


def _extract_round_date_signals(snippet: str, pkg: EvidencePackage) -> None:
    round_ctx = re.compile(
        r"(?:series|round|funding|raised|closed)[^.]{0,100}?"
        r"((?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )
    for m in round_ctx.finditer(snippet):
        pkg.round_date_signals.append(m.group(1))


def _deduplicate(evidence: list[ValuationEvidence]) -> list[ValuationEvidence]:
    """Remove near-duplicate evidence (within 15% of each other, same type)."""
    kept: list[ValuationEvidence] = []
    for ev in sorted(evidence, key=lambda e: e.confidence, reverse=True):
        is_dup = any(
            abs(ev.amount_usd - k.amount_usd) / max(k.amount_usd, 1) < 0.15
            and ev.evidence_type == k.evidence_type
            for k in kept
        )
        if not is_dup:
            kept.append(ev)
    return kept
