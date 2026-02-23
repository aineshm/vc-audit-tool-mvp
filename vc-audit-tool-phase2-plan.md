# VC Audit Tool — Phase 2: Reconciliation Architecture
## Detailed Implementation Plan

---

## Overview

Phase 1 is complete. The system can now:
- Fetch real market index data via yfinance
- Build a EDGAR company universe and rank comps by semantic similarity
- Run a LangGraph research agent that assembles valuation inputs from SEC Form D, DuckDuckGo, and USASpending.gov
- Return a fully auditable valuation from just a company name via `POST /research`

**Phase 2 restructures the valuation layer** so that instead of routing to a single methodology, the system:
1. Profiles the company to determine its stage and what data is available
2. Selects all applicable methodologies with auditable, rules-based weights
3. Runs all applicable methodologies in parallel
4. Reconciles results into a single concluded value with a written rationale
5. Flags and explains divergence between methods rather than averaging it away

This matches how a Big 4 valuation team actually works. Every step is auditable, reproducible, and citable.

---

## Guiding Principles (Phase 2)

- **The existing engine, methodologies, interfaces, and data sources are NOT modified.** `ValuationEngine` is called by the new `ReconciliationEngine`, not replaced by it.
- **The weighting logic is explicit rules, not an LLM call.** Rules live in a versioned config. Every weight decision is documented with a plain-English rationale in the output.
- **`valuation_result` for each individual methodology retains its existing schema.** The reconciliation layer is additive — it synthesizes the per-methodology results but doesn't change their internal structure.
- **Determinism is preserved.** `concluded_value` is computed deterministically from deterministic per-methodology results.
- **All 335+ existing tests must continue to pass.**

---

## Directory Changes

```
src/vc_audit_tool/
├── (all existing files — unchanged)
│
├── reconciliation/                          # NEW package
│   ├── __init__.py                          # Re-exports ReconciliationEngine, ReconciledValuation
│   ├── models.py                            # CompanyProfile, MethodologyPlan, ReconciledValuation, etc.
│   ├── profiler.py                          # CompanyProfiler — builds CompanyProfile from ResearchResult
│   ├── selector.py                          # MethodologySelector — rules engine, versioned config
│   ├── engine.py                            # ReconciliationEngine — orchestrates parallel execution + reconciliation
│   └── reconciler.py                        # Reconciler — weighted average + divergence detection
│
├── methodologies/
│   ├── (all existing files — unchanged)
│   ├── scorecard.py                         # NEW: Scorecard methodology (pre-revenue startups)
│   └── berkus.py                            # NEW: Berkus methodology (pre-revenue startups)
│
└── agent/
    └── research.py                          # MODIFIED: add company profiling output to ResearchResult

tests/
├── (all existing test files — unchanged)
├── test_reconciliation.py                   # NEW: ReconciliationEngine, Reconciler, MethodologySelector
├── test_scorecard.py                        # NEW: Scorecard methodology
└── test_berkus.py                           # NEW: Berkus methodology

config/
└── methodology_rules_v1.yaml               # NEW: versioned weighting rules (human-readable)
```

**Files modified (minimally):**
- `src/vc_audit_tool/server.py` — `POST /research` response shape updated; new `POST /reconcile` endpoint added
- `src/vc_audit_tool/engine.py` — register `scorecard` and `berkus` in `_methodologies` dict
- `src/vc_audit_tool/agent/research.py` — `ResearchResult` gets an optional `company_profile` field

---

## Epic 6: New Data Models (`reconciliation/models.py`)

Define all new dataclasses before writing any logic. Everything downstream depends on these shapes.

### Story 6.1 — `CompanyProfile`

Captures all facts about a company that drive methodology selection. Built from `ResearchResult` by the `CompanyProfiler`.

```python
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

CompanyStage = Literal["pre_seed", "seed", "early", "growth", "late"]

@dataclass(frozen=True)
class CompanyProfile:
    name: str
    stage: CompanyStage
    age_years: float | None
    has_revenue: bool
    estimated_arr: Decimal | None        # annual recurring revenue
    last_round_age_months: float | None  # months since most recent funding round
    last_round_amount: Decimal | None    # USD amount raised in last round
    last_post_money: Decimal | None      # post-money valuation at last round
    sector: str
    sic_code: str | None
    headcount: int | None
    government_contracts_usd: Decimal | None
    # Free-text summary for audit trail
    profile_summary: str
    # Which data sources populated this profile
    sources_used: tuple[str, ...]
```

