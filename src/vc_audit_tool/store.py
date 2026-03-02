"""Simple SQLite audit trail for valuation runs.

Stores the full JSON output of each valuation so past runs are
retrievable, searchable, and auditable.  Zero external dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from vc_audit_tool.store_utils import extract_run_fields

DEFAULT_DB_PATH = Path("valuation_runs.db")


class ValuationStore:
    """Thin wrapper around a SQLite database for persisting valuation results."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent readers alongside a writer and is safe
        # for the asyncio.to_thread usage pattern in the FastAPI server.
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Retry for up to 5 s when the database is locked by another writer.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    # ── public API ──

    def save(self, result_dict: dict[str, Any]) -> str:
        """Persist a valuation or reconcile result and return its request_id."""
        request_id, company_name, methodology, as_of_date, fair_value, generated_at_utc = (
            extract_run_fields(result_dict)
        )
        self._conn.execute(
            """
            INSERT INTO runs (request_id, company_name, methodology, as_of_date,
                              fair_value, generated_at_utc, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                company_name=excluded.company_name,
                methodology=excluded.methodology,
                as_of_date=excluded.as_of_date,
                fair_value=excluded.fair_value,
                generated_at_utc=excluded.generated_at_utc,
                payload=excluded.payload
            """,
            (
                request_id,
                company_name,
                methodology,
                as_of_date,
                fair_value,
                generated_at_utc,
                json.dumps(result_dict),
            ),
        )
        self._conn.commit()
        return request_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent runs (summary only, no full payload)."""
        cursor = self._conn.execute(
            """
            SELECT request_id, company_name, methodology, as_of_date,
                   fair_value, generated_at_utc
            FROM runs ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_run(self, request_id: str) -> dict[str, Any] | None:
        """Return the full payload for a single run, or None."""
        cursor = self._conn.execute(
            "SELECT payload FROM runs WHERE request_id = ?",
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        result: dict[str, Any] = json.loads(row["payload"])
        return result

    def close(self) -> None:
        self._conn.close()

    # ── private ──

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                request_id      TEXT PRIMARY KEY,
                company_name    TEXT NOT NULL,
                methodology     TEXT NOT NULL,
                as_of_date      TEXT NOT NULL,
                fair_value      REAL NOT NULL,
                generated_at_utc TEXT NOT NULL,
                payload         TEXT NOT NULL
            )
            """
        )
        # Index for the list_runs ORDER BY rowid DESC query — rowid is the
        # implicit integer primary key on SQLite tables, so this is already
        # fast.  The explicit index on generated_at_utc supports future
        # time-range filtering queries.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_generated_at ON runs (generated_at_utc DESC)"
        )
        self._conn.commit()
