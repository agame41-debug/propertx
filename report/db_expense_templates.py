"""report/db_expense_templates.py — recurring expense templates → per-month rows.

A template defines a recurring expense (amount, category, VAT, period) for an object.
Materialization expands active templates into editable `expenses` rows for each month in
their period, once per (template, month), respecting manual deletes via tombstones
(expense_template_skips) and never touching LOCKED months. See
docs/superpowers/specs/2026-05-25-object-profiles-and-recurring-expenses-design.md
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ym(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def create_expense_template(conn: sqlite3.Connection, data: dict) -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO expense_templates
           (property_slug, category_id, description, amount_czk, amount_net_czk,
            amount_dph_czk, vat_rate, start_ym, end_ym, source, active, created_at, updated_at)
           VALUES (:property_slug, :category_id, :description, :amount_czk, :amount_net_czk,
                   :amount_dph_czk, :vat_rate, :start_ym, :end_ym, :source, 1, :created_at, :updated_at)""",
        {
            "property_slug": data["property_slug"],
            "category_id": data.get("category_id"),
            "description": data["description"],
            "amount_czk": data.get("amount_czk") or 0,
            "amount_net_czk": data.get("amount_net_czk"),
            "amount_dph_czk": data.get("amount_dph_czk"),
            "vat_rate": data.get("vat_rate"),
            "start_ym": data["start_ym"],
            "end_ym": data.get("end_ym"),
            "source": data.get("source", "ui"),
            "created_at": now,
            "updated_at": now,
        },
    )
    conn.commit()
    return int(cur.lastrowid)


def get_expense_template(conn: sqlite3.Connection, template_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM expense_templates WHERE id = ?", (template_id,)).fetchone()
    return dict(row) if row else None


def list_expense_templates(conn: sqlite3.Connection, property_slug: str, *, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM expense_templates WHERE property_slug = ?"
    if active_only:
        sql += " AND active = 1"
    sql += " ORDER BY start_ym, id"
    return [dict(r) for r in conn.execute(sql, (property_slug,)).fetchall()]


_UPDATABLE = ("category_id", "description", "amount_czk", "amount_net_czk",
              "amount_dph_czk", "vat_rate", "start_ym", "end_ym", "active")


def update_expense_template(conn: sqlite3.Connection, template_id: int, data: dict) -> None:
    sets = [f"{f} = ?" for f in _UPDATABLE if f in data]
    if not sets:
        return
    params = [data[f] for f in _UPDATABLE if f in data]
    params.append(_now())
    params.append(template_id)
    conn.execute(
        f"UPDATE expense_templates SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
        params,
    )
    conn.commit()


def delete_expense_template(conn: sqlite3.Connection, template_id: int) -> None:
    """Hard-delete the template. Already-materialized expense rows are left in place
    (their template_id becomes dangling but harmless); future months stop generating."""
    conn.execute("DELETE FROM expense_template_skips WHERE template_id = ?", (template_id,))
    conn.execute("DELETE FROM expense_templates WHERE id = ?", (template_id,))
    conn.commit()


def upsert_tsv_template(conn: sqlite3.Connection, property_slug: str, source: str, data: dict) -> int:
    """Create or update the single template identified by (property_slug, source).
    Used for TSV-derived recurring expenses (e.g. source='tsv:internet')."""
    existing = conn.execute(
        "SELECT id FROM expense_templates WHERE property_slug = ? AND source = ?",
        (property_slug, source),
    ).fetchone()
    if existing:
        update_expense_template(conn, int(existing["id"]), {**data, "active": 1})
        return int(existing["id"])
    return create_expense_template(conn, {**data, "property_slug": property_slug, "source": source})


def add_template_skip(conn: sqlite3.Connection, template_id: int, year: int, month: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO expense_template_skips (template_id, year, month) VALUES (?,?,?)",
        (template_id, int(year), int(month)),
    )
    conn.commit()


def materialize_templates_for_month(conn: sqlite3.Connection, property_slug: str, year: int, month: int) -> int:
    """Ensure an expense row exists for each active template covering (year, month).
    Idempotent; respects tombstones; never writes into LOCKED months."""
    st = conn.execute(
        "SELECT status FROM report_month_state WHERE slug=? AND year=? AND month=?",
        (property_slug, year, month),
    ).fetchone()
    if st and str(st["status"]) == "LOCKED":
        return 0

    m = _ym(year, month)
    templates = conn.execute(
        """SELECT * FROM expense_templates
           WHERE property_slug = ? AND active = 1
             AND start_ym <= ? AND (end_ym IS NULL OR end_ym >= ?)""",
        (property_slug, m, m),
    ).fetchall()

    created = 0
    for t in templates:
        tid = t["id"]
        if conn.execute(
            "SELECT 1 FROM expense_template_skips WHERE template_id=? AND year=? AND month=?",
            (tid, year, month),
        ).fetchone():
            continue
        if conn.execute(
            "SELECT 1 FROM expenses WHERE property_slug=? AND year=? AND month=? AND template_id=?",
            (property_slug, year, month, tid),
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO expenses
               (property_slug, year, month, date, category_id, description,
                amount_czk, amount_net_czk, amount_dph_czk, vat_rate, template_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (property_slug, year, month, None, t["category_id"], t["description"],
             t["amount_czk"], t["amount_net_czk"], t["amount_dph_czk"], t["vat_rate"], tid, _now()),
        )
        created += 1
    conn.commit()
    return created
