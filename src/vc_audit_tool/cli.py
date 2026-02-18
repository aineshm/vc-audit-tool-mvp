"""CLI entry point for valuations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError


def _load_payload(request_file: Path) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = json.loads(request_file.read_text(encoding="utf-8"))
        return payload
    except FileNotFoundError as exc:
        raise ValidationError(f"Request file not found: {request_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Request file is not valid JSON: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VC Audit Tool CLI - produces auditable valuation output."
    )
    parser.add_argument(
        "--request-file",
        required=True,
        help="Path to JSON request payload.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request_file = Path(args.request_file)
    engine = ValuationEngine()

    try:
        payload = _load_payload(request_file)
        result = engine.evaluate_from_dict(payload)
        if args.pretty:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(json.dumps(result.to_dict()))
        return 0
    except (ValidationError, DataSourceError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
