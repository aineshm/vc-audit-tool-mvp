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

from vc_audit_tool.data_sources.evidence_patterns import (  # noqa: F401
    _DIRECT_VALUATION_PATTERNS,
    EVIDENCE_TYPES,
    SOURCE_RELIABILITY_TIERS,
    _classify_evidence_type,
    _find_nearby_date,
    _is_delta_context,
    _is_raise_amount_context,
    _is_rumoured_round,
    _is_valuation_context,
    _parse_amount,
    _rough_age_months,
    _source_reliability_multiplier,
)

logger = logging.getLogger(__name__)


# ── Date helpers ─────────────────────────────────────────────────────────


def _date_sortable(date_str: str) -> str:
    """Convert a round date string to a sortable ISO-like key.

    Handles: "YYYY-MM-DD", "Month YYYY", "Mon YYYY", "YYYY".
    Returns the original string as fallback so unknown formats still compare.

    Used by ``EvidencePackage.best_round_date`` to select the most recent
    signal rather than the first one found in document order.
    """
    cleaned = date_str.strip()
    for fmt in (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %Y",
        "%b %Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if cleaned.isdigit() and len(cleaned) == 4:
        return f"{cleaned}-01-01"
    return cleaned


# ── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class ValuationEvidence:
    """A single valuation data point scraped from the web."""

    amount_usd: float
    evidence_type: str
    source_snippet: str
    date_mentioned: str | None = None
    source_title: str | None = None
    confidence: float = 0.5
    source_reliability_tier: str | None = None

    def age_months(self, as_of: date | None = None) -> float | None:
        """Return approximate age in months, or None if no date available."""
        if not self.date_mentioned:
            return None
        aod = as_of or date.today()
        try:
            d = date.fromisoformat(str(self.date_mentioned)[:10])
        except ValueError:
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
            "source_reliability_tier": self.source_reliability_tier,
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
        """Return the most recent round date signal, or None.

        Signals are collected in document order, which is search-result order —
        an older article appearing first would otherwise anchor the methodology
        to a stale round date.  Sorting by parsed date ensures the most recent
        confirmed round drives market-adjustment calculations.
        """
        if not self.round_date_signals:
            return None
        return max(self.round_date_signals, key=_date_sortable)

    @property
    def best_post_money(self) -> float | None:
        """Best post-money from any evidence type (not secondary-market)."""
        non_secondary = [
            e
            for e in self.evidence
            if e.evidence_type not in ("secondary_market", "analyst_consensus")
        ]
        if not non_secondary:
            return None
        return max(non_secondary, key=lambda e: e.confidence).amount_usd

    @property
    def avg_confidence(self) -> float:
        """Average confidence of the top-5 evidence items (or all if fewer)."""
        if not self.evidence:
            return 0.0
        top = sorted(self.evidence, key=lambda e: e.confidence, reverse=True)[:5]
        return sum(e.confidence for e in top) / len(top)

    def recommended_methodology(self) -> str:
        """Pick the methodology that best fits the available evidence.

        Priority order — use the first methodology whose inputs are satisfied:
        1. last_round_market_adjusted   — post-money + round date (most auditable)
        2. last_round_multiple_ratchet  — post-money + revenue (no round date)
        3. comparable_companies         — revenue only (sector peer set)
        4. direct_valuation             — last resort when no structured inputs exist
        """
        best = self.best_evidence

        # 1. Any direct valuation signal + round date → market-adjusted last round.
        # Includes secondary_market, post_money_fresh, post_money_stale — all are
        # point-in-time valuations that can be market-adjusted forward.
        _POINT_IN_TIME_TYPES = {
            "secondary_market",
            "post_money_fresh",
            "post_money_stale",
        }
        has_point_valuation = best and best.evidence_type in _POINT_IN_TIME_TYPES
        if has_point_valuation and self.best_round_date:
            return "last_round_market_adjusted"

        # 2. Any point valuation + revenue (no round date) → multiple ratchet
        best_point = next(
            (e for e in self.evidence if e.evidence_type in _POINT_IN_TIME_TYPES), None
        )
        if best_point and self.best_revenue:
            return "last_round_multiple_ratchet"

        # 3. Revenue available → comparable companies
        if self.best_revenue:
            return "comparable_companies"

        # 4. Fallback: direct valuation from evidence signals
        return "direct_valuation"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidencePackage:
        """Deserialise an EvidencePackage from ``to_dict()`` output.

        Used by ``_assemble_node`` to reconstruct the package that was computed
        in ``_web_research_node`` without re-running ``extract_evidence``.  This
        preserves any LLM-judge overrides baked into the package at search time.
        """
        pkg = cls(company_name=d.get("company_name", ""))
        for ev_dict in d.get("evidence", []):
            pkg.evidence.append(
                ValuationEvidence(
                    amount_usd=float(ev_dict["amount_usd"]),
                    evidence_type=str(ev_dict["evidence_type"]),
                    source_snippet=str(ev_dict.get("source_snippet", "")),
                    date_mentioned=ev_dict.get("date_mentioned"),
                    source_title=ev_dict.get("source_title"),
                    confidence=float(ev_dict.get("confidence", 0.5)),
                    source_reliability_tier=ev_dict.get("source_reliability_tier"),
                )
            )
        if d.get("best_revenue"):
            pkg.revenue_signals = [float(d["best_revenue"])]
        # best_round_date is already resolved to a single string at storage time;
        # inject it as the sole entry so max() returns it correctly.
        if d.get("best_round_date"):
            pkg.round_date_signals = [str(d["best_round_date"])]
        return pkg

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "evidence_count": len(self.evidence),
            "consensus_valuation": self.consensus_valuation,
            "consensus_strength": self.consensus_strength,
            "recommended_methodology": self.recommended_methodology(),
            "best_revenue": self.best_revenue,
            "best_round_date": self.best_round_date,
            "evidence": [e.to_dict() for e in self.evidence[:5]],
            "extraction_timestamp": self.extraction_timestamp,
        }


