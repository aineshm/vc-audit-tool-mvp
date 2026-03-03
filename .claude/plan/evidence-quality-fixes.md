# Plan: Evidence Quality Fixes (Wispr Flow Audit)

## Task Type
- [x] Backend only — Python evidence extraction pipeline

## Summary
Three targeted fixes to `evidence_patterns.py` and `evidence_collector.py` based on gaps
discovered during the Wispr Flow valuation backtrace. No schema changes, no new dependencies.

---

## Bug 1 — Add "according to sources" to _RUMOUR_PATTERNS

### Root Cause
`_RUMOUR_PATTERNS` (evidence_patterns.py:181) does not include indirect-attribution phrases.
TechCrunch's Wispr Flow headline: *"valued at $700 million according to sources"* skips the
×0.70 confidence haircut, giving a rumour-grade source full post_money_fresh weight.

### Fix: extend regex alternation in `_RUMOUR_PATTERNS`

**File:** `src/vc_audit_tool/data_sources/evidence_patterns.py` lines 181-185

**New pattern:**
```python
_RUMOUR_PATTERNS = re.compile(
    r"\b(?:reportedly|rumou?red?|said to be|expected to|in talks|may raise|"
    r"could raise|potential|seeking|at a potential|planning to raise|"
    r"according to sources?|sources? (?:say|said|tell|told)|"
    r"people familiar|people with knowledge|"
    r"familiar with the (?:matter|deal|situation))\b",
    re.IGNORECASE,
)
```

### Phrases added
| Phrase | Example in the wild |
|--------|---------------------|
| `according to sources?` | "valued at $700M according to sources" |
| `sources? say/said/tell/told` | "sources say the valuation is $5B" |
| `people familiar` | "people familiar with the deal say $5B" |
| `people with knowledge` | "people with knowledge of the matter" |
| `familiar with the matter/deal/situation` | "familiar with the deal" |

### Tests to add (in tests/test_evidence_improvements.py — RumourPatternsTests class)
```python
def test_according_to_sources_triggers_haircut(self):
    val, _ = _llm_judge_valuation(
        [700_000_000],
        "Wispr Flow valued at $700 million according to sources",
        ...
    )

def test_is_rumoured_round_according_to_sources(self):
    self.assertTrue(_is_rumoured_round("valued at $700 million according to sources"))

def test_is_rumoured_round_sources_say(self):
    self.assertTrue(_is_rumoured_round("sources say the company is worth $5B"))

def test_is_rumoured_round_people_familiar(self):
    self.assertTrue(_is_rumoured_round("people familiar with the deal say $5B valuation"))

def test_is_rumoured_round_confirmed_does_not_trigger(self):
    self.assertFalse(_is_rumoured_round("Stripe raised $600M at a $6.5B valuation"))
```

---

## Bug 2 — Revenue signal contamination from valuation-context snippets

### Root Cause
`_extract_revenue_signals()` (evidence_collector.py:373) fires whenever `$X [within 50 chars] revenue`.
Problematic DDGS snippet: *"Wispr Flow: Valuation, Revenue & Financial Statements - Growjo"*
concatenated with a $700M valuation figure → `revenue_ltm = $700M` (actual ARR ≈ $10M).

The valuation pattern pipeline has `_is_raise_amount_context()` as a suppressor, but the
revenue extractor has no equivalent guard.

### Fix: add `_is_valuation_context()` helper + apply in `_extract_revenue_signals()`

#### Step A — New helper in `evidence_patterns.py`

```python
_VALUATION_NEAR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bvaluation\b", re.IGNORECASE),
    re.compile(r"\bvalued\s+at\b", re.IGNORECASE),
    re.compile(r"\bpost.?money\b", re.IGNORECASE),
]

def _is_valuation_context(snippet: str, match_start: int, window: int = 60) -> bool:
    """Return True if the dollar amount near match_start is surrounded by valuation keywords.

    Used by _extract_revenue_signals() to suppress false positives where a
    valuation figure appears near the word 'Revenue' in an SEO-style title like
    'Company: Valuation, Revenue & Financial Statements'.
    """
    start = max(0, match_start - window)
    end = min(len(snippet), match_start + window)
    context = snippet[start:end]
    return any(pat.search(context) for pat in _VALUATION_NEAR_PATTERNS)
```

