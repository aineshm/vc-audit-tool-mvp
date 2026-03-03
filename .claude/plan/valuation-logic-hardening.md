# Implementation Plan: Valuation Logic Hardening

**Source**: Review of valuation logic & agentic workflow (2026-03-03)
**Scope**: Critical + High + selected Medium fixes from the review table.
**Approach**: Pure backend changes (no frontend or schema changes). All changes are
additive or surgical replacements — no architectural rewrites.

---

## Task Type
- [x] Backend only

---

## Implementation Steps

### Step 1 — Dynamic year tokens in search queries
**Files**: `src/vc_audit_tool/agent/nodes/web_research.py`
**Review items**: #1 (Critical)
**Effort**: ~15 lines

`_SEARCH_QUERIES` hard-codes `2024 OR 2025`. Pass `as_of` into `_ddg_search()` and
generate the year-range string dynamically.

```python
# Current signature
def _ddg_search(company_name: str, max_results_per_query: int = 6) -> ...:
    return _ddg_search_queries(company_name, _SEARCH_QUERIES, max_results_per_query)

# After
def _ddg_search(
    company_name: str,
    max_results_per_query: int = 6,
    as_of: date | None = None,
) -> ...:
    queries = _make_queries(as_of)   # new helper
    return _ddg_search_queries(company_name, queries, max_results_per_query)

def _make_queries(as_of: date | None = None) -> list[str]:
    aod = as_of or date.today()
    year_range = f"{aod.year - 1} OR {aod.year}"
    return [q.replace("2024 OR 2025", year_range) for q in _SEARCH_QUERIES]
```

Update caller in `_web_research_node`:
```python
raw_snippets, source_titles, source_dates = _ddg_search(name, as_of=as_of)
```

Also update `_TARGETED_QUERIES["round_date"]` which hard-codes `2022 OR 2023 OR 2024`:
```python
"round_date": [
    f'"{name}" funding date closed announced {aod.year-2} OR {aod.year-1} OR {aod.year}',
    ...
],
```
Thread `as_of` into the adaptive loop so targeted queries also get fresh years.

Cache key must include the year range to avoid stale cache hits across year boundaries:
```python
_cache_key = f"{company_name}|{_today}|{year_range}|{'|'.join(query_templates)}"
```

---

### Step 2 — Replace `max()` merge with date-anchored valuation selection
**Files**: `src/vc_audit_tool/agent/nodes/web_research.py`
**Review items**: #2 (Critical)
**Effort**: ~40 lines

Current code picks `max(pkg_best_val, llm_post_money)`. This is wrong for down-rounds
or when LLM picks an older higher value. Replace with a new helper that picks the
candidate associated with the **more recent date**.

```python
def _select_valuation_by_recency(
    pkg_candidates: list[ValuationEvidence],
    llm_post_money: float | None,
    llm_round_date: str | None,
) -> float | None:
    """
    Return the valuation amount whose associated date is most recent.

    Priority:
    1. If only one source has a value, use it.
    2. If both have a value and a round date, prefer the more recent one.
    3. If dates are missing or equal, fall back to the higher-confidence
       evidence-package value (regex patterns are raise-suppressed, more reliable
       than the raw LLM number when both lack dates).
    """
    pkg_top = max(pkg_candidates, key=lambda e: e.confidence) if pkg_candidates else None
    pkg_val = pkg_top.amount_usd if pkg_top and pkg_top.confidence >= 0.60 else None
    pkg_date = pkg_top.date_mentioned if pkg_top else None

    if pkg_val and llm_post_money:
        # Compare dates
        pkg_sortable = _date_sortable(pkg_date) if pkg_date else ""
        llm_sortable = _date_sortable(llm_round_date) if llm_round_date else ""
        if pkg_sortable and llm_sortable:
            return pkg_val if pkg_sortable >= llm_sortable else llm_post_money
        # No date info — prefer evidence package (raise-safe regex)
        return pkg_val
    return pkg_val or llm_post_money
```

Replace the `max()` call in `_build_web_facts`:
```python
# Old
chosen_post_money = max(pkg_best_val, llm_post_money)

# New
chosen_post_money = _select_valuation_by_recency(
    pkg_post_money_candidates,
    llm_post_money,
    llm_facts.get("last_round_date"),
)
```

