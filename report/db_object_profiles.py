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
    "client_type": "rentero", "city_tax_rate": 0, "balicky_per_person": 0,
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
) -> int:
    now = _now()
    merged = {**_DEFAULTS, **{k: v for k, v in fields.items() if k in PROFILE_FIELDS}}
    cols = ["slug", "valid_from_ym", "valid_to_ym", *PROFILE_FIELDS, "source", "created_at", "updated_at"]
    vals = [slug, valid_from_ym, valid_to_ym, *[merged[f] for f in PROFILE_FIELDS], source, now, now]
    placeholders = ",".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO report_object_profiles ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
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
