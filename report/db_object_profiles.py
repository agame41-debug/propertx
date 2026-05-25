"""report/db_object_profiles.py — month-versioned object profile segments.

Effective-dated, non-overlapping monthly segments per report object. Each report
month resolves to exactly one segment. Holds owner identity + business state
(client_type, rates, active, středisko) that change over time. See
docs/superpowers/specs/2026-05-25-object-profiles-and-recurring-expenses-design.md
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# Editable profile fields, in insert order. valid_from_ym/valid_to_ym/slug/source
# are handled separately.
PROFILE_FIELDS = (
    "owner_name", "ico", "dic", "platce_dph", "adresa", "bank_account",
    "email", "phone", "notes",
    "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
    "rentero_commission", "stredisko", "active",
)

_DEFAULTS = {
    "owner_name": "", "ico": "", "dic": "", "platce_dph": 0, "adresa": "",
    "bank_account": "", "email": "", "phone": "", "notes": "",
    "client_type": "rentero", "city_tax_rate": 0, "balicky_per_person": 249,
    "vat_rate": 0.21, "rentero_commission": 0.15, "stredisko": "", "active": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ym(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def _ym_add(ym_str: str, delta: int) -> str:
    y, m = int(ym_str[:4]), int(ym_str[5:7])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def prev_ym(ym_str: str) -> str:
    return _ym_add(ym_str, -1)


def next_ym(ym_str: str) -> str:
    return _ym_add(ym_str, 1)


def insert_segment(
    conn: sqlite3.Connection,
    slug: str,
    valid_from_ym: str | None,
    valid_to_ym: str | None,
    fields: dict,
    *,
    source: str = "ui",
    commit: bool = True,
) -> int:
    now = _now()
    # Coalesce present-but-None values to defaults: every profile column is NOT NULL,
    # so a None from a nullable legacy report_objects row (backfill) or a sparse
    # caller must fall back to _DEFAULTS rather than violate the constraint.
    merged = {**_DEFAULTS, **{k: v for k, v in fields.items() if k in PROFILE_FIELDS and v is not None}}
    cols = ["slug", "valid_from_ym", "valid_to_ym", *PROFILE_FIELDS, "source", "created_at", "updated_at"]
    vals = [slug, valid_from_ym, valid_to_ym, *[merged[f] for f in PROFILE_FIELDS], source, now, now]
    placeholders = ",".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO report_object_profiles ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def get_object_profile(conn: sqlite3.Connection, slug: str, year: int, month: int) -> dict | None:
    m = ym(year, month)
    row = conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
             AND (valid_from_ym IS NULL OR valid_from_ym <= ?)
             AND (valid_to_ym IS NULL OR valid_to_ym >= ?)
           ORDER BY valid_from_ym DESC
           LIMIT 1""",
        (slug, m, m),
    ).fetchone()
    return dict(row) if row else None


def list_object_profile_segments(conn: sqlite3.Connection, slug: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
           ORDER BY (valid_from_ym IS NOT NULL), valid_from_ym""",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def _covering_segment_row(conn, slug, m):
    return conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
             AND (valid_from_ym IS NULL OR valid_from_ym <= ?)
             AND (valid_to_ym IS NULL OR valid_to_ym >= ?)
           ORDER BY valid_from_ym DESC LIMIT 1""",
        (slug, m, m),
    ).fetchone()


def _next_segment_start_after(conn, slug, m):
    row = conn.execute(
        """SELECT MIN(valid_from_ym) AS b FROM report_object_profiles
           WHERE slug = ? AND valid_from_ym IS NOT NULL AND valid_from_ym > ?""",
        (slug, m),
    ).fetchone()
    return row["b"] if row else None


def _merged_fields(base_row: dict | None, changes: dict) -> dict:
    base = {f: (base_row[f] if base_row is not None else _DEFAULTS[f]) for f in PROFILE_FIELDS}
    base.update({k: v for k, v in changes.items() if k in PROFILE_FIELDS})
    return base


