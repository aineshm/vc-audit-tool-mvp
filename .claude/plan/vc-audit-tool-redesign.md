# Implementation Plan: VC Audit Tool Redesign

## Task Type
- [x] Backend (Architecture Simplification)
- [x] Fullstack (API + UX improvements)

---

## Executive Summary

The VC Audit Tool currently has 35 Python source files implementing 6 valuation methodologies, a LangGraph research agent, and a reconciliation layer. The system is **over-engineered for its core use case**: finding valuation evidence, scoring it by quality, and producing an auditable valuation.

**Key Insight**: The evidence-first refactor (`direct_valuation` methodology) has already solved the core problem for companies with strong public signals. The reconciliation layer adds complexity that duplicates this functionality.

---

## Technical Solution

### Phase 1: Simplify Core Workflow (High Priority)

#### 1.1 Unify Entry Points
**Current State**: 3 endpoints (`/value`, `/research`, `/reconcile`) with overlapping functionality
**Proposed**: Single `/valuation` endpoint with mode parameter

```
POST /valuation
{
  "company_name": "Anthropic",
  "mode": "auto" | "structured" | "multi_method",
  "as_of_date": "2026-02-25",
  "description_hint": "AI safety company"
}
```

**Files to Modify**:
| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/server.py` | Modify | Consolidate endpoints, add `/valuation` |
| `src/vc_audit_tool/cli.py` | Modify | Add `--mode` flag to CLI |

#### 1.2 Simplify Output Structure
**Current State**: 5 nested top-level keys with buried confidence scores
**Proposed**: Flat, user-friendly structure with prominent confidence

```json
{
  "company": "Anthropic",
  "valuation": {
    "estimate": 45000000000,
    "range_low": 38000000000,
    "range_high": 52000000000,
    "confidence": "HIGH",
    "methodology": "direct_valuation"
  },
  "evidence": [
    {"source": "secondary_market", "value": 50000000000, "confidence": 0.90, "date": "2025-01"},
    {"source": "analyst_consensus", "value": 40000000000, "confidence": 0.70, "date": "2025-02"}
  ],
  "audit_trail": {
    "derivation_steps": [...],
    "assumptions": [...],
    "citations": [...]
  },
  "alternative_methods": [...]
}
```

**Files to Modify**:
| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/reconciliation/models.py` | Modify | Simplify ReconciledValuation structure |
| `src/vc_audit_tool/models.py` | Modify | Add flat output format |

---

### Phase 2: Consolidate Evidence-First Logic (Medium Priority)

#### 2.1 Merge Reconciliation into Direct Valuation
**Current State**: Two parallel paths:
- `direct_valuation` → evidence synthesis
- `reconciliation` → stage-based methodology selection

**Proposed**: Evidence quality drives methodology selection, not company stage

```
Evidence Quality → Methodology Selection:
- STRONG (≥0.70): direct_valuation only
- MODERATE (≥0.50): direct_valuation + last_round cross-check
- WEAK (<0.50): fall back to stage-based reconciliation
```

**Files to Modify**:
| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/agent/research.py` | Modify | Add quality-based methodology routing |
| `src/vc_audit_tool/reconciliation/engine.py` | Modify | Simplify to fallback-only |
| `config/methodology_rules_v1.yaml` | Modify | Add evidence-quality rules |

#### 2.2 Simplify Research Agent
**Current State**: 5-node LangGraph with complex state management
**Proposed**: 3-node linear pipeline

```
Current: parse → form_d → web_research → contracts → assemble
Proposed: gather → score → recommend

- gather: Form D + web + contracts (parallel fetch)
- score: Evidence quality assessment
- recommend: Methodology + assembled request
```

**Files to Modify**:
| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/agent/research.py` | Refactor | Simplify to 3-node pipeline |
| `src/vc_audit_tool/data_sources/evidence_collector.py` | Enhance | Add parallel fetching |

---

### Phase 3: Reduce File Count (Low Priority)

#### 3.1 Consolidate Methodologies
**Current State**: 6 methodology files in `methodologies/`
**Proposed**: Group by stage

```
methodologies/
├── early_stage.py      # scorecard + berkus (pre-revenue)
├── growth_stage.py     # comps + multiple_ratchet (revenue-based)
└── market_adjusted.py  # last_round + direct (round-based)
```

#### 3.2 Consolidate Data Sources
**Current State**: 8 data source files
**Proposed**: Group by protocol

```
data_sources/
├── protocols.py        # MarketIndexSource, ComparableCompanySource
├── live.py             # EDGAR, YFinance, USASpending
├── mock.py             # All mock implementations
└── evidence.py         # EvidenceCollector, EvidencePackage
```

---

## Implementation Steps

### Step 1: Add Unified Valuation Endpoint
**Expected deliverable**: New `/valuation` endpoint that wraps existing functionality
- Add endpoint to `server.py`
- Add `mode` parameter handling
- Maintain backward compatibility with existing endpoints
- Tests: `/valuation` with all 3 modes

### Step 2: Simplify Output Format
**Expected deliverable**: New `SimpleValuationResult` model with flat structure
- Add new model to `models.py`
- Add converter from `ReconciledValuation` to `SimpleValuationResult`
- Update UI to use new format
- Tests: Output format validation

### Step 3: Add Quality-Based Routing
**Expected deliverable**: Evidence quality determines methodology path
- Add quality threshold logic to research agent
- Add `recommended_methodology()` to EvidencePackage
- Wire into `/valuation` endpoint
- Tests: Quality threshold routing

### Step 4: Simplify Research Pipeline
**Expected deliverable**: 3-node linear pipeline
- Refactor LangGraph to parallel gather + score + recommend
- Remove intermediate state fields
- Maintain evidence collection quality
- Tests: Research agent pipeline

### Step 5: Consolidate Files (Optional)
**Expected deliverable**: Reduced file count from 35 to ~20
- Group methodologies by stage
- Group data sources by protocol
- Update imports across codebase
- Tests: Full test suite passes

---

## Key Files

| File | Operation | Description |
|------|-----------|-------------|
| `src/vc_audit_tool/server.py:L45-L120` | Modify | Add unified `/valuation` endpoint |
| `src/vc_audit_tool/models.py:L62-L99` | Modify | Add `SimpleValuationResult` model |
| `src/vc_audit_tool/agent/research.py:L861-L950` | Refactor | Simplify to 3-node pipeline |
| `src/vc_audit_tool/reconciliation/engine.py:L33-L85` | Modify | Add quality-based routing |
| `src/vc_audit_tool/data_sources/evidence_collector.py` | Enhance | Add `recommended_methodology()` |

---

## Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing API consumers | Keep old endpoints as deprecated aliases for 2 releases |
| Loss of audit trail detail | New format includes full `audit_trail` object |
| Quality-based routing errors | Thresholds configurable, fallback to stage-based |
| Reduced flexibility | `mode=multi_method` preserves full reconciliation |

---

## Recommended Priority Order

1. **Immediate** (1-2 days): Unified `/valuation` endpoint with backward compatibility
2. **Short-term** (1 week): Simplified output format with confidence at top level
3. **Medium-term** (2 weeks): Quality-based methodology routing
4. **Long-term** (1 month): Research pipeline simplification + file consolidation

---

## SESSION_ID (for /ccg:execute use)
- CODEX_SESSION: N/A (external model unavailable)
- GEMINI_SESSION: N/A (external model unavailable)