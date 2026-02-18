"""Typed models for requests and outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from vc_audit_tool import __version__
from vc_audit_tool.validation import parse_date, require_field


@dataclass(frozen=True)
class Citation:
    label: str
    detail: str


@dataclass(frozen=True)
class MonetaryAmount:
    amount: Decimal
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {"amount": float(self.amount), "currency": self.currency}


@dataclass(frozen=True)
class ValuationRequest:
    company_name: str
    methodology: str
    inputs: dict[str, Any]
    as_of_date: date

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> ValuationRequest:
        company_name = require_field(payload, "company_name", str)
        methodology = require_field(payload, "methodology", str)
        inputs = require_field(payload, "inputs", dict)
        as_of_raw = payload.get("as_of_date")
        as_of_date = parse_date(as_of_raw) if as_of_raw else date.today()
        return ValuationRequest(
            company_name=company_name,
            methodology=methodology,
            inputs=inputs,
            as_of_date=as_of_date,
        )


@dataclass
class ValuationResult:
    company_name: str
    methodology: str
    as_of_date: date
    estimated_fair_value: MonetaryAmount
    assumptions: list[str]
    inputs_used: dict[str, Any]
    citations: list[Citation]
    derivation_steps: list[str]
    confidence_indicators: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    generated_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    engine_version: str = field(default_factory=lambda: __version__)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "methodology": self.methodology,
            "as_of_date": self.as_of_date.isoformat(),
            "estimated_fair_value": self.estimated_fair_value.to_dict(),
            "assumptions": self.assumptions,
            "inputs_used": self.inputs_used,
            "citations": [asdict(citation) for citation in self.citations],
            "derivation_steps": self.derivation_steps,
            "confidence_indicators": self.confidence_indicators,
            "audit_metadata": {
                "request_id": self.request_id,
                "generated_at_utc": self.generated_at_utc,
                "engine_version": self.engine_version,
            },
        }
