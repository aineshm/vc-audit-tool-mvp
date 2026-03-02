# Data Sources Codemap

**Last Updated:** 2026-03-01

Protocol-based pluggable data sources with mock/live swapping.

## Architecture

All data sources implement Python `typing.Protocol` for structural subtyping. This allows the engine to accept either mock or live implementations without type checking or inheritance.

```
┌────────────────────────────────────────────────┐
│  DataSources (protocol container)              │
│  - metrics: MetricsFetcher                     │
│  - universe: CompanyUniverse                   │
│  - ranker: CompsRanker                         │
│  - market_index: MarketIndexSource             │
│  - form_d: FormDProvider                       │
│  - contracts: ContractProvider                 │
└────────────────────────────────────────────────┘
           │                    │
    ┌──────┴────────────┬───────┴────────────┐
    │                   │                    │
    v                   v                    v
  LIVE              MOCK            PINECONE (opt)
(yfinance,         (hardcoded      (hosted vectors)
 EDGAR,             curated
 DuckDuckGo,        datasets)
 Pinecone)
```

## Core Protocols

### 1. MetricsFetcher

**Purpose:** Fetch EV, Revenue, sector multiples for public companies

**File:** Defined in `interfaces.py`, implemented by:
- `yfinance_metrics.py` (live — Yahoo Finance)
- `mock.py` (mock — curated data)

**Interface:**
```python
class MetricsFetcher(Protocol):
    def fetch_metrics(self, ticker: str, metric: str) -> Decimal:
        """Get a single metric (e.g., "marketcap", "revenue")."""
        ...

    def fetch_multiples(
        self, tickers: list[str], metric: str
    ) -> dict[str, Decimal]:
        """Get EV/Revenue, P/E, etc. for multiple tickers."""
        ...
```

**Live Implementation:**
```python
# yfinance_metrics.py
import yfinance as yf
from decimal import Decimal

class YFinanceMetricsFetcher:
    def fetch_multiples(
        self, tickers: list[str], metric: str
    ) -> dict[str, Decimal]:
        """Fetch EV/Revenue or similar from Yahoo Finance."""
        results = {}
        for ticker in tickers:
            try:
                data = yf.Ticker(ticker)
                # Extract metric from Yahoo Finance
                value = Decimal(str(data.info.get(metric, 0)))
                results[ticker] = value
            except Exception:
                pass
        return results
```

### 2. CompanyUniverse

**Purpose:** Find public companies by sector or criteria

**File:** Implemented by:
- `edgar_universe.py` (live — SEC EDGAR)
- `mock.py` (mock)

**Interface:**
```python
class CompanyUniverse(Protocol):
    def find_by_sector(self, sector: str, limit: int = 100) -> list[Company]:
        """Find public companies in a sector (by SIC code)."""
        ...

    def find_by_sic(self, sic_code: str, limit: int = 100) -> list[Company]:
        """Find companies by SIC code."""
        ...
```

### 3. CompsRanker

**Purpose:** Rank companies by semantic similarity to a target description

**File:** Implemented by:
- `embedding_ranker.py` (live local — sentence-transformers)
- `pinecone_ranker.py` (live hosted — Pinecone) (Phase 4)
- `mock.py` (mock)

**Interface:**
```python
class CompsRanker(Protocol):
    def rank(
        self, candidates: list[Company], target: str, top_k: int = 5
    ) -> list[tuple[Company, float]]:
        """Rank candidates by similarity to target description.

        Returns list of (Company, similarity_score) tuples sorted by score.
        """
        ...
```

**Local Implementation (sentence-transformers):**
```python
# embedding_ranker.py
from sentence_transformers import SentenceTransformer, util

class EmbeddingCompsRanker:
    def __init__(self):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def rank(
        self, candidates: list[Company], target: str, top_k: int = 5
    ) -> list[tuple[Company, float]]:
        """Rank via sentence-transformer embeddings."""
        # Embed target description
        target_embedding = self.model.encode(target, convert_to_tensor=True)

        # Embed all candidate descriptions
        scores = []
        for company in candidates:
            embedding = self.model.encode(company.description, convert_to_tensor=True)
            similarity = util.pytorch_cos_sim(target_embedding, embedding)[0][0].item()
            scores.append((company, float(similarity)))

        # Sort by score and return top-k
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
```

