# Fix: Agent Returns 422 When `revenue_ltm` Is Missing

## Problem Summary

`POST /research` returns HTTP 422 with `missing=['revenue_ltm']` even when the
research agent successfully completes all five LangGraph nodes.  This is not a
crash — it is a design gap: the assembly step picks `comparable_companies` as the
methodology, discovers that `revenue_ltm` was not found in web snippets, and
refuses to return anything at all rather than trying an alternative.

```
FormDSource  → HTTP 403 from SEC EFTS  (non-fatal, rounds_dicts = [])
DuckDuckGo   → ran, but revenue_ltm not extracted from snippets
_assemble_node → methodology = comparable_companies
               → _assemble_comps() → missing = ['revenue_ltm']
               → assembled = None
server       → 422 {"error": "Could not assemble complete valuation inputs."}
```

## Why `revenue_ltm` Is Frequently Missing

Revenue figures for private companies are almost never in DuckDuckGo snippets:
- Private companies do not file public financials.
- Press coverage uses vague language ("hundreds of millions in ARR") that the
  current regex (`\$[\d,.]+\s*(billion|million).*revenue`) misses unless the
  dollar amount precedes the word "revenue" within the same sentence.
- The SEC EFTS 403 means Form D amounts are also unavailable (and Form D only
  discloses offering amount, not revenue anyway — `amount_raised=0.0` is
  hardcoded in `_parse_efts_hit`).

## What Real-World Practitioners Use When Revenue Is Unknown

Practitioners do not block a valuation because one data point is absent. They
cascade through methodologies in priority order depending on what data *is*
available:

| Available Data | Methodology Used |
|---|---|
| Last round date + post-money valuation | `last_round_market_adjusted` |
| Last round + revenue at that round + current revenue | `last_round_multiple_ratchet` |
| Revenue (LTM) + sector comps | `comparable_companies` |
| Qualitative factors only | `scorecard` or `berkus` |

The standard VC practice when revenue is unknown but a funding round is on
record is to use **`last_round_market_adjusted`**: take the last known
post-money valuation and index it forward (or backward) using a public market
index such as the NASDAQ Composite.  This is explicitly supported by the engine
today but the agent never falls back to it.

A secondary option when both round data and revenue are missing is to use
**`scorecard`** or **`berkus`** with analyst-supplied qualitative factors, but
that requires human input the agent cannot supply from web research alone.

## Root Cause in Code

`_assemble_node` in `src/vc_audit_tool/agent/research.py`:

1. Auto-selects `comparable_companies` when `_has_last_round_data()` returns
   `False` (which it does when Form D 403'd and web snippets contain no
   post-money valuation figure).
2. Calls `_assemble_comps()`, which requires `revenue_ltm`.
3. Returns `assembled = None` and `missing = ['revenue_ltm']` with no fallback.

There is no cascade: if the first methodology attempt fails for missing data,
the agent gives up.

A secondary contributing factor is that `_has_last_round_data()` requires
**both** `last_round_date` **and** `last_post_money_valuation`.  If only one is
found (e.g. DuckDuckGo returns a round date but no valuation figure), the check
returns `False` and the agent still selects `comparable_companies` even though
`last_round_market_adjusted` might be achievable with the date alone and a
valuation sourced elsewhere.

## Proposed Resolution

### 1 — Implement methodology cascade in `_assemble_node`

Replace the current single-shot methodology selection with an ordered cascade.
The agent should try each methodology in priority order and return the first one
that can be fully assembled:

```
Priority 1: last_round_market_adjusted
  Requires: last_post_money_valuation + last_round_date
  Source:   web_facts (LLM/regex) OR form_d_rounds[0].filing_date

Priority 2: comparable_companies
  Requires: revenue_ltm + sector
  Source:   web_facts (LLM/regex)

Priority 3: last_round_multiple_ratchet
  Requires: last_post_money_valuation + revenue_at_last_round + current_revenue
  Source:   web_facts

Priority 4: (out of scope for automated agent — requires analyst input)
  scorecard / berkus
```

When the caller explicitly passes `methodology=`, honour it but still return a
structured error (not 422) listing exactly which fields are missing, so the
caller knows what to supply manually.

### 2 — Fix Form D fallback endpoint (403 on EFTS)

`FormDSource._fetch_form_d()` calls `efts.sec.gov/LATEST/search-index`.  The
SEC EFTS endpoint blocks automated clients with HTTP 403.  The SEC submissions
API (`data.sec.gov/submissions/CIK{cik}.json`) is publicly accessible and does
not require authentication.  A fallback that:

a. Searches for the company CIK via `www.sec.gov/cgi-bin/browse-edgar` (same
   endpoint already used in `EdgarCompanyUniverse._fetch_ciks_for_sic()`), then
b. Fetches `data.sec.gov/submissions/CIK{cik}.json` and filters for
   `formType in ('D', 'D/A')`

…would retrieve Form D filing dates and amounts without hitting the blocked
endpoint.

### 3 — Add revenue-proxy queries to DuckDuckGo search

The current search queries in `_SEARCH_QUERIES` are funding-round focused.
Adding revenue-specific queries improves the chance of regex extraction:

```python
"{name} annual recurring revenue ARR 2024 2025",
"{name} revenue run rate millions",
"{name} revenue growth Series funding",
```

The regex pattern should also be extended to catch common journalistic phrasings
such as "ARR of $X million" and "run rate of $X million" which precede rather
than follow the dollar amount relative to the word "revenue".

### 4 — Return partial results with confidence metadata instead of 422

Even when the agent cannot assemble a complete request, it should return HTTP
200 with whatever methodology *could* be assembled (or a clear structured
explanation of what is missing and what was found), rather than 422.  The 422
currently swallows all research metadata, making it impossible for a caller to
understand what the agent did find.

The response shape when no methodology is fully assemblable should be:

```json
{
  "assembled_request": null,
  "best_available_methodology": "last_round_market_adjusted",
  "missing_for_best_available": ["last_post_money_valuation"],
  "research_metadata": { ... },
  "web_facts": { ... }
}
```

This gives a human operator or a downstream reconciliation step enough
information to decide whether to supply the missing field manually.

## Files to Change

| File | Change |
|---|---|
| `src/vc_audit_tool/agent/research.py` | Replace single-shot assembly with cascade in `_assemble_node`; add revenue-proxy DDG queries; extend regex patterns |
| `src/vc_audit_tool/data_sources/form_d.py` | Add fallback to `data.sec.gov/submissions` API when EFTS returns 403 |
| `src/vc_audit_tool/server.py` | Change `/research` 422 path to return 200 with partial metadata |

## Acceptance Criteria for Codex Agent

- [ ] `POST /research {"company_name": "Anthropic"}` returns HTTP 200 (not 422)
      when EFTS is blocked, using `last_round_market_adjusted` if a round date
      and post-money are found in web snippets, or `comparable_companies` if
      revenue is found.
- [ ] If neither methodology can be assembled, server returns HTTP 200 with
      `assembled_request: null` and a populated `best_available_methodology` key
      (not HTTP 422 with an empty body).
- [ ] `FormDSource.search()` does not raise on HTTP 403 from EFTS; it retries
      against `data.sec.gov/submissions`.
- [ ] Existing 392-test suite continues to pass (mock path unchanged).
- [ ] New unit test: `_assemble_node` with no `revenue_ltm` but with
      `last_post_money_valuation` + `last_round_date` selects
      `last_round_market_adjusted` and returns a complete `assembled_request`.
