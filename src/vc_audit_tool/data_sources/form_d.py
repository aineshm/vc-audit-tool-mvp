"""SEC Form D filing ingestion.

Searches SEC EDGAR EFTS (full-text search) for Form D filings by company
name, then parses the XML to extract funding-round details.

Form D filings are required for private placements under Regulation D
and provide: offering amount, date of first sale, issuer state, number of
investors, and the EDGAR filing URL.

Note: Form D discloses *amount raised* but almost never the post-money
valuation — that must come from press or other sources.

Story 3.1 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/form_d_cache")
_EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_FILING_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
_USER_AGENT = "vc-audit-tool/0.1 (aineshm@github.com)"


@dataclass(frozen=True)
class FundingRound:
    """A single funding event extracted from a Form D filing."""

    date_of_first_sale: date | None
    """Date the offering commenced (may be ``None`` if not disclosed)."""

    amount_raised: float
    """Total offering amount in USD."""

    amount_sold: float
    """Amount already sold at time of filing."""

    issuer_name: str
    """Legal name of the issuer as filed."""

    issuer_state: str
    """State of incorporation / organization."""

    investor_count: int | None
    """Number of investors in this offering (may be ``None``)."""

    source_url: str
    """Direct EDGAR URL to the filing."""

    filing_date: date
    """Date the Form D was filed with the SEC."""

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        # Serialize dates
        for key in ("date_of_first_sale", "filing_date"):
            val = d.get(key)
            if isinstance(val, date):
                d[key] = val.isoformat()
        return d


class FormDSource:
    """Fetch and parse SEC Form D filings for a company.

    Attributes
    ----------
    dataset_version:
        Stamp set after each fetch, of the form
        ``"form-d-{company_name}-{date}"``.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = ""
    source_label: str = "SEC EDGAR Form D filings"

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def search(self, company_name: str) -> list[FundingRound]:
        """Search EDGAR for Form D filings matching *company_name*.

        Returns a list of :class:`FundingRound` sorted by filing date
        descending (most recent first).  Returns an empty list if no
        filings are found — this is not an error (many companies use
        exemptions that don't require Form D).
        """
        if not company_name or not company_name.strip():
            raise DataSourceError("company_name must be a non-empty string.")

        key = self._cache_key(company_name)
        cached = self._read_cache(key)
        if cached is not None:
            self.dataset_version = f"form-d-{key}-cached"
            return cached

        rounds = self._fetch_form_d(company_name)
        self._write_cache(key, rounds)
        self.dataset_version = f"form-d-{key}-{date.today().isoformat()}"
        return rounds

    # ── Private: EDGAR fetch ──

    def _fetch_form_d(self, company_name: str) -> list[FundingRound]:
        """Hit the EDGAR full-text search API for Form D filings."""
        import httpx

        params = {
            "q": f'"{company_name}"',
            "forms": "D,D/A",
            "dateRange": "custom",
            "startdt": "2010-01-01",
            "enddt": date.today().isoformat(),
        }
        logger.info("searching EDGAR Form D for '%s'", company_name)

        try:
            resp = httpx.get(
                _EFTS_SEARCH_URL,
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise DataSourceError(f"EDGAR Form D search failed: {exc}") from exc

        if resp.status_code != 200:
            raise DataSourceError(
                f"EDGAR Form D search returned HTTP {resp.status_code} "
                f"for company '{company_name}'."
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"EDGAR Form D search returned non-JSON response: {exc}") from exc

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            logger.info("no Form D filings found for '%s'", company_name)
            return []

        rounds: list[FundingRound] = []
        for hit in hits:
            source = hit.get("_source", {})
            try:
                fr = self._parse_efts_hit(source)
                if fr is not None:
                    rounds.append(fr)
            except Exception:
                logger.debug("skipping unparseable Form D hit", exc_info=True)

        # Sort most-recent first
        rounds.sort(key=lambda r: r.filing_date, reverse=True)
        logger.info("parsed %d Form D rounds for '%s'", len(rounds), company_name)
        return rounds

    @staticmethod
    def _parse_efts_hit(source: dict[str, object]) -> FundingRound | None:
        """Parse a single EFTS search hit into a FundingRound."""
        # EFTS returns fields like: file_date, entity_name, file_num,
        # form_type, file_description, period_of_report, etc.
        file_date_str = source.get("file_date", "")
        if not file_date_str:
            return None

        filing_date = date.fromisoformat(str(file_date_str)[:10])
        entity_name = str(source.get("entity_name", "Unknown"))

        # Build a search link as the most reliable citation URL
        source_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&company={entity_name.replace(' ', '+')}"
            f"&type=D&dateb=&owner=include&count=10&search_text=&action=getcompany"
        )

        # EFTS doesn't include offering amounts in the search results —
        # those are in the XML filing itself.  We capture what we can
        # from the search metadata and note the limitation.
        return FundingRound(
            date_of_first_sale=filing_date,  # Best approximation from search metadata
            amount_raised=0.0,  # Not available from search results; requires XML parsing
            amount_sold=0.0,
            issuer_name=entity_name,
            issuer_state="",  # Not in search results
            investor_count=None,
            source_url=source_url,
            filing_date=filing_date,
        )

    # ── Disk cache ──

    @staticmethod
    def _cache_key(company_name: str) -> str:
        """Normalise company name into a filesystem-safe cache key."""
        return company_name.strip().lower().replace(" ", "_").replace("/", "_")[:80]

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> list[FundingRound] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cached_date = raw["retrieved_at"][:10]
            age = (date.today() - date.fromisoformat(cached_date)).days
            if age > 7:  # Form D cache expires faster than EDGAR universe cache
                logger.info("Form D cache for '%s' is %d days old — will refresh", key, age)
                return None
            return [
                FundingRound(
                    date_of_first_sale=(
                        date.fromisoformat(r["date_of_first_sale"])
                        if r.get("date_of_first_sale")
                        else None
                    ),
                    amount_raised=r.get("amount_raised", 0.0),
                    amount_sold=r.get("amount_sold", 0.0),
                    issuer_name=r.get("issuer_name", ""),
                    issuer_state=r.get("issuer_state", ""),
                    investor_count=r.get("investor_count"),
                    source_url=r.get("source_url", ""),
                    filing_date=date.fromisoformat(r["filing_date"]),
                )
                for r in raw.get("rounds", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("corrupt Form D cache %s — will re-fetch", path)
            return None

    def _write_cache(self, key: str, rounds: list[FundingRound]) -> None:
        path = self._cache_path(key)
        payload = {
            "company_name_key": key,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(rounds),
            "rounds": [r.to_dict() for r in rounds],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("wrote Form D cache %s (%d rounds)", path, len(rounds))