#### Step B — Update `_extract_revenue_signals()` in `evidence_collector.py`

1. Import `_is_valuation_context` from `evidence_patterns`
2. Add guard inside the match loop:
```python
def _extract_revenue_signals(snippet: str, pkg: EvidencePackage) -> None:
    ...
    for pat in rev_patterns:
        m = pat.search(snippet)
        if m:
            try:
                # Suppress if the matched amount is in a valuation context
                # (e.g. "Valuation, Revenue & Financial Statements $700M")
                if _is_valuation_context(snippet, m.start()):
                    continue
                amount = _parse_amount(m.group(1), m.group(2))
                ...
```

### Tests to add (RevenueContaminationTests class)
```python
def test_valuation_revenue_seo_title_suppressed(self):
    """SEO snippet 'Valuation, Revenue & Financial Statements' near $700M must not add revenue."""
    pkg = EvidencePackage(company_name="TestCo")
    snippet = "TestCo: Valuation, Revenue & Financial Statements - Growjo | $700M valuation"
    _extract_revenue_signals(snippet, pkg)
    self.assertEqual(pkg.revenue_signals, [])

def test_clean_revenue_snippet_passes(self):
    """Genuine revenue snippet must still be captured."""
    pkg = EvidencePackage(company_name="TestCo")
    _extract_revenue_signals("TestCo annual revenue of $50M ARR in 2025", pkg)
    self.assertEqual(pkg.revenue_signals, [50_000_000])

def test_revenue_run_rate_not_suppressed(self):
    """Revenue run-rate snippet without valuation keyword passes."""
    pkg = EvidencePackage(company_name="TestCo")
    _extract_revenue_signals("TestCo crosses $10M ARR run rate", pkg)
    self.assertGreater(len(pkg.revenue_signals), 0)
```

---

## Bug 3 — Round date loses day precision ("November 2025" vs "November 20, 2025")

### Root Cause
Two places strip day-level precision:

**A. `_DATE_NEAR_SIGNAL` (evidence_patterns.py:129)**
```python
# Current — does NOT match "November 20, 2025"
r"((?:Month)\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})"
```
`Month\s+\d{4}` requires whitespace then 4-digit year immediately after month name.
"November 20, 2025" has "20," between month and year → no match.

**B. `_extract_round_date_signals()` (evidence_collector.py:396)**
Its inner pattern also only captures `Month YYYY` and `YYYY-MM-DD`.

**C. `_date_sortable()` (evidence_collector.py:55)**
Only handles `%Y-%m-%d`, `%B %Y`, `%b %Y` — not `%B %d, %Y`.

**Impact for Wispr Flow:** "November 2025" → normalises to 2025-11-01 (vs actual Nov 20).
19-day gap means NASDAQ index anchor is 19 days early → small but real valuation error.

### Fix

#### Step A — Extend `_DATE_NEAR_SIGNAL` in `evidence_patterns.py`

```python
_MONTHS_RE = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
)

_DATE_NEAR_SIGNAL = re.compile(
    r"("
    + _MONTHS_RE + r"\s+\d{1,2}[,\s]+\d{4}"   # "November 20, 2025" / "November 20 2025"
    r"|" + _MONTHS_RE + r"\s+\d{4}"             # "November 2025"
    r"|\d{4}-\d{2}-\d{2}"                        # "2025-11-20"
    r"|\d{4}"                                     # "2025"
    r")",
    re.IGNORECASE,
)
```

#### Step B — Extend `_extract_round_date_signals()` in `evidence_collector.py`

```python
_MONTHS_PAT = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
)
round_ctx = re.compile(
    r"(?:series|round|funding|raised|closed)[^.]{0,100}?"
    r"(" + _MONTHS_PAT + r"\s+\d{1,2}[,\s]+\d{4}"   # November 20, 2025
    r"|" + _MONTHS_PAT + r"\s+\d{4}"                  # November 2025
    r"|\d{4}-\d{2}-\d{2})",                            # 2025-11-20
    re.IGNORECASE,
)
```