**Hosted Implementation (Pinecone) — Phase 4:**
```python
# pinecone_ranker.py
from pinecone import Pinecone, ServerlessSpec

class PineconeCompsRanker:
    def __init__(self, api_key: str, index_name: str = "vc-audit-edgar-comps"):
        self.client = Pinecone(api_key=api_key)
        self.index_name = index_name
        self._ensure_index()

    def _ensure_index(self) -> None:
        """Create index if it doesn't exist."""
        if self.index_name not in self.client.list_indexes().names():
            self.client.create_index(
                name=self.index_name,
                dimension=384,  # multilingual-e5-large
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )

    def rank(
        self, candidates: list[Company], target: str, top_k: int = 5
    ) -> list[tuple[Company, float]]:
        """Rank via Pinecone hosted inference."""
        index = self.client.Index(self.index_name)

        # Query Pinecone with target description
        results = index.query(
            vector=self._embed(target),
            top_k=top_k,
            include_metadata=True
        )

        # Map results to candidates
        ranked = []
        for match in results.matches:
            ticker = match.metadata.get("ticker")
            company = next((c for c in candidates if c.ticker == ticker), None)
            if company:
                ranked.append((company, match.score))

        return ranked
```

### 4. MarketIndexSource

**Purpose:** Fetch historical index levels (NASDAQ, Russell 2000, etc.)

**File:** Implemented by:
- `yfinance_market_index.py` (live)
- `mock.py` (mock)

**Interface:**
```python
class MarketIndexSource(Protocol):
    def fetch_index_level(self, index: str, date: str) -> Decimal:
        """Get index level on a specific date."""
        ...
```

### 5. FormDProvider

**Purpose:** Find Regulation D filings (funding rounds) for a company

**File:** `form_d.py` (live — SEC EDGAR EFTS)

**Interface:**
```python
class FormDProvider(Protocol):
    def find_filings(
        self, company_name: str, limit: int = 10
    ) -> list[FormDFiling]:
        """Find Form D filings for a company."""
        ...
```

### 6. ContractProvider

**Purpose:** Find federal contract revenue

**File:** `usaspending.py` (live — USASpending.gov API)

**Interface:**
```python
class ContractProvider(Protocol):
    def find_contracts(self, company_name: str) -> list[Contract]:
        """Find federal contracts for a company."""
        ...
```

## Factory Pattern: Ranker Selection

**File:** `ranker_factory.py`

Auto-selects Pinecone or local embeddings:

```python
def get_ranker() -> CompsRanker:
    """Return the best available comps ranker.

    Priority:
      1. PineconeCompsRanker — if PINECONE_API_KEY is set
      2. EmbeddingCompsRanker (local) — fallback default
    """
    api_key = os.getenv("PINECONE_API_KEY")
    if api_key:
        index_name = os.getenv("PINECONE_INDEX_NAME", "vc-audit-edgar-comps")
        return PineconeCompsRanker(api_key=api_key, index_name=index_name)

    return EmbeddingCompsRanker()
```

## Evidence & Confidence Scoring

### Evidence Extraction

**File:** `evidence_collector.py`

```python
def extract_evidence(
    web_facts: list[str], llm_extracted: dict
) -> list[Evidence]:
    """Classify evidence with source reliability tiers."""
    evidence_list = []

    for fact in web_facts:
        type_name, base_confidence, domain = _classify_evidence_type(fact)

        source_tier = _source_reliability_multiplier(domain)

        # Recency multiplier (freshness)
        recency_mult = _recency_multiplier(extract_date(fact))

        # Confidence = base × recency × source_tier
        score = base_confidence * recency_mult * source_tier

        evidence = Evidence(
            text=fact,
            source_url=extract_url(fact),
            evidence_type=type_name,
            source_reliability_tier=_tier_name(source_tier),
            confidence_score=score,
            recency=format_date(extract_date(fact)),
        )
        evidence_list.append(evidence)

    return evidence_list
```

### Source Reliability Tiers

**File:** `evidence_patterns.py`

35-entry domain mapping:

```python
SOURCE_RELIABILITY_TIERS = {
    # Tier 1: Financial data terminals (0.95)
    "bloomberg.com": 0.95,
    "reuters.com": 0.95,
    "wsj.com": 0.95,

    # Tier 2: Authoritative tech/finance press (0.90)
    "techcrunch.com": 0.90,
    "forbes.com": 0.90,
    "cnbc.com": 0.90,

    # Tier 3: General news (0.80)
    "businessinsider.com": 0.80,
    "nytimes.com": 0.80,

    # Tier 4: Aggregators (0.70)
    "crunchbase.com": 0.70,
    "pitchbook.com": 0.70,

    # Tier 5: Blogs, social (0.50–0.60)
    "medium.com": 0.60,
    "reddit.com": 0.50,

    # ... more entries
}
```

## Caching

**File:** `cache.py`

Daily dataset caching to prevent redundant API calls:

```python
class DatasetCache:
    """Cache datasets for 24 hours."""

    def __init__(self, cache_dir: Path = Path("~/.vc-audit-cache")):
        self.cache_dir = cache_dir.expanduser()
        self.cache_dir.mkdir(exist_ok=True)

    def get(self, key: str) -> Any | None:
        """Return cached value if <24h old."""
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            age = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).days
            if age < 1:
                return json.loads(cache_file.read_text())
        return None

    def set(self, key: str, value: Any) -> None:
        """Cache value for 24 hours."""
        cache_file = self.cache_dir / f"{key}.json"
        cache_file.write_text(json.dumps(value))
```

## Dependency Injection

**Data sources passed to methodologies via container:**

```python
@dataclass
class DataSources:
    """Container for all pluggable data sources."""
    metrics: MetricsFetcher
    universe: CompanyUniverse
    ranker: CompsRanker
    market_index: MarketIndexSource
    form_d: FormDProvider
    contracts: ContractProvider


# Engine wires sources based on mode
class ValuationEngine:
    def __init__(self, sources: DataSources | None = None):
        if sources is None:
            sources = DataSources(
                metrics=YFinanceMetricsFetcher(),
                universe=EdgarCompanyUniverse(),
                ranker=get_ranker(),  # Pinecone or local
                market_index=YFinanceMarketIndexSource(),
                form_d=FormDProvider(),
                contracts=ContractProvider(),
            )
        self.sources = sources

    @classmethod
    def mock(cls) -> "ValuationEngine":
        """Create engine with mock data sources."""
        from vc_audit_tool.data_sources.mock import (
            MockMetricsFetcher,
            MockCompanyUniverse,
            MockCompsRanker,
            MockMarketIndexSource,
            MockFormDProvider,
            MockContractProvider,
        )

        return cls(DataSources(
            metrics=MockMetricsFetcher(),
            universe=MockCompanyUniverse(),
            ranker=MockCompsRanker(),
            market_index=MockMarketIndexSource(),
            form_d=MockFormDProvider(),
            contracts=MockContractProvider(),
        ))
```

## Data Flow Example: Live Comps

```
POST /value (with sector="enterprise_software", revenue_ltm=10_000_000)
    │
    ├─> EdgarCompanyUniverse.find_by_sector("enterprise_software")
    │   └─> Query SEC EDGAR SIC index
    │       Returns ~500 companies: [MSFT, ORCL, SNOW, DDOG, ...]
    │
    ├─> YFinanceMetricsFetcher.fetch_multiples([...tickers...], "ev_revenue")
    │   └─> For each ticker, fetch from Yahoo Finance API
    │       Returns: {MSFT: 12.5, ORCL: 8.3, SNOW: 15.2, ...}
    │
    ├─> EmbeddingCompsRanker.rank(companies, target_description, top_k=5)
    │   └─> sentence-transformers.encode(description) for each company
    │       Compute cosine similarity to target
    │       Returns: [(DDOG, 0.91), (SNOW, 0.89), (CRM, 0.87), ...]
    │
    └─> Compute valuation:
        Median EV/Revenue = 11.8×
        Value = 10,000,000 × 11.8 = 118,000,000
        Apply discount: 118,000,000 × 0.75 = $88,500,000
```

## Integration Tests

Tests that hit live APIs (marked `@pytest.mark.integration`):

```bash
python3 -m pytest tests/ -q -m integration
```

Unit tests use mocks (default):

```bash
python3 -m pytest tests/ -q  # Mock mode only
```

## Related Codemaps

- **[backend.md](./backend.md)** — Valuation engine that consumes data sources
- **[agent.md](./agent.md)** — Evidence extraction and confidence scoring
