# Implementation Plan: VC Audit Tool Redesign

## Task Type
- [ ] Frontend
- [x] Backend
- [x] Architecture
- [x] Database Migration (SQLite → Supabase PostgreSQL)
- [x] Infrastructure

---

## Executive Summary

The VC Audit Tool is over-engineered for its core purpose. This plan proposes a **simplified three-tier workflow** that:
1. Reduces 35 source files to ~15
2. Consolidates three endpoints into one primary workflow
3. Migrates from SQLite to **Supabase PostgreSQL** for cloud persistence
4. Leverages the existing evidence-first approach to eliminate redundant reconciliation logic

---

## Infrastructure Changes

### Supabase PostgreSQL Migration

**Current:** SQLite (`store.py`) - local file-based audit trail
**Target:** Supabase PostgreSQL - cloud-hosted with built-in API

**Available Supabase Project:**
- Project: `aineshm's Project` (drykfbevdfyivyhnkyfc)
- Region: us-west-2
- PostgreSQL Version: 17.6.1

**Benefits:**
| Feature | SQLite (Current) | Supabase (Target) |
|---------|------------------|-------------------|
| Persistence | Local file only | Cloud-hosted |
| Multi-instance | ❌ Not supported | ✅ Connection pooling |
| API | Manual | ✅ Auto-generated REST |
| Realtime | ❌ | ✅ WebSockets |
| Query | Basic SQL | ✅ JSONB, full-text |
| Observability | Manual | ✅ Dashboard, logs |
| RLS | ❌ | ✅ Row-level security |

**Schema Design:**
```sql
-- Valuation runs with JSONB for flexible querying
CREATE TABLE valuation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id TEXT UNIQUE NOT NULL,
    company_name TEXT NOT NULL,
    methodology TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    fair_value DECIMAL(20,2) NOT NULL,
    confidence_score DECIMAL(3,2),
    evidence_tier TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Evidence signals for audit trail
CREATE TABLE evidence_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES valuation_runs(id),
    evidence_type TEXT NOT NULL,
    amount_usd DECIMAL(20,2),
    confidence DECIMAL(3,2),
    source_url TEXT,
    source_snippet TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_runs_company ON valuation_runs(company_name);
CREATE INDEX idx_runs_date ON valuation_runs(as_of_date DESC);
CREATE INDEX idx_runs_methodology ON valuation_runs(methodology);
```

---

## Current State Analysis

### Architecture Overview

```
Current: 35 source files across 6 packages
├── engine.py (132 LOC) - Core orchestrator
├── methodologies/ (6 files, ~800 LOC)
│   ├── comps.py, last_round.py, multiple_ratchet.py
│   ├── scorecard.py, berkus.py, direct_valuation.py
├── data_sources/ (10 files, ~1200 LOC)
│   ├── mock.py, edgar_comps.py, yfinance_*.py
│   ├── evidence_collector.py, form_d.py, usaspending.py
├── reconciliation/ (4 files, ~600 LOC)
│   ├── profiler.py, selector.py, reconciler.py, engine.py
├── agent/ (1 file, ~950 LOC) - LangGraph research agent
└── server.py (~500 LOC) - 3 endpoints: /value, /research, /reconcile
```

### Key Problems

| Problem | Impact | Root Cause |
|---------|--------|------------|
| Over-abstraction | Confusing mental model | Protocol-based sources for 2 implementations |
| Redundant layers | Evidence + reconciliation do same thing | Built sequentially without consolidation |
| Too many endpoints | Users don't know which to use | Feature creep without UX design |
| Buried confidence | Evidence quality hidden in nested output | No top-level summary |
| Stage-based rules | Overlook evidence quality for company stage | YAML rules don't use evidence hierarchy |
| Local-only storage | No cloud persistence | SQLite limits multi-instance deployments |

---

## Proposed Redesign: Three-Tier Workflow

### Core Insight

The evidence-first refactor already solved the fundamental problem: **pick methodology based on evidence quality, not stage-based rules**. The reconciliation layer was built before this insight and now duplicates it.

### New Architecture