Avoid recompiling this on every `_extract_round_date_signals()` call — move it to module level.

#### Step C — Extend `_date_sortable()` in `evidence_collector.py`

```python
def _date_sortable(date_str: str) -> str:
    cleaned = date_str.strip()
    for fmt in (
        "%Y-%m-%d",
        "%B %d, %Y", "%b %d, %Y",   # "November 20, 2025"
        "%B %d %Y",  "%b %d %Y",    # "November 20 2025"
        "%B %Y",     "%b %Y",       # "November 2025"
    ):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if cleaned.isdigit() and len(cleaned) == 4:
        return f"{cleaned}-01-01"
    return cleaned
```

### Tests to add (RoundDatePrecisionTests class)
```python
def test_date_near_signal_captures_day_level(self):
    from vc_audit_tool.data_sources.evidence_patterns import _DATE_NEAR_SIGNAL
    m = _DATE_NEAR_SIGNAL.search("closed November 20, 2025 at $5B")
    self.assertIsNotNone(m)
    self.assertEqual(m.group(1), "November 20, 2025")

def test_date_sortable_day_level(self):
    self.assertEqual(_date_sortable("November 20, 2025"), "2025-11-20")
    self.assertEqual(_date_sortable("November 20 2025"), "2025-11-20")

def test_date_sortable_month_level(self):
    self.assertEqual(_date_sortable("November 2025"), "2025-11-01")

def test_extract_round_date_day_level(self):
    pkg = EvidencePackage(company_name="TestCo")
    _extract_round_date_signals("closed Series B November 20, 2025", pkg)
    self.assertIn("November 20, 2025", pkg.round_date_signals)
```

---

## Implementation Order

1. **Bug 1** — `evidence_patterns.py`: extend `_RUMOUR_PATTERNS` (3 lines changed)
2. **Bug 3A** — `evidence_patterns.py`: extend `_DATE_NEAR_SIGNAL` and add `_MONTHS_RE` constant
3. **Bug 2A** — `evidence_patterns.py`: add `_VALUATION_NEAR_PATTERNS` + `_is_valuation_context()`
4. **Bug 3B** — `evidence_collector.py`: move `round_ctx` to module-level `_ROUND_DATE_PATTERN`, extend format list
5. **Bug 3C** — `evidence_collector.py`: extend `_date_sortable()` format list
6. **Bug 2B** — `evidence_collector.py`: import `_is_valuation_context`, apply in `_extract_revenue_signals()`
7. **Tests** — add 12 new tests across 3 test classes
8. **Quality gate** — `ruff check && ruff format --check && mypy src/ && pytest tests/ -q`

---

## Key Files

| File | Operation | Lines affected |
|------|-----------|----------------|
| `src/vc_audit_tool/data_sources/evidence_patterns.py` | Modify | 181-185, 129-133, +new helper ~20 lines |
| `src/vc_audit_tool/data_sources/evidence_collector.py` | Modify | 55-63, 373-392, 395-403, import line |
| `tests/test_evidence_improvements.py` | Modify | +12 new test methods |

---

## Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| `_is_valuation_context()` over-suppresses legitimate revenue near "valued at" in same sentence | Window is 60 chars (narrow); confirmed revenue snippets like "ARR of $50M... valued at $5B" have >60 char gap |
| Extending `_DATE_NEAR_SIGNAL` matches false "20 2025" in numeric text | Pattern requires `Month` word prefix before digit — purely numeric strings won't match |
| New rumour phrases catch over-qualified confirmed rounds ("people familiar with the deal confirmed") | Haircut is ×0.70 not zero; STRONG consensus from 3 sources would still dominate |
| Module-level `_ROUND_DATE_PATTERN` vs inline compile — initialisation order | Define after `_MONTHS_RE` constant (already module-level) |

---

## SESSION_ID
- CODEX_SESSION: N/A (backend unavailable)
- GEMINI_SESSION: N/A (backend unavailable)
