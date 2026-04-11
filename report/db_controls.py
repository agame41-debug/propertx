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
    Only one active assignment per (slug, confirmation_code).
    """
    conn.execute(
        """INSERT INTO reservation_month_assignments
               (slug, confirmation_code, target_year, target_month,
                original_year, original_month, reason, actor, created_at)
           VALUES (:slug, :confirmation_code, :target_year, :target_month,
                   :original_year, :original_month, :reason, :actor, :created_at)
           ON CONFLICT(slug, confirmation_code) DO UPDATE SET
               target_year    = excluded.target_year,
               target_month   = excluded.target_month,
               original_year  = excluded.original_year,
               original_month = excluded.original_month,
               reason         = excluded.reason,
               actor          = excluded.actor,
               created_at     = excluded.created_at,
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
        },
    )
    conn.commit()


def revert_reservation_month_assignment(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    actor: str = "",
) -> None:
    """Mark an assignment as reverted (soft-delete)."""
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
) -> dict[str, dict]:
    """
    Return all active (non-reverted) month assignments for a property.
    Returns {confirmation_code: {target_year, target_month, original_year, original_month, reason}}.
    """
    rows = conn.execute(
        """SELECT confirmation_code, target_year, target_month,
                  original_year, original_month, reason, actor, created_at
             FROM reservation_month_assignments
            WHERE slug = ? AND reverted_at IS NULL""",
        (slug,),
    ).fetchall()
    return {
        row["confirmation_code"]: dict(row)
        for row in rows
    }


def get_codes_assigned_to_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> set[str]:
    """
    Return confirmation_codes that are actively assigned INTO (target) this month.
    Used by the pipeline to pull in reservations moved from other months.
    """
    rows = conn.execute(
        """SELECT confirmation_code
             FROM reservation_month_assignments
            WHERE slug = ? AND target_year = ? AND target_month = ?
              AND reverted_at IS NULL""",
        (slug, year, month),
    ).fetchall()
    return {row["confirmation_code"] for row in rows}


def get_assignment_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
) -> dict | None:
    """Return the active assignment for a single code, or None."""
    row = conn.execute(
        """SELECT * FROM reservation_month_assignments
            WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL""",
        (slug, confirmation_code),
    ).fetchone()
    return dict(row) if row else None


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
    """Re-include a previously excluded reservation."""
    conn.execute(
        """UPDATE reservation_exclusions
              SET reinstated_at = ?, reinstated_by = ?
            WHERE slug = ? AND confirmation_code = ? AND reinstated_at IS NULL""",
        (_now(), actor, slug, confirmation_code),
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