**Acceptance criteria:**
- [ ] `CompanyProfile` is a frozen dataclass — immutable after construction
- [ ] All monetary fields use `Decimal`, not `float`
- [ ] `stage` is a `Literal` type — only valid values accepted
- [ ] `profile_summary` is a one-sentence human-readable description of the company's stage classification
- [ ] mypy --strict passes

---

### Story 6.2 — `MethodologyPlan`

The output of the `MethodologySelector` — which methods to run and at what weight.

```python
@dataclass(frozen=True)
class MethodologyWeight:
    methodology: str            # matches existing methodology .name
    weight: Decimal             # 0.0 to 1.0; all weights sum to 1.0
    rationale: str              # plain-English: why this weight was assigned
    data_requirements_met: bool # False = method selected but data is unavailable

@dataclass(frozen=True)
class MethodologyPlan:
    weights: tuple[MethodologyWeight, ...]
    selector_version: str       # e.g. "v1.0" — matches config/methodology_rules_v1.yaml
    applicable_count: int       # how many methods have data_requirements_met=True
```

**Acceptance criteria:**
- [ ] Weights of all `data_requirements_met=True` methods sum to exactly `Decimal("1.0")`
- [ ] `applicable_count` equals the count of weights where `data_requirements_met=True`
- [ ] `selector_version` always matches the loaded rules config version

---

### Story 6.3 — `ReconciledValuation` (top-level output)

The new top-level output type returned by `ReconciliationEngine.value()` and the updated `POST /research` endpoint.

```python
from vc_audit_tool.models import AuditMetadata, ValuationResult

@dataclass(frozen=True)
class ConcludedValue:
    point_estimate: Decimal
    range_low: Decimal
    range_high: Decimal
    currency: str
    as_of_date: date

@dataclass(frozen=True)
class ReconciliationSummary:
    concluded_value: ConcludedValue
    methodology_weights: tuple[MethodologyWeight, ...]
    divergence_flag: bool
    divergence_note: str | None    # populated when divergence_flag=True
    reconciliation_rationale: str  # 1–2 sentence explanation of weighting decisions
    selector_version: str

@dataclass
class ReconciledValuation:
    reconciliation: ReconciliationSummary
    methodology_results: dict[str, dict]  # per-methodology ValuationResult.to_dict() output
    company_profile: CompanyProfile
    audit_metadata: dict             # from the first methodology's audit_metadata
    research_metadata: dict | None   # forwarded from ResearchResult if available

    def to_dict(self) -> dict:
        """Produce the full JSON-serializable output envelope."""
        ...
```

**`to_dict()` output shape:**
```json
{
  "concluded_value": {
    "point_estimate": 4200000000,
    "range_low": 3100000000,
    "range_high": 5800000000,
    "currency": "USD",
    "as_of_date": "2026-02-22"
  },
  "reconciliation": {
    "methodology_weights": [
      {
        "methodology": "comparable_companies",
        "weight": 0.60,
        "point_estimate": 4800000000,
        "range_low": 3500000000,
        "range_high": 6000000000,
        "rationale": "Weighted highest — strong peer set quality (0.81 mean similarity), recent financial data",
        "data_requirements_met": true
      },
      {
        "methodology": "last_round_market_adjusted",
        "weight": 0.40,
        "point_estimate": 3300000000,
        "range_low": 2500000000,
        "range_high": 4500000000,
        "rationale": "Weighted lower — funding round is 22 months old, staleness risk HIGH",
        "data_requirements_met": true
      }
    ],
    "divergence_flag": false,
    "divergence_note": null,
    "reconciliation_rationale": "Concluded value reflects primary weight on comparable companies given strong peer quality. Last round market-adjusted used as secondary check, discounted for round age.",
    "selector_version": "v1.0"
  },
  "methodology_results": {
    "comparable_companies": { /* full existing ValuationResult.to_dict() output */ },
    "last_round_market_adjusted": { /* full existing ValuationResult.to_dict() output */ }
  },
  "company_profile": {
    "name": "Anthropic",
    "stage": "growth",
    "age_years": 4.2,
    "has_revenue": true,
    "estimated_arr": 1000000000,
    "last_round_age_months": 22,
    "last_round_amount": 750000000,
    "last_post_money": 18000000000,
    "sector": "enterprise_software",
    "sic_code": "7372",
    "headcount": null,
    "government_contracts_usd": null,
    "profile_summary": "Growth-stage AI software company with disclosed revenue and a recent but aging institutional round.",
    "sources_used": ["SEC Form D", "DuckDuckGo web research"]
  },
  "audit_metadata": { /* standard audit_metadata */ },
  "research_metadata": { /* forwarded from agent if available */ }
}
```

