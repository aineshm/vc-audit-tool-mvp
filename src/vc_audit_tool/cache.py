"""Cache management utilities.

Discovers all data cache directories, lists their contents with
retrieval timestamps and sizes, and supports clearing entries by age.

Story 5.1 of the Production Upgrade Plan.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Every data source stores its cache under a sub-directory of ``data/``.
# We register all known sub-directory names here so the cache manager
# can discover them regardless of whether the source has been imported.
_KNOWN_CACHE_DIRS: tuple[str, ...] = (
    "yfinance_cache",
    "yfinance_metrics_cache",
    "edgar_cache",
    "embedding_cache",
    "form_d_cache",
    "usaspending_cache",
)


@dataclass(frozen=True)
class CacheEntry:
    """One cached JSON file."""

    path: Path
    source: str  # e.g. "yfinance_cache"
    size_bytes: int
    retrieved_at: str  # ISO-8601 or "" if not parseable


@dataclass(frozen=True)
class CacheSummary:
    """Aggregate summary returned by :func:`list_cache`."""

    entries: tuple[CacheEntry, ...]
    total_files: int
    total_bytes: int


def _retrieved_at_from_json(path: Path) -> str:
    """Best-effort extraction of ``retrieved_at`` from a JSON cache file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            val = raw.get("retrieved_at", "")
            if isinstance(val, str) and val:
                return val
    except Exception:  # noqa: BLE001
        pass
    return ""


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string, returning *None* on failure."""
    if not ts:
        return None
    try:
        # Handle both "Z" and "+00:00" suffixes
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def list_cache(data_root: Path = Path("data")) -> CacheSummary:
    """Scan all known cache directories and return a summary.

    Parameters
    ----------
    data_root:
        The root ``data/`` directory.  Defaults to ``Path("data")``.

    Returns
    -------
    CacheSummary
        Contains every discovered cache entry, total file count, and
        total size in bytes.
    """
    entries: list[CacheEntry] = []
    for subdir_name in _KNOWN_CACHE_DIRS:
        subdir = data_root / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("*.json")):
            if not path.is_file():
                continue
            size = path.stat().st_size
            retrieved = _retrieved_at_from_json(path)
            entries.append(
                CacheEntry(
                    path=path,
                    source=subdir_name,
                    size_bytes=size,
                    retrieved_at=retrieved,
                )
            )

    total_bytes = sum(e.size_bytes for e in entries)
    return CacheSummary(
        entries=tuple(entries),
        total_files=len(entries),
        total_bytes=total_bytes,
    )


def clear_cache(
    *,
    older_than: timedelta | None = None,
    data_root: Path = Path("data"),
) -> Sequence[Path]:
    """Delete cache files, optionally filtered by age.

    Parameters
    ----------
    older_than:
        If provided, only files whose ``retrieved_at`` timestamp is
        older than ``now - older_than`` are removed.  Files without a
        parseable timestamp are **skipped** (not removed) to avoid
        accidental data loss.  If *None*, **all** cache files are
        removed.
    data_root:
        The root ``data/`` directory.

    Returns
    -------
    list[Path]
        Paths of the files that were actually deleted.
    """
    summary = list_cache(data_root)
    removed: list[Path] = []

    if older_than is None:
        # Clear everything
        for entry in summary.entries:
            try:
                entry.path.unlink()
                removed.append(entry.path)
            except OSError:
                logger.warning("Failed to remove %s", entry.path)
        return removed

    cutoff = datetime.now(timezone.utc) - older_than
    for entry in summary.entries:
        ts = _parse_iso(entry.retrieved_at)
        if ts is None:
            # Cannot determine age — skip to be safe
            logger.debug("Skipping %s (no parseable timestamp)", entry.path)
            continue
        # Make tz-aware if naive
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            try:
                entry.path.unlink()
                removed.append(entry.path)
            except OSError:
                logger.warning("Failed to remove %s", entry.path)

    return removed
