import json
import sqlite3
from datetime import datetime, timedelta, timezone

from report.db_months import _assert_report_month_mutable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alias_valid_to_before(valid_from: str) -> str:
    text = str(valid_from or "").strip()
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed.date() - timedelta(days=1)).isoformat()
    except ValueError:
        pass
    try:
        parsed_date = datetime.strptime(text[:10], "%Y-%m-%d").date()
        return (parsed_date - timedelta(days=1)).isoformat()
    except ValueError:
        return text


def list_report_objects(
    conn: sqlite3.Connection,
    *,
    active_only: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM report_objects"
    params: list = []
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY slug"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_report_object(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM report_objects WHERE slug = ?",
        (slug,),
    ).fetchone()
    return dict(row) if row else None


def upsert_report_object(conn: sqlite3.Connection, data: dict) -> None:
    now = _now()
    existing = get_report_object(conn, data["slug"])
    created_at = (existing or {}).get("created_at") or now
    conn.execute(
        """INSERT INTO report_objects
           (slug, display_name, hostify_listing_id, listing_nickname,
            balicky_per_person, city_tax_rate, vat_rate, rentero_commission,
            client_type, active, created_at, updated_at)
           VALUES (:slug, :display_name, :hostify_listing_id, :listing_nickname,
                   :balicky_per_person, :city_tax_rate, :vat_rate, :rentero_commission,
                   :client_type, :active, :created_at, :updated_at)
           ON CONFLICT(slug) DO UPDATE SET
             display_name=excluded.display_name,
             hostify_listing_id=excluded.hostify_listing_id,
             listing_nickname=excluded.listing_nickname,
             balicky_per_person=excluded.balicky_per_person,
             city_tax_rate=excluded.city_tax_rate,
             vat_rate=excluded.vat_rate,
             rentero_commission=excluded.rentero_commission,
             client_type=excluded.client_type,
             active=excluded.active,
             updated_at=excluded.updated_at""",
        {
            "slug": data["slug"],
            "display_name": data.get("display_name", ""),
            "hostify_listing_id": data.get("hostify_listing_id"),
            "listing_nickname": data.get("listing_nickname", ""),
            "balicky_per_person": data.get("balicky_per_person", 0),
            "city_tax_rate": data.get("city_tax_rate", 0),
            "vat_rate": data.get("vat_rate", 0.21),
            "rentero_commission": data.get("rentero_commission", 0.15),
            "client_type": data.get("client_type", "rentero"),
            "active": 1 if data.get("active", True) else 0,
            "created_at": created_at,
            "updated_at": now,
        },
    )
    conn.commit()


