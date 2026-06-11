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


def test_migration_runs_once_per_process(tmp_path):
    """The deactivation decodes every active checkin BLOB, and get_connection()
    runs migrations on every request — so it must be guarded once-per-process
    (fresh uploads are covered at import time in source_registry instead)."""
    from report.db import get_connection, _reset_one_shot_migration_guards_for_tests

    db_path = str(tmp_path / "guarded.db")
    _reset_one_shot_migration_guards_for_tests()
    c1 = get_connection(db_path)  # first connection: helper runs (no-op, empty DB)
    _insert_source(c1, name="old.csv", body=f"{_LEGACY_HEADER}\n")
    c1.close()

    # Same process, guard already flipped — the legacy file must survive.
    c2 = get_connection(db_path)
    is_active = c2.execute(
        "SELECT is_active FROM source_files WHERE original_name = 'old.csv'"
    ).fetchone()["is_active"]
    c2.close()
    assert is_active == 1

    # "Process restart" (guard reset) — now the helper deactivates it.
    _reset_one_shot_migration_guards_for_tests()
    c3 = get_connection(db_path)
    is_active = c3.execute(
        "SELECT is_active FROM source_files WHERE original_name = 'old.csv'"
    ).fetchone()["is_active"]
    c3.close()
    assert is_active == 0


def test_upload_of_legacy_checkin_file_is_deactivated_at_import(conn):
    """source_registry catches fresh legacy uploads now that the boot-time
    migration is once-per-process."""
    from report.source_registry import import_uploaded_source

    summary = import_uploaded_source(
        conn, "checkin", "fresh-legacy.csv",
        f"{_LEGACY_HEADER}\n".encode("utf-8"), imported_by="admin",
    )
    assert summary.get("legacy_checkin_deactivated") is True
    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE original_name = 'fresh-legacy.csv'"
    ).fetchone()["is_active"]
    assert is_active == 0


def test_upload_of_new_format_checkin_file_stays_active(conn):
    from report.source_registry import import_uploaded_source

    summary = import_uploaded_source(
        conn, "checkin", "fresh-new.csv",
        f"{_NEW_HEADER}\n".encode("utf-8"), imported_by="admin",
    )
    assert summary.get("legacy_checkin_deactivated") is None
    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE original_name = 'fresh-new.csv'"
    ).fetchone()["is_active"]
    assert is_active == 1


def test_activate_route_re_deactivates_legacy_checkin_file(conn, monkeypatch):
    """Re-activating an archived legacy checkin file via /sources must not
    leave it active — the boot-time migration runs once per process."""
    import asyncio
    from types import SimpleNamespace

    import report.web as web_module

    file_id = _insert_source(
        conn, name="legacy-reactivate.csv", body=f"{_LEGACY_HEADER}\n", active=False
    )
    monkeypatch.setattr(
        web_module, "_set_flash",
        lambda req, lvl, msg, detail=None: req.session.update(
            {"_flash": {"level": lvl, "message": msg}}
        ),
    )
    request = SimpleNamespace(
        session={},
        state=SimpleNamespace(user={"id": 1, "username": "admin", "role": "admin"}),
        headers={},
    )

    response = asyncio.run(
        web_module.source_activate(
            request=request,
            background_tasks=None,
            file_id=file_id,
            source_type="checkin",
            conn=conn,
            config={},
        )
    )

    assert response.status_code == 303
    is_active = conn.execute(
        "SELECT is_active FROM source_files WHERE id = ?", (file_id,)
    ).fetchone()["is_active"]
    assert is_active == 0
    assert request.session["_flash"]["level"] == "error"
    assert "Birth Date" in request.session["_flash"]["message"]