**Acceptance criteria:**
- [ ] `to_dict()` produces a JSON-serializable dict with all five top-level keys
- [ ] `methodology_results` preserves the complete existing `ValuationResult.to_dict()` output, including `valuation_result` and `audit_metadata` sub-keys
- [ ] All monetary values are `float` in JSON (converted from `Decimal`)
- [ ] mypy --strict passes

---

## Epic 7: `CompanyProfiler` (`reconciliation/profiler.py`)

Converts a `ResearchResult` (from the existing agent) into a `CompanyProfile`. Pure deterministic logic — no LLM calls, no external API calls.

### Story 7.1 — Stage Classification Rules

The profiler classifies company stage based on available data:

| Condition | Stage |
|---|---|
| Age < 18 months OR (no revenue AND no institutional round) | `pre_seed` |
| Age 18–36 months AND revenue < $1M AND round < Series A size ($5M+) | `seed` |
| Age < 4 years AND revenue $1M–$10M OR (round ≤ Series B) | `early` |
| Age 3–8 years AND revenue $10M–$100M OR (round Series C/D) | `growth` |
| Age > 6 years AND revenue > $100M OR (round Series E+) | `late` |

These rules are intentionally **overlapping with priority ordering** — earlier rules take precedence. The profiler logs which rule triggered the classification in `profile_summary`.

**Acceptance criteria:**
- [ ] `CompanyProfiler.build(research_result)` returns a `CompanyProfile`
- [ ] Stage classification is deterministic — same `ResearchResult` always produces the same stage
- [ ] `profile_summary` includes the specific rule that triggered the classification
- [ ] If `research_result.assembled_request` contains `as_of_date`, it is used to compute round age
- [ ] Unit tests cover: pre-seed (no revenue, no round), seed, early, growth, late, and edge cases (age unavailable, revenue unavailable)
- [ ] mypy --strict passes

### Story 7.2 — Profiler Integration with `ResearchResult`

Modify `ResearchResult` in `agent/research.py` to include an optional `company_profile` field:

```python
@dataclass
class ResearchResult:
    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    error: str | None = None
    company_profile: CompanyProfile | None = None  # NEW — populated by profiler
```

The `CompanyResearchAgent.run()` method calls `CompanyProfiler.build(self)` at the end of the agent pipeline and attaches the result to `ResearchResult`. The profiler is called after `_assemble_node`, not inside it — keeping concerns separated.

**Acceptance criteria:**
- [ ] All existing `ResearchResult` tests still pass (new field is optional with default `None`)
- [ ] When `company_profile` is `None`, the `ReconciliationEngine` falls back to default growth-stage assumptions with a warning in `confidence_indicators`
- [ ] mypy --strict passes (field type is `CompanyProfile | None`)

---

## Epic 8: `MethodologySelector` (`reconciliation/selector.py`)

The rules engine that takes a `CompanyProfile` and returns a `MethodologyPlan`. This is the most important component to get right — it determines which methods apply and at what weight, and all decisions must be documented and version-controlled.

### Story 8.1 — Rules Config (`config/methodology_rules_v1.yaml`)

All weighting rules live in a YAML file, not hardcoded in Python. This makes them reviewable without touching code.

```yaml
version: "v1.0"

# Stage-level method exclusions (highest priority)
stage_exclusions:
  pre_seed:
    exclude: [comparable_companies, last_round_market_adjusted, last_round_multiple_ratchet]
    require: [scorecard, berkus]
    note: "Pre-revenue company — quantitative methods require revenue or round data"
  seed:
    exclude: [last_round_multiple_ratchet]
    note: "Seed stage — ratchet methodology requires meaningful revenue at last round"

# Data-availability rules (applied after stage exclusions)
data_rules:
  last_round_age_months:
    - condition: "< 6"
      weight_modifier: HIGH       # 40-50%
      rationale: "Round < 6 months old — highly reliable anchor"
    - condition: "6 to 18"
      weight_modifier: MEDIUM     # 20-35%
      rationale: "Round 6-18 months old — moderate staleness"
    - condition: "18 to 36"
      weight_modifier: LOW        # 10-20%
      rationale: "Round > 18 months old — staleness risk HIGH"
    - condition: "> 36"
      exclude: true
      rationale: "Round > 3 years old — excluded as unreliable anchor"

  peer_set_quality:
    - condition: "HIGH"
      weight_modifier: HIGH       # 50-65%
      rationale: "Strong peer set quality — comps method is most reliable"
    - condition: "MEDIUM"
      weight_modifier: MEDIUM     # 30-50%
    - condition: "LOW"
      weight_modifier: LOW        # 15-25%
      rationale: "Low peer set quality — comps method used with caution"

  has_revenue:
    - condition: false
      exclude: [comparable_companies]
      rationale: "No revenue — cannot apply EV/Revenue multiple"

  revenue_cagr_pct:
    - condition: "> 150"
      exclude: []
      note: "Hyper-growth — weight comps higher than last round"

# Base weights by stage (before data-availability adjustments)
base_weights:
  pre_seed:
    scorecard: 0.50
    berkus: 0.50
  seed:
    scorecard: 0.35
    berkus: 0.30
    last_round_market_adjusted: 0.35
  early:
    comparable_companies: 0.50
    last_round_market_adjusted: 0.50
  growth:
    comparable_companies: 0.60
    last_round_market_adjusted: 0.40
  late:
    comparable_companies: 0.70
    last_round_market_adjusted: 0.30
```

