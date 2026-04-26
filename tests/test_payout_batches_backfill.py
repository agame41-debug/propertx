"""Boot-time backfill: parse every active airbnb/booking source_file and
re-materialize its payout artifacts. Idempotent — UPSERTs on duplicate
batch_refs."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import (
    _SCHEMA,
    _backfill_payout_batches_from_active_sources,
    _reset_payout_batches_backfill_guard_for_tests,
)

# Reuse the inline CSV fixture crafted in tests/test_source_registry_payout_batch_persistence.py
# (avoid drift between two fixtures parsing the same parser).
from tests.test_source_registry_payout_batch_persistence import (
    _AIRBNB_PAYOUT_CSV,
    _BOOKING_PAYOUT_CSV,
)


@pytest.fixture(autouse=True)
def _reset_backfill_guard():
    """The helper memoizes itself once-per-process. Reset between tests
    so each one exercises a fresh run."""
    _reset_payout_batches_backfill_guard_for_tests()
    yield
    _reset_payout_batches_backfill_guard_for_tests()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _insert_source(conn, *, source_type: str, name: str, body: bytes, active: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO source_files (source_type, original_name, content, sha256, imported_at, is_active)
           VALUES (?, ?, ?, ?, '2026-04-01T00:00:00+00:00', ?)""",
        (source_type, name, body, name, 1 if active else 0),
    )
    conn.commit()
    return cur.lastrowid


def test_backfill_populates_from_active_airbnb_source(conn):
    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"),
    )

    _backfill_payout_batches_from_active_sources(conn)

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()[0]
    assert n > 0


def test_backfill_populates_from_active_booking_source(conn):
    _insert_source(
        conn, source_type="booking", name="bk.csv",
        body=_BOOKING_PAYOUT_CSV.encode("utf-8"),
    )

    _backfill_payout_batches_from_active_sources(conn)

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'booking'"
    ).fetchone()[0]
    assert n > 0


def test_backfill_skips_inactive_sources(conn):
    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"), active=False,
    )

    _backfill_payout_batches_from_active_sources(conn)

    n = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    assert n == 0


def test_backfill_is_idempotent(conn):
    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"),
    )

    _backfill_payout_batches_from_active_sources(conn)
    first = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    _backfill_payout_batches_from_active_sources(conn)
    second = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]

    assert first == second


def test_backfill_runs_only_once_per_process(conn, monkeypatch):
    """get_connection() fires per request; the backfill must not re-parse
    CSVs on every call. Once it ran successfully, further calls are no-ops
    even if new source rows appear (those go through the import path)."""
    import report.db as db_mod

    call_count = {"n": 0}
    real_build = db_mod.__dict__.get("build_airbnb_payout_data")  # not module-level
    from report.verifier import build_airbnb_payout_data as real_airbnb_build

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return real_airbnb_build(*args, **kwargs)

    monkeypatch.setattr("report.verifier.build_airbnb_payout_data", _spy)

    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"),
    )

    db_mod._backfill_payout_batches_from_active_sources(conn)
    assert call_count["n"] == 1, "first call should parse"

    db_mod._backfill_payout_batches_from_active_sources(conn)
    db_mod._backfill_payout_batches_from_active_sources(conn)
    assert call_count["n"] == 1, "subsequent calls must NOT re-parse"


def test_backfill_no_active_sources_also_marks_done(conn, monkeypatch):
    """Even when there are no active sources to parse, mark the guard so
    we don't re-run the SELECT on every request."""
    import report.db as db_mod
    from report.verifier import build_airbnb_payout_data as real_airbnb_build

    call_count = {"n": 0}

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return real_airbnb_build(*args, **kwargs)

    monkeypatch.setattr("report.verifier.build_airbnb_payout_data", _spy)

    db_mod._backfill_payout_batches_from_active_sources(conn)
    db_mod._backfill_payout_batches_from_active_sources(conn)
    assert call_count["n"] == 0, "no sources means parser is never called"

    # Inserting a source after the guard fired must NOT trigger re-parse —
    # the import path is responsible for new sources.
    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"),
    )
    db_mod._backfill_payout_batches_from_active_sources(conn)
    assert call_count["n"] == 0, "guard must persist across calls"


def test_backfill_no_op_when_no_active_sources(conn):
    """Boot path on a fresh DB — must not crash."""
    _backfill_payout_batches_from_active_sources(conn)
    n = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    assert n == 0


def test_backfill_swallows_exception_does_not_brick_boot(conn, monkeypatch):
    """If a source CSV is corrupt, the backfill must log and return,
    not raise — otherwise every subsequent get_connection() fails."""
    import report.db as db_mod
    from report.verifier import build_airbnb_payout_data  # noqa: F401

    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=_AIRBNB_PAYOUT_CSV.encode("utf-8"),
    )

    def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated corrupt CSV")

    monkeypatch.setattr(
        "report.verifier.build_airbnb_payout_data", _explode
    )

    # Must NOT raise:
    db_mod._backfill_payout_batches_from_active_sources(conn)
