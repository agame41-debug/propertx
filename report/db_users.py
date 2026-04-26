"""User management database functions for RBAC."""

import hashlib
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_CLIENT = "client"
VALID_ROLES = {ROLE_ADMIN, ROLE_MANAGER, ROLE_CLIENT}

# OWASP-recommended Argon2id defaults from argon2-cffi.
_HASHER = PasswordHasher()

# Login throttle parameters (OWASP defaults).
_THROTTLE_WINDOW_MINUTES = 15
_MAX_USERNAME_FAILURES = 5
_MAX_IP_FAILURES = 20

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _throttle_cutoff() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=_THROTTLE_WINDOW_MINUTES)
    ).isoformat()


def _is_argon2(password_hash: str) -> bool:
    return password_hash.startswith("$argon2")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (encoded_argon2_string, "").

    Argon2's encoded form self-contains its salt and parameters, so the
    legacy `password_salt` column is unused for new hashes. The `salt`
    parameter is kept only for signature compatibility with old callers.
    """
    del salt
    return _HASHER.hash(password), ""


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    """Verify a password against either an Argon2 or legacy SHA-256 hash."""
    if _is_argon2(password_hash):
        try:
            _HASHER.verify(password_hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
    h = hashlib.sha256((password_salt + password).encode()).hexdigest()
    return secrets.compare_digest(h, password_hash)


def is_login_locked(
    conn: sqlite3.Connection, username: str, ip: str = ""
) -> bool:
    """Return True if username or IP has too many recent failed attempts."""
    cutoff = _throttle_cutoff()
    if username:
        username_fails = conn.execute(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE username = ? AND success = 0 AND attempted_at > ?",
            (username, cutoff),
        ).fetchone()[0]
        if username_fails >= _MAX_USERNAME_FAILURES:
            return True
    if ip:
        ip_fails = conn.execute(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE ip = ? AND success = 0 AND attempted_at > ?",
            (ip, cutoff),
        ).fetchone()[0]
        if ip_fails >= _MAX_IP_FAILURES:
            return True
    return False


def _record_login_attempt(
    conn: sqlite3.Connection, username: str, ip: str, success: bool
) -> None:
    conn.execute(
        "INSERT INTO login_attempts (username, ip, attempted_at, success) "
        "VALUES (?, ?, ?, ?)",
        (username, ip, _now(), 1 if success else 0),
    )
    if success and username:
        conn.execute(
            "DELETE FROM login_attempts WHERE username = ? AND success = 0",
            (username,),
        )
    conn.commit()


def authenticate_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    ip: str = "",
) -> dict | None:
    """Return user dict if credentials valid and user active, else None.

    Throttled: returns None silently when the username has 5+ failed attempts
    or the IP has 20+ failed attempts within the last 15 minutes — even if
    the password is correct. The throttle resets for a username on its next
    successful login. Successful login of a legacy SHA-256 user also
    transparently rehashes the password with Argon2.
    """
    if is_login_locked(conn, username, ip):
        log.warning(
            "login throttled: username=%r ip=%r exceeded recent-failure threshold",
            username, ip,
        )
        return None

    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1",
        (username,),
    ).fetchone()
    user = dict(row) if row else None
    success = bool(
        user
        and verify_password(password, user["password_hash"], user["password_salt"])
    )

    _record_login_attempt(conn, username, ip, success)

    if not success:
        return None
    if not _is_argon2(user["password_hash"]):
        new_hash, new_salt = hash_password(password)
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?",
            (new_hash, new_salt, _now(), user["id"]),
        )
        conn.commit()
        user["password_hash"] = new_hash
        user["password_salt"] = new_salt
    return user


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str = ROLE_CLIENT,
    display_name: str = "",
) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    pw_hash, pw_salt = hash_password(password)
    now = _now()
    cur = conn.execute(
        """INSERT INTO users
           (username, password_hash, password_salt, role, display_name, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (username, pw_hash, pw_salt, role, display_name, now, now),
    )
    conn.commit()
    return get_user_by_id(conn, cur.lastrowid)


def update_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> None:
    sets, params = [], []
    if display_name is not None:
        sets.append("display_name = ?")
        params.append(display_name)
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role}")
        sets.append("role = ?")
        params.append(role)
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if is_active else 0)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(user_id)
    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def change_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    pw_hash, pw_salt = hash_password(new_password)
    conn.execute(
        "UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?",
        (pw_hash, pw_salt, _now(), user_id),
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM user_properties WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


# ── Property assignments ──────────────────────────────────────────────────


def get_user_property_slugs(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT property_slug FROM user_properties WHERE user_id = ? ORDER BY property_slug",
        (user_id,),
    ).fetchall()
    return [r["property_slug"] for r in rows]


def set_user_properties(conn: sqlite3.Connection, user_id: int, property_slugs: list[str]) -> None:
    """Replace all property assignments for user."""
    now = _now()
    conn.execute("DELETE FROM user_properties WHERE user_id = ?", (user_id,))
    for slug in property_slugs:
        conn.execute(
            "INSERT INTO user_properties (user_id, property_slug, created_at) VALUES (?, ?, ?)",
            (user_id, slug, now),
        )
    conn.commit()


def get_users_for_property(conn: sqlite3.Connection, property_slug: str) -> list[dict]:
    rows = conn.execute(
        """SELECT u.* FROM users u
           JOIN user_properties up ON u.id = up.user_id
           WHERE up.property_slug = ?
           ORDER BY u.username""",
        (property_slug,),
    ).fetchall()
    return [dict(r) for r in rows]
