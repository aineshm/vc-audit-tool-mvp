"""Immutable LLM cost tracking for the research agent.

Each LLM call appends a CostRecord and returns a new CostTracker — state is
never mutated in-place, making cost history easy to audit or log.

Usage::

    tracker = CostTracker()
    result, tracker = call_llm_with_tracking(llm, snippets, tracker)
    logger.info("total_cost=%.6f over_budget=%s", tracker.total_cost, tracker.over_budget)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CostRecord:
    """Cost record for a single LLM API call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    provider: str = ""


@dataclass(frozen=True, slots=True)
class CostTracker:
    """Immutable accumulator of LLM call costs.

    Each call to ``add()`` returns a NEW CostTracker — never mutates self.
    """

    budget_limit: float = 1.00  # USD; raise BudgetExceededError if exceeded
    records: tuple[CostRecord, ...] = field(default_factory=tuple)

    def add(self, record: CostRecord) -> CostTracker:
        """Return a new tracker with the record appended."""
        return CostTracker(
            budget_limit=self.budget_limit,
            records=(*self.records, record),
        )

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def over_budget(self) -> bool:
        return self.total_cost > self.budget_limit

    @property
    def call_count(self) -> int:
        return len(self.records)

    def summary(self) -> dict[str, object]:
        return {
            "calls": self.call_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost, 6),
            "budget_limit_usd": self.budget_limit,
            "over_budget": self.over_budget,
        }


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cost_per_1k_input: float,
    cost_per_1k_output: float,
) -> float:
    """Compute USD cost from token counts and per-1k pricing."""
    return (input_tokens / 1000) * cost_per_1k_input + (output_tokens / 1000) * cost_per_1k_output
