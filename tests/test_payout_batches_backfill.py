"""Boot-time backfill: parse every active airbnb/booking source_file and
re-materialize its payout artifacts. Idempotent — UPSERTs on duplicate
batch_refs."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA, _backfill_payout_batches_from_active_sources

# Reuse the inline CSV fixture crafted in tests/test_source_registry_payout_batch_persistence.py
# (avoid drift between two fixtures parsing the same parser).
from tests.test_source_registry_payout_batch_persistence import (
    _AIRBNB_PAYOUT_CSV,
    _BOOKING_PAYOUT_CSV,
)


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
