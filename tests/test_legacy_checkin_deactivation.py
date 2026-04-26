"""Boot-time migration must deactivate checkin source files that use the
pre-Birth-Date header — they parse to zero rows and silently empty out
checkin overlays in the engine."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA, _deactivate_legacy_checkin_source_files


_LEGACY_HEADER = (
    "Property Name;Full Name;Check-Out Date;Reservation ID;Check-In Date;"
    "Name;Surname;Nights of Stay;Booking Reference;Reservation External ID"
)
_NEW_HEADER = (
    "Property Name;Full Name;Check-Out Date;Reservation ID;Check-In Date;"
    "Name;Surname;Birth Date;Nights of Stay;Booking Reference;Reservation External ID"
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = str(tmp_path / "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


def _insert_source(conn, *, name: str, body: str, active: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO source_files (source_type, original_name, content, sha256, imported_at, is_active)
           VALUES ('checkin', ?, ?, ?, '2026-04-01T00:00:00+00:00', ?)""",
        (name, body.encode("utf-8"), name, 1 if active else 0),
    )
    conn.commit()
    return cur.lastrowid


def test_deactivates_legacy_format(conn):
    legacy_id = _insert_source(conn, name="old.csv", body=f"{_LEGACY_HEADER}\n")

    _deactivate_legacy_checkin_source_files(conn)

    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (legacy_id,)
    ).fetchone()["is_active"]
    assert is_active == 0


def test_keeps_new_format_active(conn):
    new_id = _insert_source(conn, name="new.csv", body=f"{_NEW_HEADER}\n")

    _deactivate_legacy_checkin_source_files(conn)

    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (new_id,)
    ).fetchone()["is_active"]
    assert is_active == 1


def test_idempotent_on_already_inactive_files(conn):
    legacy_id = _insert_source(
        conn, name="old.csv", body=f"{_LEGACY_HEADER}\n", active=False
    )

    _deactivate_legacy_checkin_source_files(conn)
    _deactivate_legacy_checkin_source_files(conn)

    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (legacy_id,)
    ).fetchone()["is_active"]
    assert is_active == 0


def test_handles_bom_prefixed_legacy_file(conn):
    legacy_id = _insert_source(
        conn, name="bom.csv", body=f"﻿{_LEGACY_HEADER}\n"
    )

    _deactivate_legacy_checkin_source_files(conn)

    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (legacy_id,)
    ).fetchone()["is_active"]
    assert is_active == 0


def test_only_touches_checkin_source_files(conn):
    cur = conn.execute(
        """INSERT INTO source_files (source_type, original_name, content, sha256, imported_at, is_active)
           VALUES ('airbnb', 'airbnb.csv', ?, 'abc', '2026-04-01T00:00:00+00:00', 1)""",
        ("Date,Type,Confirmation Code\n".encode("utf-8"),),
    )
    conn.commit()
    airbnb_id = cur.lastrowid

    _deactivate_legacy_checkin_source_files(conn)

    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (airbnb_id,)
    ).fetchone()["is_active"]
    assert is_active == 1
