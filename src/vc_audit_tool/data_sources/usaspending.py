"""USASpending.gov federal contract lookup.

Queries the public USASpending.gov API to find federal contracts
awarded to a given company.  This is supplementary data — its absence
is not a blocking error, but it provides valuable context for companies
with significant government revenue (e.g. AI companies with DoD /
intelligence contracts).

Story 3.3 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/usaspending_cache")
_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_USER_AGENT = "vc-audit-tool/0.1 (aineshm@github.com)"


@dataclass(frozen=True)
class GovernmentContract:
    """A single federal contract award."""

    award_id: str
    """USASpending award ID."""

    recipient_name: str
    """Name of the award recipient."""

    award_amount: float
    """Total obligation amount in USD."""

    award_description: str
    """Description of the award / contract."""

    awarding_agency: str
    """Federal agency that made the award."""

    start_date: str
    """Period of performance start date (ISO string)."""

    end_date: str
    """Period of performance end date (ISO string)."""

    source_url: str
    """USASpending.gov URL for this award."""

    def to_dict(self) -> dict[str, object]:
        return {
            "award_id": self.award_id,
            "recipient_name": self.recipient_name,
            "award_amount": self.award_amount,
            "award_description": self.award_description,
            "awarding_agency": self.awarding_agency,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "source_url": self.source_url,
        }


class USASpendingSource:
    """Fetch federal contract data from USASpending.gov.

    Attributes
    ----------
    dataset_version:
        Stamp set after each fetch, of the form
        ``"usaspending-{company}-{date}"``.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = ""
    source_label: str = "USASpending.gov federal contracts"

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def search(self, company_name: str) -> list[GovernmentContract]:
        """Search USASpending.gov for contracts awarded to *company_name*.

        Returns a list of :class:`GovernmentContract` sorted by award
        amount descending.  Returns an empty list if no contracts are
        found — this is not an error.
        """
        if not company_name or not company_name.strip():
            raise DataSourceError("company_name must be a non-empty string.")

        key = self._cache_key(company_name)
        cached = self._read_cache(key)
        if cached is not None:
            self.dataset_version = f"usaspending-{key}-cached"
            return cached

        contracts = self._fetch_contracts(company_name)
        self._write_cache(key, contracts)
        self.dataset_version = f"usaspending-{key}-{date.today().isoformat()}"
        return contracts

    def total_contract_value(self, company_name: str) -> float | None:
        """Return total USD value of federal contracts, or ``None`` if none found."""
        contracts = self.search(company_name)
        if not contracts:
            return None
        return sum(c.award_amount for c in contracts)

    # ── Private: API fetch ──

    def _fetch_contracts(self, company_name: str) -> list[GovernmentContract]:
        """Hit the USASpending.gov API."""
        import httpx

        payload = {
            "filters": {
                "recipient_search_text": [company_name],
                "time_period": [
                    {
                        "start_date": "2015-01-01",
                        "end_date": date.today().isoformat(),
                    }
                ],
                "award_type_codes": [
                    "A",
                    "B",
                    "C",
                    "D",
                    "IDV_A",
                    "IDV_B",
                    "IDV_B_A",
                    "IDV_B_B",
                    "IDV_B_C",
                    "IDV_C",
                    "IDV_D",
                    "IDV_E",
                ],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Description",
                "Awarding Agency",
                "Start Date",
                "End Date",
            ],
            "limit": 50,
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
        }

        logger.info("searching USASpending.gov for '%s'", company_name)

        try:
            resp = httpx.post(
                _SEARCH_URL,
                json=payload,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        except httpx.HTTPError as exc:
            logger.warning("USASpending.gov API error: %s", exc)
            return []  # Non-blocking — absence of contract data is not fatal

        if resp.status_code != 200:
            logger.warning(
                "USASpending.gov returned HTTP %d for '%s'",
                resp.status_code,
                company_name,
            )
            return []

        try:
            data = resp.json()
        except Exception:
            logger.warning("USASpending.gov returned non-JSON response")
            return []

        results = data.get("results", [])
        contracts: list[GovernmentContract] = []

        for r in results:
            try:
                award_id = str(r.get("Award ID", ""))
                contracts.append(
                    GovernmentContract(
                        award_id=award_id,
                        recipient_name=str(r.get("Recipient Name", "")),
                        award_amount=float(r.get("Award Amount", 0)),
                        award_description=str(r.get("Description", "")),
                        awarding_agency=str(r.get("Awarding Agency", "")),
                        start_date=str(r.get("Start Date", "")),
                        end_date=str(r.get("End Date", "")),
                        source_url=f"https://www.usaspending.gov/award/{award_id}",
                    )
                )
            except (ValueError, TypeError):
                logger.debug("skipping unparseable USASpending result", exc_info=True)

        contracts.sort(key=lambda c: c.award_amount, reverse=True)
        logger.info("found %d contracts for '%s'", len(contracts), company_name)
        return contracts

    # ── Disk cache ──

    @staticmethod
    def _cache_key(company_name: str) -> str:
        return company_name.strip().lower().replace(" ", "_").replace("/", "_")[:80]

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> list[GovernmentContract] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cached_date = raw["retrieved_at"][:10]
            age = (date.today() - date.fromisoformat(cached_date)).days
            if age > 7:
                logger.info("USASpending cache for '%s' is %d days old — refreshing", key, age)
                return None
            return [
                GovernmentContract(
                    award_id=c["award_id"],
                    recipient_name=c["recipient_name"],
                    award_amount=c["award_amount"],
                    award_description=c.get("award_description", ""),
                    awarding_agency=c.get("awarding_agency", ""),
                    start_date=c.get("start_date", ""),
                    end_date=c.get("end_date", ""),
                    source_url=c.get("source_url", ""),
                )
                for c in raw.get("contracts", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("corrupt USASpending cache %s — will re-fetch", path)
            return None

    def _write_cache(self, key: str, contracts: list[GovernmentContract]) -> None:
        path = self._cache_path(key)
        payload = {
            "company_name_key": key,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(contracts),
            "contracts": [c.to_dict() for c in contracts],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("wrote USASpending cache %s (%d contracts)", path, len(contracts))