def get_report_object_channel_configs(
    conn: sqlite3.Connection,
    slug: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM report_object_channel_config"
    params: list = []
    if slug:
        sql += " WHERE report_object_slug = ?"
        params.append(slug)
    sql += " ORDER BY report_object_slug, channel"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json") or "{}")
        result.append(item)
    return result


def save_report_object_channel_config(
    conn: sqlite3.Connection,
    slug: str,
    channel: str,
    config: dict,
) -> None:
    now = _now()
    existing = conn.execute(
        """SELECT created_at
           FROM report_object_channel_config
           WHERE report_object_slug = ? AND channel = ?""",
        (slug, channel),
    ).fetchone()
    conn.execute(
        """INSERT INTO report_object_channel_config
           (report_object_slug, channel, config_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(report_object_slug, channel) DO UPDATE SET
             config_json=excluded.config_json,
             updated_at=excluded.updated_at""",
        (
            slug,
            channel,
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            (existing["created_at"] if existing else now),
            now,
        ),
    )
    conn.commit()


def get_report_object_aliases(
    conn: sqlite3.Connection,
    slug: str | None = None,
    *,
    channel: str | None = None,
    alias_type: str | None = None,
    include_inactive: bool = True,
) -> list[dict]:
    sql = "SELECT * FROM report_object_aliases"
    clauses: list[str] = []
    params: list = []
    if slug:
        clauses.append("report_object_slug = ?")
        params.append(slug)
    if channel:
        clauses.append("channel = ?")
        params.append(channel)
    if alias_type:
        clauses.append("alias_type = ?")
        params.append(alias_type)
    if not include_inactive:
        clauses.append("is_active = 1")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY report_object_slug, channel, alias_type, id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_report_object_aliases(
    conn: sqlite3.Connection,
    slug: str,
    channel: str,
    alias_type: str,
    alias_values: list[str],
    *,
    valid_from: str | None = None,
) -> list[dict]:
    now = _now()
    effective_from = valid_from or now
    clean_values = [
        value.strip()
        for value in alias_values
        if isinstance(value, str) and value.strip()
    ]
    clean_values = list(dict.fromkeys(clean_values))

    active_rows = get_report_object_aliases(
        conn,
        slug,
        channel=channel,
        alias_type=alias_type,
        include_inactive=False,
    )
    active_values = {row["alias_value"] for row in active_rows}

    for row in active_rows:
        if row["alias_value"] not in clean_values:
            conn.execute(
                """UPDATE report_object_aliases
                   SET is_active = 0, valid_to = ?, updated_at = ?
                   WHERE id = ?""",
                (_alias_valid_to_before(effective_from), now, row["id"]),
            )

    for value in clean_values:
        if value in active_values:
            continue
        conn.execute(
            """INSERT INTO report_object_aliases
               (report_object_slug, channel, alias_type, alias_value,
                valid_from, valid_to, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, 1, ?, ?)""",
            (slug, channel, alias_type, value, effective_from, now, now),
        )

    conn.commit()
    return get_report_object_aliases(
        conn,
        slug,
        channel=channel,
        alias_type=alias_type,
        include_inactive=True,
    )


def get_client(conn: sqlite3.Connection, property_slug: str) -> dict:
    row = conn.execute(
        "SELECT * FROM clients WHERE property_slug = ?", (property_slug,)
    ).fetchone()
    if row:
        return dict(row)
    return {
        "property_slug": property_slug, "name": "", "ico": "", "dic": "",
        "platce_dph": 0, "adresa": "", "bank_account": "", "email": "",
        "phone": "", "notes": "", "updated_at": "",
    }


def save_client(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """INSERT INTO clients
           (property_slug, name, ico, dic, platce_dph, adresa,
            bank_account, email, phone, notes, updated_at)
           VALUES (:property_slug, :name, :ico, :dic, :platce_dph, :adresa,
                   :bank_account, :email, :phone, :notes, :updated_at)
           ON CONFLICT(property_slug) DO UPDATE SET
             name=excluded.name, ico=excluded.ico, dic=excluded.dic,
             platce_dph=excluded.platce_dph, adresa=excluded.adresa,
             bank_account=excluded.bank_account, email=excluded.email,
             phone=excluded.phone, notes=excluded.notes,
             updated_at=excluded.updated_at""",
        {**data, "updated_at": _now()},
    )
    conn.commit()


def get_all_clients(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM clients ORDER BY property_slug").fetchall()
    return [dict(r) for r in rows]


def get_expense_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM expense_categories ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def add_expense_category(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO expense_categories (name) VALUES (?)", (name.strip(),)
    )
    conn.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT id FROM expense_categories WHERE name = ?", (name.strip(),)
    ).fetchone()
    return int(row["id"])


def delete_expense_category(conn: sqlite3.Connection, category_id: int) -> None:
    conn.execute("DELETE FROM expense_categories WHERE id = ?", (category_id,))
    conn.commit()


def get_expenses(
    conn: sqlite3.Connection,
    property_slug: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    sql = """SELECT e.id, e.property_slug, e.year, e.month, e.date,
                    e.description, e.amount_czk, e.created_at,
                    c.name AS category_name, e.category_id
             FROM expenses e
             LEFT JOIN expense_categories c ON c.id = e.category_id"""
    conditions, params = [], []
    if property_slug:
        conditions.append("e.property_slug = ?")
        params.append(property_slug)
    if year is not None:
        conditions.append("e.year = ?")
        params.append(year)
    if month is not None:
        conditions.append("e.month = ?")
        params.append(month)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY e.year DESC, e.month DESC, e.date DESC, e.id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_expense(conn: sqlite3.Connection, expense_id: int) -> dict | None:
    row = conn.execute(
        """SELECT e.id, e.property_slug, e.year, e.month, e.date,
                  e.description, e.amount_czk, e.created_at,
                  c.name AS category_name, e.category_id
           FROM expenses e
           LEFT JOIN expense_categories c ON c.id = e.category_id
           WHERE e.id = ?""",
        (expense_id,),
    ).fetchone()
    return dict(row) if row else None


def add_expense(conn: sqlite3.Connection, data: dict) -> int:
    _assert_report_month_mutable(
        conn,
        str(data["property_slug"]),
        int(data["year"]),
        int(data["month"]),
    )
    cur = conn.execute(
        """INSERT INTO expenses
           (property_slug, year, month, date, category_id, description, amount_czk, created_at)
           VALUES (:property_slug, :year, :month, :date, :category_id, :description, :amount_czk, :created_at)""",
        {**data, "created_at": _now()},
    )
    conn.commit()
    return int(cur.lastrowid)


def update_expense(conn: sqlite3.Connection, expense_id: int, data: dict) -> None:
    _assert_report_month_mutable(
        conn,
        str(data["property_slug"]),
        int(data["year"]),
        int(data["month"]),
    )
    conn.execute(
        """UPDATE expenses SET
             property_slug=:property_slug, year=:year, month=:month,
             date=:date, category_id=:category_id,
             description=:description, amount_czk=:amount_czk
           WHERE id=:id""",
        {**data, "id": expense_id},
    )
    conn.commit()


def delete_expense(conn: sqlite3.Connection, expense_id: int) -> None:
    existing = get_expense(conn, expense_id)
    if existing:
        _assert_report_month_mutable(
            conn,
            str(existing["property_slug"]),
            int(existing["year"]),
            int(existing["month"]),
        )
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()


def get_expense_total(
    conn: sqlite3.Connection, property_slug: str, year: int, month: int
) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(amount_czk), 0) AS total
           FROM expenses WHERE property_slug=? AND year=? AND month=?""",
        (property_slug, year, month),
    ).fetchone()
    return float(row["total"])


OVERRIDE_SCOPE_RESERVATION = "reservation"

OVERRIDE_FIELD_LABELS: dict[str, str] = {
    "payout_czk": "Výplata (Kč)",
    "verification_status": "Stav verifikace",
}

_VERIFICATION_STATUS_ALIASES = {
    "CHYBÍ_CSV": "CHYBÍ_V_CSV",
    "CHYBÍ_HOSTIFY": "CHYBÍ_V_HOSTIFY",
}

VERIFICATION_STATUS_OPTIONS = [
    "MATCHED",
    "ROZDÍL",
    "KE KONTROLE",
    "CHYBÍ_V_CSV",
    "CHYBÍ_V_HOSTIFY",
]


def normalize_override_value(field: str, value) -> str:
    text = str(value or "").strip()
    if field != "verification_status":
        return text
    normalized = _VERIFICATION_STATUS_ALIASES.get(text, text)
    if normalized not in VERIFICATION_STATUS_OPTIONS:
        raise ValueError(
            "Unsupported verification_status override: "
            f"{text}. Expected one of: {', '.join(VERIFICATION_STATUS_OPTIONS)}."
        )
    return normalized


def create_override_event(conn: sqlite3.Connection, data: dict) -> dict:
    now = _now()
    field = str(data["field"])
    new_value = normalize_override_value(field, data.get("new_value") or "")
    cur = conn.execute(
        """INSERT INTO override_events
           (scope_type, scope_id, slug, year, month, field,
            old_value, new_value, reason, actor, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            data.get("scope_type", OVERRIDE_SCOPE_RESERVATION),
            str(data["scope_id"]),
            str(data["slug"]),
            int(data["year"]),
            int(data["month"]),
            field,
            str(data.get("old_value") or ""),
            new_value,
            str(data.get("reason") or ""),
            str(data.get("actor") or "admin"),
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM override_events WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


def get_override_events(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM override_events
           WHERE slug = ? AND year = ? AND month = ?
           ORDER BY created_at DESC, id DESC""",
        (slug, int(year), int(month)),
    ).fetchall()
    return [dict(r) for r in rows]


def revert_override_event(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    reverted_by: str = "admin",
) -> None:
    conn.execute(
        """UPDATE override_events
           SET is_active = 0, reverted_at = ?, reverted_by = ?
           WHERE id = ? AND is_active = 1""",
        (_now(), reverted_by, int(event_id)),
    )
    conn.commit()


def get_active_overrides_for_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict:
    rows = conn.execute(
        """SELECT scope_id, field, new_value
           FROM override_events
           WHERE slug = ? AND year = ? AND month = ?
             AND scope_type = 'reservation' AND is_active = 1
           ORDER BY id ASC""",
        (slug, int(year), int(month)),
    ).fetchall()
    result: dict = {}
    for row in rows:
        result.setdefault(row["scope_id"], {})[row["field"]] = row["new_value"]
    return result


def _recalculate_row_after_override(row: dict, overridden_fields: set[str]) -> dict:
    updated = dict(row)

    # Manual payout overrides must propagate into the net accommodation amount.
    # Other fee components stay as originally calculated unless they get their
    # own dedicated override types in the future.
    if "payout_czk" in overridden_fields:
        payout_czk = float(updated.get("payout_czk") or 0.0)
        priprava_pokoje = float(updated.get("priprava_pokoje_czk") or 0.0)
        city_tax = float(updated.get("city_tax_czk") or 0.0)
        dph_provize = float(updated.get("dph_provize_czk") or 0.0)
        dph_uklid_balicky = float(updated.get("dph_uklid_balicky_czk") or 0.0)
        updated["cena_ubytovani_czk"] = round(
            max(payout_czk - priprava_pokoje - city_tax - dph_provize - dph_uklid_balicky, 0.0),
            2,
        )

    return updated


def apply_overrides_to_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    slug: str,
    year: int,
    month: int,
) -> list[dict]:
    active = get_active_overrides_for_month(conn, slug, year, month)
    if not active:
        return rows

    result = []
    for row in rows:
        code = row.get("confirmation_code", "")
        field_overrides = active.get(code)
        if not field_overrides:
            result.append(row)
            continue
        modified = dict(row)
        overridden: dict = {}
        overridden_fields: set[str] = set()
        for field, new_val in field_overrides.items():
            old = modified.get(field)
            overridden[field] = old
            if field == "payout_czk":
                try:
                    modified[field] = float(new_val)
                    overridden_fields.add(field)
                except (ValueError, TypeError):
                    pass
            elif field == "verification_status":
                modified[field] = normalize_override_value(field, new_val)
                overridden_fields.add(field)
            else:
                modified[field] = new_val
                overridden_fields.add(field)
        modified = _recalculate_row_after_override(modified, overridden_fields)
        modified["_overridden"] = overridden
        result.append(modified)
    return result
