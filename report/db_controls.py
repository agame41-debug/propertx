"""
report/db_controls.py — DB functions for reservation move and exclude controls.

Two tables managed here:
  reservation_month_assignments — manual month reassignments (MOVE action)
  reservation_exclusions        — soft-exclusions from financial calculation
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Month assignments ──────────────────────────────────────────────────────

def create_reservation_month_assignment(
    conn: sqlite3.Connection,
    data: dict,
) -> None:
    """
    Create or replace a month assignment for a reservation.

    With month-scoped uniqueness, one code can have separate assignments for
    different source months (e.g. main reservation in Feb, adjustment in Mar).
    UNIQUE(slug, confirmation_code, original_year, original_month).
    """
    conn.execute(
        """INSERT INTO reservation_month_assignments
               (slug, confirmation_code, target_year, target_month,
                original_year, original_month, reason, actor, created_at,
                is_adjustment, batch_ref)
           VALUES (:slug, :confirmation_code, :target_year, :target_month,
                   :original_year, :original_month, :reason, :actor, :created_at,
                   :is_adjustment, :batch_ref)
           ON CONFLICT(slug, confirmation_code, original_year, original_month) DO UPDATE SET
               target_year    = excluded.target_year,
               target_month   = excluded.target_month,
               reason         = excluded.reason,
               actor          = excluded.actor,
               created_at     = excluded.created_at,
               is_adjustment  = excluded.is_adjustment,
               batch_ref      = excluded.batch_ref,
               reverted_at    = NULL,
               reverted_by    = NULL""",
        {
            "slug": data["slug"],
            "confirmation_code": data["confirmation_code"],
            "target_year": int(data["target_year"]),
            "target_month": int(data["target_month"]),
            "original_year": int(data["original_year"]),
            "original_month": int(data["original_month"]),
            "reason": str(data.get("reason") or "").strip(),
            "actor": str(data.get("actor") or "").strip(),
            "created_at": _now(),
            "is_adjustment": 1 if data.get("is_adjustment") else 0,
            "batch_ref": str(data.get("batch_ref") or "").strip(),
        },
    )
    conn.commit()


def revert_reservation_month_assignment(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    original_year: int | None = None,
    original_month: int | None = None,
    actor: str = "",
) -> None:
    """Mark an assignment as reverted (soft-delete).

    If original_year/month are given, reverts only that specific assignment.
    Otherwise reverts ALL active assignments for the code (backward compat).
    """
    if original_year is not None and original_month is not None:
        conn.execute(
            """UPDATE reservation_month_assignments
                  SET reverted_at = ?, reverted_by = ?
                WHERE slug = ? AND confirmation_code = ?
                  AND original_year = ? AND original_month = ?
                  AND reverted_at IS NULL""",
            (_now(), actor, slug, confirmation_code,
             original_year, original_month),
        )
    else:
        conn.execute(
            """UPDATE reservation_month_assignments
                  SET reverted_at = ?, reverted_by = ?
                WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL""",
            (_now(), actor, slug, confirmation_code),
        )
    conn.commit()


def get_reservation_month_assignments(
    conn: sqlite3.Connection,
    slug: str,
) -> list[dict]:
    """
    Return all active (non-reverted) month assignments for a property.
    Returns a flat list of assignment dicts.
    """
    rows = conn.execute(
        """SELECT confirmation_code, target_year, target_month,
                  original_year, original_month, reason, actor, created_at,
                  is_adjustment, batch_ref
             FROM reservation_month_assignments
            WHERE slug = ? AND reverted_at IS NULL""",
        (slug,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_codes_assigned_to_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> list[dict]:
    """
    Return assignments that are actively assigned INTO (target) this month.
    Used by the pipeline to pull in reservations moved from other months.
    Returns list of assignment dicts (with is_adjustment, batch_ref, etc.).
    """
    rows = conn.execute(
        """SELECT confirmation_code, original_year, original_month,
                  is_adjustment, batch_ref
             FROM reservation_month_assignments
            WHERE slug = ? AND target_year = ? AND target_month = ?
              AND reverted_at IS NULL""",
        (slug, year, month),
    ).fetchall()
    return [dict(row) for row in rows]


def get_assignment_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    original_year: int | None = None,
    original_month: int | None = None,
) -> dict | None:
    """Return the active assignment for a single code, optionally scoped to month."""
    if original_year is not None and original_month is not None:
        row = conn.execute(
            """SELECT * FROM reservation_month_assignments
                WHERE slug = ? AND confirmation_code = ?
                  AND original_year = ? AND original_month = ?
                  AND reverted_at IS NULL""",
            (slug, confirmation_code, original_year, original_month),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM reservation_month_assignments
                WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL
                ORDER BY created_at DESC LIMIT 1""",
            (slug, confirmation_code),
        ).fetchone()
    return dict(row) if row else None