**Acceptance criteria:**
- [ ] Config is loaded once at import time and cached
- [ ] `selector_version` in `MethodologyPlan` matches the `version` field in the YAML
- [ ] Adding a new version of the config (e.g. `methodology_rules_v2.yaml`) does not require code changes — just point the selector at the new file
- [ ] Config is validated at load time: weights in each stage sum to 1.0, no unknown methodology names

### Story 8.2 — `MethodologySelector` Implementation

```python
class MethodologySelector:
    def __init__(self, rules_path: Path = Path("config/methodology_rules_v1.yaml")) -> None:
        self._rules = self._load_rules(rules_path)

    def select(
        self,
        profile: CompanyProfile,
        data_package: DataPackage,
    ) -> MethodologyPlan:
        """Return a MethodologyPlan for the given profile and available data."""
        ...
```

The selector applies rules in this order:
1. Load base weights for `profile.stage`
2. Apply stage-level exclusions
3. Apply data-availability rules (modifies weights based on round age, peer quality, etc.)
4. Check data requirements for each remaining method — mark `data_requirements_met=False` for methods that lack required inputs
5. Normalize remaining weights to sum to 1.0
6. Build rationale string for each weight

**DataPackage** is a simple struct passed alongside the profile:
```python
@dataclass(frozen=True)
class DataPackage:
    """Available data for methodology execution. Mirrors assembled_request inputs."""
    last_post_money: Decimal | None
    last_round_date: date | None
    revenue_ltm: Decimal | None
    sector: str
    peer_set_quality: str | None   # "HIGH" | "MEDIUM" | "LOW" | None
    government_contracts_usd: Decimal | None
    as_of_date: date
```

**Acceptance criteria:**
- [ ] For a `pre_seed` profile: only `scorecard` and `berkus` are returned; all others excluded with documented rationale
- [ ] For a `growth` profile with a 22-month-old round: comps weight > last_round weight; round weight flagged as LOW due to staleness
- [ ] For a `growth` profile with missing revenue: `comparable_companies` is excluded (`data_requirements_met=False`) with rationale
- [ ] For a `growth` profile with a 2-month-old round: last_round weight is HIGH (40-50%)
- [ ] Weights of `data_requirements_met=True` methods always sum to exactly `Decimal("1.0")`
- [ ] When all methods are excluded (edge case), raises `DataSourceError` with explanation
- [ ] Unit tests cover all stage types + all data-availability rule combinations
- [ ] mypy --strict passes

---

## Epic 9: New Methodologies

### Story 9.1 — Scorecard Method (`methodologies/scorecard.py`)

For pre-revenue startups. Starts from a regional/sector median pre-money valuation and applies multipliers for five qualitative factors. This is the Payne Scorecard method used by angel investors.

**Required new input fields (added to `ValuationRequest.inputs`):**

```json
{
  "methodology": "scorecard",
  "inputs": {
    "regional_median_pre_money": 5000000,
    "sector": "enterprise_software",
    "factors": {
      "strength_of_team":        0.8,   // 0.0 - 1.5 scale
      "size_of_opportunity":     1.0,
      "product_technology":      1.2,
      "competitive_environment": 0.9,
      "marketing_sales_channels": 0.7,
      "need_for_additional_investment": 1.0,
      "other":                   1.0
    }
  }
}
```

**Derivation:**
1. `weighted_sum = sum(factor_score * factor_weight for each factor)` where weights are fixed by the method: team=30%, opportunity=25%, product=15%, competitive=10%, marketing=10%, need=5%, other=5%
2. `estimated_fair_value = regional_median_pre_money * weighted_sum`

All factor weights are documented in `assumptions`. All factor scores and their contribution to the final number are in `derivation_steps`.

