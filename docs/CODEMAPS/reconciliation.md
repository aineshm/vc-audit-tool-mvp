# Reconciliation Codemap (Phase 2)

**Last Updated:** 2026-03-01

Multi-methodology selection, weighting, and reconciliation into single valuation.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  POST /reconcile (company_name, description_hint)      │
└─────────────────────────────────────────────────────────┘
                    │
                    v
        ┌───────────────────────────┐
        │   Research Agent          │
        │ (gather company data)     │
        └───────────────────────────┘
                    │
                    v
        ┌───────────────────────────────┐
        │  CompanyProfiler              │
        │  (classify lifecycle stage)   │
        │  → pre_seed, seed, early,     │
        │     growth, late              │
        └───────────────────────────────┘
                    │
                    v
        ┌──────────────────────────────┐
        │  MethodologySelector         │
        │  (load YAML rules, pick      │
        │   applicable methods &       │
        │   base weights)              │
        └──────────────────────────────┘
                    │
                    v
    ┌───────────────────────────────────────┐
    │  Parallel Valuation Execution         │
    │  ├─ Engine.evaluate(comps)       │
    │  ├─ Engine.evaluate(last_round)  │
    │  └─ Engine.evaluate(scorecard)   │
    │    (weights adjusted for         │
    │     data availability)           │
    └───────────────────────────────────────┘
                    │
                    v
        ┌──────────────────────────────────────┐
        │  Reconciler                          │
        │  ├─ Compute weighted average         │
        │  ├─ Derive range (±10% or min/max)   │
        │  ├─ Check divergence (>40% flag)     │
        │  └─ Assemble output                  │
        └──────────────────────────────────────┘
                    │
                    v
            ReconciliationResult
            (point estimate, range, weights)
```

## Core Components

### 1. CompanyProfiler

**File:** `src/vc_audit_tool/reconciliation/profiler.py`

**Purpose:** Classify company into lifecycle stage

**Class:** `CompanyProfiler`

```python
class CompanyProfiler:
    """Classify a company's lifecycle stage."""

    def profile(
        self,
        research_data: dict,
        company_name: str
    ) -> CompanyProfile:
        """Profile company based on gathered intelligence.

        Returns CompanyProfile with stage: pre_seed, seed, early, growth, late
        """
        # Analyze signals
        age_years = _estimate_age(research_data)
        revenue = research_data.get("estimated_annual_revenue")
        headcount = research_data.get("headcount")
        funding_rounds = research_data.get("funding_rounds", [])
        last_round_date = _get_last_round_date(funding_rounds)

        # Classification logic
        if age_years < 1 or not funding_rounds:
            return CompanyProfile(stage="pre_seed", ...)

        if revenue is None or revenue < 100_000:
            return CompanyProfile(stage="seed", ...)

        if revenue < 2_000_000:
            return CompanyProfile(stage="early", ...)

        if revenue < 20_000_000:
            return CompanyProfile(stage="growth", ...)

        return CompanyProfile(stage="late", ...)
```

**Output:**
```python
@dataclass
class CompanyProfile:
    name: str
    stage: Literal["pre_seed", "seed", "early", "growth", "late"]
    has_revenue: bool
    estimated_revenue: Decimal | None
    headcount: int | None
    sector: str | None
    has_secondary_evidence: bool
    ...
```

### 2. MethodologySelector

**File:** `src/vc_audit_tool/reconciliation/selector.py`

**Purpose:** Load YAML rules and select applicable methodologies

**Class:** `MethodologySelector`

```python
class MethodologySelector:
    """Select and weight methodologies based on company stage."""

    def __init__(self, rules_path: Path = Path("config/methodology_rules_v1.yaml")):
        self.rules = self._load_rules(rules_path)

    def select(self, profile: CompanyProfile) -> MethodologyPlan:
        """Select applicable methodologies and assign base weights.

        Returns MethodologyPlan with list of (methodology, base_weight, rationale)
        """
        stage = profile.stage
        stage_rules = self.rules.get(stage, {})

        plan = MethodologyPlan(stage=stage, methodologies=[])

        # For each methodology in YAML:
        for method_name, config in stage_rules.items():
            if config.get("excluded"):
                continue

            if method_name == "comparable_companies" and not profile.has_revenue:
                continue  # Skip comps if no revenue

            if method_name == "scorecard" and not profile.headcount:
                continue  # Skip scorecard if no headcount estimate

            # Add with base weight
            plan.methodologies.append(MethodologyWeight(
                methodology=method_name,
                base_weight=Decimal(str(config["base_weight"])),
                rationale=config.get("rationale", ""),
            ))

        return plan
```

**YAML Configuration:**

File: `config/methodology_rules_v1.yaml`

```yaml
pre_seed:
  scorecard:
    base_weight: 0.5
    rationale: "Early-stage company — scorecard method primary"
  berkus:
    base_weight: 0.5
    rationale: "Early-stage company — berkus method secondary"
  comparable_companies:
    excluded: true
    reason: "Pre-seed lacks sufficient public market comparables"

