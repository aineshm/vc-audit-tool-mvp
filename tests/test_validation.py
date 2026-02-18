"""Unit tests for input parsing and validation helpers."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from vc_audit_tool.exceptions import ValidationError
from vc_audit_tool.validation import parse_date, parse_decimal, require_field


class RequireFieldTests(unittest.TestCase):
    """Tests for require_field()."""

    def test_returns_value_when_present_and_correct_type(self) -> None:
        self.assertEqual(require_field({"a": "hello"}, "a", str), "hello")

    def test_raises_when_key_missing(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            require_field({}, "name", str)
        self.assertIn("name", str(ctx.exception))

    def test_raises_when_value_is_none(self) -> None:
        with self.assertRaises(ValidationError):
            require_field({"a": None}, "a", str)

    def test_raises_on_type_mismatch_single(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            require_field({"a": 42}, "a", str)
        self.assertIn("str", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_raises_on_type_mismatch_tuple(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            require_field({"a": [1]}, "a", (int, float, str))
        self.assertIn("int", str(ctx.exception))

    def test_accepts_tuple_of_types(self) -> None:
        self.assertEqual(require_field({"a": 3.14}, "a", (int, float)), 3.14)

    def test_raises_on_empty_string_key_missing(self) -> None:
        with self.assertRaises(ValidationError):
            require_field({"": "val"}, "missing", str)

    def test_bool_is_instance_of_int(self) -> None:
        # Python quirk: bool is subclass of int
        result = require_field({"a": True}, "a", int)
        self.assertIs(result, True)


class ParseDateTests(unittest.TestCase):
    """Tests for parse_date()."""

    def test_valid_iso_date(self) -> None:
        self.assertEqual(parse_date("2024-06-30"), date(2024, 6, 30))

    def test_leap_year(self) -> None:
        self.assertEqual(parse_date("2024-02-29"), date(2024, 2, 29))

    def test_invalid_format_slash(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date("06/30/2024")

    def test_invalid_format_garbage(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date("not-a-date")

    def test_empty_string(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date("")

    def test_non_string_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date(20240630)  # type: ignore[arg-type]

    def test_none_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date(None)  # type: ignore[arg-type]

    def test_invalid_day_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date("2024-02-30")


class ParseDecimalTests(unittest.TestCase):
    """Tests for parse_decimal()."""

    def test_int_input(self) -> None:
        self.assertEqual(parse_decimal(100, "x"), Decimal("100"))

    def test_float_input(self) -> None:
        self.assertEqual(parse_decimal(3.14, "x"), Decimal("3.14"))

    def test_string_input(self) -> None:
        self.assertEqual(parse_decimal("99.99", "x"), Decimal("99.99"))

    def test_zero_is_valid(self) -> None:
        self.assertEqual(parse_decimal(0, "x"), Decimal("0"))

    def test_negative_raises(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            parse_decimal(-5, "amount")
        self.assertIn("non-negative", str(ctx.exception))

    def test_non_numeric_string_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_decimal("abc", "x")

    def test_none_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_decimal(None, "x")

    def test_large_value(self) -> None:
        result = parse_decimal("999999999999.99", "x")
        self.assertEqual(result, Decimal("999999999999.99"))

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(ValidationError):
            parse_decimal("", "x")


if __name__ == "__main__":
    unittest.main()