**Acceptance criteria:**
- [ ] `ScorecardMethodology` registered in `engine.py` as `name = "scorecard"`
- [ ] `estimated_fair_value` is computed deterministically from inputs
- [ ] `derivation_steps` shows every factor score, its weight, and its contribution to the final weighted sum
- [ ] `assumptions` lists all factor weights used (these are fixed by the methodology)
- [ ] Missing any `factors` key raises `ValidationError` with the specific missing key named
- [ ] Factor scores outside [0.0, 2.0] raise `ValidationError`
- [ ] `confidence_indicators` includes `data_source_type: "analyst_assessment"` (scores are analyst judgments, not data-derived)
- [ ] Unit tests: happy path, missing factor, out-of-range factor, zero regional median
- [ ] mypy --strict passes

### Story 9.2 — Berkus Method (`methodologies/berkus.py`)

For pre-revenue startups. Assigns up to a configurable maximum value to each of five risk-mitigating factors.

**Required new input fields:**
```json
{
  "methodology": "berkus",
  "inputs": {
    "max_pre_money_valuation": 2000000,
    "factors": {
      "sound_idea":            true,   // or partial: 0.75
      "working_prototype":     true,
      "quality_management":    false,
      "strategic_relationships": true,
      "product_rollout_or_sales": false
    }
  }
}
```

Each factor contributes up to `max_pre_money_valuation / 5` (equal split). Boolean `true` = full value; boolean `false` = zero; float [0.0, 1.0] = partial value.

**Acceptance criteria:**
- [ ] `BerkusMethodology` registered in `engine.py` as `name = "berkus"`
- [ ] Accepts both `bool` and `float` factor values (unlike most input fields, bool is valid here)
- [ ] `estimated_fair_value` is sum of all factor contributions, capped at `max_pre_money_valuation`
- [ ] `derivation_steps` shows each factor name, its value, the per-factor maximum, and its contribution
- [ ] `confidence_indicators.factor_completeness` indicates how many factors are non-zero (e.g. "3/5 factors present")
- [ ] Unit tests: all-true, all-false, partial values, zero max valuation
- [ ] mypy --strict passes

---

## Epic 10: `ReconciliationEngine` (`reconciliation/engine.py`)

### Story 10.1 — Engine Implementation

The `ReconciliationEngine` wraps the existing `ValuationEngine`. It orchestrates the full reconciliation flow: profile → plan → parallel execution → reconcile → output.

```python
from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.reconciliation.models import (
    CompanyProfile, DataPackage, MethodologyPlan, ReconciledValuation
)
from vc_audit_tool.reconciliation.selector import MethodologySelector
from vc_audit_tool.reconciliation.reconciler import Reconciler
from vc_audit_tool.interfaces import MarketIndexSource, ComparableCompanySource

class ReconciliationEngine:
    def __init__(
        self,
        *,
        index_source: MarketIndexSource | None = None,
        comps_source: ComparableCompanySource | None = None,
        rules_path: Path | None = None,
    ) -> None:
        self._engine = ValuationEngine(
            index_source=index_source,
            comps_source=comps_source,
        )
        self._selector = MethodologySelector(rules_path or _DEFAULT_RULES_PATH)

    def value(
        self,
        profile: CompanyProfile,
        data_package: DataPackage,
        as_of_date: date,
        company_name: str,
        research_metadata: dict | None = None,
    ) -> ReconciledValuation:
        """Run all applicable methodologies and reconcile to a single value."""
        plan = self._selector.select(profile, data_package)
        results = self._run_methodologies(plan, data_package, company_name, as_of_date)
        reconciliation = Reconciler.reconcile(results, plan)
        return ReconciledValuation(
            reconciliation=reconciliation,
            methodology_results=results,
            company_profile=profile,
            audit_metadata=self._extract_audit_metadata(results),
            research_metadata=research_metadata,
        )

    def _run_methodologies(
        self,
        plan: MethodologyPlan,
        data_package: DataPackage,
        company_name: str,
        as_of_date: date,
    ) -> dict[str, dict]:
        """Run each applicable methodology and return {method_name: result_dict}."""
        results: dict[str, dict] = {}
        for weight in plan.weights:
            if not weight.data_requirements_met:
                continue
            request = self._build_request(weight.methodology, data_package, company_name, as_of_date)
            try:
                result = self._engine.evaluate(request)
                results[weight.methodology] = result.to_dict()
            except Exception as exc:
                # Log but don't propagate — missing one method shouldn't break the whole valuation
                logger.warning("methodology %s failed: %s", weight.methodology, exc)
        return results
```

**`_build_request()` is responsible for constructing a `ValuationRequest` for each methodology from the `DataPackage`.** This is a straightforward mapping — the DataPackage contains all the fields that any methodology might need.