seed:
  scorecard:
    base_weight: 0.35
  berkus:
    base_weight: 0.3
  last_round_market_adjusted:
    base_weight: 0.35

early:
  comparable_companies:
    base_weight: 0.5
  last_round_market_adjusted:
    base_weight: 0.5

growth:
  comparable_companies:
    base_weight: 0.6
  last_round_market_adjusted:
    base_weight: 0.4

late:
  comparable_companies:
    base_weight: 0.7
  last_round_market_adjusted:
    base_weight: 0.3
```

### 3. Reconciler

**File:** `src/vc_audit_tool/reconciliation/reconciler.py`

**Purpose:** Execute selected methodologies and reconcile results

**Class:** `Reconciler`

```python
class Reconciler:
    """Reconcile multiple valuation methodologies."""

    def __init__(self, engine: ValuationEngine):
        self.engine = engine

    async def reconcile(
        self,
        plan: MethodologyPlan,
        request: ValuationRequest
    ) -> ReconciliationResult:
        """Execute multiple methodologies and reconcile."""

        # 1. Run each methodology (in parallel)
        results = {}
        for method_weight in plan.methodologies:
            method = method_weight.methodology

            # Adjust request for this methodology
            method_request = request.copy(update={
                "methodology": method,
            })

            result = await asyncio.to_thread(
                self.engine.evaluate, method_request
            )
            results[method] = (result, method_weight)

        # 2. Adjust weights based on data availability
        adjusted_weights = self._adjust_weights_for_data(results, plan)

        # 3. Compute weighted-average point estimate
        point_estimate = self._compute_point_estimate(results, adjusted_weights)

        # 4. Derive range
        range_low, range_high = self._derive_range(results, adjusted_weights)

        # 5. Check divergence
        divergence_flag, divergence_note = self._check_divergence(results)

        return ReconciliationResult(
            concluded_value=ConcludedValue(
                point_estimate=point_estimate,
                range_low=range_low,
                range_high=range_high,
            ),
            reconciliation=ReconciliationMetadata(
                methodology_weights=[...],
                divergence_flag=divergence_flag,
                divergence_note=divergence_note,
            ),
            methodology_results=results,
            company_profile=profile,
        )

    def _adjust_weights_for_data(
        self,
        results: dict,
        plan: MethodologyPlan
    ) -> dict[str, Decimal]:
        """Adjust weights if some methodologies lack data."""
        adjusted = {}

        # Check each methodology for data sufficiency
        total_weight = Decimal("0")
        for method_weight in plan.methodologies:
            result = results.get(method_weight.methodology)

            if result and _has_sufficient_data(result):
                adjusted[method_weight.methodology] = method_weight.base_weight
                total_weight += method_weight.base_weight
            # else: exclude from adjusted weights

        # Renormalize to sum to 1.0
        if total_weight > 0:
            for method in adjusted:
                adjusted[method] = adjusted[method] / total_weight

        return adjusted

    def _compute_point_estimate(
        self,
        results: dict,
        weights: dict[str, Decimal]
    ) -> Decimal:
        """Weighted average of point estimates."""
        total = Decimal("0")
        for method, weight in weights.items():
            value = Decimal(str(
                results[method][0].valuation_result["estimated_fair_value"]["amount"]
            ))
            total += value * weight
        return total

    def _derive_range(
        self,
        results: dict,
        weights: dict[str, Decimal]
    ) -> tuple[Decimal, Decimal]:
        """Derive valuation range (±10% or min/max from results)."""
        point = self._compute_point_estimate(results, weights)

        # Range = ±10% of point estimate
        margin = point * Decimal("0.10")
        return (point - margin, point + margin)

    def _check_divergence(self, results: dict) -> tuple[bool, str | None]:
        """Flag divergence if any pair of results differs by >40%."""
        values = [
            Decimal(str(r[0].valuation_result["estimated_fair_value"]["amount"]))
            for r in results.values()
        ]

        if len(values) < 2:
            return False, None

        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                diff_pct = abs(values[i] - values[j]) / max(values[i], values[j])
                if diff_pct > Decimal("0.40"):
                    return True, (
                        f"Divergence detected: {values[i]} vs {values[j]} "
                        f"({diff_pct * 100:.1f}% difference)"
                    )

        return False, None
```

## Models

**File:** `src/vc_audit_tool/reconciliation/models.py`

```python
@dataclass
class MethodologyWeight:
    methodology: str
    base_weight: Decimal
    adjusted_weight: Decimal | None = None
    rationale: str = ""
    data_requirements_met: bool = True
    point_estimate: Decimal | None = None


@dataclass
class MethodologyPlan:
    stage: str
    methodologies: list[MethodologyWeight]


@dataclass
class ConcludedValue:
    point_estimate: Decimal
    range_low: Decimal
    range_high: Decimal
    currency: str = "USD"
    as_of_date: str = Field(default_factory=lambda: datetime.now().isoformat()[:10])


