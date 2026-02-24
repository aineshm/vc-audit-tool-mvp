"""SEC EDGAR company universe builder.

Builds a universe of public companies by SIC code using two EDGAR
data sources:

1. ``company_tickers.json`` — maps every SEC filer to its CIK + ticker.
2. Per-CIK submission JSON (``data.sec.gov/submissions/CIK{cik}.json``) —
   gives us SIC code, company name, exchanges, and filing history.

The universe is cached locally per SIC code and refreshed monthly.

Story 2.1 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from vc_audit_tool.exceptions import DataSourceError

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/edgar_cache")
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_USER_AGENT = "vc-audit-tool/0.1 (contact@example.com)"


def _sec_headers() -> dict[str, str]:
    user_agent = os.environ.get("VC_AUDIT_SEC_USER_AGENT", _USER_AGENT).strip()
    if user_agent == _USER_AGENT:
        logger.warning(
            "SEC requests using default User-Agent; set VC_AUDIT_SEC_USER_AGENT to a real contact "
            "(e.g., 'YourName your.email@company.com') to reduce 403 risk."
        )
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }

# SIC codes we understand, mapped to a human-readable label and our
# internal sector key used by the comps methodology.
SIC_SECTOR_MAP: dict[str, str] = {
    "7372": "enterprise_software",
    "7371": "enterprise_software",
    "7374": "enterprise_software",
    "7379": "enterprise_software",
    "3674": "semiconductors",
    "5045": "technology_distribution",
    "6282": "investment_advice",
    "4813": "telecommunications",
    "3812": "defense_electronics",
    "5961": "ecommerce",
    "3669": "communications_equipment",
}


@dataclass(frozen=True)
class EdgarCompany:
    """A public company record from the EDGAR universe."""

    cik: str
    ticker: str
    company_name: str
    sic: str
    sector: str
    exchange: str


class EdgarCompanyUniverse:
    """Build and cache a universe of public companies from EDGAR by SIC code.

    Attributes
    ----------
    dataset_version:
        Stamp set after each build, of the form
        ``"edgar-sic-{sic}-{date}"``.
    source_label:
        Human-readable label for citation purposes.
    """

    dataset_version: str = ""
    source_label: str = "SEC EDGAR company universe"

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Full ticker→CIK map (loaded lazily)
        self._tickers_map: dict[str, dict[str, str]] | None = None

    def list_by_sic(self, sic_code: str) -> list[EdgarCompany]:
        """Return all companies with the given SIC code.

        Tries disk cache first (valid for 30 days), otherwise fetches
        from EDGAR.
        """
        cached = self._read_cache(sic_code)
        if cached is not None:
            self.dataset_version = f"edgar-sic-{sic_code}-cached"
            return cached

        companies = self._build_universe(sic_code)
        self._write_cache(sic_code, companies)
        self.dataset_version = f"edgar-sic-{sic_code}-{date.today().isoformat()}"
        return companies

    # ── Private helpers ──

    def _build_universe(self, sic_code: str) -> list[EdgarCompany]:
        """Fetch company_tickers.json, then probe each CIK for its SIC."""
        tickers_map = self._get_tickers_map()

        # For each entry in the tickers map, we need to check the SIC.
        # EDGAR has ~10k entries — we batch-check CIKs whose submissions
        # we can fetch.  To keep this efficient, we'll use the EDGAR
        # company search by SIC endpoint first to get a list of CIKs.
        sic_ciks = self._fetch_ciks_for_sic(sic_code)
        if not sic_ciks:
            raise DataSourceError(f"EDGAR returned no companies for SIC code '{sic_code}'.")

        # Cross-reference with tickers_map to get tickers
        # Build a reverse CIK→ticker map
        cik_to_entry: dict[str, dict[str, str]] = {}
        for entry in tickers_map.values():
            cik_str = str(entry["cik_str"]).zfill(10)
            cik_to_entry[cik_str] = entry

        sector = SIC_SECTOR_MAP.get(sic_code, sic_code)
        companies: list[EdgarCompany] = []

        for cik in sic_ciks:
            matched = cik_to_entry.get(cik)
            if matched is None:
                continue
            ticker = str(matched.get("ticker", ""))
            if not ticker:
                continue
            companies.append(
                EdgarCompany(
                    cik=cik,
                    ticker=ticker,
                    company_name=str(matched.get("title", "")),
                    sic=sic_code,
                    sector=sector,
                    exchange="",  # not in the tickers JSON
                )
            )

        logger.info(
            "built EDGAR universe for SIC %s: %d companies with tickers",
            sic_code,
            len(companies),
        )
        return companies

    def _get_tickers_map(self) -> dict[str, dict[str, str]]:
        """Download and cache (in memory) the full EDGAR company_tickers.json."""
        if self._tickers_map is not None:
            return self._tickers_map

        # Try disk cache
        path = self._cache_dir / "company_tickers.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if "retrieved_at" in raw:
                    cached_date = raw["retrieved_at"][:10]
                    age = (date.today() - date.fromisoformat(cached_date)).days
                    if age < 30:
                        cached_data: dict[str, dict[str, str]] = raw.get("data", {})
                        self._tickers_map = cached_data
                        return cached_data
            except (json.JSONDecodeError, KeyError):
                pass

        import httpx

        logger.info("fetching company_tickers.json from EDGAR")
        resp = httpx.get(
            _TICKERS_URL,
            headers=_sec_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            raise DataSourceError(
                "EDGAR company_tickers.json returned HTTP "
                f"{resp.status_code}. Set VC_AUDIT_SEC_USER_AGENT to a valid contact string."
            )

        data: dict[str, dict[str, str]] = resp.json()
        self._tickers_map = data

        # Persist with timestamp
        payload = {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(data),
            "data": data,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return data

    def _fetch_ciks_for_sic(self, sic_code: str) -> list[str]:
        """Use EDGAR company search to get CIKs for a given SIC code.

        Uses the Atom feed at ``browse-edgar`` filtered by SIC.
        Returns up to 100 CIKs.
        """
        import xml.etree.ElementTree as ET

        import httpx

        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&SIC={sic_code}&type=10-K"
            f"&dateb=&owner=include&count=100&search_text=&action=getcompany"
            f"&output=atom"
        )
        logger.info("fetching EDGAR companies for SIC %s", sic_code)
        resp = httpx.get(url, headers=_sec_headers(), timeout=30)
        if resp.status_code != 200:
            raise DataSourceError(
                f"EDGAR company search returned HTTP {resp.status_code} for SIC {sic_code}"
            )

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        tree = ET.fromstring(resp.text)
        ciks: list[str] = []

        for entry in tree.findall("atom:entry", ns):
            content = entry.find("atom:content", ns)
            if content is None:
                continue
            # The XML doesn't namespace <cik>, it's under <company-info>
            # Parse from the entry ID instead: urn:tag:www.sec.gov:cik=XXXXXXXXXX
            entry_id = entry.find("atom:id", ns)
            if entry_id is not None and entry_id.text:
                # Format: urn:tag:www.sec.gov:cik=0001640147
                parts = entry_id.text.split("cik=")
                if len(parts) == 2:
                    ciks.append(parts[1])

        logger.info("found %d CIKs for SIC %s", len(ciks), sic_code)
        return ciks

    # ── Disk cache ──

    def _cache_path(self, sic_code: str) -> Path:
        return self._cache_dir / f"sic_{sic_code}.json"

    def _read_cache(self, sic_code: str) -> list[EdgarCompany] | None:
        path = self._cache_path(sic_code)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Cache is valid for 30 days
            cached_date = raw["retrieved_at"][:10]
            age = (date.today() - date.fromisoformat(cached_date)).days
            if age > 30:
                logger.info("SIC %s cache is %d days old — will refresh", sic_code, age)
                return None
            return [
                EdgarCompany(
                    cik=c["cik"],
                    ticker=c["ticker"],
                    company_name=c["company_name"],
                    sic=c["sic"],
                    sector=c.get("sector", sic_code),
                    exchange=c.get("exchange", ""),
                )
                for c in raw["companies"]
            ]
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("corrupt EDGAR cache %s — will re-fetch", path)
            return None

    def _write_cache(self, sic_code: str, companies: list[EdgarCompany]) -> None:
        path = self._cache_path(sic_code)
        payload = {
            "sic_code": sic_code,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "count": len(companies),
            "companies": [
                {
                    "cik": c.cik,
                    "ticker": c.ticker,
                    "company_name": c.company_name,
                    "sic": c.sic,
                    "sector": c.sector,
                    "exchange": c.exchange,
                }
                for c in companies
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("wrote EDGAR cache %s (%d companies)", path, len(companies))