# ── Main entry point ─────────────────────────────────────────────────────


def extract_evidence(
    snippets: list[str],
    source_titles: list[str],
    company_name: str,
    as_of: date | None = None,
    source_dates: list[str | None] | None = None,
) -> EvidencePackage:
    """Parse all snippets and return a ranked ``EvidencePackage``.

    This is the main entry point called from ``_web_research_node``.

    Args:
        snippets: Raw text snippets from web search results.
        source_titles: Titles parallel to snippets.
        company_name: Company being researched.
        as_of: Date anchor for recency calculations and relative date parsing.
        source_dates: Optional list of structured ISO dates from the search
            backend (e.g. DDGS result ``date`` field), parallel to snippets.
            When provided and non-None for a snippet, takes priority over
            text-based date extraction.
    """
    pkg = EvidencePackage(company_name=company_name)

    for i, snippet in enumerate(snippets):
        title = source_titles[i] if i < len(source_titles) else None

        # Prefer structured date from search backend over text extraction.
        structured_date: str | None = None
        if source_dates and i < len(source_dates):
            structured_date = source_dates[i]

        for pattern, label in _DIRECT_VALUATION_PATTERNS:
            for m in pattern.finditer(snippet):
                try:
                    try:
                        num_str, unit = m.group(1), m.group(2)
                    except IndexError:
                        continue

                    amount = _parse_amount(num_str, unit)

                    if amount < 10_000_000 or amount > 10_000_000_000_000:
                        continue

                    # Skip delta/increment amounts (e.g. "boost by $15B from $70B").
                    if _is_delta_context(snippet, m.start()):
                        continue

                    # Skip funding-raise amounts (e.g. "raised $110B" when the
                    # valuation is a separate number like "at a $840B valuation").
                    # Only applied to "direct" label — "round", "secondary",
                    # "analyst", and "direct_val_first" patterns are exempt
                    # because they already extract valuation explicitly.
                    if label == "direct" and _is_raise_amount_context(snippet, m.start()):
                        continue

                    date_str = structured_date or _find_nearby_date(snippet, m.start(), as_of=as_of)
                    ev_type, confidence, src_tier = _classify_evidence_type(
                        label, amount, snippet, date_str, as_of, source_title=title
                    )

                    pkg.evidence.append(
                        ValuationEvidence(
                            amount_usd=amount,
                            evidence_type=ev_type,
                            source_snippet=snippet[:300],
                            date_mentioned=date_str,
                            source_title=title,
                            confidence=confidence,
                            source_reliability_tier=src_tier,
                        )
                    )
                except (ValueError, IndexError):
                    continue

        _extract_revenue_signals(snippet, pkg)
        _extract_round_date_signals(snippet, pkg)

    pkg.evidence = _deduplicate(pkg.evidence)
    pkg.evidence = _filter_outliers(pkg.evidence)
    pkg.evidence.sort(key=lambda e: e.confidence, reverse=True)

    logger.info(
        "evidence_collector: company=%s signals=%d consensus=%s strength=%s",
        company_name,
        len(pkg.evidence),
        f"${pkg.consensus_valuation / 1e9:.1f}B" if pkg.consensus_valuation else "none",
        pkg.consensus_strength,
    )
    return pkg


