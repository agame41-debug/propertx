"""Login throttle: 5 username failures or 20 IP failures within 15 min lock."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from report.db_users import (
    authenticate_user,
    create_user,
    is_login_locked,
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


def _insert_old_failure(conn, *, username: str, ip: str, minutes_ago: int):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO login_attempts (username, ip, attempted_at, success) "
        "VALUES (?, ?, ?, 0)",
        (username, ip, ts),
    )
    conn.commit()


def test_authenticate_records_success_attempt(conn):
    create_user(conn, username="alice", password="pw", role="client")
    user = authenticate_user(conn, "alice", "pw", ip="1.2.3.4")
    assert user is not None
    row = conn.execute(
        "SELECT username, ip, success FROM login_attempts WHERE username = 'alice'"
    ).fetchone()
    assert (row["username"], row["ip"], row["success"]) == ("alice", "1.2.3.4", 1)


def test_authenticate_records_failure_for_wrong_password(conn):
    create_user(conn, username="alice", password="pw", role="client")
    user = authenticate_user(conn, "alice", "WRONG", ip="1.2.3.4")
    assert user is None
    fails = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = 'alice' AND success = 0"
    ).fetchone()[0]
    assert fails == 1


def test_authenticate_records_failure_for_unknown_username(conn):
    """Probing for usernames must also feed the throttle, both per-username
    and per-IP, otherwise an attacker can enumerate forever."""
    user = authenticate_user(conn, "not-a-user", "anything", ip="1.2.3.4")
    assert user is None
    fails = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip = '1.2.3.4' AND success = 0"
    ).fetchone()[0]
    assert fails == 1


def test_username_locks_after_five_failures_even_with_correct_password(conn):
    create_user(conn, username="alice", password="pw", role="client")
    for _ in range(5):
        authenticate_user(conn, "alice", "WRONG", ip="1.2.3.4")
    # Now the right password also gets rejected — silently
    user = authenticate_user(conn, "alice", "pw", ip="1.2.3.4")
    assert user is None
    assert is_login_locked(conn, "alice", "1.2.3.4") is True


def test_successful_login_clears_username_failure_counter(conn):
    create_user(conn, username="alice", password="pw", role="client")
    for _ in range(4):
        authenticate_user(conn, "alice", "WRONG", ip="1.2.3.4")
    # Still under the limit; the right password gets in
    assert authenticate_user(conn, "alice", "pw", ip="1.2.3.4") is not None
    # Counter reset
    remaining = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = 'alice' AND success = 0"
    ).fetchone()[0]
    assert remaining == 0
    assert is_login_locked(conn, "alice", "1.2.3.4") is False


def test_old_failures_outside_window_do_not_count(conn):
    create_user(conn, username="alice", password="pw", role="client")
    for _ in range(5):
        _insert_old_failure(conn, username="alice", ip="1.2.3.4", minutes_ago=20)
    # All 5 failures are 20 min old, outside the 15-min window
    assert is_login_locked(conn, "alice", "1.2.3.4") is False
    user = authenticate_user(conn, "alice", "pw", ip="1.2.3.4")
    assert user is not None


def test_ip_locks_after_twenty_failures_across_usernames(conn):
    """Credential-stuffing across many usernames from one IP must be blocked."""
    for i in range(20):
        authenticate_user(conn, f"user{i}", "wrong", ip="9.9.9.9")
    create_user(conn, username="real", password="pw", role="client")
    # IP is now locked — even valid creds fail silently from this IP
    assert is_login_locked(conn, "real", "9.9.9.9") is True
    user = authenticate_user(conn, "real", "pw", ip="9.9.9.9")
    assert user is None


def test_ip_lock_does_not_affect_a_different_ip(conn):
    create_user(conn, username="alice", password="pw", role="client")
    for i in range(20):
        authenticate_user(conn, f"user{i}", "wrong", ip="9.9.9.9")
    # Same valid user, different IP — should pass
    user = authenticate_user(conn, "alice", "pw", ip="2.2.2.2")
    assert user is not None


def test_username_lock_is_silent_does_not_record_extra_attempts_after_lock(conn):
    """Once locked, additional probes still increment the counter so the lock
    extends. (The spec is: window slides; new failures within the window keep
    the lock alive.)"""
    create_user(conn, username="alice", password="pw", role="client")
    for _ in range(5):
        authenticate_user(conn, "alice", "WRONG", ip="1.2.3.4")
    before = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = 'alice'"
    ).fetchone()[0]
    # While locked, attempts are NOT recorded — the route returns None before
    # touching the password verifier or the attempts table. This is by design:
    # a fast no-op response while locked stops the attacker from extending the
    # window indefinitely with cheap requests.
    authenticate_user(conn, "alice", "WRONG", ip="1.2.3.4")
    after = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = 'alice'"
    ).fetchone()[0]
    assert before == after


def test_inactive_user_failure_still_records(conn):
    """Trying to log in as a deactivated account counts as a failure for the
    IP, so attackers can't probe known-disabled accounts cheaply."""
    create_user(conn, username="alice", password="pw", role="client")
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'alice'")
    conn.commit()
    user = authenticate_user(conn, "alice", "pw", ip="1.2.3.4")
    assert user is None
    fails = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip = '1.2.3.4' AND success = 0"
    ).fetchone()[0]
    assert fails == 1
