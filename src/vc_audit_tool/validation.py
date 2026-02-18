"""Input parsing and validation helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from vc_audit_tool.exceptions import ValidationError


def require_field(payload: dict[str, Any], key: str, expected_type: type | tuple[type, ...]) -> Any:
    value = payload.get(key)
    if value is None:
        raise ValidationError(f"Missing required field: '{key}'.")
    # Reject bool where numeric types are expected — bool is a subclass of int
    # in Python, but accepting True/False as a "number" is a data-quality bug.
    if isinstance(value, bool):
        _is_numeric = (
            expected_type is int
            or expected_type is float
            or (isinstance(expected_type, tuple) and any(t in (int, float) for t in expected_type))
        )
        if _is_numeric:
            raise ValidationError(f"Field '{key}' must be numeric, received bool.")
    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            expected_name = ", ".join(t.__name__ for t in expected_type)
        else:
            expected_name = expected_type.__name__
        raise ValidationError(
            f"Field '{key}' must be of type {expected_name}, received {type(value).__name__}."
        )
    return value


def parse_date(value: str) -> date:
    if not isinstance(value, str):
        raise ValidationError(f"Date must be string in YYYY-MM-DD format, received {value!r}.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid date '{value}'. Expected format: YYYY-MM-DD.") from exc


def parse_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"Field '{field_name}' must be numeric, received bool.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(f"Field '{field_name}' must be numeric.") from exc
    if parsed < Decimal("0"):
        raise ValidationError(f"Field '{field_name}' must be non-negative.")
    return parsed