# ── Signal extractors ────────────────────────────────────────────────────


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
                if _is_valuation_context(snippet, m.start()):
                    continue
                amount = _parse_amount(m.group(1), m.group(2))
                if 100_000 < amount < 100_000_000_000:
                    pkg.revenue_signals.append(amount)
            except (ValueError, IndexError):
                pass


_MONTHS_PAT = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
)

_ROUND_DATE_PATTERN = re.compile(
    r"(?:series|round|funding|raised|closed)[^.]{0,100}?"
    r"(" + _MONTHS_PAT + r"\s+\d{1,2}[,\s]+\d{4}"
    r"|" + _MONTHS_PAT + r"\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _extract_round_date_signals(snippet: str, pkg: EvidencePackage) -> None:
    for m in _ROUND_DATE_PATTERN.finditer(snippet):
        pkg.round_date_signals.append(m.group(1))


def _source_domain(ev: ValuationEvidence) -> str:
    """Extract a short domain identifier for deduplication keying.

    Two evidence items from the same publisher (same source_domain) reporting
    the same valuation are considered duplicates.  Items from different publishers
    reporting the same figure are kept as independent confirmations — this allows
    consensus_strength to correctly fire STRONG when 3+ distinct sources agree.
    """
    title = (ev.source_title or "").lower()
    for keyword, _, _ in SOURCE_RELIABILITY_TIERS:
        if keyword in title:
            return keyword  # e.g. "bloomberg", "techcrunch"
    return title[:30]  # fallback: first 30 chars of title


def _deduplicate(evidence: list[ValuationEvidence]) -> list[ValuationEvidence]:
    """Retain one item per (amount_bucket, evidence_type, source_domain) triple.

    Same source + same amount = duplicate.
    Different sources + same amount = independent confirmation (kept).
    """
    kept: list[ValuationEvidence] = []
    for ev in sorted(evidence, key=lambda e: e.confidence, reverse=True):
        domain = _source_domain(ev)
        is_dup = any(
            abs(ev.amount_usd - k.amount_usd) / max(k.amount_usd, 1) < 0.15
            and ev.evidence_type == k.evidence_type
            and _source_domain(k) == domain
            for k in kept
        )
        if not is_dup:
            kept.append(ev)
    return kept


def _filter_outliers(
    evidence: list[ValuationEvidence],
    outlier_floor_pct: float = 0.10,
) -> list[ValuationEvidence]:
    """Remove signals that are extreme outliers vs the high-confidence median.

    Keeps any signal with amount >= outlier_floor_pct * median(high_conf_amounts).
    Default 10%: for a $130B median, floor is $13B — so $150M and $6.5B are
    filtered while $36B (old but real) survives (then downweighted by recency).

    Returns the original list unchanged when < 3 evidence signals (too few to
    establish a meaningful median).
    """
    if len(evidence) < 3:
        return evidence
    high_conf = [e for e in evidence if e.confidence >= 0.60]
    if len(high_conf) < 2:
        return evidence
    amounts = sorted(e.amount_usd for e in high_conf)
    median_val = amounts[len(amounts) // 2]
    floor = median_val * outlier_floor_pct
    filtered = [e for e in evidence if e.amount_usd >= floor]
    removed = len(evidence) - len(filtered)
    if removed:
        logger.info(
            "evidence_collector: filtered %d outlier signal(s) below $%.1fB floor",
            removed,
            floor / 1e9,
        )
    return filtered
