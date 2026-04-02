"""Shared error-handling utilities for API routers."""

from __future__ import annotations

# Path markers that indicate internal details — covers macOS, Linux, and Windows.
_INTERNAL_MARKERS = ("/Users/", "/home/", "site-packages", "Traceback", "C:\\", "\\Users\\")


def sanitize_error(exc: Exception) -> str:
    """Return a user-safe error message, stripping internal paths and details."""
    msg = str(exc)
    if any(marker in msg for marker in _INTERNAL_MARKERS):
        return "Internal error during processing. Please try again or contact support."
    if "database" in msg.lower() and ("locked" in msg.lower() or "operational" in msg.lower()):
        return "Service temporarily unavailable. Please retry in a moment."
    return msg
