"""_seed_admin_user must not fall back to admin/admin or seed test clients."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _seed_admin_user
from report.db_users import authenticate_user


@pytest.fixture
def empty_users_db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return c


def test_seed_admin_does_nothing_when_env_vars_unset(empty_users_db, monkeypatch):
    monkeypatch.delenv("RENTERO_USERNAME", raising=False)
    monkeypatch.delenv("RENTERO_PASSWORD", raising=False)

    _seed_admin_user(empty_users_db)

    rows = empty_users_db.execute("SELECT username FROM users").fetchall()
    assert rows == []


def test_seed_admin_does_not_seed_test_clients_when_env_vars_present(
    empty_users_db, monkeypatch
):
    monkeypatch.setenv("RENTERO_USERNAME", "myadmin")
    monkeypatch.setenv("RENTERO_PASSWORD", "supersecret")

    _seed_admin_user(empty_users_db)

    usernames = sorted(
        r["username"]
        for r in empty_users_db.execute("SELECT username FROM users").fetchall()
    )
    assert usernames == ["myadmin"]
    assert "client1" not in usernames
    assert "client2" not in usernames


def test_seed_admin_creates_argon2_hash(empty_users_db, monkeypatch):
    monkeypatch.setenv("RENTERO_USERNAME", "myadmin")
    monkeypatch.setenv("RENTERO_PASSWORD", "supersecret")

    _seed_admin_user(empty_users_db)

    user = authenticate_user(empty_users_db, "myadmin", "supersecret")
    assert user is not None
    assert user["role"] == "admin"
    row = empty_users_db.execute(
        "SELECT password_hash FROM users WHERE username = 'myadmin'"
    ).fetchone()
    assert row["password_hash"].startswith("$argon2")


def test_seed_admin_skips_when_users_already_exist(empty_users_db, monkeypatch):
    monkeypatch.setenv("RENTERO_USERNAME", "myadmin")
    monkeypatch.setenv("RENTERO_PASSWORD", "supersecret")

    empty_users_db.execute(
        """INSERT INTO users (username, password_hash, password_salt, role,
                              display_name, is_active, created_at, updated_at)
           VALUES ('existing', 'x', 'y', 'admin', '', 1, 'now', 'now')"""
    )
    empty_users_db.commit()

    _seed_admin_user(empty_users_db)

    rows = empty_users_db.execute("SELECT username FROM users ORDER BY username").fetchall()
    assert [r["username"] for r in rows] == ["existing"]
