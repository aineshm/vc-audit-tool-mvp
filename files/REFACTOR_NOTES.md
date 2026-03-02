# VC Audit Tool — Evidence-First Refactor

## What Was Wrong

The original agent had a fundamental logic error: it tried to *compute* a valuation from first
principles (comps, multiples, market adjustment) even when the web had already *told it* the
answer. For SpaceX at $1.25T, the old agent would hunt for EV/Revenue comps from the aerospace
SIC code — an absurd approach when dozens of credible sources report the number directly.

The system was also structurally over-engineered: 35 source files for what is conceptually a
3-step problem:
  1. Find evidence of what the company is worth
  2. Score that evidence by quality
  3. Pick the methodology that fits the evidence quality

---

## The Fix: Evidence Hierarchy

The refactor adds one concept that was entirely missing: **evidence quality scoring**.

```
Level 1: Direct evidence (confidence ≥ 0.70)
         → secondary market trades, tender offers, confirmed rounds < 6 months old
         → methodology: direct_valuation
         → example: SpaceX secondary trades at $1.25T → point estimate $1.125T (10% illiquidity haircut)

Level 2: Anchored (fresh round)
         → post-money < 12 months, market-adjusted to today
         → methodology: last_round_market_adjusted

Level 3: Market-derived (revenue exists)
         → revenue × sector median EV/Revenue multiple
         → methodology: comparable_companies

Level 4: Stale anchor
         → post-money > 12 months, still better than pure comps
         → methodology: last_round_market_adjusted (with HIGH staleness flag)
```

The agent picks Level 1 when evidence is STRONG or MODERATE. It falls back down the hierarchy
when evidence is too thin.

---

## New Files

### `data_sources/evidence_collector.py`
Replaces the flat `web_facts` dict with a structured `EvidencePackage`. Key properties:

- `evidence: list[ValuationEvidence]` — all valuation signals found, ranked by confidence
- `consensus_valuation` — weighted average of all signals (weighted by confidence)
- `consensus_strength` — STRONG / MODERATE / WEAK / NONE
  - STRONG: 3+ high-confidence signals within 30% of each other
  - MODERATE: 2+ signals or 1 very high-confidence signal
  - WEAK: 1 signal
  - NONE: nothing found
- `recommended_methodology()` — returns the right methodology for the evidence at hand

Evidence types and confidence levels:
```python
EVIDENCE_TYPES = {
    "secondary_market":  0.90,   # SpaceX tender offer at $X
    "post_money_fresh":  0.85,   # Round closed < 12 months ago
    "analyst_consensus": 0.70,   # Multiple sources agree on $X
    "post_money_stale":  0.50,   # Round > 12 months ago
    "revenue_implied":   0.30,   # Computed from revenue × multiple
}
```

### `methodologies/direct_valuation.py`
A new methodology that synthesises evidence signals directly:
- Weighted-average point estimate (weight = evidence confidence)
- Range = min/max across evidence items (or ±15% if single signal)
- Illiquidity discount: 10% when secondary-market evidence exists (already has price discovery),
  20% otherwise
- Full audit trail: cites each evidence item with its type, date, and source

### `agent/research.py` (replacement)
Key changes:
1. `_web_research_node` now calls `extract_evidence()` in addition to LLM extraction.
   Evidence and LLM facts are merged — LLM adds signals regex missed, regex catches
   what LLM missed.
2. `_assemble_node` uses `pkg.recommended_methodology()` as the default, not a
   fixed priority list.
3. Smarter search queries target valuation signals explicitly:
   `"{name}" secondary market valuation tender offer` etc.

---

## What Changes for the User

**Before (SpaceX):**
```
agent.run("SpaceX")
→ no Form D (private)
→ no revenue found
→ methodology: last_round_market_adjusted
→ missing: last_post_money_valuation, last_round_date
→ result: assembled_request = None
```

**After (SpaceX):**
```
agent.run("SpaceX")
→ web search finds: "$1.25T valuation" (secondary), "$180B valuation" (2023 round),
    "$350B valuation" (2024 tender), "analyst: $300-500B range"
→ evidence package:
    secondary_market: $1.25T @ 0.90
    post_money_fresh: $350B @ 0.85 (2024 tender)
    analyst_consensus: $400B @ 0.70
    post_money_stale: $180B @ 0.50 (2023)
  consensus: ~$950B (weighted) | strength: STRONG
→ methodology: direct_valuation
→ point estimate: $950B × 0.90 (10% secondary discount) = ~$855B
→ assembled_request: complete ✓
```

---

## What the Refactor Does NOT Change

- All existing methodologies (comps, last_round, ratchet, scorecard, berkus) are untouched
- The reconciliation layer (/reconcile endpoint) is untouched
- The SQLite store, CLI, server routes are untouched
- Test structure is untouched (new tests needed for the 3 new files)
- The Protocol-based data source architecture is untouched

---

## Remaining Over-Engineering (Honest Assessment)

The reconciliation layer (`reconciliation/`) adds ~500 lines for a feature that essentially
duplicates what `direct_valuation` now does for companies with good web data. For most real
research-first workflows, the decision tree is:

```
strong evidence → direct_valuation (done in one call)
weak evidence  → comps or last_round (done in one call)
both          → direct_valuation with comps as a sanity check (two calls, simple average)
```

The YAML-rules MethodologySelector was built for a world where methodology selection is
complex and stage-dependent. That's true for early-stage companies. For growth/late-stage
companies with public web signals, the evidence hierarchy is simpler and more correct.

**Recommendation:** Keep the reconciliation layer for pre-seed/seed companies (where
scorecard and berkus are genuinely needed). For growth/late stage, route through
`direct_valuation` first, use comps as a cross-check, and skip the full reconciliation
machinery.

---

## Integration Steps

1. Copy the 3 new files into the project
2. Apply the 2-line engine.py patch (see `engine_patch.py`)
3. Run: `python -m pytest tests/ -q` — existing tests should still pass
4. Add tests for the new files (see test patterns in `test_epic3.py`)
