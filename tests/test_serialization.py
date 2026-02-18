"""Serialization contract tests — ensure to_dict() schema is stable."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from vc_audit_tool import __version__
from vc_audit_tool.models import Citation, MonetaryAmount, ValuationResult

REQUIRED_TOP_KEYS = {"valuation_result", "audit_metadata"}

REQUIRED_VR_KEYS = {
    "company_name",
    "methodology",
    "as_of_date",
    "estimated_fair_value",
    "assumptions",
    "inputs_used",
    "citations",
    "derivation_steps",
    "confidence_indicators",
}

REQUIRED_AUDIT_KEYS = {"request_id", "generated_at_utc", "engine_version"}
REQUIRED_FAIR_VALUE_KEYS = {"amount", "currency"}


class ValuationResultSerializationTests(unittest.TestCase):
    """Verify the output contract that downstream consumers depend on."""

    def _make_result(self, **kwargs: object) -> ValuationResult:
        defaults = dict(
            company_name="TestCo",
            methodology="test",
            as_of_date=date(2026, 2, 18),
            estimated_fair_value=MonetaryAmount(Decimal("100.00")),
            assumptions=["a1"],
            inputs_used={"key": "value"},
            citations=[Citation("source", "detail")],
            derivation_steps=["step 1"],
            confidence_indicators={"risk": "LOW"},
        )
        defaults.update(kwargs)  # type: ignore[arg-type]
        return ValuationResult(**defaults)  # type: ignore[arg-type]

    def test_top_level_keys(self) -> None:
        d = self._make_result().to_dict()
        self.assertEqual(set(d.keys()), REQUIRED_TOP_KEYS)

    def test_valuation_result_keys(self) -> None:
        d = self._make_result().to_dict()
        self.assertEqual(set(d["valuation_result"].keys()), REQUIRED_VR_KEYS)

    def test_audit_metadata_keys(self) -> None:
        d = self._make_result().to_dict()
        self.assertEqual(set(d["audit_metadata"].keys()), REQUIRED_AUDIT_KEYS)

    def test_fair_value_keys(self) -> None:
        d = self._make_result().to_dict()
        vr = d["valuation_result"]
        self.assertEqual(set(vr["estimated_fair_value"].keys()), REQUIRED_FAIR_VALUE_KEYS)

    def test_engine_version_matches_package(self) -> None:
        d = self._make_result().to_dict()
        self.assertEqual(d["audit_metadata"]["engine_version"], __version__)

    def test_amount_is_float(self) -> None:
        d = self._make_result().to_dict()
        self.assertIsInstance(d["valuation_result"]["estimated_fair_value"]["amount"], float)

    def test_as_of_date_is_iso_string(self) -> None:
        d = self._make_result().to_dict()
        self.assertEqual(d["valuation_result"]["as_of_date"], "2026-02-18")

    def test_citations_are_dicts(self) -> None:
        d = self._make_result().to_dict()
        for cit in d["valuation_result"]["citations"]:
            self.assertIsInstance(cit, dict)
            self.assertIn("label", cit)
            self.assertIn("detail", cit)

    def test_confidence_indicators_preserved(self) -> None:
        d = self._make_result(confidence_indicators={"peer_count": 5}).to_dict()
        self.assertEqual(d["valuation_result"]["confidence_indicators"]["peer_count"], 5)

    def test_monetary_amount_default_currency(self) -> None:
        m = MonetaryAmount(Decimal("50.00"))
        self.assertEqual(m.to_dict()["currency"], "USD")

    def test_monetary_amount_custom_currency(self) -> None:
        m = MonetaryAmount(Decimal("50.00"), currency="EUR")
        self.assertEqual(m.to_dict()["currency"], "EUR")

    def test_citation_dataset_version(self) -> None:
        c = Citation("src", "detail", dataset_version="v1")
        d = c.to_dict()
        self.assertEqual(d["dataset_version"], "v1")

    def test_citation_resolved_data_points(self) -> None:
        c = Citation("src", "detail", resolved_data_points=("pt1", "pt2"))
        d = c.to_dict()
        self.assertEqual(d["resolved_data_points"], ["pt1", "pt2"])

    def test_citation_minimal(self) -> None:
        """Citation with no extras should only have label + detail."""
        c = Citation("src", "detail")
        d = c.to_dict()
        self.assertEqual(set(d.keys()), {"label", "detail"})


if __name__ == "__main__":
    unittest.main()
