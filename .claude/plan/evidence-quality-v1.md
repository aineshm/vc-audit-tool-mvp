# Implementation Plan: Evidence Quality — Direct Valuation Accuracy

## Root Cause Analysis (from Stripe run)

### Bug 1 — "Round" pattern captures amount raised, not valuation
Pattern in `evidence_patterns.py:61`:
```python
re.compile(r"(?:raised|closed|completed)\s+[^$]{0,20}?\$([\d,.]+)\s*(billion|million|B|M)\b")
```
Snippet: `"Payments provider Stripe has raised another $150M at a $9B"`
- Extracts: `$150M` (the raise amount) ← WRONG
- Should extract: `$9B` (the at-valuation figure) ← RIGHT

Same bug hits `"raised $6.5 billion in Series I"` → extracts $6.5B as a post-money valuation,
when $6.5B is actually the amount of capital raised.

### Bug 2 — No recency weighting on confidence
A `$36B` signal from 2021 and a `$159B` tender offer from February 2025 both get
`analyst_consensus` base confidence `0.595`. There's zero age penalty. Result: 2021 data
pollutes the weighted average alongside current data.

### Bug 3 — No outlier/noise filter
The `_deduplicate()` guard (15% same-type dedup) doesn't remove cross-type outliers.
`$150M` (noise) and `$159B` (signal) are different types so both survive dedup. With 8 signals
including two sub-$10B noise values, the weighted average is dragged to ~$87B vs the correct ~$120-130B.

### Bug 4 — LLM JSON truncation silently fails
`max_output_tokens=1024` is too small for a structured response over 5,000 chars of input.
Gemini truncates the JSON mid-string. The current handler only tries `json.loads(text)` — one
failure mode: `"Unterminated string starting at: line 1 column 75"`. No recovery attempted.

---

## Technical Solution

Four targeted fixes, all in the evidence pipeline. No architecture changes.

```
[web search snippets]
        │
        ▼
evidence_patterns.py   ← Fix 1: new round pattern (captures "at $Xb valuation")
        │                  Fix 2: _recency_multiplier() applied inside _classify_evidence_type()
        ▼
evidence_collector.py  ← Fix 3: _filter_outliers() called after _deduplicate()
        │
        ▼
direct_valuation.py    (unchanged — cleaner inputs fix the output automatically)
        │
        ▼
llm_adapter.py         ← Fix 4: _extract_json_robust() replaces bare json.loads()
                                  max_output_tokens: 1024 → 2048
```

---

## Implementation Steps

### Step 1 — Fix the "round" regex pattern (`evidence_patterns.py`)

**Remove** the broken pattern that matches `raised $X`:
```python
# REMOVE:
(re.compile(r"(?:raised|closed|completed)\s+[^$]{0,20}?\$([\d,.]+)\s*(billion|million|B|M)\b"), "round"),
```

**Replace** with two precise patterns:
```python
# Pattern A — "raised $X at a $Y valuation / post-money"
# Captures the at-valuation figure, not the raise amount.
(re.compile(
    r"(?:raised|closed|completed)[^.]{0,100}?\bat\s+(?:a\s+)?\$?([\d,.]+)\s*(billion|B)\b"
    r"[^.]{0,60}?(?:valuation|post.money|value)",
    re.IGNORECASE,
), "round"),

# Pattern B — explicit post-money statement (already exists, keep unchanged)
# post-money valuation of $X billion
```

**Expected result for Stripe**: `"raised $150M at a $9B"` → captures `$9B`, not `$150M`.
`"raised $6.5 billion in Series I"` → no match (no "at $Y valuation" clause) → filtered out.

**Deliverable**: `evidence_patterns.py` — `_DIRECT_VALUATION_PATTERNS` list updated.

---

### Step 2 — Add recency multiplier to confidence scoring (`evidence_patterns.py`)

Add a new pure function `_recency_multiplier()`:

```python
def _recency_multiplier(date_str: str | None, as_of: date | None) -> float:
    """Age-based decay factor applied on top of evidence-type base confidence."""
    if not date_str:
        return 0.85  # unknown recency — moderate penalty
    age = _rough_age_months(date_str, as_of)
    if age is None:
        return 0.85
    if age < 6:
        return 1.00   # fresh — no decay
    if age < 12:
        return 0.92   # < 1 year
    if age < 24:
        return 0.75   # 1-2 years
    if age < 36:
        return 0.55   # 2-3 years
    return 0.30       # > 3 years — heavily discounted
```

Update `_classify_evidence_type()` to apply multiplier at the end:

```python
def _classify_evidence_type(pattern_label, amount, snippet, date_str, as_of=None):
    # ... existing type-detection logic unchanged ...
    ev_type, base_confidence = _existing_logic(...)

    # Apply recency decay
    multiplier = _recency_multiplier(date_str, as_of)
    final_confidence = round(base_confidence * multiplier, 4)
    return ev_type, final_confidence
```

**Expected result for Stripe**: `$36B` from 2021 (~60 months old) gets `0.595 * 0.30 = 0.18` confidence.
`$159B` from February 2025 (~1 month old) gets `0.90 * 1.00 = 0.90`. The 2021 signal becomes a
footnote rather than an anchor.

**Deliverable**: `evidence_patterns.py` — `_recency_multiplier()` + updated `_classify_evidence_type()`.

---

### Step 3 — Outlier filter in evidence pipeline (`evidence_collector.py`)

Add `_filter_outliers()` function:

```python
def _filter_outliers(
    evidence: list[ValuationEvidence],
    outlier_floor_pct: float = 0.10,
) -> list[ValuationEvidence]:
    """Remove signals that are extreme outliers vs the high-confidence median.

    Keeps any signal that is ≥ outlier_floor_pct of the median high-confidence
    amount. Default 10%: for Stripe median ~$130B, floor = $13B, so $150M and
    $6.5B are filtered; $36B survives (already heavily penalised by recency).

    Returns the original list unchanged if < 3 evidence signals (not enough
    to compute a meaningful median).
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
        logger.info("evidence_collector: filtered %d outlier signal(s) below $%.1fB floor",
                    removed, floor / 1e9)
    return filtered
```

Call it in `extract_evidence()` after `_deduplicate()`:

```python
pkg.evidence = _deduplicate(pkg.evidence)
pkg.evidence = _filter_outliers(pkg.evidence)   # ← NEW
pkg.evidence.sort(key=lambda e: e.confidence, reverse=True)
```

**Expected result for Stripe**: $150M and $6.5B filtered. Remaining signals: $159B×2, $106.7B, $91.5B, $36B (low weight). Weighted average shifts from ~$87B to ~$125-135B (pre-discount).

**Deliverable**: `evidence_collector.py` — `_filter_outliers()` function + call in `extract_evidence()`.

---

### Step 4 — Robust LLM JSON parsing (`llm_adapter.py`)

Add `_extract_json_robust()`:

```python
def _extract_json_robust(text: str) -> dict[str, Any] | None:
    """Try multiple strategies to recover a valid JSON object from LLM output.

    Handles: markdown fences, truncation, trailing commas.
    """
    # Strip markdown fences
    if "```" in text:
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.strip().startswith("```"))

    text = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: find outermost JSON object bounds
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3: truncation recovery — try closing the JSON at each `"` boundary
    if start != -1:
        for end_pos in range(len(text) - 1, start, -1):
            if text[end_pos] in (",", "\n"):
                candidate = text[start:end_pos].rstrip(", \n") + "\n}"
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

    return None
```

Update `_llm_extract_structured()`:
```python
# Before:
parsed: dict[str, Any] = json.loads(text)

# After:
parsed = _extract_json_robust(text)
if parsed is None:
    logger.warning("LLM extraction failed: could not parse JSON from response")
    return {}
```

Increase token limit in `_get_llm()` for Gemini and Anthropic:
```python
# Gemini: max_output_tokens=1024 → 2048
# Anthropic: max_tokens=1024 → 2048
```

**Expected result**: Truncated responses partially recovered. Full responses correctly parsed even with markdown fences.

**Deliverable**: `llm_adapter.py` — `_extract_json_robust()` + updated token limits.

---

### Step 5 — Tests (`tests/test_evidence_improvements.py`, NEW)

```python
# Test 1: round pattern does NOT extract raise amount
def test_round_pattern_ignores_raise_amount():
    snippets = ["Stripe raised $150M at a $9B valuation."]
    pkg = extract_evidence(snippets, ["src"], "Stripe", date.today())
    amounts = [e.amount_usd for e in pkg.evidence]
    assert 150_000_000 not in amounts          # raise amount excluded
    assert any(abs(a - 9e9) < 1e8 for a in amounts)  # valuation captured

# Test 2: recency multiplier penalises old signals
def test_recency_multiplier_decay():
    from vc_audit_tool.data_sources.evidence_patterns import _recency_multiplier
    from datetime import date
    as_of = date(2026, 2, 27)
    assert _recency_multiplier("February 2025", as_of) == 1.0   # 1 month → no decay
    assert _recency_multiplier("2024", as_of) < 0.95            # ~14 months → decay
    assert _recency_multiplier("2021", as_of) == 0.30           # ~60 months → max decay
    assert _recency_multiplier(None, as_of) == 0.85             # unknown → moderate penalty

