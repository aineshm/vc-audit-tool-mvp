# Research Agent Codemap

**Last Updated:** 2026-03-01

LangGraph-based automated research agent for company intelligence gathering.

## Architecture

```
┌────────────────────────────────────────────────┐
│         ResearchAgent (LangGraph)              │
│  Nodes → State Transitions → Output            │
└────────────────────────────────────────────────┘
        │
        ├─ Node 1: Parse Input
        │  └─ Normalize company name, infer sector
        │
        ├─ Node 2: SEC Form D Search
        │  └─ Query EDGAR EFTS for funding rounds
        │
        ├─ Node 3: Web Research (DDGS)
        │  └─ 7 queries × 6 results = 42 web snippets
        │     ├─ Company revenue
        │     ├─ Funding history
        │     ├─ Leadership
        │     ├─ Product positioning
        │     ├─ Market sizing
        │     ├─ Customer base
        │     └─ Strategic partnerships
        │
        ├─ Node 4: USASpending Contracts
        │  └─ Federal contract revenue signals
        │
        ├─ Node 5: LLM Extraction
        │  └─ Structured data extraction from web facts
        │
        ├─ Node 6: Evidence Classification
        │  └─ Assign source reliability tiers & confidence scores
        │
        └─ Node 7: Assemble Request
           └─ Build complete ValuationRequest
              ├─ Auto-select methodology
              └─ Return to engine
```

## State Management

**File:** `src/vc_audit_tool/agent/state.py`

```python
from typing import TypedDict

class ResearchState(TypedDict, total=False):
    """LangGraph state for research agent."""

    # Input
    input_company_name: str
    as_of_date: str | None

    # Intermediate findings
    parsed_company_name: str
    inferred_sector: str | None
    funding_rounds: list[dict]  # From Form D
    web_facts: list[str]  # Raw web snippets
    federal_contracts: list[dict]

    # Extracted & classified
    evidence_package: EvidencePackage  # Structured evidence
    extracted_data: dict  # LLM-extracted facts

    # Output
    assembled_request: ValuationRequest | None
    best_available_methodology: str | None
    missing_for_best_available: list[str]

    # Metadata
    request_id: str
    execution_logs: list[str]
```

## Node Implementation

### Node 1: Parse Input

**File:** `src/vc_audit_tool/agent/nodes/parse_input.py`

```python
def parse_input_node(state: ResearchState) -> ResearchState:
    """Parse company name and infer sector."""
    name = state["input_company_name"]

    # Normalize name
    parsed = name.strip().lower()

    # Infer sector from keywords (hardcoded map)
    sector = _infer_sector(parsed)

    return {
        **state,
        "parsed_company_name": parsed,
        "inferred_sector": sector,
    }
```

### Node 2: SEC Form D Search

**File:** `src/vc_audit_tool/agent/nodes/form_d_research.py`

```python
def form_d_node(state: ResearchState) -> ResearchState:
    """Search EDGAR EFTS for Regulation D filings."""
    company_name = state["parsed_company_name"]

    form_d_provider = FormDProvider()
    filings = form_d_provider.find_filings(company_name, limit=10)

    # Extract funding rounds
    rounds = [
        {
            "filing_date": f.filing_date,
            "amount_raised": f.amount_raised,
            "investors": f.investors,
        }
        for f in filings
    ]

    return {**state, "funding_rounds": rounds}
```

### Node 3: Web Research

**File:** `src/vc_audit_tool/agent/nodes/web_research.py`

```python
def web_research_node(state: ResearchState) -> ResearchState:
    """Search DuckDuckGo for company information."""
    company_name = state["parsed_company_name"]

    # 7 thematic queries
    queries = [
        f"{company_name} revenue",
        f"{company_name} funding",
        f"{company_name} valuation",
        f"{company_name} leadership",
        f"{company_name} product",
        f"{company_name} market",
        f"{company_name} customers",
    ]

    facts = []
    for query in queries:
        results = _ddg_search(query)  # 6 results per query
        facts.extend([r["snippet"] + f" (from {r['link']})" for r in results])

    return {**state, "web_facts": facts}

def _ddg_search(query: str) -> list[dict]:
    """Search DuckDuckGo with DDGS library (mocked in unit tests)."""
    from duckduckgo_search import DDGS
    ddgs = DDGS()
    return list(ddgs.text(query, max_results=6))
```

### Node 4: USASpending Contracts

**File:** `src/vc_audit_tool/agent/nodes/usaspending_research.py`

```python
def usaspending_node(state: ResearchState) -> ResearchState:
    """Search USASpending.gov for federal contracts."""
    company_name = state["parsed_company_name"]

    provider = ContractProvider()
    contracts = provider.find_contracts(company_name)

    return {**state, "federal_contracts": contracts}
```

