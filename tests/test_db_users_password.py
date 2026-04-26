"""Password hashing and lazy SHA-256 → Argon2 migration."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from report.db_users import (
    authenticate_user,
    change_password,
    create_user,
    hash_password,
    verify_password,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
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
        CREATE TABLE user_properties (
            user_id INTEGER NOT NULL,
            property_slug TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            attempted_at TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return c


def _insert_legacy_sha256_user(
    conn: sqlite3.Connection, username: str, password: str
) -> int:
    salt = "a" * 32
    sha = hashlib.sha256((salt + password).encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO users (username, password_hash, password_salt, role,
                              display_name, is_active, created_at, updated_at)
           VALUES (?, ?, ?, 'client', '', 1, ?, ?)""",
        (username, sha, salt, now, now),
    )
    conn.commit()
    return cur.lastrowid


def test_hash_password_returns_argon2_encoded_string():
    pw_hash, pw_salt = hash_password("hunter2")
    assert pw_hash.startswith("$argon2")
    assert pw_salt == ""


def test_verify_password_argon2_correct():
    pw_hash, pw_salt = hash_password("hunter2")
    assert verify_password("hunter2", pw_hash, pw_salt) is True


def test_verify_password_argon2_wrong():
    pw_hash, pw_salt = hash_password("hunter2")
    assert verify_password("wrong", pw_hash, pw_salt) is False


def test_verify_password_legacy_sha256_correct():
    salt = "a" * 32
    sha = hashlib.sha256((salt + "hunter2").encode()).hexdigest()
    assert verify_password("hunter2", sha, salt) is True


def test_verify_password_legacy_sha256_wrong():
    salt = "a" * 32
    sha = hashlib.sha256((salt + "hunter2").encode()).hexdigest()
    assert verify_password("not-it", sha, salt) is False


def test_authenticate_user_rehashes_legacy_on_successful_login(conn):
    _insert_legacy_sha256_user(conn, "alice", "hunter2")

    user = authenticate_user(conn, "alice", "hunter2")
    assert user is not None

    row = conn.execute(
        "SELECT password_hash, password_salt FROM users WHERE username = 'alice'"
    ).fetchone()
    assert row["password_hash"].startswith("$argon2")
    assert row["password_salt"] == ""

    # Subsequent logins still succeed against the migrated hash.
    assert authenticate_user(conn, "alice", "hunter2") is not None


def test_authenticate_user_does_not_rehash_argon2_user(conn):
    create_user(conn, username="bob", password="hunter2", role="client")
    before = conn.execute(
        "SELECT password_hash FROM users WHERE username = 'bob'"
    ).fetchone()["password_hash"]

    assert authenticate_user(conn, "bob", "hunter2") is not None

    after = conn.execute(
        "SELECT password_hash FROM users WHERE username = 'bob'"
    ).fetchone()["password_hash"]
    assert after == before


def test_authenticate_user_wrong_password_does_not_mutate_legacy_hash(conn):
    _insert_legacy_sha256_user(conn, "alice", "real")
    before = conn.execute(
        "SELECT password_hash FROM users WHERE username = 'alice'"
    ).fetchone()["password_hash"]

    assert authenticate_user(conn, "alice", "wrong") is None

    after = conn.execute(
        "SELECT password_hash FROM users WHERE username = 'alice'"
    ).fetchone()["password_hash"]
    assert after == before


def test_authenticate_user_inactive_user_rejected(conn):
    create_user(conn, username="bob", password="hunter2", role="client")
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'bob'")
    conn.commit()
    assert authenticate_user(conn, "bob", "hunter2") is None


def test_change_password_writes_argon2(conn):
    user = create_user(conn, username="bob", password="old", role="client")
    change_password(conn, user["id"], "new")
    row = conn.execute(
        "SELECT password_hash, password_salt FROM users WHERE id = ?",
        (user["id"],),
    ).fetchone()
    assert row["password_hash"].startswith("$argon2")
    assert row["password_salt"] == ""
    assert authenticate_user(conn, "bob", "new") is not None
    assert authenticate_user(conn, "bob", "old") is None


def test_create_user_writes_argon2(conn):
    user = create_user(conn, username="bob", password="hunter2", role="client")
    row = conn.execute(
        "SELECT password_hash, password_salt FROM users WHERE id = ?",
        (user["id"],),
    ).fetchone()
    assert row["password_hash"].startswith("$argon2")
    assert row["password_salt"] == ""