Import `_date_sortable` from evidence_collector at the top of the file (already used
by `_most_recent_date`).

---

### Step 3 — Fix degenerate ratchet: separate `revenue_at_last_round` from current
**Files**:
- `src/vc_audit_tool/agent/llm_adapter.py` (extraction prompt)
- `src/vc_audit_tool/agent/nodes/web_research.py` (`_build_web_facts`)
- `src/vc_audit_tool/agent/nodes/assemble.py` (`_assemble_comps`)

**Review items**: #4 (Critical)
**Effort**: ~35 lines

Currently `revenue_at_last_round = revenue = current_revenue` → ratchet revenue growth is
always 0%.

**3a** — Add `revenue_at_last_round` to the LLM extraction prompt:
```python
- revenue_at_last_round: number or null
  <rule>Revenue the company had AT THE TIME of the last funding round, not today's revenue.
  Only populate when both the round date and a historical revenue figure for that period
  are mentioned. Return null when only current revenue is available.</rule>
```

**3b** — Extract it in `_build_web_facts`:
```python
return {
    ...
    "revenue_ltm": pkg.best_revenue,
    "revenue_at_last_round": llm_facts.get("revenue_at_last_round"),
    ...
}
```

**3c** — Use it in `_assemble_comps`, with `revenue_ltm` as fallback:
```python
rev_at_round = web_facts.get("revenue_at_last_round") or revenue
payload = {
    ...
    "revenue_at_last_round": rev_at_round,
    "current_revenue": revenue,   # always current
    ...
}
```

When `revenue_at_last_round` is `None` (the common case), the ratchet will use the
same value for both fields — explicit and intentional, and derivation steps will note it.

---

### Step 4 — LLM judge: pass relevant snippets, add proximity guard, surface reason
**Files**:
- `src/vc_audit_tool/agent/nodes/web_research.py` (judge invocation)
- `src/vc_audit_tool/agent/llm_adapter.py` (judge prompt + return validation)

**Review items**: #3 (Critical), #10 (Medium), #13 (Medium)
**Effort**: ~50 lines

**4a** — Pass candidate-relevant snippets to the judge instead of `raw_snippets[:5]`.
Build a relevance set: snippets that contain any of the candidate dollar amounts.

```python
def _relevant_snippets_for_judge(
    candidates: list[ValuationEvidence],
    raw_snippets: list[str],
    max_snippets: int = 8,
) -> list[str]:
    """Return snippets most likely to contain evidence about candidate values."""
    # Build amount strings to search for (e.g. "5B", "5 billion", "$5")
    needles: list[str] = []
    for ev in candidates:
        b = ev.amount_usd / 1e9
        needles += [f"{b:.0f}B", f"{b:.1f}B", f"{b:.0f} billion"]

    scored: list[tuple[int, str]] = []
    for snippet in raw_snippets:
        sl = snippet.lower()
        score = sum(1 for n in needles if n.lower() in sl)
        if score > 0:
            scored.append((score, snippet))
    scored.sort(key=lambda x: x[0], reverse=True)
    result = [s for _, s in scored[:max_snippets]]
    # Always include at least the first 3 snippets as fallback
    for s in raw_snippets[:3]:
        if s not in result:
            result.append(s)
    return result[:max_snippets]
```

Update the judge call site:
```python
judge_snippets = _relevant_snippets_for_judge(judge_candidates, raw_snippets)
judged_val, judge_reason = _llm_judge_valuation(
    llm, model_label, name, judge_candidates, judge_snippets, ...
)
```

**4b** — Change judge prompt to return `candidate_id` and a longer reason:
```python
'Return JSON only: {"candidate_id": <1-5 or null>, "validated_valuation": <USD or null>,'
' "reason": "<one sentence, cite source>"}'
```

**4c** — Add proximity validation in `_llm_judge_valuation`:
```python
# After parsing val:
candidate_amounts = [c.amount_usd for c in candidates]
if not any(abs(val - a) / max(a, 1) < 0.05 for a in candidate_amounts):
    logger.warning(
        "llm_judge: returned $%.2fB not close to any candidate — discarding",
        val / 1e9,
    )
    return None, None
return float(val), reason
```

**4d** — Surface judge reason in `research_metadata`. In `_web_research_node`, store it:
```python
if judged_val is not None:
    web_facts["last_post_money_valuation"] = judged_val
    web_facts["llm_judge_reason"] = judge_reason   # new field
```
In `_assemble_node`, include `llm_judge_reason` in `research_metadata["extracted_facts"]`.

