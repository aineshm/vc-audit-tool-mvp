"""Tests for the async valuation service."""

from __future__ import annotations

import asyncio
from unittest import mock

from vc_audit_tool.services import valuation_service


class MockResult:
    """Mock valuation result object."""

    def __init__(self, company_name: str = "Test Co", methodology: str = "test"):
        self.company_name = company_name
        self.methodology = methodology
        self.request_id = "test-123"

    def to_dict(self) -> dict:
        return {
            "valuation_result": {
                "company_name": self.company_name,
                "methodology": self.methodology,
                "as_of_date": "2026-02-26",
                "estimated_fair_value": {"amount": 1000000.0, "currency": "USD"},
                "assumptions": [],
                "inputs_used": {},
                "citations": [],
                "derivation_steps": [],
                "confidence_indicators": {},
            },
            "audit_metadata": {
                "request_id": self.request_id,
                "generated_at_utc": "2026-02-26T00:00:00+00:00",
                "engine_version": "0.1.0",
            },
        }


def test_run_valuation_is_async() -> None:
    """Verify run_valuation is an async function."""
    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(return_value=MockResult())
    store = mock.Mock()

    result = asyncio.run(
        valuation_service.run_valuation(
            payload={},
            engine=engine,
            store=store,
            persist=False,
        )
    )

    assert result.status_code == 200
    assert engine.evaluate_from_dict.called


def test_run_valuation_calls_engine_via_to_thread() -> None:
    """Verify engine.evaluate_from_dict is called via asyncio.to_thread."""
    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(return_value=MockResult())
    store = mock.Mock()

    payload = {"company_name": "Test Co"}

    asyncio.run(
        valuation_service.run_valuation(
            payload=payload,
            engine=engine,
            store=store,
            persist=False,
        )
    )

    engine.evaluate_from_dict.assert_called_once_with(payload)


def test_run_valuation_persists_when_enabled() -> None:
    """Verify store.save is called when persist=True."""
    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(return_value=MockResult())
    store = mock.Mock()

    asyncio.run(
        valuation_service.run_valuation(
            payload={},
            engine=engine,
            store=store,
            persist=True,
        )
    )

    assert store.save.called
    call_args = store.save.call_args[0][0]
    assert "valuation_result" in call_args
    assert "audit_metadata" in call_args


def test_run_valuation_skips_persist_when_disabled() -> None:
    """Verify store.save is NOT called when persist=False."""
    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(return_value=MockResult())
    store = mock.Mock()

    asyncio.run(
        valuation_service.run_valuation(
            payload={},
            engine=engine,
            store=store,
            persist=False,
        )
    )

    store.save.assert_not_called()


def test_run_valuation_handles_validation_error() -> None:
    """Verify ValidationError returns 400 status code."""
    from vc_audit_tool.exceptions import ValidationError

    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(side_effect=ValidationError("invalid"))
    store = mock.Mock()

    result = asyncio.run(
        valuation_service.run_valuation(
            payload={},
            engine=engine,
            store=store,
            persist=False,
        )
    )

    assert result.status_code == 400


def test_run_valuation_handles_data_source_error() -> None:
    """Verify DataSourceError returns 400 status code."""
    from vc_audit_tool.exceptions import DataSourceError

    engine = mock.Mock()
    engine.evaluate_from_dict = mock.Mock(side_effect=DataSourceError("data unavailable"))
    store = mock.Mock()

    result = asyncio.run(
        valuation_service.run_valuation(
            payload={},
            engine=engine,
            store=store,
            persist=False,
        )
    )

    assert result.status_code == 400
