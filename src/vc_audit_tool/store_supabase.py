"""Supabase-backed valuation run store (Phase 4 — PostgreSQL).

Drop-in replacement for ValuationStore.  Set SUPABASE_URL and SUPABASE_KEY
environment variables to enable.  The store is stateless: each call opens
a new Supabase client call (the SDK manages connection pooling internally).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from vc_audit_tool.store_utils import extract_run_fields

logger = logging.getLogger(__name__)


class SupabaseValuationStore:
    """Persist valuation runs in Supabase (PostgreSQL).

    Matches the interface of ``ValuationStore`` so the server can swap
    them transparently via ``store_factory.get_store()``.
    """

    _TABLE = "valuation_runs"

    def __init__(self, url: str, key: str) -> None:
        try:
            from supabase import create_client

            self._client = create_client(url, key)
        except ImportError as exc:
            raise RuntimeError(
                "supabase-py is required for SupabaseValuationStore. "
                "Install it with: pip install 'vc-audit-tool[supabase]'"
            ) from exc
        logger.info("SupabaseValuationStore initialised (url=%s)", url[:30])

    # ── public API (mirrors ValuationStore) ──

    def save(self, result_dict: dict[str, Any]) -> str:
        """Upsert a valuation or reconcile result and return its request_id."""
        request_id, company_name, methodology, as_of_date, fair_value, generated_at_utc = (
            extract_run_fields(result_dict)
        )
        row = {
            "request_id": request_id,
            "company_name": company_name,
            "methodology": methodology,
            "as_of_date": as_of_date,
            "fair_value": fair_value,
            "generated_at_utc": generated_at_utc,
            "payload": json.dumps(result_dict),
        }
        try:
            self._client.table(self._TABLE).upsert(row).execute()  # type: ignore[arg-type]
        except Exception as exc:
            raise RuntimeError(f"Supabase save failed: {exc}") from exc
        logger.debug("saved run %s to Supabase", request_id)
        return request_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs (summary only — no payload)."""
        try:
            response = (
                self._client.table(self._TABLE)
                .select(
                    "request_id,company_name,methodology,as_of_date,fair_value,generated_at_utc"
                )
                .order("generated_at_utc", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as exc:
            raise RuntimeError(f"Supabase list_runs failed: {exc}") from exc
        rows: list[dict[str, Any]] = list(response.data) if response.data else []  # type: ignore[arg-type]
        return rows

    def get_run(self, request_id: str) -> dict[str, Any] | None:
        """Return the full payload for a single run, or None."""
        try:
            response = (
                self._client.table(self._TABLE)
                .select("payload")
                .eq("request_id", request_id)
                .maybe_single()
                .execute()
            )
        except Exception as exc:
            raise RuntimeError(f"Supabase get_run failed: {exc}") from exc
        row: dict[str, Any] | None = response.data  # type: ignore[union-attr,assignment]
        if row is None:
            return None
        result: dict[str, Any] = json.loads(str(row["payload"]))
        return result

    def close(self) -> None:
        """No-op — Supabase client is stateless."""