def set_profile_from_month_onward(conn, slug: str, year: int, month: int, changes: dict, *, source: str = "ui", commit: bool = True) -> int:
    m = ym(year, month)
    covering = _covering_segment_row(conn, slug, m)
    cov = dict(covering) if covering else None
    next_start = _next_segment_start_after(conn, slug, m)
    new_to = prev_ym(next_start) if next_start else None

    if cov is not None:
        if cov["valid_from_ym"] is not None and cov["valid_from_ym"] == m:
            # covering segment starts exactly at M → it gets replaced
            conn.execute("DELETE FROM report_object_profiles WHERE id = ?", (cov["id"],))
        elif cov["valid_from_ym"] is None or cov["valid_from_ym"] < m:
            # trim covering to end at M-1
            conn.execute(
                "UPDATE report_object_profiles SET valid_to_ym = ?, updated_at = ? WHERE id = ?",
                (prev_ym(m), _now(), cov["id"]),
            )
    return insert_segment(conn, slug, m, new_to, _merged_fields(cov, changes), source=source, commit=commit)


def set_profile_this_month_only(conn, slug: str, year: int, month: int, changes: dict, *, source: str = "ui", commit: bool = True) -> int:
    m = ym(year, month)
    covering = _covering_segment_row(conn, slug, m)
    cov = dict(covering) if covering else None

    # Single-month covering segment already at M → edit in place.
    if cov is not None and cov["valid_from_ym"] == m and cov["valid_to_ym"] == m:
        update_profile_segment(conn, cov["id"], changes, commit=commit)
        return cov["id"]

    orig_from = cov["valid_from_ym"] if cov else None
    orig_to = cov["valid_to_ym"] if cov else None

    if cov is not None:
        if orig_from is None or orig_from < m:
            # left part [orig_from, M-1]
            conn.execute(
                "UPDATE report_object_profiles SET valid_to_ym = ?, updated_at = ? WHERE id = ?",
                (prev_ym(m), _now(), cov["id"]),
            )
        else:
            # covering starts at M (and extends beyond) → no left part
            conn.execute("DELETE FROM report_object_profiles WHERE id = ?", (cov["id"],))
        # right part [M+1, orig_to] with ORIGINAL values
        if orig_to is None or orig_to > m:
            insert_segment(conn, slug, next_ym(m), orig_to, _merged_fields(cov, {}), source=cov["source"], commit=False)

    return insert_segment(conn, slug, m, m, _merged_fields(cov, changes), source=source, commit=commit)


def backfill_object_profiles(conn: sqlite3.Connection) -> int:
    """Create one open segment ([NULL, NULL]) per report_object that has no
    profile segment yet, merging legacy report_objects + clients values.
    Idempotent: skips objects that already have any segment."""
    inserted = 0
    objs = conn.execute("SELECT * FROM report_objects").fetchall()
    for o in objs:
        slug = o["slug"]
        if conn.execute(
            "SELECT 1 FROM report_object_profiles WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone():
            continue
        c = conn.execute(
            "SELECT * FROM clients WHERE property_slug = ?", (slug,)
        ).fetchone()
        c = dict(c) if c else {}
        fields = {
            "owner_name": c.get("name", ""),
            "ico": c.get("ico", ""),
            "dic": c.get("dic", ""),
            "platce_dph": c.get("platce_dph", 0) or 0,
            "adresa": c.get("adresa", ""),
            "bank_account": c.get("bank_account", ""),
            "email": c.get("email", ""),
            "phone": c.get("phone", ""),
            "notes": c.get("notes", ""),
            "client_type": o["client_type"],
            "city_tax_rate": o["city_tax_rate"],
            "balicky_per_person": o["balicky_per_person"],
            "vat_rate": o["vat_rate"],
            "rentero_commission": o["rentero_commission"],
            "stredisko": "",
            "active": o["active"],
        }
        insert_segment(conn, slug, None, None, fields, source="migration")
        inserted += 1
    return inserted


def update_profile_segment(conn, segment_id: int, changes: dict, *, commit: bool = True) -> None:
    sets = [f"{f} = ?" for f in PROFILE_FIELDS if f in changes]
    if not sets:
        return
    params = [changes[f] for f in PROFILE_FIELDS if f in changes]
    params.append(_now())
    params.append(segment_id)
    conn.execute(
        f"UPDATE report_object_profiles SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
        params,
    )
    if commit:
        conn.commit()