**Acceptance criteria:**
- [ ] `ReconciliationEngine.value()` returns a `ReconciledValuation` in all cases where at least one methodology succeeds
- [ ] If a methodology fails at runtime (data source error), it is excluded from results with a warning — the reconciliation proceeds with remaining methods
- [ ] If ALL methodologies fail, raises `DataSourceError` with a summary of each failure
- [ ] Per-methodology results in `methodology_results` are complete `ValuationResult.to_dict()` envelopes (including their own `audit_metadata`)
- [ ] `ReconciliationEngine` can be instantiated with mock sources for testing
- [ ] Unit tests: single method succeeds, multiple methods succeed, one method fails, all methods fail
- [ ] mypy --strict passes

---

## Epic 11: `Reconciler` (`reconciliation/reconciler.py`)

### Story 11.1 — Weighted Average and Range

```python
class Reconciler:
    DIVERGENCE_THRESHOLD = Decimal("0.40")  # 40% divergence from midpoint triggers flag

    @staticmethod
    def reconcile(
        results: dict[str, dict],
        plan: MethodologyPlan,
    ) -> ReconciliationSummary:
        """Synthesize methodology results into a concluded value."""
        ...
```

**Point estimate computation:**
```
concluded_point = sum(
    result["valuation_result"]["estimated_fair_value"]["amount"] * weight.weight
    for weight, result in zip(applicable_weights, applicable_results)
)
```

