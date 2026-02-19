"""Shared pytest fixtures for all tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

import vc_audit_tool.server as server_module
from vc_audit_tool.store import ValuationStore

# Close the module-level store that was created at import time so the
# default ``valuation_runs.db`` file can be cleaned up.
_original_store: ValuationStore = server_module.store
_original_store.close()
_default_db = Path("valuation_runs.db")
if _default_db.exists():
    _default_db.unlink()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path) -> Generator[ValuationStore]:
    """Replace the module-level store with a temp-dir-backed instance.

    Prevents test-to-test leakage and avoids leaving a
    ``valuation_runs.db`` file in the repo root.
    """
    store = ValuationStore(tmp_path / "test.db")
    server_module.store = store
    yield store
    store.close()
