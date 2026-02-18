"""Minimal HTTP JSON API for VC valuation workflows."""

from __future__ import annotations

import argparse
import json
import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from vc_audit_tool.engine import ValuationEngine
from vc_audit_tool.exceptions import DataSourceError, ValidationError

logger = logging.getLogger("vc_audit_tool.server")


class ValuationRequestHandler(BaseHTTPRequestHandler):
    engine = ValuationEngine()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})

    def do_POST(self) -> None:
        if self.path != "/value":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})
            return

        start = time.monotonic()
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            result = self.engine.evaluate_from_dict(payload)
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "valuation_ok company=%s methodology=%s request_id=%s elapsed_ms=%.1f",
                result.company_name,
                result.methodology,
                result.request_id,
                elapsed_ms,
            )
            self._write_json(HTTPStatus.OK, result.to_dict())
        except json.JSONDecodeError as exc:
            logger.warning("bad_json error=%s", exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc}"})
        except (ValidationError, DataSourceError) as exc:
            logger.warning("validation_error error=%s", exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover
            logger.exception("unhandled_error error=%s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Suppress default stderr logging in favour of structured logger above.
        logger.debug(fmt, *args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VC Audit Tool HTTP service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = ThreadingHTTPServer((args.host, args.port), ValuationRequestHandler)
    logger.info("listening host=%s port=%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