```
Proposed: ~15 source files
├── engine.py - Simplified orchestrator
├── valuation/
│   ├── methods.py (consolidated 6 methodologies)
│   ├── evidence.py (evidence collection + scoring)
│   └── models.py (all data structures)
├── research/
│   ├── agent.py (simplified LangGraph)
│   └── sources.py (consolidated data sources)
├── api/
│   ├── routes.py (single /valuate endpoint)
│   └── schemas.py (input/output models)
└── config/
    └── settings.py (consolidated configuration)
```

---

## Technical Solution

### Tier 1: Evidence Collection (Preserve, Simplify)

**Keep:** `evidence_collector.py` - this is the core innovation

**Changes:**
- Move `EvidencePackage`, `ValuationEvidence` to `valuation/models.py`
- Add top-level `confidence_score` (0.0-1.0) based on consensus_strength
- Simplify evidence types to 3 levels instead of 5

```python
# Simplified evidence hierarchy
EVIDENCE_TIERS = {
    "direct": 0.90,      # Secondary market, tender offer, fresh round
    "anchored": 0.65,    # Stale round, analyst consensus
    "derived": 0.35,     # Revenue × multiple, no direct evidence
}
```

### Tier 2: Methodology Selection (Simplify)

**Replace:** YAML-based `MethodologySelector` with evidence-driven selection

**New Logic:**
```python
def select_methodology(evidence: EvidencePackage) -> str:
    """Single decision point based on evidence quality."""
    if evidence.consensus_strength in ("STRONG", "MODERATE"):
        return "direct_valuation"
    if evidence.has_fresh_round():
        return "last_round_market_adjusted"
    if evidence.has_revenue():
        return "comparable_companies"
    # Fallback for pre-revenue
    return "scorecard"
```

**Remove:**
- `reconciliation/profiler.py` - stage classification not needed for selection
- `reconciliation/selector.py` - YAML rules replaced by evidence-driven logic
- `reconciliation/reconciler.py` - weighted averaging moved to direct_valuation
- `config/methodology_rules_v1.yaml` - no longer needed

### Tier 3: Valuation Execution (Consolidate)

**Keep:** Core methodology math (comps, last_round, scorecard)

**Remove:** `direct_valuation.py` - merge into evidence collection output

**Simplify:** Single output format instead of 3 different response shapes

---

## Implementation Steps

### Phase 0: Database Migration (1-2 days)

#### Step 0.1: Set Up Supabase Schema
- **Tool:** `mcp__plugin_supabase_supabase__apply_migration`
- **Operation:** Create PostgreSQL schema
- **Description:** Create `valuation_runs` and `evidence_signals` tables with indexes

#### Step 0.2: Create Supabase Store Adapter
- **File:** `src/vc_audit_tool/store_supabase.py`
- **Operation:** Create new store implementation
- **Description:** Implement `SupabaseValuationStore` with same interface as SQLite version

#### Step 0.3: Write TDD Tests for Store
- **File:** `tests/test_supabase_store.py`
- **Operation:** Create test suite
- **Description:** Test CRUD operations, JSONB queries, connection handling

### Phase 1: Foundation (2-3 days)

#### Step 1.1: Consolidate Data Models
- **File:** `valuation/models.py`
- **Operation:** Create new consolidated models file
- **Description:** Move `ValuationRequest`, `ValuationResult`, `EvidencePackage`, `Citation`, `MonetaryAmount` to single location

#### Step 1.2: Add E2E Test Infrastructure
- **File:** `tests/e2e/test_valuation_flow.py`
- **Operation:** Create E2E test suite
- **Description:** Test full valuation flow from API to database storage

```python
# valuation/models.py
@dataclass
class ValuationInput:
    """Single input model for all workflows."""
    company_name: str
    as_of_date: date
    # Optional - auto-filled by research
    target_description: str | None = None
    sector: str | None = None
    revenue_ltm: Decimal | None = None
    last_post_money_valuation: Decimal | None = None
    last_round_date: date | None = None
    # Override methodology (optional)
    methodology: str | None = None

@dataclass
class ValuationOutput:
    """Clear, flat output structure."""
    company_name: str
    as_of_date: date
    fair_value: MonetaryAmount
    value_range: ValueRange  # low, high
    confidence_score: float  # 0.0-1.0
    methodology: str
    methodology_rationale: str
    evidence_summary: EvidenceSummary
    derivation_steps: list[str]
    citations: list[Citation]
    audit_metadata: AuditMetadata
```