# Test 3: outlier filter removes noise below 10% of median
def test_outlier_filter_removes_noise():
    from vc_audit_tool.data_sources.evidence_collector import _filter_outliers, ValuationEvidence
    evidence = [
        ValuationEvidence(159e9, "secondary_market", "s1", confidence=0.90),
        ValuationEvidence(106e9, "post_money_fresh",  "s2", confidence=0.85),
        ValuationEvidence( 91e9, "secondary_market",  "s3", confidence=0.90),
        ValuationEvidence(150e6, "post_money_fresh",  "s4", confidence=0.68),  # noise
        ValuationEvidence(6.5e9, "post_money_fresh",  "s5", confidence=0.68),  # borderline
    ]
    filtered = _filter_outliers(evidence)
    amounts = [e.amount_usd for e in filtered]
    assert 150e6 not in amounts   # $150M filtered
    assert 159e9 in amounts       # $159B kept

# Test 4: LLM JSON truncation recovery
def test_llm_json_truncation_recovery():
    from vc_audit_tool.agent.llm_adapter import _extract_json_robust
    truncated = '{"last_post_money_valuation": 159000000000, "last_round_date": "2025-02'
    result = _extract_json_robust(truncated)
    assert result is not None
    assert result["last_post_money_valuation"] == 159_000_000_000

# Test 5: markdown fence stripping
def test_llm_json_markdown_stripped():
    from vc_audit_tool.agent.llm_adapter import _extract_json_robust
    fenced = '```json\n{"last_post_money_valuation": 159000000000}\n```'
    result = _extract_json_robust(fenced)
    assert result is not None
    assert result["last_post_money_valuation"] == 159_000_000_000

# Test 6: Stripe end-to-end weighted average in expected range
def test_stripe_weighted_average_in_range(monkeypatch):
    """After fixes, consensus valuation should be 100B-160B (not 78B)."""
    # ... mock DuckDuckGo to return canned Stripe snippets with known amounts
    # ... assert 100e9 <= pkg.consensus_valuation <= 160e9
```

**Deliverable**: `tests/test_evidence_improvements.py` — 6 tests.

---

## Key Files

| File | Operation | Lines |
|------|-----------|-------|
| `src/vc_audit_tool/data_sources/evidence_patterns.py` | Modify | ~35 new |
| `src/vc_audit_tool/data_sources/evidence_collector.py` | Modify | ~25 new |
| `src/vc_audit_tool/agent/llm_adapter.py` | Modify | ~30 new |
| `tests/test_evidence_improvements.py` | Create | ~120 |

**Files NOT touched**: `direct_valuation.py`, `web_research.py`, `assemble.py`, `server.py`
(cleaner evidence inputs fix the output automatically — no methodology logic changes needed).

---

## Expected Output After Fix (Stripe)

| Signal | Amount | Type | Age | Base conf | Recency | Final conf |
|--------|--------|------|-----|-----------|---------|------------|
| TechCrunch $159B tender | $159B | secondary | 1mo | 0.90 | 1.00 | **0.90** |
| Reuters $91.5B tender | $91.5B | secondary | 1mo | 0.90 | 1.00 | **0.90** |
| Stats 2026 $106.7B | $106.7B | post_money | 5mo | 0.85 | 1.00 | **0.85** |
| Analyst $159B | $159B | analyst | 1mo | 0.70 | 1.00 | **0.70** |
| Bloomberg $100B | $100B | analyst | ~18mo | 0.70 | 0.75 | **0.53** |
| Series G $36B 2021 | $36B | analyst | ~60mo | 0.595 | 0.30 | **0.18** |
| **$150M raise** | ~~$150M~~ | filtered | — | — | — | **REMOVED** |
| **$6.5B raise** | ~~$6.5B~~ | filtered | — | — | — | **REMOVED** |

Weighted average (pre-discount): **~$133B**
After 10% illiquidity discount: **~$120B** ← in market consensus range ($91.5B–$159B)

---

## Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Outlier filter too aggressive — removes valid small-cap signals | `outlier_floor_pct=0.10` (10% of median). Only removes if median is well-established (≥2 high-conf signals). Configurable. |
| Recency multiplier too aggressive — penalises pre-revenue companies | For companies with no date on signals, 0.85 default preserves most confidence. |
| New round pattern too strict — misses valid rounds | Keep existing `post.money` pattern unchanged; only the `raised X at Y` pattern is modified. |
| JSON repair produces wrong partial result | Recovered partial dict is still better than `{}`. LLM path is optional enrichment on top of regex. |

---

## Quality Gate

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
python3 -m pytest tests/test_evidence_improvements.py -v   # new tests
python3 -m pytest tests/ -q                                # full suite (~477 expected)
```