def get_all_assignments_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
) -> list[dict]:
    """Return ALL active assignments for a code (may be multiple months)."""
    rows = conn.execute(
        """SELECT * FROM reservation_month_assignments
            WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL
            ORDER BY original_year, original_month""",
        (slug, confirmation_code),
    ).fetchall()
    return [dict(row) for row in rows]


# ── Exclusions ─────────────────────────────────────────────────────────────

def create_reservation_exclusion(
    conn: sqlite3.Connection,
    data: dict,
) -> None:
    """
    Exclude a reservation from financial calculations.
    Idempotent: re-excluding an already-excluded reservation resets reinstated_at.
    """
    conn.execute(
        """INSERT INTO reservation_exclusions
               (slug, confirmation_code, reason, actor, excluded_at)
           VALUES (:slug, :confirmation_code, :reason, :actor, :excluded_at)
           ON CONFLICT(slug, confirmation_code) DO UPDATE SET
               reason        = excluded.reason,
               actor         = excluded.actor,
               excluded_at   = excluded.excluded_at,
               reinstated_at = NULL,
               reinstated_by = NULL""",
        {
            "slug": data["slug"],
            "confirmation_code": data["confirmation_code"],
            "reason": str(data.get("reason") or "").strip(),
            "actor": str(data.get("actor") or "").strip(),
            "excluded_at": _now(),
        },
    )
    conn.commit()


def reinstate_reservation(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    actor: str = "",
) -> None:
    """Re-include a previously excluded reservation.

    If no exclusion record exists (e.g. AirCover with hardcoded
    is_excluded), create one already marked as reinstated so that
    get_reinstated_codes() picks it up during generation.
    """
    now = _now()
    updated = conn.execute(
        """UPDATE reservation_exclusions
              SET reinstated_at = ?, reinstated_by = ?
            WHERE slug = ? AND confirmation_code = ? AND reinstated_at IS NULL""",
        (now, actor, slug, confirmation_code),
    ).rowcount
    if updated == 0:
        # No active exclusion record — create one pre-reinstated
        conn.execute(
            """INSERT OR IGNORE INTO reservation_exclusions
                   (slug, confirmation_code, reason, actor, excluded_at, reinstated_at, reinstated_by)
               VALUES (?, ?, 'auto-excluded', ?, ?, ?, ?)""",
            (slug, confirmation_code, actor, now, now, actor),
        )
    conn.commit()


def get_active_exclusions(
    conn: sqlite3.Connection,
    slug: str,
) -> set[str]:
    """Return confirmation_codes that are currently excluded (not reinstated)."""
    rows = conn.execute(
        """SELECT confirmation_code FROM reservation_exclusions
            WHERE slug = ? AND reinstated_at IS NULL""",
        (slug,),
    ).fetchall()
    return {row["confirmation_code"] for row in rows}


def get_reinstated_codes(
    conn: sqlite3.Connection,
    slug: str,
) -> set[str]:
    """Return confirmation_codes that were explicitly reinstated by user."""
    rows = conn.execute(
        """SELECT confirmation_code FROM reservation_exclusions
            WHERE slug = ? AND reinstated_at IS NOT NULL""",
        (slug,),
    ).fetchall()
    return {row["confirmation_code"] for row in rows}


def get_exclusion_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
) -> dict | None:
    """Return the active exclusion record for a single code, or None."""
    row = conn.execute(
        """SELECT * FROM reservation_exclusions
            WHERE slug = ? AND confirmation_code = ? AND reinstated_at IS NULL""",
        (slug, confirmation_code),
    ).fetchone()
    return dict(row) if row else None
