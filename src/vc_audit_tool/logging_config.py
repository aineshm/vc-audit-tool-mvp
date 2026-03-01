"""Structured JSON logging with per-request correlation IDs."""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime, timezone

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _request_id_var.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(rid: str) -> contextvars.Token[str]:
    return _request_id_var.set(rid)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_var.reset(token)