---

### Step 5 — LLM prompt: add `as_of_date`, `sector`, and remove `valuation_signals`
**Files**: `src/vc_audit_tool/agent/llm_adapter.py`
**Review items**: #7 (High), Prompt-A, Prompt-B, Prompt-C
**Effort**: ~40 lines

**5a** — Add `as_of_date` and `sector` parameters to `_build_messages()`:
```python
def _build_messages(
    company_name: str,
    combined: str,
    provider_name: str,
    as_of_date: str = "",
    sector_hint: str = "",
) -> list[Any]:
    context_block = ""
    if as_of_date:
        context_block += f"<context>\nToday's date: {as_of_date}. "
        context_block += "Prefer data from the last 24 months. "
        context_block += "The most recent confirmed round is the one that matters.\n</context>\n\n"
    if sector_hint:
        context_block += f"<sector_hint>{sector_hint}</sector_hint>\n\n"

    user_content = (
        f"{context_block}"
        f"<company>{company_name}</company>\n\n"
        f"<snippets>\n{combined}\n</snippets>"
    )
    ...
```

Pass from `_llm_extract_structured`:
```python
def _llm_extract_structured(
    llm, model_label, company_name, snippets,
    tracker=None, provider_cfg=None,
    as_of_date: str = "",
    sector_hint: str = "",
) -> dict[str, Any]:
    ...
    messages = _build_messages(company_name, combined, provider_name, as_of_date, sector_hint)
```

Pass from `_web_research_node`:
```python
llm_facts = _llm_extract_structured(
    llm, model_label, name, raw_snippets,
    tracker=cost_tracker, provider_cfg=provider_cfg,
    as_of_date=as_of.isoformat(),
    sector_hint=state.get("inferred_sector", ""),
)
```

**5b** — Remove `valuation_signals` from `_LLM_SYSTEM_PROMPT`. The array is parsed
but never consumed. Removing it saves ~60 output tokens per call and reduces hallucination
surface:
```python
# Remove this block from _LLM_SYSTEM_PROMPT:
# - valuation_signals: array of objects
#   <schema>[{"amount_usd": number, "source": string, "date": string|null,
#              "type": "post_money"|"secondary_market"|"analyst_estimate"}]</schema>
```

**5c** — Add `sector` as an extraction field with explicit options:
```python
- sector: string or null
  <rule>Infer from the company's business model and news context.
  Use ONLY one of: enterprise_software | fintech | payments | consumer |
  ecommerce | cybersecurity | semiconductors | AI_infrastructure |
  biotech | cleantech | defense | telecommunications | media | gaming | hardware
  Return null when sector is ambiguous.</rule>
```

Consume `llm_facts.get("sector")` in `_web_research_node` and return it in
`web_facts["llm_inferred_sector"]`. In `_assemble_node`, prefer this over the
keyword-inferred sector:
```python
sector = (
    web_facts.get("llm_inferred_sector")
    or state.get("inferred_sector", "enterprise_software")
)
```

---

### Step 6 — Evidence deduplication: allow multi-source confirmation
**Files**: `src/vc_audit_tool/data_sources/evidence_collector.py`
**Review items**: #8 (High)
**Effort**: ~30 lines

Current dedup groups by `(amount ±15%, evidence_type)`. Three independent Bloomberg/TC/Reuters
articles citing the same $65B all collapse to 1 signal → `consensus_strength = WEAK`.

Change: deduplicate by `(amount ±15%, evidence_type, source_domain)`, not just by amount+type.
Same source domain + same amount = dup. Different source domain + same amount = independent confirmation.

```python
def _source_domain(ev: ValuationEvidence) -> str:
    """Extract a short domain identifier for dedup keying."""
    title = (ev.source_title or "").lower()
    for keyword, _, _ in SOURCE_RELIABILITY_TIERS:
        if keyword in title:
            return keyword   # e.g. "bloomberg", "techcrunch"
    return title[:30]   # fallback: first 30 chars of title

def _deduplicate(evidence: list[ValuationEvidence]) -> list[ValuationEvidence]:
    """Retain one item per (amount_bucket, evidence_type, source_domain) triple."""
    kept: list[ValuationEvidence] = []
    for ev in sorted(evidence, key=lambda e: e.confidence, reverse=True):
        domain = _source_domain(ev)
        is_dup = any(
            abs(ev.amount_usd - k.amount_usd) / max(k.amount_usd, 1) < 0.15
            and ev.evidence_type == k.evidence_type
            and _source_domain(k) == domain   # same source → dup
            for k in kept
        )
        if not is_dup:
            kept.append(ev)
    return kept
```