**Range computation:**
Each methodology's result contributes its own uncertainty band. If a methodology doesn't produce an explicit range (current implementations don't), the `Reconciler` derives one using a methodology-specific spread factor:
- `comparable_companies`: ± `multiple_spread / median_multiple * point_estimate * 0.5`
- `last_round_market_adjusted`: ± `0.20 * point_estimate` (flat 20% for index-adjusted valuations)
- `last_round_multiple_ratchet`: ± `0.25 * point_estimate`
- `scorecard` / `berkus`: ± `0.35 * point_estimate` (wide range for early-stage methods)

The concluded range is the weighted average of the per-methodology ranges, not the min/max.

**Divergence detection:**
```python
if len(point_estimates) >= 2:
    max_val = max(point_estimates)
    min_val = min(point_estimates)
    midpoint = (max_val + min_val) / 2
    if midpoint > 0:
        divergence = (max_val - min_val) / midpoint
        if divergence > DIVERGENCE_THRESHOLD:
            divergence_flag = True
            divergence_note = _generate_divergence_note(results, plan)
```

**`_generate_divergence_note()`** produces a structured explanation:
```
"Comparable companies methodology ($4.8B) and last round market-adjusted ($1.2B) 
diverge significantly (divergence: 120%). Possible explanations: 
(1) Material market re-rating of the AI sector since the last funding round in March 2023; 
(2) The post-money valuation at last round may have included favorable liquidation preference 
terms that inflated the headline figure; 
(3) The comparable company set may not fully reflect the target company's AI-native positioning. 
Manual review recommended before concluding."
```

**Reconciliation rationale** is a single paragraph generated from the plan weights and profile:
```
"Concluded value applies 60% weight to comparable companies (strong peer set quality, 
5 comps with mean similarity 0.81) and 40% weight to last round market-adjusted 
(round is 22 months old — staleness risk HIGH). Comps weighted higher given data recency."
```

**Acceptance criteria:**
- [ ] `Reconciler.reconcile()` is a static method — no instance state
- [ ] `concluded_value.point_estimate` equals the weighted sum of per-methodology point estimates
- [ ] `concluded_value.range_low` and `range_high` are always less than / greater than `point_estimate` respectively
- [ ] `divergence_flag=True` when any two method estimates diverge > 40% from their midpoint
- [ ] `divergence_note` is populated whenever `divergence_flag=True`
- [ ] `divergence_note` names the specific methods that diverge and suggests concrete possible explanations
- [ ] `reconciliation_rationale` references the specific weight assigned to each method and the reason
- [ ] When only one method is applicable, `divergence_flag=False` unconditionally
- [ ] All monetary math uses `Decimal` — no float intermediates
- [ ] Unit tests:
  - Two methods, no divergence
  - Two methods, divergence > 40% → flag + note
  - Single method → no divergence, range derived from method-specific spread
  - Three methods (all three existing + new) → correct weighted average
  - Edge case: all estimates are zero
- [ ] mypy --strict passes

---

## Epic 12: Updated API

### Story 12.1 — `POST /research` Returns `ReconciledValuation`

The existing `POST /research` endpoint is updated to use `ReconciliationEngine` instead of calling `ValuationEngine` directly. **The request schema is unchanged.**

The updated flow in `server.py`:
```python
@app.post("/research")
async def post_research(request: Request) -> JSONResponse:
    # 1. Run research agent (unchanged)
    agent = CompanyResearchAgent()
    research = agent.run(company_name, ...)

    if not research.is_complete:
        return JSONResponse({"error": ..., "missing_fields": ...}, status_code=422)

    # 2. Build CompanyProfile from ResearchResult
    profile = research.company_profile or CompanyProfiler.build_from_research(research)

    # 3. Build DataPackage from assembled_request inputs
    data_package = DataPackage.from_assembled_request(research.assembled_request)

    # 4. Run ReconciliationEngine
    recon_engine = ReconciliationEngine()
    reconciled = recon_engine.value(
        profile=profile,
        data_package=data_package,
        as_of_date=...,
        company_name=company_name,
        research_metadata=research.research_metadata,
    )

    return JSONResponse(reconciled.to_dict(), status_code=200)
```

**Acceptance criteria:**
- [ ] `POST /research` response now contains all five top-level keys: `concluded_value`, `reconciliation`, `methodology_results`, `company_profile`, `audit_metadata`
- [ ] `research_metadata` is still present as a top-level key
- [ ] The 422 response path (incomplete research) is unchanged
- [ ] Existing integration tests for `POST /research` that check 200/422 behavior still pass
- [ ] New integration tests assert `concluded_value` is present in 200 responses
- [ ] mypy --strict passes on the modified server.py

### Story 12.2 — `POST /reconcile` (Direct Input Endpoint)

A new endpoint analogous to `POST /value` — it accepts fully structured inputs and returns a `ReconciledValuation` without running the research agent. Useful for testing and for callers who already have structured data.

**Request schema:**
```json
{
  "company_name": "TechCo",
  "as_of_date": "2026-02-22",
  "profile": {
    "stage": "growth",
    "age_years": 4.0,
    "has_revenue": true,
    "estimated_arr": 50000000,
    "last_round_age_months": 18,
    "last_post_money": 400000000,
    "sector": "enterprise_software"
  },
  "inputs": {
    "last_post_money_valuation": 400000000,
    "last_round_date": "2024-07-01",
    "revenue_ltm": 50000000,
    "private_company_discount_pct": 25
  }
}
```

**Acceptance criteria:**
- [ ] `POST /reconcile` accepts the schema above and returns a `ReconciledValuation`
- [ ] If `profile.stage` is invalid, returns 400 with a clear error
- [ ] Appears in OpenAPI docs at `/docs`
- [ ] Unit test: valid request returns 200; invalid stage returns 400; missing inputs returns 422

---

## Epic 13: Test Suite

### Story 13.1 — `test_reconciliation.py`

**Reconciler tests:**
- Weighted average with two methods (exact arithmetic check)
- Weighted average with three methods
- Divergence flag triggers at 40%
- Divergence flag does not trigger at 39%
- Divergence note names the diverging methods
- Single method: no divergence, range derived from spread
- Range low < point_estimate < range high always

**MethodologySelector tests:**
- `pre_seed` profile: only scorecard + berkus selected
- `growth` profile with revenue: comps + last_round selected
- `growth` profile without revenue: comps excluded
- `growth` profile with 40-month-old round: last_round excluded
- `growth` profile with 3-month-old round: last_round weight >= 0.40
- All weights sum to 1.0 for every test case
- `data_requirements_met=False` when required inputs missing
- Rules YAML version stamped on output

**ReconciliationEngine tests:**
- Single method succeeds: returns reconciled valuation
- Two methods: both in `methodology_results`
- One method fails at runtime: excluded from results, warning logged
- All methods fail: raises `DataSourceError`
- `company_profile` is present in all success outputs
- `research_metadata=None` when not provided

**CompanyProfiler tests:**
- Pre-seed classification (no revenue, no round)
- Seed classification (early revenue, small round)
- Growth classification (meaningful revenue, institutional round)
- Age < 18 months forces `pre_seed` regardless of revenue
- Round age computed correctly from `last_round_date` and `as_of_date`
- `profile_summary` is non-empty string

### Story 13.2 — `test_scorecard.py` and `test_berkus.py`

**Scorecard:**
- Happy path: all factors provided, correct weighted sum
- Factor weights sum to 1.0 (internal check)
- Missing factor key raises `ValidationError` with key name
- Factor score > 2.0 raises `ValidationError`
- Zero regional median produces zero output
- `derivation_steps` has one entry per factor + one summary step
- mypy --strict on method file

**Berkus:**
- All-true: fair value equals `max_pre_money_valuation`
- All-false: fair value equals zero
- Partial float factors: correct partial contribution
- Mixed bool + float factors
- Factor count in `confidence_indicators`
- mypy --strict on method file

### Story 13.3 — Existing Test Compatibility

**Requirement:** All 335 existing tests must pass without modification after Phase 2 is complete.

This is enforced by:
- Never modifying `engine.py`, `models.py`, `interfaces.py`, or any existing methodology files except to register new methodologies
- The `ReconciliationEngine` is a separate class that wraps `ValuationEngine` — it does not replace it
- `POST /value` and `POST /reconcile` are separate endpoints — `/value` still uses `ValuationEngine` directly
- `ResearchResult.company_profile` has a default value of `None` — all existing tests that create `ResearchResult` without this field continue to work

---

## Build Order

**Build these strictly in sequence.** Each step's output is a dependency of the next.

### Step 1 — Models (`reconciliation/models.py`)
Write all dataclasses first. Everything else imports from here. No external dependencies — pure Python. Fastest to write, fastest to test. Get mypy to pass before moving on.

### Step 2 — New Methodologies (`scorecard.py`, `berkus.py`)
These are independent of the reconciliation layer — they plug into the existing `ValuationEngine` via the existing `ValuationMethodology` ABC. Write them, register them in `engine.py`, and write their tests. This validates that the new method abstraction works before building the layer that calls them.

### Step 3 — `CompanyProfiler` (`reconciliation/profiler.py`)
Pure deterministic logic. No external APIs, no LLM calls. Easy to unit test. Write all tests before wiring into the agent.

### Step 4 — Rules Config + `MethodologySelector` (`config/methodology_rules_v1.yaml` + `reconciliation/selector.py`)
Write the YAML config first. Then implement the selector. Test every rule combination. This is the most logic-dense component — get it fully tested before moving on.

### Step 5 — `Reconciler` (`reconciliation/reconciler.py`)
Pure math — weighted averages, range derivation, divergence detection. All arithmetic should be testable with hardcoded inputs. No external dependencies.

### Step 6 — `ReconciliationEngine` (`reconciliation/engine.py`)
Wire together `MethodologySelector`, `ValuationEngine`, and `Reconciler`. Test with mocked sub-components before end-to-end testing.

### Step 7 — Update `ResearchResult` and `CompanyResearchAgent`
Add the optional `company_profile` field and the `CompanyProfiler.build()` call at the end of `agent.run()`. Verify all existing agent tests still pass.

### Step 8 — Update `POST /research` and add `POST /reconcile`
Update the server. Run the full test suite. Fix any integration issues.

### Step 9 — Full test sweep
```bash
mypy src/
ruff check src/ tests/
ruff format --check src/ tests/
python -m pytest tests/ -q
```
All must pass before Phase 2 is considered complete.

---

## Phase 2 Completion Criteria

**Behavioral:**
- [ ] `POST /research {"company_name": "Anthropic"}` returns a `ReconciledValuation` with `concluded_value`, `reconciliation.methodology_weights`, and `methodology_results` for each applicable method
- [ ] For a 6-month-old AI startup with no revenue: only `scorecard` and `berkus` are in `methodology_results`; concluded value has wide range; `confidence_indicators` in each method result flags high uncertainty
- [ ] For a 4-year-old growth company with $50M ARR: `comparable_companies` and `last_round_market_adjusted` both appear; comps weighted higher; weights documented with rationale
- [ ] When two method estimates diverge > 40%: `reconciliation.divergence_flag = true` and `reconciliation.divergence_note` explains possible causes with specific numbers
- [ ] `reconciliation.selector_version` is stamped on every output
- [ ] `POST /reconcile` with valid structured inputs returns `ReconciledValuation` without running the research agent

**Technical:**
- [ ] All 335 existing tests pass without modification
- [ ] New test files add ≥ 80 tests covering the reconciliation layer
- [ ] `mypy src/` — success: no issues in all source files
- [ ] `ruff check src/ tests/` — no issues
- [ ] `ruff format --check src/ tests/` — no issues

---

## What Is Not Changing

- `engine.py` — only addition: two lines registering `scorecard` and `berkus` in `_methodologies`
- `models.py` — unchanged
- `interfaces.py` — unchanged
- `methodologies/base.py`, `comps.py`, `last_round.py`, `multiple_ratchet.py` — unchanged
- All data sources in `data_sources/` — unchanged
- `store.py`, `cache.py`, `confidence.py`, `validation.py`, `exceptions.py` — unchanged
- `POST /value` endpoint — unchanged
- `cli.py` — unchanged (no new CLI subcommands in Phase 2)
- All existing example JSON files — unchanged

The Phase 2 surface area is additive. The system grows a new layer on top of what exists; nothing underneath it is removed or replaced.