### Node 5: LLM Extraction

**File:** `src/vc_audit_tool/agent/nodes/llm_extractor.py`

```python
def llm_extractor_node(state: ResearchState) -> ResearchState:
    """Extract structured data from web facts using LLM."""
    web_facts = state["web_facts"]
    funding_rounds = state["funding_rounds"]

    # Build prompt from web facts
    prompt = f"""
    Based on the following research facts about {state['input_company_name']},
    extract structured data in JSON format:

    Facts:
    {chr(10).join(web_facts)}

    Extract (as JSON):
    {{
        "company_description": "...",
        "estimated_annual_revenue": <number or null>,
        "headcount": <number or null>,
        "last_round_date": "YYYY-MM-DD" or null,
        "last_round_amount": <number or null>,
        "sector": "...",
        "public_comparable": "ticker or null"
    }}
    """

    # Multi-provider LLM fallback
    llm = get_llm_provider()  # Gemini > OpenAI > Claude > Ollama > Regex
    response = llm.invoke(prompt)

    try:
        extracted = json.loads(response)
    except json.JSONDecodeError:
        # Regex fallback if LLM response is unparseable
        extracted = _regex_extract(response)

    return {**state, "extracted_data": extracted}
```

### Node 6: Evidence Classification

**File:** `src/vc_audit_tool/agent/nodes/evidence_classifier.py`

```python
def evidence_classifier_node(state: ResearchState) -> ResearchState:
    """Classify evidence with source reliability tiers."""
    web_facts = state["web_facts"]

    evidence_list = []
    for fact in web_facts:
        # Classify using evidence_patterns.py
        type_name, base_conf, domain = _classify_evidence_type(fact)
        source_tier = _source_reliability_multiplier(domain)
        recency_mult = _recency_multiplier(extract_date(fact))
        score = base_conf * source_tier * recency_mult

        evidence = Evidence(
            text=fact,
            source_url=extract_url(fact),
            evidence_type=type_name,
            source_reliability_tier=_tier_name(source_tier),
            confidence_score=score,
            recency=format_date(extract_date(fact)),
        )
        evidence_list.append(evidence)

    package = EvidencePackage(
        evidence_list=evidence_list,
        total_snippets=len(web_facts),
        unique_sources=len(set(e.source_url for e in evidence_list)),
    )

    return {**state, "evidence_package": package}
```

### Node 7: Assemble Request

**File:** `src/vc_audit_tool/agent/nodes/request_assembler.py`

```python
def request_assembler_node(state: ResearchState) -> ResearchState:
    """Assemble ValuationRequest from gathered intelligence."""
    extracted = state["extracted_data"]
    funding_rounds = state["funding_rounds"]
    sector = state["inferred_sector"] or extracted.get("sector")

    # Determine best methodology based on available data
    best_method = _select_best_methodology(extracted, funding_rounds)

    # Build inputs for selected methodology
    if best_method == "comparable_companies":
        if not sector:
            return {
                **state,
                "assembled_request": None,
                "best_available_methodology": best_method,
                "missing_for_best_available": ["sector inference failed"],
            }

        inputs = {
            "sector": sector,
            "revenue_ltm": extracted.get("estimated_annual_revenue"),
            "target_description": extracted.get("company_description"),
        }

    elif best_method == "last_round_market_adjusted":
        inputs = {
            "last_post_money_valuation": extracted.get("last_round_amount"),
            "last_round_date": extracted.get("last_round_date"),
            "public_index": "NASDAQ_COMPOSITE",
        }

    else:  # direct_valuation or fallback
        inputs = {}

    request = ValuationRequest(
        company_name=state["input_company_name"],
        methodology=best_method,
        as_of_date=state.get("as_of_date") or datetime.now().isoformat()[:10],
        inputs=inputs,
    )

    return {
        **state,
        "assembled_request": request,
        "best_available_methodology": best_method,
    }

def _select_best_methodology(extracted: dict, funding_rounds: list) -> str:
    """Select methodology based on data availability."""
    # Prefer comps if we have sector + revenue
    if extracted.get("estimated_annual_revenue") and extracted.get("sector"):
        return "comparable_companies"

    # Fall back to last-round if we have funding data
    if funding_rounds and extracted.get("last_round_date"):
        return "last_round_market_adjusted"

    # Last resort: direct valuation or scorecard
    return "direct_valuation"
```

## LLM Provider Fallback

**File:** `src/vc_audit_tool/llm_config.py`

