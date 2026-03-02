"""Factory for creating the active valuation store.

Priority:
  1. SupabaseValuationStore — if SUPABASE_URL and SUPABASE_KEY are set
  2. ValuationStore (SQLite WAL) — fallback default
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol

from vc_audit_tool.store import ValuationStore

logger = logging.getLogger(__name__)


class ValuationStoreProtocol(Protocol):
    """Structural interface satisfied by both ValuationStore and SupabaseValuationStore."""

    def save(self, result_dict: dict[str, Any]) -> str: ...

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def get_run(self, request_id: str) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


def get_store(db_path: Path = Path("valuation_runs.db")) -> ValuationStoreProtocol:
    """Return the best available valuation store.

    If ``SUPABASE_URL`` and ``SUPABASE_KEY`` are both set, returns a
    ``SupabaseValuationStore``.  Otherwise returns ``ValuationStore``
    backed by *db_path* (SQLite WAL mode).
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if url and key:
        logger.info("using SupabaseValuationStore (SUPABASE_URL set)")
        from vc_audit_tool.store_supabase import SupabaseValuationStore

        return SupabaseValuationStore(url=url, key=key)
    logger.info("using SQLite ValuationStore at %s", db_path)
    return ValuationStore(db_path)