This allows `consensus_strength = STRONG` (3+ high-confidence items) to fire correctly
when three distinct publications independently report the same valuation.

---

### Step 7 — Rumoured-round confidence haircut
**Files**: `src/vc_audit_tool/data_sources/evidence_patterns.py`
**Review items**: #9 (Medium)
**Effort**: ~20 lines

Add a `_is_rumoured_round(snippet: str) -> bool` helper:
```python
_RUMOUR_PATTERNS = re.compile(
    r"\b(?:reportedly|rumoured?|said to be|expected to|in talks|may raise|"
    r"could raise|potential|seeking|at a potential|planning to raise)\b",
    re.IGNORECASE,
)

def _is_rumoured_round(snippet: str) -> bool:
    return bool(_RUMOUR_PATTERNS.search(snippet))
```

Apply a `×0.70` haircut in `_classify_evidence_type` after the base confidence is set:
```python
# At the end of _classify_evidence_type, before return:
if _is_rumoured_round(snippet):
    base_conf *= 0.70   # unconfirmed raise, lower evidential weight

rec_mult = _recency_multiplier(date_str, as_of, evidence_type=ev_type)
...
```

---

### Step 8 — Assemble node: use existing evidence package from state
**Files**:
- `src/vc_audit_tool/data_sources/evidence_collector.py` (add `EvidencePackage.from_dict`)
- `src/vc_audit_tool/agent/nodes/assemble.py` (use it)

**Review items**: #5 (High)
**Effort**: ~40 lines

Add a `from_dict` classmethod to `EvidencePackage`:
```python
@classmethod
def from_dict(cls, d: dict[str, Any]) -> "EvidencePackage":
    pkg = cls(company_name=d.get("company_name", ""))
    for ev_dict in d.get("evidence", []):
        pkg.evidence.append(
            ValuationEvidence(
                amount_usd=ev_dict["amount_usd"],
                evidence_type=ev_dict["evidence_type"],
                source_snippet=ev_dict.get("source_snippet", ""),
                date_mentioned=ev_dict.get("date_mentioned"),
                source_title=ev_dict.get("source_title"),
                confidence=ev_dict.get("confidence", 0.5),
                source_reliability_tier=ev_dict.get("source_reliability_tier"),
            )
        )
    pkg.revenue_signals = [d["best_revenue"]] if d.get("best_revenue") else []
    return pkg
```

In `_assemble_node`, use it instead of re-running `extract_evidence`:
```python
# Old
if raw_snippets:
    pkg = extract_evidence(raw_snippets, source_titles, name, as_of)
else:
    pkg = EvidencePackage(company_name=name)

# New
if evidence_pkg_dict:
    pkg = EvidencePackage.from_dict(evidence_pkg_dict)
elif raw_snippets:
    pkg = extract_evidence(raw_snippets, source_titles, name, as_of)
else:
    pkg = EvidencePackage(company_name=name)
```

This ensures the LLM judge's override propagates into `pkg.recommended_methodology()`
because the `evidence_package` dict reflects the judged state.

Note: `best_round_date` deserialized from dict will be read back as a single string
(the already-selected most-recent date), which is correct.

---

## Key Files Summary