#### Step 1.2: Simplify Evidence Collector
- **File:** `valuation/evidence.py`
- **Operation:** Refactor existing `evidence_collector.py`
- **Description:** Reduce evidence types, add top-level confidence, merge into new location

### Phase 2: Core Logic (3-4 days)

#### Step 2.1: Create Unified Engine
- **File:** `engine.py`
- **Operation:** Rewrite with simplified flow
- **Description:** Replace multi-methodology dispatch with evidence-driven single path

```python
class ValuationEngine:
    def evaluate(self, input: ValuationInput) -> ValuationOutput:
        # 1. Gather evidence (if not provided)
        evidence = self._gather_evidence(input)

        # 2. Select methodology based on evidence
        methodology = select_methodology(evidence)

        # 3. Execute single methodology
        result = self._execute_methodology(methodology, input, evidence)

        # 4. Add confidence and range
        return self._finalize_output(result, evidence)
```

#### Step 2.2: Consolidate Methodologies
- **File:** `valuation/methods.py`
- **Operation:** Merge 6 methodology files into single file
- **Description:** Keep core math, remove boilerplate, share validation logic

### Phase 3: API Simplification (1-2 days)

#### Step 3.1: Single Endpoint Design
- **File:** `api/routes.py`
- **Operation:** Create new unified endpoint
- **Description:** Replace `/value`, `/research`, `/reconcile` with single `/valuate`

```python
@app.post("/valuate")
async def valuate(request: ValuationRequest) -> ValuationOutput:
    """
    Single entry point for all valuations.

    Modes:
    - Research mode: Only company_name provided → auto-research
    - Guided mode: Partial inputs → research fills gaps
    - Manual mode: Full inputs → direct execution
    """
    ...
```

#### Step 3.2: Output Schema Simplification
- **File:** `api/schemas.py`
- **Operation:** Define flat, user-friendly output
- **Description:** Replace nested 5-level output with flat 10-field output

### Phase 4: Migration & Cleanup (2-3 days)

#### Step 4.1: Backward Compatibility Layer
- **File:** `api/compat.py`
- **Operation:** Create adapter for old endpoints
- **Description:** Map old `/research`, `/reconcile` to new `/valuate`

#### Step 4.2: Remove Dead Code
- **Files:** Multiple
- **Operation:** Delete unused modules
- **Description:** Remove reconciliation/, profile.py, selector.py, reconciler.py

#### Step 4.3: Update Tests
- **Files:** `tests/`
- **Operation:** Refactor test suite
- **Description:** Consolidate tests around new engine, maintain coverage

---

## Key Files

| File | Operation | Description |
|------|-----------|-------------|
| `valuation/models.py` | Create | Consolidated data structures |
| `valuation/evidence.py` | Create | Simplified evidence collection |
| `valuation/methods.py` | Create | Consolidated methodologies |
| `engine.py` | Modify | Simplified orchestrator |
| `api/routes.py` | Create | Unified /valuate endpoint |
| `api/schemas.py` | Create | Flat output models |
| `reconciliation/*.py` | Delete | Replaced by evidence-driven logic |
| `config/methodology_rules_v1.yaml` | Delete | No longer needed |

---

## Risks and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing integrations | Medium | High | Maintain backward compat layer for 6 months |
| Test coverage drops during refactor | Medium | Medium | Incremental migration with parallel tests |
| Evidence-driven selection misses edge cases | Low | Medium | Keep stage-based as fallback for pre-revenue |
| Performance regression | Low | Low | Simpler code should be faster |

---

## Success Criteria

1. **Simplicity:** Reduce from 35 to ~15 source files
2. **Clarity:** Single `/valuate` endpoint with clear documentation
3. **Speed:** < 2 second response for research mode (vs current 5-10s)
4. **Coverage:** Maintain 80%+ test coverage
5. **UX:** Top-level `confidence_score` in every response

---

## SESSION_ID (for /ccg:execute use)
- CODEX_SESSION: N/A (external model unavailable)
- GEMINI_SESSION: N/A (external model unavailable)

---

## Next Steps

1. Review this plan with stakeholders
2. Create detailed task breakdown for Phase 1
3. Set up feature branch for incremental development
4. Begin with `valuation/models.py` consolidation