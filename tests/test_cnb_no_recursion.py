"""Regression tests for the CNB ↔ migrations recursion fix.

Before the fix, _load_db_into_memory called report.db.get_connection(), which
ran _run_migrations(), which ran _backfill_payout_batches_from_active_sources(),
which parsed Airbnb CSV → for-each batch → _cnb_rate_for_batch_date() →
get_rate_for_reservation() → _load_db_into_memory(). Infinite recursion until
"maximum recursion depth exceeded" was raised.

The fix: _load_db_into_memory now uses a direct sqlite3.connect() (no
migrations) and sets _db_loaded=True up-front (belt-and-braces guard against
any future re-entry path).
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest


@pytest.fixture
def fresh_cnb_module(monkeypatch, tmp_path):
    """Reset CNB module-level state and point _DB_PATH at a temp DB.

    The DB is created with the cnb_rates schema only (no migrations), which
    is exactly what _load_db_into_memory should be able to read from.
    """
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE cnb_rates (date TEXT PRIMARY KEY, rate REAL NOT NULL, "
        "fetched_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO cnb_rates (date, rate, fetched_at) VALUES "
        "('2026-01-15', 24.123, '2026-01-15T10:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    sys.modules.pop("report.cnb", None)
    from report import cnb  # noqa: WPS433 — fresh import after pop

    monkeypatch.setattr(cnb, "_db_loaded", False, raising=True)
    cnb._rate_cache.clear()

    # Point report.db._DB_PATH at the temp DB so _load_db_into_memory reads it.
    from report import db as _db
    monkeypatch.setattr(_db, "_DB_PATH", db_path, raising=True)
    return cnb


def test_load_db_into_memory_does_not_recurse_via_migrations(fresh_cnb_module, monkeypatch):
    """If a re-entry path triggers _load_db_into_memory while it is already
    running, the second call must short-circuit on the _db_loaded guard rather
    than recursing.
    """
    cnb = fresh_cnb_module
    call_count = {"n": 0}
    real_get_all_cnb_rates = None

    from report import db as _db

    def reentrant_get_all_cnb_rates(conn):
        # Simulate the historical recursion: this function is called from
        # within _load_db_into_memory, and tries to call _load_db_into_memory
        # again (as the original migration cascade did).
        call_count["n"] += 1
        cnb._load_db_into_memory()  # must NOT recurse
        return real_get_all_cnb_rates(conn)

    real_get_all_cnb_rates = _db.get_all_cnb_rates
    monkeypatch.setattr(_db, "get_all_cnb_rates", reentrant_get_all_cnb_rates)

    # First call. The guard must be set BEFORE get_all_cnb_rates runs, so the
    # nested _load_db_into_memory inside reentrant_get_all_cnb_rates returns
    # immediately. Without the fix this raises RecursionError.
    cnb._load_db_into_memory()
    assert call_count["n"] == 1, (
        "get_all_cnb_rates should only be invoked once — the nested "
        "_load_db_into_memory call must short-circuit on the guard"
    )
    assert cnb._db_loaded is True


def test_load_db_into_memory_uses_direct_sqlite_connect(fresh_cnb_module, monkeypatch):
    """_load_db_into_memory must NOT go through report.db.get_connection,
    because that triggers migrations (which is the root cause of recursion).
    """
    cnb = fresh_cnb_module
    from report import db as _db

    def fail_get_connection(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError(
            "_load_db_into_memory must not call get_connection() — that is "
            "what re-introduces the recursion via _run_migrations."
        )

    monkeypatch.setattr(_db, "get_connection", fail_get_connection)
    cnb._load_db_into_memory()
    # Cache should be populated from the fixture's seeded row.
    assert cnb._rate_cache.get("2026-01-15") == pytest.approx(24.123)


def test_load_db_into_memory_idempotent(fresh_cnb_module):
    """Second call is a no-op — guard already True."""
    cnb = fresh_cnb_module
    cnb._load_db_into_memory()
    snapshot = dict(cnb._rate_cache)
    cnb._rate_cache["TAMPERED"] = 99.0
    cnb._load_db_into_memory()
    # Second call did NOT reload from DB (otherwise TAMPERED would be gone).
    assert "TAMPERED" in cnb._rate_cache
    cnb._rate_cache.pop("TAMPERED", None)
    assert cnb._rate_cache == snapshot


def test_load_db_into_memory_swallows_db_errors(fresh_cnb_module, monkeypatch, tmp_path):
    """If the DB path is missing or unreadable, the function must log and
    continue (cache stays empty), not raise. Guard still flips True so we
    don't retry the broken path on every call.
    """
    cnb = fresh_cnb_module
    from report import db as _db
    monkeypatch.setattr(_db, "_DB_PATH", str(tmp_path / "definitely-missing.db"), raising=True)
    cnb._db_loaded = False
    cnb._rate_cache.clear()

    cnb._load_db_into_memory()  # must not raise
    assert cnb._db_loaded is True
    # Cache may be empty (sqlite3 will create the file but cnb_rates table
    # won't exist), and that's fine — get_all_cnb_rates will throw, caught
    # by the outer try/except.
