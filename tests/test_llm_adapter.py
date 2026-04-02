"""Unit tests for the LLM adapter: JSON extraction, retry, cost tracking."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vc_audit_tool.agent.cost_tracker import CostRecord, CostTracker, estimate_cost
from vc_audit_tool.agent.llm_adapter import (
    _extract_json_robust,
    _is_fatal,
    _is_transient,
    _llm_extract_structured,
    _needs_judgment,
)


class TestExtractJsonRobust:
    def test_clean_json(self) -> None:
        assert _extract_json_robust('{"key": "value"}') == {"key": "value"}

    def test_json_with_markdown_fences(self) -> None:
        assert _extract_json_robust('```json\n{"key": "value"}\n```') == {"key": "value"}

    def test_json_embedded_in_text(self) -> None:
        assert _extract_json_robust('Here: {"key": 42} done') == {"key": 42}

    def test_truncated_json_recovery(self) -> None:
        result = _extract_json_robust('{"key1": "val1",\n"key2": "val2",\n"key3": "trunc')
        assert result is not None
        assert result["key1"] == "val1"

    def test_completely_invalid_returns_none(self) -> None:
        assert _extract_json_robust("not json at all") is None

    def test_empty_string_returns_none(self) -> None:
        assert _extract_json_robust("") is None

    def test_nested_json(self) -> None:
        assert _extract_json_robust('{"outer": {"inner": 1}, "list": [1, 2]}') == {"outer": {"inner": 1}, "list": [1, 2]}


class TestErrorClassification:
    def test_transient_errors(self) -> None:
        for name in ["RateLimitError", "Timeout", "ConnectError"]:
            exc = type(name, (Exception,), {})()
            assert _is_transient(exc) is True

    def test_fatal_errors(self) -> None:
        for name in ["AuthenticationError", "BadRequestError"]:
            exc = type(name, (Exception,), {})()
            assert _is_fatal(exc) is True

    def test_unknown_error_is_neither(self) -> None:
        exc = ValueError("something")
        assert _is_transient(exc) is False
        assert _is_fatal(exc) is False


class TestNeedsJudgment:
    def test_single_candidate_no_judgment(self) -> None:
        assert _needs_judgment([MagicMock(amount_usd=5e9)]) is False

    def test_agreeing_candidates_no_judgment(self) -> None:
        assert _needs_judgment([MagicMock(amount_usd=5e9), MagicMock(amount_usd=5.1e9)]) is False

    def test_diverging_candidates_needs_judgment(self) -> None:
        assert _needs_judgment([MagicMock(amount_usd=1e9), MagicMock(amount_usd=5e9)]) is True

    def test_sub_million_skipped(self) -> None:
        assert _needs_judgment([MagicMock(amount_usd=100_000), MagicMock(amount_usd=500_000)]) is False


class TestLlmExtractStructured:
    def test_successful_extraction(self) -> None:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"last_post_money_valuation": 5000000000, "revenue_ltm": null}'
        mock_llm.invoke.return_value = mock_response
        result = _llm_extract_structured(mock_llm, "test/model", "Anthropic", ["snippet1"])
        assert result["last_post_money_valuation"] == 5_000_000_000
        assert result["_model_label"] == "test/model"

    def test_fatal_error_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = type("AuthenticationError", (Exception,), {})("bad key")
        result = _llm_extract_structured(mock_llm, "test/model", "TestCo", ["snippet"])
        assert result == {}

    def test_non_string_content_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=None)
        result = _llm_extract_structured(mock_llm, "test/model", "TestCo", ["snippet"])
        assert result == {}


class TestCostTracker:
    def test_empty_tracker(self) -> None:
        t = CostTracker()
        assert t.total_cost == 0.0
        assert t.call_count == 0
        assert t.over_budget is False

    def test_add_returns_new_tracker(self) -> None:
        t = CostTracker()
        r = CostRecord(model="m", input_tokens=100, output_tokens=50, cost_usd=0.01)
        t2 = t.add(r)
        assert t.call_count == 0
        assert t2.call_count == 1
        assert t2.total_cost == 0.01

    def test_over_budget_detection(self) -> None:
        t = CostTracker(budget_limit=0.05)
        t2 = t.add(CostRecord(model="m", input_tokens=1000, output_tokens=500, cost_usd=0.06))
        assert t2.over_budget is True

    def test_summary_shape(self) -> None:
        s = CostTracker().summary()
        assert "calls" in s
        assert "total_cost_usd" in s


class TestEstimateCost:
    def test_basic_calculation(self) -> None:
        assert estimate_cost(1000, 500, 0.001, 0.002) == pytest.approx(0.002)

    def test_zero_tokens(self) -> None:
        assert estimate_cost(0, 0, 0.001, 0.002) == 0.0
