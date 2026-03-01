"""Shared state types for the company research agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

# ── Sector inference map ────────────────────────────────────────────────

_KEYWORD_SECTORS: dict[str, str] = {
    "artificial intelligence": "enterprise_software",
    "machine learning": "enterprise_software",
    "cybersecurity": "cybersecurity",
    "semiconductor": "semiconductors",
    "aerospace": "defense_electronics",
    "defense": "defense_electronics",
    "rocket": "defense_electronics",
    "space": "defense_electronics",
    "ecommerce": "ecommerce",
    "e-commerce": "ecommerce",
    "telecom": "telecommunications",
    "fintech": "enterprise_software",
    "saas": "enterprise_software",
    "cloud": "enterprise_software",
    "software": "enterprise_software",
    "ai": "enterprise_software",
    "data": "enterprise_software",
    "chip": "semiconductors",
    "security": "cybersecurity",
}


# ── LangGraph state ──────────────────────────────────────────────────────


class ResearchState(TypedDict, total=False):
    # Input
    company_name: str
    as_of_date: str
    methodology: str
    description_hint: str

    # Intermediate
    normalised_name: str
    inferred_sector: str
    inferred_sic: str

    # Raw data
    form_d_rounds: list[dict[str, Any]]
    government_contracts: list[dict[str, Any]]
    government_contracts_usd: float | None
    raw_snippets: list[str]
    source_titles: list[str]

    # Structured evidence (replaces unstructured web_facts)
    evidence_package: dict[str, Any]  # EvidencePackage.to_dict()
    web_facts: dict[str, Any]  # kept for backward compat

    # Final output
    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    best_available_methodology: str | None
    missing_for_best_available: list[str]
    error: str | None


# ── Result dataclass ─────────────────────────────────────────────────────


@dataclass
class ResearchResult:
    """Return type of :meth:`CompanyResearchAgent.run`."""

    assembled_request: dict[str, Any] | None
    research_metadata: dict[str, Any]
    missing_fields: list[str]
    best_available_methodology: str | None = None
    missing_for_best_available: list[str] | None = None
    web_facts: dict[str, Any] | None = None
    error: str | None = None
    company_profile: Any | None = None

    @property
    def is_complete(self) -> bool:
        return self.assembled_request is not None and not self.missing_fields