| File | Step | Change |
|------|------|--------|
| `web_research.py` | 1 | `_make_queries(as_of)`, thread year range into cache key and targeted queries |
| `web_research.py` | 2 | Replace `max()` with `_select_valuation_by_recency()` |
| `web_research.py` | 3b | Add `revenue_at_last_round` to web_facts output |
| `web_research.py` | 4a | `_relevant_snippets_for_judge()`, update judge call site |
| `web_research.py` | 4d | Store `llm_judge_reason` in web_facts |
| `web_research.py` | 5a | Pass `as_of_date`, `sector_hint` to `_llm_extract_structured` |
| `web_research.py` | 5c | Return `llm_inferred_sector` in web_facts |
| `llm_adapter.py` | 3a | Add `revenue_at_last_round` field to `_LLM_SYSTEM_PROMPT` |
| `llm_adapter.py` | 4b | Judge prompt: add `candidate_id`, longer reason |
| `llm_adapter.py` | 4c | Proximity guard on judge return value |
| `llm_adapter.py` | 4d | `_llm_judge_valuation` returns `(float|None, str|None)` 2-tuple |
| `llm_adapter.py` | 5a | `_build_messages(as_of_date, sector_hint)` |
| `llm_adapter.py` | 5b | Remove `valuation_signals` from prompt |
| `llm_adapter.py` | 5c | Add `sector` extraction field |
| `assemble.py` | 3c | Use `revenue_at_last_round` from web_facts with fallback |
| `assemble.py` | 5c | Prefer `llm_inferred_sector` over keyword sector |
| `assemble.py` | 8 | Use `EvidencePackage.from_dict` instead of re-extracting |
| `evidence_collector.py` | 6 | `_source_domain()`, update `_deduplicate()` |
| `evidence_collector.py` | 8 | Add `EvidencePackage.from_dict()` |
| `evidence_patterns.py` | 7 | `_is_rumoured_round()`, confidence haircut in `_classify_evidence_type` |

---

## Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Year-range cache key change invalidates existing cache entries | Acceptable — cache is in-process only (no disk persistence). Old entries just expire unused. |
| `_select_valuation_by_recency` may return LLM value over evidence-package value | Only happens when LLM date is more recent AND both have dates. Evidence package is still preferred when dates are equal or absent. |
| `EvidencePackage.from_dict` round-trips lose `round_date_signals` list | `best_round_date` is already resolved at storage time; downstream only reads `best_round_date`. Acceptable. |
| `_llm_judge_valuation` now returns 2-tuple — breaks existing call sites | Only one call site in `web_research.py`. Update simultaneously. Add return-type annotation. |
| Removing `valuation_signals` from prompt may break tests that assert the field | Search `tests/` for `valuation_signals`; update or remove assertions. |
| Dedup change allows more evidence items → performance impact | `_filter_outliers` caps at consensus-based floor; `to_dict` caps at 5 items. No significant impact. |
| Rumour haircut (×0.70) may incorrectly fire on valid confirmed rounds | Pattern checked: only fires on explicit uncertainty words. Real closed-round language ("closed", "announced") doesn't match. |

---

## Implementation Order

Implement in this order to minimise merge conflicts and enable incremental testing:

1. **Step 5 (prompt)** — standalone prompt-only change, easy to test with a mock LLM
2. **Step 7 (rumour haircut)** — isolated to `evidence_patterns.py`, has no dependencies
3. **Step 1 (year tokens)** — isolated to `web_research.py`, easy to verify
4. **Step 6 (dedup)** — isolated to `evidence_collector.py`
5. **Step 8 (from_dict)** — adds method to EvidencePackage, then updates assemble.py
6. **Step 3 (revenue_at_last_round)** — spans prompt + web_facts + assemble; do together
7. **Step 2 (date-anchored merge)** — replaces logic in _build_web_facts
8. **Step 4 (judge improvements)** — most complex; do last, depends on steps 2 and 5

---

## Test Plan

For each step, write or update tests in the following files:

| Step | Test File | What to test |
|------|-----------|-------------|
| 1 | `tests/test_epic3.py` | `_make_queries(date(2026,3,1))` generates "2025 OR 2026" |
| 2 | `tests/test_epic3.py` | Down-round: pkg=$45B (2021), llm=$6.7B (2022) → picks $6.7B |
| 3 | `tests/test_evidence_improvements.py` | ratchet uses separate revenue_at_last_round when LLM provides it |
| 4 | `tests/test_evidence_improvements.py` | judge: hallucinated value outside candidates → returns None; reason in web_facts |
| 5 | `tests/test_epic3.py` | LLM extraction prompt contains as_of_date and sector_hint; valuation_signals absent |
| 6 | `tests/test_evidence.py` | 3 items same amount, 3 different source domains → all 3 kept (STRONG consensus) |
| 7 | `tests/test_evidence_improvements.py` | "reportedly raising at $5B" → confidence haircut applied |
| 8 | `tests/test_epic3.py` | assemble node uses pkg from evidence_package dict; extract_evidence not called |
