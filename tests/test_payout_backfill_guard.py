"""Regression tests for _backfill_payout_batches_from_active_sources guard.

Two guarantees:
  1) The guard is set BEFORE the parsing work, so a transient exception
     does not cause every subsequent get_connection() to re-parse all
     active source CSVs (which historically saturated startup with 124×
     redundant parses).
  2) When RENTERO_SKIP_PAYOUT_BACKFILL=1 is in the environment, the
     function returns immediately without touching source_files.
"""
from __future__ import annotations

import os
import sqlite3

import pytest


def _make_db(tmp_path):
    """Create a minimal DB with the source_files table populated."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE source_files ("
        "id INTEGER PRIMARY KEY, source_type TEXT NOT NULL, "
        "original_name TEXT NOT NULL, content BLOB NOT NULL, "
        "is_active INTEGER NOT NULL DEFAULT 1)"
    )
    # One synthetic active airbnb row — content is not a valid CSV; that's
    # fine, because the guard test deliberately makes parsing throw.
    conn.execute(
        "INSERT INTO source_files (source_type, original_name, content, is_active) "
        "VALUES ('airbnb', 'fake.csv', ?, 1)",
        (b"not-real-csv",),
    )
    conn.commit()
    return conn


@pytest.fixture
def reset_guards(monkeypatch):
    """Reset the once-per-process guards so each test gets a fresh starting state."""
    from report import db as _db
    _db._reset_one_shot_migration_guards_for_tests()
    monkeypatch.delenv("RENTERO_SKIP_PAYOUT_BACKFILL", raising=False)
    yield
    _db._reset_one_shot_migration_guards_for_tests()


def test_guard_is_set_before_work_so_exceptions_dont_re_enter(tmp_path, reset_guards, monkeypatch):
    """If parsing throws, a second call must still short-circuit on the guard
    (no re-parsing of source_files).
    """
    from report import db as _db
    from report import verifier as _verifier

    parse_calls = {"n": 0}

    def explode(*args, **kwargs):
        parse_calls["n"] += 1
        raise RuntimeError("simulated CSV parse failure")

    # Make parsing fail. The function must still set the guard up-front so
    # the second call is a pure no-op.
    monkeypatch.setattr(_verifier, "build_airbnb_payout_data", explode)

    conn = _make_db(tmp_path)
    try:
        _db._backfill_payout_batches_from_active_sources(conn)
        # Second call: must NOT re-invoke build_airbnb_payout_data.
        _db._backfill_payout_batches_from_active_sources(conn)
    finally:
        conn.close()

    assert parse_calls["n"] == 1, (
        "Guard must be set before parsing — second call should short-circuit "
        "without re-running build_airbnb_payout_data"
    )
    assert _db._payout_batches_backfill_done is True


def test_skip_backfill_env_var_short_circuits(tmp_path, reset_guards, monkeypatch):
    """With RENTERO_SKIP_PAYOUT_BACKFILL=1 set, the function must not even
    SELECT from source_files, let alone call any CSV parser.
    """
    from report import db as _db
    from report import verifier as _verifier

    monkeypatch.setenv("RENTERO_SKIP_PAYOUT_BACKFILL", "1")

    parser_called = {"flag": False}

    def fail_parse(*args, **kwargs):
        parser_called["flag"] = True
        raise AssertionError("CSV parser must not run when skip env-var is set")

    monkeypatch.setattr(_verifier, "build_airbnb_payout_data", fail_parse)
    monkeypatch.setattr(_verifier, "build_booking_payout_data", fail_parse)

    conn = _make_db(tmp_path)
    # Sanity: source_files has rows; without the env-var the function would
    # read them and call the parser.
    n = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
    assert n >= 1

    try:
        _db._backfill_payout_batches_from_active_sources(conn)
    finally:
        conn.close()

    assert parser_called["flag"] is False
    assert _db._payout_batches_backfill_done is True


def test_empty_source_files_still_sets_guard(tmp_path, reset_guards):
    """Empty source_files is a no-op, but the guard must still be set so the
    function is not retried on every later call.
    """
    from report import db as _db

    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE source_files ("
        "id INTEGER PRIMARY KEY, source_type TEXT NOT NULL, "
        "original_name TEXT NOT NULL, content BLOB NOT NULL, "
        "is_active INTEGER NOT NULL DEFAULT 1)"
    )
    conn.commit()
    try:
        _db._backfill_payout_batches_from_active_sources(conn)
    finally:
        conn.close()
    assert _db._payout_batches_backfill_done is True