@dataclass
class ReconciliationMetadata:
    methodology_weights: list[MethodologyWeight]
    divergence_flag: bool
    divergence_note: str | None
    reconciliation_rationale: str
    selector_version: str = "v1.0"


@dataclass
class ReconciliationResult:
    concluded_value: ConcludedValue
    reconciliation: ReconciliationMetadata
    methodology_results: dict[str, ValuationResult]  # Full results per method
    company_profile: CompanyProfile
    audit_metadata: dict
```

## Endpoint Integration

**File:** `src/vc_audit_tool/routers/reconcile.py`

```python
@router.post("/reconcile")
async def post_reconcile(request: Request) -> JSONResponse:
    """Multi-methodology reconciled valuation."""
    payload = await read_json(request)
    company_name = payload.get("company_name")

    if not company_name:
        return JSONResponse({"error": "company_name required"}, status_code=400)

    # 1. Research
    agent_state = research_agent.invoke({
        "input_company_name": company_name,
        "as_of_date": payload.get("as_of_date"),
    })

    if not agent_state.get("assembled_request"):
        return JSONResponse({
            "error": "Could not assemble valuation request from research"
        }, status_code=400)

    research_data = agent_state["extracted_data"]

    # 2. Profile
    profiler = CompanyProfiler()
    profile = profiler.profile(research_data, company_name)

    # 3. Select methodologies
    selector = MethodologySelector()
    plan = selector.select(profile)

    # 4. Reconcile
    reconciler = Reconciler(request.app.state.engine)
    result = await reconciler.reconcile(
        plan,
        agent_state["assembled_request"]
    )

    # 5. Persist
    result_dict = result.to_dict()
    request.app.state.store.save(result_dict)

    return JSONResponse(result_dict)
```

## Example Output

```json
{
  "concluded_value": {
    "point_estimate": 120000000.0,
    "range_low": 108000000.0,
    "range_high": 132000000.0,
    "currency": "USD",
    "as_of_date": "2026-03-01"
  },
  "reconciliation": {
    "methodology_weights": [
      {
        "methodology": "comparable_companies",
        "weight": 0.60,
        "data_requirements_met": true,
        "point_estimate": 130000000.0,
        "rationale": "Growth-stage company with strong revenue — comps method primary anchor"
      },
      {
        "methodology": "last_round_market_adjusted",
        "weight": 0.40,
        "data_requirements_met": true,
        "point_estimate": 105000000.0,
        "rationale": "Recent round within 18 months — reliable secondary anchor"
      }
    ],
    "divergence_flag": false,
    "divergence_note": null,
    "reconciliation_rationale": "Weighted average of 2 applicable methodologies for growth-stage company.",
    "selector_version": "v1.0"
  },
  "methodology_results": {
    "comparable_companies": { "valuation_result": { ... } },
    "last_round_market_adjusted": { "valuation_result": { ... } }
  },
  "company_profile": {
    "name": "Anthropic",
    "stage": "growth",
    "has_revenue": true,
    "sector": "enterprise_software"
  },
  "audit_metadata": { "request_id": "...", "generated_at_utc": "..." }
}
```

## Stage-Based Weights (YAML)

| Stage | Scorecard | Berkus | Comps | Last-Round | Ratchet |
|-------|-----------|--------|-------|-----------|---------|
| `pre_seed` | 50% | 50% | ❌ | ❌ | ❌ |
| `seed` | 35% | 30% | — | 35% | ❌ |
| `early` | — | — | 50% | 50% | — |
| `growth` | — | — | 60% | 40% | — |
| `late` | — | — | 70% | 30% | — |

Weights are **dynamically adjusted** based on data availability. If a methodology is excluded at runtime, remaining weights are renormalized to sum to 1.0.

## Testing

**File:** `tests/test_reconciliation.py` (marked `@pytest.mark.integration`)

```python
def test_reconciliation_growth_stage():
    """Test reconciliation for growth-stage company."""
    profile = CompanyProfile(
        name="Stripe",
        stage="growth",
        has_revenue=True,
        estimated_revenue=Decimal("100000000"),
    )

    selector = MethodologySelector()
    plan = selector.select(profile)

    # Should select comps + last_round
    assert len(plan.methodologies) == 2
    assert plan.methodologies[0].methodology == "comparable_companies"
    assert plan.methodologies[0].base_weight == Decimal("0.6")

def test_reconciliation_pre_seed():
    """Test reconciliation for pre-seed company."""
    profile = CompanyProfile(
        name="NovaBio",
        stage="pre_seed",
        has_revenue=False,
    )

    selector = MethodologySelector()
    plan = selector.select(profile)

    # Should select scorecard + berkus (no comps)
    assert len(plan.methodologies) == 2
    methods = {m.methodology for m in plan.methodologies}
    assert "scorecard" in methods
    assert "berkus" in methods
    assert "comparable_companies" not in methods
```

## Related Codemaps

- **[backend.md](./backend.md)** — Valuation engine that runs individual methodologies
- **[INDEX.md](./INDEX.md)** — Overview of all codemaps
