"""CLI entry point — subcommands for valuation, cache management, and confidence reports.

Usage
-----
    vc-audit value   --request-file payload.json [--pretty]
    vc-audit cache   list
    vc-audit cache   clear --all
    vc-audit cache   clear --older-than 30d
    vc-audit confidence <request_id>
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_payload(request_file: Path) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = json.loads(request_file.read_text(encoding="utf-8"))
        return payload
    except FileNotFoundError as exc:
        raise ValidationError(f"Request file not found: {request_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Request file is not valid JSON: {exc}") from exc


_DURATION_RE = re.compile(r"^(\d+)\s*(d|h|m)$", re.IGNORECASE)


def _parse_duration(text: str) -> timedelta:
    """Parse a human duration like ``30d``, ``12h``, or ``45m``."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid duration '{text}'. Use e.g. 30d, 12h, 45m.")
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "d":
        return timedelta(days=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(minutes=value)


def _fmt_bytes(n: int) -> str:
    """Human-readable file size."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_value(args: argparse.Namespace) -> int:
    """Run a valuation from a JSON request file."""
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


def _cmd_cache_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    """List all cached datasets."""
    from vc_audit_tool.cache import list_cache

    summary = list_cache()
    if summary.total_files == 0:
        print("No cache files found.")
        return 0
    print(f"{'Source':<25} {'Retrieved At':<28} {'Size':>10}  Path")
    print("─" * 100)
    for entry in summary.entries:
        ts = entry.retrieved_at or "(unknown)"
        print(f"{entry.source:<25} {ts:<28} {_fmt_bytes(entry.size_bytes):>10}  {entry.path}")
    print("─" * 100)
    print(f"Total: {summary.total_files} file(s), {_fmt_bytes(summary.total_bytes)}")
    return 0


def _cmd_cache_clear(args: argparse.Namespace) -> int:
    """Clear cached datasets, optionally filtered by age."""
    from vc_audit_tool.cache import clear_cache

    if not args.all and args.older_than is None:
        print("Error: specify --all or --older-than <duration>")
        return 1

    older_than: timedelta | None = None
    if not args.all:
        try:
            older_than = _parse_duration(args.older_than)
        except argparse.ArgumentTypeError as exc:
            print(f"Error: {exc}")
            return 1

    removed = clear_cache(older_than=older_than)
    if not removed:
        print("No cache files matched the criteria.")
    else:
        for p in removed:
            print(f"  Removed: {p}")
        print(f"\n{len(removed)} file(s) removed.")
    return 0


def _cmd_confidence(args: argparse.Namespace) -> int:
    """Print a confidence report for a stored valuation run."""
    from vc_audit_tool.confidence import confidence_report_for_request_id

    try:
        report = confidence_report_for_request_id(args.request_id)
        print(report)
        return 0
    except KeyError as exc:
        print(f"Error: {exc}")
        return 1


def _cmd_research(args: argparse.Namespace) -> int:
    """Run the research agent and produce a valuation."""
    from vc_audit_tool.agent.research import CompanyResearchAgent

    try:
        agent = CompanyResearchAgent()
        research = agent.run(
            args.company,
            methodology=args.methodology or "",
            as_of_date=args.as_of_date or "",
        )

        # If research is incomplete, print what we have and exit
        if not research.is_complete:
            incomplete_result = {
                "assembled_request": None,
                "best_available_methodology": research.best_available_methodology,
                "missing_for_best_available": (
                    research.missing_for_best_available or research.missing_fields
                ),
                "missing_fields": research.missing_fields,
                "research_metadata": research.research_metadata,
                "web_facts": research.web_facts or {},
            }
            if args.pretty:
                print(json.dumps(incomplete_result, indent=2))
            else:
                print(json.dumps(incomplete_result))
            return 0

        # Research complete - run the valuation engine
        assembled_request = research.assembled_request
        if assembled_request is None:
            print(json.dumps({"error": "Research returned no assembled request."}))
            return 1
        engine = ValuationEngine()
        result = engine.evaluate_from_dict(assembled_request)
        result_dict = result.to_dict()
        result_dict["research_metadata"] = research.research_metadata

        if args.pretty:
            print(json.dumps(result_dict, indent=2))
        else:
            print(json.dumps(result_dict))
        return 0
    except ImportError as exc:
        print(json.dumps({"error": f"Research agent dependencies not installed: {exc}"}))
        return 1
    except (ValidationError, DataSourceError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vc-audit",
        description="VC Audit Tool CLI — valuations, cache management, confidence reports.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── vc-audit value ──
    value_p = subparsers.add_parser("value", help="Run a valuation from a JSON request file.")
    value_p.add_argument(
        "--request-file",
        required=True,
        help="Path to JSON request payload.",
    )
    value_p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    # ── vc-audit cache ──
    cache_p = subparsers.add_parser("cache", help="Manage local data caches.")
    cache_sub = cache_p.add_subparsers(dest="cache_action")

    cache_sub.add_parser("list", help="Show all cached datasets with timestamps and sizes.")

    clear_p = cache_sub.add_parser("clear", help="Remove cached datasets.")
    clear_p.add_argument(
        "--all",
        action="store_true",
        help="Remove all cache files.",
    )
    clear_p.add_argument(
        "--older-than",
        help="Remove files older than this duration (e.g. 30d, 12h, 45m).",
    )

    # ── vc-audit confidence ──
    conf_p = subparsers.add_parser(
        "confidence",
        help="Print confidence-indicator summary for a stored run.",
    )
    conf_p.add_argument(
        "request_id",
        help="The request_id (UUID) of a stored valuation run.",
    )

    # ── vc-audit research ──
    research_p = subparsers.add_parser(
        "research",
        help="Run the research agent to gather company data and produce a valuation.",
    )
    research_p.add_argument(
        "company",
        help="Company name to research.",
    )
    research_p.add_argument(
        "--as-of-date",
        help=" valuation date (YYYY-MM-DD, defaults to today).",
    )
    research_p.add_argument(
        "--methodology",
        choices=["scorecard", "berkus", "comparable_companies"],
        help="Valuation methodology to use (defaults to best available based on research).",
    )
    research_p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "value":
        return _cmd_value(args)

    if args.command == "cache":
        if args.cache_action == "list":
            return _cmd_cache_list(args)
        if args.cache_action == "clear":
            return _cmd_cache_clear(args)
        parser.parse_args(["cache", "--help"])
        return 1

    if args.command == "confidence":
        return _cmd_confidence(args)

    if args.command == "research":
        return _cmd_research(args)

    # No subcommand → show help
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