```python
def get_llm_provider() -> Any:
    """Return first available LLM provider (multi-provider fallback)."""
    providers = [
        ("google", os.getenv("GOOGLE_API_KEY"), _get_gemini),
        ("openai", os.getenv("OPENAI_API_KEY"), _get_openai),
        ("anthropic", os.getenv("ANTHROPIC_API_KEY"), _get_claude),
        ("ollama", os.getenv("OLLAMA_MODEL"), _get_ollama),
    ]

    for name, env_var, factory in providers:
        if env_var:
            try:
                llm = factory(env_var)
                logger.info("using %s as LLM provider", name)
                return llm
            except Exception as e:
                logger.warning("failed to initialize %s: %s", name, e)

    # Regex fallback (no API key needed)
    logger.info("using regex fallback (no LLM)")
    return RegexExtractor()
```

## Graph Assembly

**File:** `src/vc_audit_tool/agent/__init__.py` or `agent/graph.py`

```python
from langgraph.graph import StateGraph

def build_research_graph() -> StateGraph:
    """Assemble research agent graph."""
    graph = StateGraph(ResearchState)

    # Add nodes
    graph.add_node("parse_input", parse_input_node)
    graph.add_node("form_d_research", form_d_node)
    graph.add_node("web_research", web_research_node)
    graph.add_node("usaspending", usaspending_node)
    graph.add_node("llm_extractor", llm_extractor_node)
    graph.add_node("evidence_classifier", evidence_classifier_node)
    graph.add_node("request_assembler", request_assembler_node)

    # Define edges
    graph.add_edge("parse_input", "form_d_research")
    graph.add_edge("form_d_research", "web_research")
    graph.add_edge("web_research", "usaspending")
    graph.add_edge("usaspending", "llm_extractor")
    graph.add_edge("llm_extractor", "evidence_classifier")
    graph.add_edge("evidence_classifier", "request_assembler")

    # Set entry point
    graph.set_entry_point("parse_input")
    graph.set_finish_point("request_assembler")

    return graph.compile()


# Global instance
research_agent = build_research_graph()
```

## Endpoint Integration

**File:** `src/vc_audit_tool/routers/research.py`

```python
@router.post("/research")
async def post_research(request: Request) -> JSONResponse:
    """Automated research + valuation from company name."""
    payload = await read_json(request)
    company_name = payload.get("company_name")

    if not company_name:
        return JSONResponse({"error": "company_name required"}, status_code=400)

    # Run research agent
    agent_state = research_agent.invoke({
        "input_company_name": company_name,
        "as_of_date": payload.get("as_of_date"),
        "request_id": str(uuid.uuid4()),
    })

    # If agent assembled a request, run engine
    if agent_state["assembled_request"]:
        result = request.app.state.engine.evaluate(agent_state["assembled_request"])
        result_dict = result.to_dict()

        # Attach research metadata
        result_dict["research_metadata"] = {
            "funding_rounds": agent_state["funding_rounds"],
            "evidence_package": agent_state["evidence_package"].to_dict(),
            "extracted_data": agent_state["extracted_data"],
        }

        if payload.get("persist", True):
            request.app.state.store.save(result_dict)

        return JSONResponse(result_dict)

    # Partial result (couldn't assemble complete request)
    return JSONResponse({
        "assembled_request": None,
        "best_available_methodology": agent_state["best_available_methodology"],
        "missing_for_best_available": agent_state["missing_for_best_available"],
        "research_metadata": {
            "funding_rounds": agent_state["funding_rounds"],
            "evidence_package": agent_state["evidence_package"].to_dict(),
            "extracted_data": agent_state["extracted_data"],
        },
    })
```

## Testing

**File:** `tests/test_agent.py` (marked `@pytest.mark.agent`)

```python
@pytest.mark.agent
def test_research_agent_stripe(mock_engine):
    """Test research agent with mocked data."""
    state = research_agent.invoke({
        "input_company_name": "Stripe",
        "as_of_date": "2026-03-01",
        "request_id": "test-123",
    })

    assert state["assembled_request"] is not None
    assert state["assembled_request"]["company_name"] == "stripe"
    assert state["evidence_package"] is not None
    assert len(state["evidence_package"].evidence_list) > 0

@pytest.mark.agent
def test_llm_extraction_fallback():
    """Test regex fallback when LLM is unavailable."""
    state = research_agent.invoke({
        "input_company_name": "UnknownStartup",
    })

    # Regex should extract some data even without LLM
    assert state["extracted_data"] is not None or state["web_facts"]
```

## Key Design Decisions

1. **Stateless nodes** — Each node is a pure function of state (easy to test/parallelize)
2. **LangGraph for orchestration** — Clear node → edge flow, easy to visualize
3. **Multi-provider LLM fallback** — System works even if no LLM is available (regex mode)
4. **Evidence as first-class** — Evidence package preserved through full pipeline
5. **Parallel execution potential** — Form D, web search, USASpending could run in parallel

## Related Codemaps

- **[backend.md](./backend.md)** — Valuation engine that consumes assembled requests
- **[data-sources.md](./data-sources.md)** — Form D, web search, contracts providers
