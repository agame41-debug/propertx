import inspect
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from report.bank import _normalize_booking_ref
from report.config import (
    get_airbnb_listing_names,
    get_all_properties,
    get_booking_config,
    get_hostify_listing_names,
)
from report.db import (
    BULK_GENERATION_PENDING,
    BULK_GENERATION_RUNNING,
    MONTH_DATA_STATE_EMPTY,
    MONTH_STATUS_LOCKED,
    OVERRIDE_FIELD_LABELS,
    VERIFICATION_STATUS_OPTIONS,
    create_report_month_notification,
    get_active_bulk_generation_run,
    get_all_clients,
    get_bulk_generation_run,
    get_client,
    get_expense_categories,
    get_expenses,
    get_hostify_reservation_counts,
    get_latest_bulk_generation_run,
    get_latest_report_generation_job,
    get_override_events,
    get_report_history,
    get_report_month_state,
    get_report_month_states,
    get_report_rows,
    list_report_month_notifications,
)
from report.status import count_effective_verification_statuses, effective_verification_status

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_BASE_DIR, "config", "properties.json")


def _set_flash(request: Request, level: str, message: str, detail: str = "") -> None:
    request.session["_flash"] = {
        "level": level,
        "message": message,
        "detail": detail,
    }


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("_flash", None)


def _summarize_generation_error(detail: str) -> str:
    lines = [line.strip() for line in str(detail or "").splitlines() if line.strip()]
    if not lines:
        return "Generování reportu selhalo."
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line
    return lines[-1]


def _truncate_generation_detail(detail: str, max_chars: int = 40000) -> str:
    text = str(detail or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n... output truncated"


def _format_import_summary(summary: dict) -> tuple[str, str]:
    source_label = _source_type_label(summary.get("source_type", ""))
    source_name = summary.get("source_name") or "uploaded file"
    if summary.get("is_duplicate"):
        duplicate_id = summary.get("duplicate_of_source_file_id")
        detail = f"Soubor: {source_name}"
        if duplicate_id:
            detail += f"\nDuplicitní archivní záznam: #{duplicate_id}"
        return (
            f"Soubor '{source_name}' už v archivu existuje. Duplicitní import nebyl uložen.",
            detail,
        )

    message = summary.get("message") or f"Soubor '{source_name}' byl importován."
    lines = [
        f"Soubor: {source_name}",
        f"Typ: {source_label}",
        f"Detekováno řádků: {int(summary.get('detected_rows_count') or 0)}",
    ]
    if summary.get("new_rows_count") is not None:
        lines.append(f"Nové řádky: {int(summary.get('new_rows_count') or 0)}")
    if summary.get("new_transactions_count"):
        lines.append(f"Nové transakce: {int(summary.get('new_transactions_count') or 0)}")
    if summary.get("new_reservations_count"):
        lines.append(f"Nové rezervace: {int(summary.get('new_reservations_count') or 0)}")
    if summary.get("changed_groups_count"):
        lines.append(f"Změněné skupiny: {int(summary.get('changed_groups_count') or 0)}")
    if summary.get("affected_months"):
        formatted = ", ".join(f"{month:02d}/{year}" for year, month in summary["affected_months"])
        lines.append(f"Dotčené měsíce: {formatted}")
    return message, "\n".join(lines)


def _property_listing_names(prop: dict, *, year: int | None = None, month: int | None = None) -> list[str]:
    names = []
    names.extend(get_hostify_listing_names(prop, year=year, month=month))
    booking_nickname = (get_booking_config(prop, year=year, month=month).get("listing_nickname") or "").strip()
    if booking_nickname:
        names.append(booking_nickname)
    return [name for name in dict.fromkeys(n.strip() for n in names if n and n.strip())]


def _get_active_properties(config: dict) -> list[dict]:
    try:
        signature = inspect.signature(get_all_properties)
    except (TypeError, ValueError):
        signature = None
    if signature and "active_only" in signature.parameters:
        return get_all_properties(config, active_only=True)
    return get_all_properties(config)


def _source_type_label(source_type: str) -> str:
    labels = {
        "airbnb": "Airbnb",
        "booking": "Booking",
        "bank": "Banka",
        "accounting": "Účetnictví",
        "checkin": "Checkin report",
    }
    return labels.get(str(source_type or "").strip().lower(), str(source_type or ""))


def _inventory_redirect_path(path: str | None, *, default: str = "/inventory") -> str:
    target = str(path or "").strip()
    if not target.startswith("/"):
        return default
    return target


def _format_inventory_sync_summary(summary: dict) -> tuple[str, str]:
    created = int(summary.get("created") or 0)
    updated = int(summary.get("updated") or 0)
    drafts = int(summary.get("draft_created") or 0)
    activated = int(summary.get("activated_new") or 0)
    message = (
        f"Hostify inventory synchronizováno. "
        f"Nové: {created}, aktualizované: {updated}, drafty: {drafts}, aktivované: {activated}."
    )
    detail = (
        f"Parent listings: {int(summary.get('parents_total') or 0)}\n"
        f"Child listings: {int(summary.get('children_total') or 0)}"
    )
    return message, detail


def _format_bulk_generation_month(year: int | None, month: int | None) -> str:
    try:
        return f"{int(month):02d}/{int(year)}"
    except (TypeError, ValueError):
        return "--/----"


def _decorate_bulk_generation_run(run: dict | None) -> dict | None:
    if not run:
        return None
    item = dict(run)
    status = str(item.get("status") or "").upper()
    total = int(item.get("total_objects") or 0)
    processed = int(item.get("processed_objects") or 0)
    succeeded = int(item.get("succeeded_objects") or 0)
    failed = int(item.get("failed_objects") or 0)
    skipped_locked = int(item.get("skipped_locked_objects") or 0)
    skipped_no_data = int(item.get("skipped_no_data_objects") or 0)
    skipped_running = int(item.get("skipped_running_objects") or 0)
    active = status in {BULK_GENERATION_PENDING, BULK_GENERATION_RUNNING}
    status_map = {
        BULK_GENERATION_PENDING: ("Ve frontě", "badge-amber"),
        BULK_GENERATION_RUNNING: ("Běží", "badge-blue"),
        "SUCCEEDED": ("Hotovo", "badge-green"),
        "FAILED": ("Dokončeno s chybami", "badge-red"),
    }
    item["month_label"] = _format_bulk_generation_month(item.get("year"), item.get("month"))
    item["status_label"], item["status_badge_class"] = status_map.get(status, (status or "—", "badge-neutral"))
    item["is_active"] = active
    item["progress_label"] = f"{processed}/{total}" if total else "0/0"
    item["summary_line"] = (
        f"OK {succeeded} · chyby {failed} · zamčeno {skipped_locked} · bez dat {skipped_no_data} · obsazeno {skipped_running}"
    )
    return item


def _build_inventory_view(conn, config: dict, *, status_filter: str = "") -> tuple[dict, list[dict]]:
    properties = sorted(
        get_all_properties(config),
        key=lambda prop: (
            0 if not bool(prop.get("active", True)) else 1,
            str(prop.get("display_name") or prop.get("listing_nickname") or prop.get("slug") or "").lower(),
        ),
    )
    client_map = {row["property_slug"]: row for row in get_all_clients(conn)}
    rows: list[dict] = []
    summary = {
        "total": len(properties),
        "active_count": 0,
        "draft_count": 0,
        "missing_client_count": 0,
        "missing_mapping_count": 0,
        "review_needed_count": 0,
    }

    normalized_filter = str(status_filter or "").strip().lower()
    for prop in properties:
        slug = prop["slug"]
        active = bool(prop.get("active", True))
        client = client_map.get(slug) or {}
        booking_cfg = get_booking_config(prop)
        hostify_names = [name.strip() for name in get_hostify_listing_names(prop) if str(name).strip()]
        hostify_primary = str(prop.get("listing_nickname") or "").strip()
        hostify_extra_names = [name for name in hostify_names if name != hostify_primary]
        airbnb_names = [name.strip() for name in get_airbnb_listing_names(prop) if str(name).strip()]

        issues: list[str] = []
        if not client.get("name"):
            issues.append("Chybí klient")
        if not prop.get("listing_id"):
            issues.append("Chybí Hostify listing ID")
        if not str(prop.get("listing_nickname") or "").strip():
            issues.append("Chybí Hostify nickname")

        if active:
            summary["active_count"] += 1
        else:
            summary["draft_count"] += 1
        if not client.get("name"):
            summary["missing_client_count"] += 1
        if (
            not hostify_extra_names
            and not str(booking_cfg.get("property_id") or "").strip()
            and not str(booking_cfg.get("listing_nickname") or "").strip()
            and not airbnb_names
        ):
            summary["missing_mapping_count"] += 1
        if (not active) or issues:
            summary["review_needed_count"] += 1

        if normalized_filter == "draft" and active:
            continue
        if normalized_filter == "active" and not active:
            continue

        rows.append(
            {
                "slug": slug,
                "display_name": prop.get("display_name") or prop.get("listing_nickname") or slug,
                "listing_id": prop.get("listing_id"),
                "listing_nickname": prop.get("listing_nickname") or "",
                "active": active,
                "client_name": client.get("name") or "",
                "hostify_listing_names": hostify_extra_names,
                "booking_property_id": str(booking_cfg.get("property_id") or "").strip(),
                "booking_listing_nickname": str(booking_cfg.get("listing_nickname") or "").strip(),
                "airbnb_listing_names": airbnb_names,
                "issue_labels": issues,
                "needs_review": (not active) or bool(issues),
            }
        )

    return summary, rows


def _resolve_inventory_bulk_run(conn, *, bulk_run_id: int | None = None) -> dict | None:
    if bulk_run_id:
        run = get_bulk_generation_run(conn, int(bulk_run_id))
        if run:
            return _decorate_bulk_generation_run(run)
    active = get_active_bulk_generation_run(conn)
    if active:
        return _decorate_bulk_generation_run(active)
    return _decorate_bulk_generation_run(get_latest_bulk_generation_run(conn))


def _display_verification_status(row: dict) -> tuple[str, str]:
    return effective_verification_status(row)


def _import_change_lines(summary: dict) -> list[str]:
    source_type = str(summary.get("source_type") or "").strip().lower()
    lines: list[str] = []
    if source_type == "airbnb":
        lines.append(
            f"Airbnb: +{int(summary.get('new_reservations_count') or 0)} rezervací, "
            f"+{int(summary.get('new_batches_count') or 0)} payout batchů"
        )
    elif source_type == "booking":
        lines.append(
            f"Booking: +{int(summary.get('new_reservations_count') or 0)} rezervací, "
            f"+{int(summary.get('new_batches_count') or 0)} payout batchů"
        )
    elif source_type == "bank":
        lines.append(
            f"Banka: +{int(summary.get('new_transactions_count') or 0)} transakcí "
            f"(Airbnb {int(summary.get('new_airbnb_transactions_count') or 0)}, "
            f"Booking {int(summary.get('new_booking_transactions_count') or 0)})"
        )
    elif source_type == "checkin":
        lines.append(
            f"Checkin: +{int(summary.get('new_reservations_count') or 0)} nových skupin, "
            f"{int(summary.get('changed_groups_count') or 0)} změněných"
        )
    elif summary.get("message"):
        lines.append(str(summary.get("message")))

    example_fields = (
        ("new_confirmation_codes", "Kódy"),
        ("new_batch_refs", "Batch refs"),
        ("new_checkin_reservation_ids", "Nové reservation IDs"),
        ("changed_checkin_reservation_ids", "Změněné reservation IDs"),
        ("new_transaction_keys", "Nové transakce"),
    )
    for field_name, label in example_fields:
        values = [str(value).strip() for value in (summary.get(field_name) or []) if str(value).strip()]
        if values:
            suffix = " …" if len(values) >= 5 else ""
            lines.append(f"{label}: {', '.join(values[:4])}{suffix}")
    return lines


def _create_import_impact_notification(
    conn,
    *,
    slug: str,
    year: int,
    month: int,
    event_type: str,
    source_type: str,
    message: str,
    summary: dict,
) -> dict:
    return create_report_month_notification(
        conn,
        slug=slug,
        year=year,
        month=month,
        event_type=event_type,
        source_type=source_type,
        message=message,
        payload={
            "import_run_id": summary.get("import_run_id"),
            "summary_message": summary.get("message", ""),
            "change_lines": _import_change_lines(summary),
            "source_name": summary.get("source_name", ""),
        },
    )


def _latest_month_notification_map(conn, properties: list[dict], months: list[tuple[int, int]]) -> dict[tuple[str, int, int], dict]:
    if not properties or not months:
        return {}
    slugs = [str(prop["slug"]) for prop in properties if str(prop.get("slug") or "").strip()]
    if not slugs:
        return {}
    slug_placeholders = ",".join("?" for _ in slugs)
    month_cond = " OR ".join(f"(year={int(y)} AND month={int(m)})" for y, m in months)
    rows = conn.execute(
        f"""SELECT *
              FROM report_month_notifications
             WHERE slug IN ({slug_placeholders}) AND ({month_cond})
             ORDER BY created_at DESC, id DESC""",
        slugs,
    ).fetchall()
    latest: dict[tuple[str, int, int], dict] = {}
    for row in rows:
        item = dict(row)
        key = (str(item.get("slug") or ""), int(item.get("year") or 0), int(item.get("month") or 0))
        if key in latest:
            continue
        try:
            item["payload"] = json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        latest[key] = item
    return latest


def _notification_change_lines(note: dict | None) -> list[str]:
    if not note:
        return []
    payload = note.get("payload") or {}
    lines = [str(line).strip() for line in (payload.get("change_lines") or []) if str(line).strip()]
    if lines:
        return lines
    message = str(payload.get("summary_message") or note.get("message") or "").strip()
    return [message] if message else []


def _notification_change_summary(note: dict | None) -> str:
    lines = _notification_change_lines(note)
    return lines[0] if lines else ""


def _prepare_rows_for_display(rows: list[dict]) -> list[dict]:
    prepared = []
    for row in rows:
        display_status, display_note = _display_verification_status(row)
        prepared.append(
            {
                **row,
                "display_verification_status": display_status,
                "display_verification_note": display_note,
            }
        )
    return prepared


def _build_dashboard_maps(conn, properties: list[dict], months: list[tuple[int, int]]) -> tuple[dict, dict, dict, dict]:
    slugs = [p["slug"] for p in properties]
    if not slugs or not months:
        empty = {s: {} for s in slugs}
        return empty, dict(empty), dict(empty), {}

    slug_ph = ",".join("?" for _ in slugs)
    month_cond = " OR ".join(f"(year={y} AND month={m})" for y, m in months)

    # 1. History — only latest entry per slug+month (via subquery with MAX)
    history_map: dict[str, dict] = {s: {} for s in slugs}
    history_rows = conn.execute(
        f"""SELECT h.* FROM report_history h
            INNER JOIN (
                SELECT slug, year, month, MAX(generated_at) as max_gen
                FROM report_history
                WHERE slug IN ({slug_ph}) AND ({month_cond})
                GROUP BY slug, year, month
            ) latest ON h.slug = latest.slug AND h.year = latest.year
                    AND h.month = latest.month AND h.generated_at = latest.max_gen""",
        slugs,
    ).fetchall()
    for row in history_rows:
        history_map[row["slug"]][(row["year"], row["month"])] = dict(row)

    # 2. Aggregates from report_rows via SQL json_extract — replaces N×M loop
    #    JOIN report_objects to get per-property commission for client payout calc
    agg_rows = conn.execute(
        f"""SELECT r.slug, r.year, r.month,
                COUNT(*) as rows_count,
                ROUND(COALESCE(SUM(CAST(json_extract(r.data, '$.payout_czk') AS REAL)), 0), 2) as payout_sum_czk,
                ROUND(COALESCE(SUM(CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)), 0), 2) as cena_ubytovani_sum_czk,
                ROUND(COALESCE(SUM(CAST(json_extract(r.data, '$.provize_czk') AS REAL)), 0), 2) as provize_sum_czk,
                ROUND(COALESCE(SUM(CASE
                    WHEN COALESCE(o.client_type, 'rentero') = 'z_klient' THEN
                        CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)
                        + COALESCE(CAST(json_extract(r.data, '$.city_tax_czk') AS REAL), 0)
                    ELSE
                        CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)
                        * (1.0 - COALESCE(o.rentero_commission, 0.15) * (1.0 + COALESCE(o.vat_rate, 0.21)))
                END), 0), 2) as client_payout_sum_czk,
                ROUND(COALESCE(SUM(CASE
                    WHEN COALESCE(o.client_type, 'rentero') = 'z_klient' THEN
                        CAST(json_extract(r.data, '$.payout_czk') AS REAL) * 0.03
                    ELSE
                        CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)
                        * COALESCE(o.rentero_commission, 0.15) * (1.0 + COALESCE(o.vat_rate, 0.21))
                END), 0), 2) as rentero_fee_sum_czk,
                SUM(CASE WHEN json_extract(r.data, '$.verification_status') = 'MATCHED'
                         AND (NOT json_extract(r.data, '$.tax_verification_required')
                              OR (COALESCE(CAST(json_extract(r.data, '$.checkin_missing_age_guests') AS INTEGER), 0) = 0
                                  AND json_extract(r.data, '$.checkin_verified')))
                    THEN 1 ELSE 0 END) as matched,
                SUM(CASE WHEN json_extract(r.data, '$.verification_status') = 'ROZDÍL' THEN 1 ELSE 0 END) as rozdil,
                SUM(CASE WHEN json_extract(r.data, '$.verification_status') = 'CHYBÍ_V_CSV' THEN 1 ELSE 0 END) as chybi_csv,
                SUM(CASE WHEN json_extract(r.data, '$.verification_status') = 'CHYBÍ_V_HOSTIFY' THEN 1 ELSE 0 END) as chybi_hostify,
                SUM(CASE WHEN json_extract(r.data, '$.verification_status') = 'MATCHED'
                         AND json_extract(r.data, '$.tax_verification_required')
                         AND (COALESCE(CAST(json_extract(r.data, '$.checkin_missing_age_guests') AS INTEGER), 0) > 0
                              OR NOT json_extract(r.data, '$.checkin_verified'))
                    THEN 1 ELSE 0 END) as ke_kontrole
            FROM report_rows r
            LEFT JOIN report_objects o ON o.slug = r.slug
            WHERE r.slug IN ({slug_ph}) AND ({month_cond})
            GROUP BY r.slug, r.year, r.month""",
        slugs,
    ).fetchall()
    for row in agg_rows:
        slug = row["slug"]
        key = (row["year"], row["month"])
        if slug in history_map and key in history_map[slug]:
            history_map[slug][key].update({
                "rows_count": row["rows_count"],
                "matched": row["matched"] or 0,
                "rozdil": row["rozdil"] or 0,
                "chybi_csv": row["chybi_csv"] or 0,
                "chybi_hostify": row["chybi_hostify"] or 0,
                "ke_kontrole": row["ke_kontrole"] or 0,
                "payout_sum_czk": row["payout_sum_czk"] or 0,
                "cena_ubytovani_sum_czk": row["cena_ubytovani_sum_czk"] or 0,
                "provize_sum_czk": row["provize_sum_czk"] or 0,
                "client_payout_sum_czk": row["client_payout_sum_czk"] or 0,
                "rentero_fee_sum_czk": row["rentero_fee_sum_czk"] or 0,
            })

    month_state_map: dict[str, dict] = {p["slug"]: {} for p in properties}
    all_states = get_report_month_states(
        conn,
        slugs=[p["slug"] for p in properties],
        months=months,
    )
    for state in all_states:
        month_state_map.setdefault(state["slug"], {})[(state["year"], state["month"])] = state

    listing_names = []
    for prop in properties:
        for year, month in months:
            listing_names.extend(_property_listing_names(prop, year=year, month=month))
    hostify_counts = get_hostify_reservation_counts(
        conn,
        listing_nicknames=listing_names,
        months=months,
    )
    nickname_month_counts = {
        (row["listing_nickname"], row["year"], row["month"]): int(row["reservation_count"])
        for row in hostify_counts
    }
    data_exists_map: dict[str, dict] = {p["slug"]: {} for p in properties}
    for prop in properties:
        slug = prop["slug"]
        for year, month in months:
            prop_names = _property_listing_names(prop, year=year, month=month)
            state = month_state_map.get(slug, {}).get((year, month), {})
            count_based = any(
                nickname_month_counts.get((name, year, month), 0) > 0
                for name in prop_names
            )
            state_based = state.get("data_state") not in (None, MONTH_DATA_STATE_EMPTY)
            data_exists_map[slug][(year, month)] = bool(count_based or state_based)

    notification_map = _latest_month_notification_map(conn, properties, months)
    return history_map, month_state_map, data_exists_map, notification_map


def _property_count_suffix(count: int) -> str:
    if count == 1:
        return ""
    if count in (2, 3, 4):
        return "y"
    return "ů"


def _build_dashboard_view_model(
    properties: list[dict],
    months: list[tuple[int, int]],
    history_map: dict,
    month_state_map: dict,
    data_exists_map: dict,
    notification_map: dict,
) -> tuple[dict, list[dict], list[dict]]:
    cur_y, cur_m = months[-1]
    prev_y, prev_m = months[-2] if len(months) >= 2 else (cur_y, cur_m)

    total_payout_czk = sum(
        history_map.get(p["slug"], {}).get((cur_y, cur_m), {}).get("payout_sum_czk", 0) or 0
        for p in properties
    )
    total_client_payout_czk = sum(
        history_map.get(p["slug"], {}).get((cur_y, cur_m), {}).get("client_payout_sum_czk", 0) or 0
        for p in properties
    )
    total_res_cur = sum(
        (history_map.get(p["slug"], {}).get((cur_y, cur_m), {}).get("rows_count") or 0)
        for p in properties
    )
    total_res_prev = sum(
        (history_map.get(p["slug"], {}).get((prev_y, prev_m), {}).get("rows_count") or 0)
        for p in properties
    )
    res_delta = total_res_cur - total_res_prev
    sparkline_points = [
        sum(
            (history_map.get(p["slug"], {}).get((y, m), {}).get("rows_count") or 0)
            for p in properties
        )
        for y, m in months
    ]

    summary = {
        "issues": 0,
        "new_data": 0,
        "needs_report": 0,
        "locked": 0,
        "total_with_data": 0,
        "property_count": len(properties),
        "property_suffix": _property_count_suffix(len(properties)),
        "current_month_label": f"{cur_m:02d}/{cur_y}",
        "total_payout_czk": total_payout_czk,
        "total_client_payout_czk": total_client_payout_czk,
        "total_reservations": total_res_cur,
        "reservations_delta": res_delta,
        "sparkline_points": sparkline_points,
    }

    month_headers = [
        {
            "year": year,
            "month": month,
            "label": f"{month:02d}/{year}",
            "is_current": year == cur_y and month == cur_m,
        }
        for year, month in months
    ]

    dashboard_rows: list[dict] = []
    for prop in properties:
        slug = prop["slug"]
        display_name = prop.get("display_name") or prop.get("listing_nickname") or slug

        cur_h = history_map.get(slug, {}).get((cur_y, cur_m))
        cur_st = month_state_map.get(slug, {}).get((cur_y, cur_m))
        cur_de = data_exists_map.get(slug, {}).get((cur_y, cur_m))
        cur_issues = 0
        if cur_h:
            cur_issues = (
                (cur_h.get("rozdil") or 0)
                + (cur_h.get("ke_kontrole") or 0)
                + (cur_h.get("chybi_hostify") or 0)
                + (cur_h.get("chybi_csv") or 0)
            )

        if cur_de:
            summary["total_with_data"] += 1
        if cur_issues > 0:
            summary["issues"] += 1
        if cur_st and cur_st.get("has_new_data_since_generation"):
            summary["new_data"] += 1
        if cur_de and not cur_h and (not cur_st or cur_st.get("status") != "LOCKED"):
            summary["needs_report"] += 1
        if cur_st and cur_st.get("status") == "LOCKED":
            summary["locked"] += 1

        if cur_h:
            health = "issues" if cur_issues > 0 else "ok"
        elif cur_de and (not cur_st or cur_st.get("status") != "LOCKED"):
            health = "action"
        else:
            health = "empty"

        cells = []
        for year, month in months:
            h = history_map.get(slug, {}).get((year, month))
            st = month_state_map.get(slug, {}).get((year, month))
            de = data_exists_map.get(slug, {}).get((year, month))
            is_locked = bool(st and st.get("status") == "LOCKED")
            has_new = bool(st and st.get("has_new_data_since_generation"))
            note = notification_map.get((slug, year, month))

            if h:
                kind = "report"
            elif de and not is_locked:
                kind = "empty"
            elif is_locked:
                kind = "locked"
            else:
                kind = "empty"

            cells.append(
                {
                    "year": year,
                    "month": month,
                    "label": f"{month:02d}/{year}",
                    "is_current": year == cur_y and month == cur_m,
                    "detail_href": f"/property/{slug}/{year}/{month}",
                    "kind": kind,
                    "is_locked": is_locked,
                    "has_new": has_new,
                    "change_summary": _notification_change_summary(note),
                    "change_lines": _notification_change_lines(note),
                    "rows_count": (h or {}).get("rows_count", 0),
                    "payout_sum_czk": (h or {}).get("payout_sum_czk", 0) or 0,
                    "cena_ubytovani_sum_czk": (h or {}).get("cena_ubytovani_sum_czk", 0) or 0,
                    "provize_sum_czk": (h or {}).get("provize_sum_czk", 0) or 0,
                    "client_payout_sum_czk": (h or {}).get("client_payout_sum_czk", 0) or 0,
                    "rentero_fee_sum_czk": (h or {}).get("rentero_fee_sum_czk", 0) or 0,
                    "matched": (h or {}).get("matched", 0),
                    "rozdil": (h or {}).get("rozdil", 0),
                    "ke_kontrole": (h or {}).get("ke_kontrole", 0),
                    "chybi_hostify": (h or {}).get("chybi_hostify", 0),
                    "chybi_csv": (h or {}).get("chybi_csv", 0),
                }
            )

        dashboard_rows.append(
            {
                "slug": slug,
                "display_name": display_name,
                "health": health,
                "cells": cells,
            }
        )

    return summary, month_headers, dashboard_rows


def _month_has_data(conn, prop: dict, year: int, month: int) -> bool:
    state = get_report_month_state(conn, prop["slug"], year, month)
    if state.get("data_state") != MONTH_DATA_STATE_EMPTY:
        return True
    counts = get_hostify_reservation_counts(
        conn,
        listing_nicknames=_property_listing_names(prop, year=year, month=month),
        months=[(year, month)],
    )
    return any(int(row["reservation_count"]) > 0 for row in counts)


def _ensure_month_open(conn, slug: str, year: int, month: int) -> dict:
    state = get_report_month_state(conn, slug, year, month)
    if state.get("status") == MONTH_STATUS_LOCKED:
        raise HTTPException(
            status_code=423,
            detail=f"Month {month:02d}/{year} for {slug} is locked.",
        )
    return state


def _db_path_for_connection(conn) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row["file"]:
        raise RuntimeError("Unable to resolve SQLite database path for generation job.")
    return row["file"]


def _sources_redirect(source_type: str | None = None) -> str:
    clean = (source_type or "").strip().lower()
    if clean:
        return f"/sources?source_type={clean}"
    return "/sources"


def _compute_row_breakdown(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {"airbnb": [], "booking": [], "other": []}
    for row in rows:
        if row.get("is_excluded"):
            continue
        src = (row.get("source") or "").lower()
        if "airbnb" in src:
            buckets["airbnb"].append(row)
        elif "booking" in src:
            buckets["booking"].append(row)
        else:
            buckets["other"].append(row)

    def _sums(items: list[dict]) -> dict:
        def _s(field: str) -> float:
            return round(sum(float(item.get(field) or 0) for item in items), 2)
        return {
            "count": len(items),
            "payout_czk": _s("payout_czk"),
            "provize_czk": _s("provize_czk"),
            "dph_provize_czk": _s("dph_provize_czk"),
            "city_tax_czk": _s("city_tax_czk"),
            "uklid_czk": _s("uklid_czk"),
            "balicky_czk": _s("balicky_czk"),
            "priprava_pokoje_czk": _s("priprava_pokoje_czk"),
            "dph_uklid_balicky_czk": _s("dph_uklid_balicky_czk"),
            "cena_ubytovani_czk": _s("cena_ubytovani_czk"),
        }

    active_rows = [r for r in rows if not r.get("is_excluded")]
    return {
        "airbnb": _sums(buckets["airbnb"]),
        "booking": _sums(buckets["booking"]),
        "other": _sums(buckets["other"]),
        "total": _sums(active_rows),
    }


def _latest_report_for_month(conn, slug: str, year: int, month: int) -> dict | None:
    history = get_report_history(conn, slug=slug, limit=50)
    for row in history:
        if int(row.get("year") or 0) == int(year) and int(row.get("month") or 0) == int(month):
            return row
    return None


def _show_recent_generation_success(generation_job: dict | None, *, window_seconds: int = 60) -> bool:
    if not generation_job:
        return False
    if generation_job.get("status") != "SUCCEEDED":
        return False
    finished_at = str(generation_job.get("finished_at") or "").strip()
    if not finished_at:
        return False
    try:
        finished_dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if finished_dt.tzinfo is None:
        finished_dt = finished_dt.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - finished_dt.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= float(window_seconds)


def _load_all_bank_transactions_for_codes(
    conn,
    codes: list[str],
) -> dict[str, list[dict]]:
    if not codes:
        return {}

    placeholders = ",".join("?" for _ in codes)
    explicit_rows = conn.execute(
        f"""
        SELECT  pbi.confirmation_code,
                pbi.batch_ref,
                pbi.channel,
                pbi.amount_czk AS item_amount_czk,
                bt.tx_key,
                bt.datum,
                bt.amount_czk AS tx_amount_czk
        FROM payout_batch_items pbi
        JOIN payout_batch_bank_matches pbm
          ON pbm.batch_ref = pbi.batch_ref
         AND pbm.channel = pbi.channel
        JOIN bank_transactions bt ON bt.tx_key = pbm.tx_key
        WHERE pbi.confirmation_code IN ({placeholders})
        ORDER BY pbi.confirmation_code, bt.datum ASC, bt.tx_key ASC
        """,
        codes,
    ).fetchall()

    result: dict[str, list[dict]] = {code: [] for code in codes}
    seen: set[tuple] = set()

    def _add(code: str, tx_key: str, datum: str, tx_czk, item_czk, batch_ref: str, channel: str) -> None:
        key = (code, tx_key, batch_ref)
        if key in seen:
            return
        seen.add(key)
        result[code].append(
            {
                "tx_key": tx_key,
                "datum": datum or "",
                "tx_amount_czk": tx_czk,
                "item_amount_czk": item_czk,
                "batch_ref": batch_ref or "",
                "channel": channel or "",
            }
        )

    for row in explicit_rows:
        _add(
            str(row["confirmation_code"]),
            row["tx_key"],
            row["datum"],
            row["tx_amount_czk"],
            row["item_amount_czk"],
            row["batch_ref"],
            row["channel"],
        )

    booking_items = conn.execute(
        f"""
        SELECT pbi.confirmation_code,
               pbi.batch_ref,
               pbi.amount_czk AS item_amount_czk
        FROM payout_batch_items pbi
        WHERE pbi.channel = 'booking'
          AND pbi.confirmation_code IN ({placeholders})
        """,
        codes,
    ).fetchall()

    unmatched_refs: set[str] = set()
    ref_to_items: dict[str, list[dict]] = {}
    for row in booking_items:
        code = str(row["confirmation_code"])
        batch_ref = row["batch_ref"] or ""
        already_matched = any(entry["batch_ref"] == batch_ref for entry in result.get(code, []))
        if not already_matched:
            unmatched_refs.add(batch_ref)
            ref_to_items.setdefault(batch_ref, []).append(
                {
                    "code": code,
                    "item_amount_czk": row["item_amount_czk"],
                }
            )

    if unmatched_refs:
        bt_rows = conn.execute(
            "SELECT tx_key, datum, amount_czk, zprava FROM bank_transactions WHERE channel='booking'"
        ).fetchall()
        norm_to_tx: dict[str, dict] = {}
        for row in bt_rows:
            norm = _normalize_booking_ref(row["zprava"] or "")
            if norm:
                norm_to_tx[norm] = dict(row)

        for batch_ref in unmatched_refs:
            norm = _normalize_booking_ref(batch_ref)
            tx = norm_to_tx.get(norm)
            if not tx:
                continue
            for entry in ref_to_items.get(batch_ref, []):
                _add(
                    entry["code"],
                    tx["tx_key"],
                    tx["datum"],
                    tx["amount_czk"],
                    entry["item_amount_czk"],
                    batch_ref,
                    "booking",
                )

    for items in result.values():
        items.sort(key=lambda item: item["datum"])

    return result


def _render_property_template(
    request: Request,
    *,
    templates,
    conn,
    prop: dict,
    slug: str,
    year: int,
    month: int,
    rows: list[dict],
    expenses: list[dict],
    summary: dict,
    data_exists: bool,
    flash: dict | None,
    is_preview: bool,
):
    month_state = get_report_month_state(conn, slug, year, month)
    generation_job = get_latest_report_generation_job(conn, slug, year, month)
    show_generation_success_banner = _show_recent_generation_success(generation_job)
    latest_report = _latest_report_for_month(conn, slug, year, month)
    month_notifications = list_report_month_notifications(
        conn,
        slug=slug,
        year=year,
        month=month,
        created_after=month_state.get("last_generated_at"),
        limit=5,
    )
    latest_change_note = month_notifications[0] if month_notifications else None
    client = get_client(conn, slug)
    categories = get_expense_categories(conn)
    override_events = get_override_events(conn, slug, year, month)
    row_breakdown = _compute_row_breakdown(rows)
    prev_m, prev_y = (month - 1 or 12), (year if month > 1 else year - 1)
    next_m, next_y = (month % 12 + 1), (year if month < 12 else year + 1)

    codes = [str(row.get("confirmation_code") or "") for row in rows if row.get("confirmation_code")]
    bank_txns_by_code = _load_all_bank_transactions_for_codes(conn, codes)

    return templates.TemplateResponse(
        request,
        "property.html",
        {
            "prop": prop,
            "slug": slug,
            "year": year,
            "month": month,
            "rows": rows,
            "expenses": expenses,
            "categories": categories,
            "client": client,
            "summary": summary,
            "row_breakdown": row_breakdown,
            "month_state": month_state,
            "generation_job": generation_job,
            "show_generation_success_banner": show_generation_success_banner,
            "latest_report": latest_report,
            "month_notifications": month_notifications,
            "latest_change_lines": _notification_change_lines(latest_change_note),
            "latest_change_summary": _notification_change_summary(latest_change_note),
            "data_exists": data_exists,
            "flash": flash,
            "is_preview": is_preview,
            "override_events": override_events,
            "override_field_labels": OVERRIDE_FIELD_LABELS,
            "verification_status_options": VERIFICATION_STATUS_OPTIONS,
            "prev_y": prev_y,
            "prev_m": prev_m,
            "next_y": next_y,
            "next_m": next_m,
            "bank_txns_by_code": bank_txns_by_code,
        },
    )


def _build_report_main_cmd(slug: str, year: int, month: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "report.main",
        "--year",
        str(year),
        "--month",
        str(month),
        "--property",
        slug,
        "--overwrite",
        "--legacy-autodiscover",
        "--config",
        _CONFIG_PATH,
    ]


def _run_report_generation(slug: str, year: int, month: int) -> None:
    cmd = _build_report_main_cmd(slug, year, month)
    result = subprocess.run(
        cmd,
        cwd=_BASE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise HTTPException(
            status_code=500,
            detail=detail or f"Report generation failed for {slug} {month:02d}/{year}.",
        )


def _start_report_generation_runner(job_id: int, slug: str, year: int, month: int, *, db_path: str) -> None:
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "report.generation_job_runner",
        "--job-id",
        str(job_id),
        "--slug",
        slug,
        "--year",
        str(year),
        "--month",
        str(month),
        "--config",
        _CONFIG_PATH,
        "--db-path",
        db_path,
    ]
    log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"gen_job_{job_id}.log")
    fh = open(log_file, "w")  # noqa: SIM115
    subprocess.Popen(
        cmd,
        cwd=_BASE_DIR,
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _start_bulk_generation_runner(run_id: int, year: int, month: int, *, db_path: str) -> None:
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "report.bulk_generation_runner",
        "--run-id",
        str(run_id),
        "--year",
        str(year),
        "--month",
        str(month),
        "--config",
        _CONFIG_PATH,
        "--db-path",
        db_path,
    ]
    log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"bulk_run_{run_id}.log")
    fh = open(log_file, "w")  # noqa: SIM115
    subprocess.Popen(
        cmd,
        cwd=_BASE_DIR,
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _load_bank_rows_with_drilldown(
    conn,
    *,
    year: int,
    month: int,
) -> list[dict]:
    month_str = f"{int(year):04d}-{int(month):02d}"
    rows = [
        {
            **dict(row),
            "matched_batch_refs": [],
            "drilldown_batches": [],
        }
        for row in conn.execute(
            """SELECT *
                 FROM bank_transactions
                WHERE substr(datum, 1, 7) = ?
                ORDER BY datum DESC, amount_czk DESC, tx_key DESC""",
            (month_str,),
        ).fetchall()
    ]
    if not rows:
        return rows

    rows_by_tx_key = {row["tx_key"]: row for row in rows}
    tx_keys = list(rows_by_tx_key.keys())
    placeholders = ",".join("?" for _ in tx_keys)
    hostify_name_by_code: dict[str, str] = {}
    detail_rows = conn.execute(
        f"""SELECT pbm.tx_key,
                   pbm.channel,
                   pbm.batch_ref,
                   pbm.match_method,
                   pbm.matched_amount_czk,
                   pb.payout_date,
                   pb.credited_date,
                   pb.amount_czk AS batch_amount_czk,
                   pb.amount_eur AS batch_amount_eur,
                   pb.source_name AS batch_source_name,
                   pbi.item_index,
                   pbi.item_type,
                   pbi.confirmation_code,
                   pbi.guest_name,
                   pbi.listing_name,
                   pbi.property_id,
                   pbi.amount_czk,
                   pbi.amount_eur,
                   pbi.check_in,
                   pbi.check_out
              FROM payout_batch_bank_matches pbm
              LEFT JOIN payout_batches pb
                ON pb.channel = pbm.channel
               AND pb.batch_ref = pbm.batch_ref
              LEFT JOIN payout_batch_items pbi
                ON pbi.channel = pbm.channel
               AND pbi.batch_ref = pbm.batch_ref
             WHERE pbm.tx_key IN ({placeholders})
             ORDER BY pbm.tx_key, pbm.channel, pbm.batch_ref, pbi.item_index""",
        tx_keys,
    ).fetchall()

    all_codes = {
        str(row["confirmation_code"])
        for row in detail_rows
        if row["confirmation_code"]
    }
    report_row_by_code: dict[str, dict] = {}
    if all_codes:
        code_placeholders = ",".join("?" for _ in all_codes)
        hostify_rows = conn.execute(
            f"""SELECT confirmation_code, guest_name
                  FROM hostify_reservations
                 WHERE confirmation_code IN ({code_placeholders})""",
            list(all_codes),
        ).fetchall()
        hostify_name_by_code = {
            str(row["confirmation_code"]): str(row["guest_name"] or "")
            for row in hostify_rows
            if row["confirmation_code"]
        }
        rr_rows = conn.execute(
            f"""SELECT confirmation_code, slug, year, month
                  FROM report_rows
                 WHERE confirmation_code IN ({code_placeholders})""",
            list(all_codes),
        ).fetchall()
        report_row_by_code = {
            str(row["confirmation_code"]): {"slug": row["slug"], "year": row["year"], "month": row["month"]}
            for row in rr_rows
            if row["confirmation_code"]
        }

    batch_maps: dict[str, dict] = {}
    batch_lists: dict[str, list[dict]] = defaultdict(list)
    for raw in detail_rows:
        item = dict(raw)
        tx_key = item["tx_key"]
        batch_key = f"{tx_key}::{item.get('channel', '')}::{item.get('batch_ref', '')}"
        batch = batch_maps.get(batch_key)
        if batch is None:
            reservations: list[dict] = []
            batch = {
                "channel": item.get("channel", ""),
                "batch_ref": item.get("batch_ref", ""),
                "match_method": item.get("match_method", ""),
                "matched_amount_czk": item.get("matched_amount_czk"),
                "payout_date": item.get("payout_date", ""),
                "credited_date": item.get("credited_date", ""),
                "batch_amount_czk": item.get("batch_amount_czk"),
                "batch_amount_eur": item.get("batch_amount_eur"),
                "batch_source_name": item.get("batch_source_name", ""),
                "reservations": reservations,
                "items": reservations,
            }
            batch_maps[batch_key] = batch
            batch_lists[tx_key].append(batch)
        if item.get("item_index") is None:
            continue
        code = str(item.get("confirmation_code") or "")
        report_row = report_row_by_code.get(code, {})
        batch["reservations"].append(
            {
                "item_index": int(item.get("item_index") or 0),
                "item_type": item.get("item_type", ""),
                "confirmation_code": code,
                "guest_name": item.get("guest_name", "") or hostify_name_by_code.get(code, ""),
                "listing_name": item.get("listing_name", ""),
                "property_id": item.get("property_id", ""),
                "amount_czk": item.get("amount_czk"),
                "amount_eur": item.get("amount_eur"),
                "check_in": item.get("check_in", ""),
                "check_out": item.get("check_out", ""),
                "slug": report_row.get("slug", ""),
                "report_year": report_row.get("year"),
                "report_month": report_row.get("month"),
            }
        )

    booking_fallback_rows = conn.execute(
        """SELECT pb.batch_ref,
                  pb.payout_date,
                  pb.credited_date,
                  pb.amount_czk AS batch_amount_czk,
                  pb.amount_eur AS batch_amount_eur,
                  pb.source_name AS batch_source_name,
                  pbi.item_index,
                  pbi.item_type,
                  pbi.confirmation_code,
                  pbi.guest_name,
                  pbi.listing_name,
                  pbi.property_id,
                  pbi.amount_czk,
                  pbi.amount_eur,
                  pbi.check_in,
                  pbi.check_out
             FROM payout_batches pb
             LEFT JOIN payout_batch_items pbi
               ON pbi.channel = pb.channel
              AND pbi.batch_ref = pb.batch_ref
            WHERE pb.channel = 'booking'
            ORDER BY pb.batch_ref, pbi.item_index"""
    ).fetchall()
    booking_batches_by_norm: dict[str, dict] = {}
    for raw in booking_fallback_rows:
        item = dict(raw)
        norm_ref = _normalize_booking_ref(item.get("batch_ref", ""))
        if not norm_ref:
            continue
        batch = booking_batches_by_norm.get(norm_ref)
        if batch is None:
            reservations: list[dict] = []
            batch = {
                "channel": "booking",
                "batch_ref": item.get("batch_ref", ""),
                "match_method": "descriptor_ref_fallback",
                "matched_amount_czk": None,
                "payout_date": item.get("payout_date", ""),
                "credited_date": item.get("credited_date", ""),
                "batch_amount_czk": item.get("batch_amount_czk"),
                "batch_amount_eur": item.get("batch_amount_eur"),
                "batch_source_name": item.get("batch_source_name", ""),
                "reservations": reservations,
                "items": reservations,
            }
            booking_batches_by_norm[norm_ref] = batch
        if item.get("item_index") is None:
            continue
        code = str(item.get("confirmation_code") or "")
        report_row = report_row_by_code.get(code, {})
        batch["reservations"].append(
            {
                "item_index": int(item.get("item_index") or 0),
                "item_type": item.get("item_type", ""),
                "confirmation_code": code,
                "guest_name": item.get("guest_name", "") or hostify_name_by_code.get(code, ""),
                "listing_name": item.get("listing_name", ""),
                "property_id": item.get("property_id", ""),
                "amount_czk": item.get("amount_czk"),
                "amount_eur": item.get("amount_eur"),
                "check_in": item.get("check_in", ""),
                "check_out": item.get("check_out", ""),
                "slug": report_row.get("slug", ""),
                "report_year": report_row.get("year"),
                "report_month": report_row.get("month"),
            }
        )

    for tx_key, row in rows_by_tx_key.items():
        batches = batch_lists.get(tx_key, [])
        if not batches and str(row.get("channel") or "").lower() == "booking":
            norm_ref = _normalize_booking_ref(str(row.get("zprava") or ""))
            fallback_batch = booking_batches_by_norm.get(norm_ref)
            if fallback_batch:
                batches = [fallback_batch]
        row["drilldown_batches"] = batches
        row["matched_batch_refs"] = [batch["batch_ref"] for batch in batches if batch.get("batch_ref")]
        row["matched_batch_ref"] = row["matched_batch_refs"][0] if row["matched_batch_refs"] else ""
        row["matched_reservation_count"] = sum(
            1 for batch in batches for item in batch["reservations"]
            if item.get("confirmation_code") or item.get("guest_name")
        )
        guest_names: list[str] = []
        seen_names: set[str] = set()
        for batch in batches:
            for item in batch["reservations"]:
                name = str(item.get("guest_name") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                guest_names.append(name)
        if guest_names:
            row["display_guest_summary"] = ", ".join(guest_names[:2]) + (" +" + str(len(guest_names) - 2) if len(guest_names) > 2 else "")
        else:
            row["display_guest_summary"] = ""

    return rows


def _filter_bank_rows(
    rows: list[dict],
    *,
    channel: str = "",
    match_state: str = "",
    query: str = "",
) -> list[dict]:
    filtered = list(rows)
    channel_filter = str(channel or "").strip().lower()
    if channel_filter in {"airbnb", "booking"}:
        filtered = [row for row in filtered if str(row.get("channel") or "").strip().lower() == channel_filter]

    match_filter = str(match_state or "").strip().lower()
    if match_filter == "matched":
        filtered = [row for row in filtered if row.get("matched_batch_ref")]
    elif match_filter == "unmatched":
        filtered = [row for row in filtered if not row.get("matched_batch_ref")]

    q = str(query or "").strip().lower()
    if q:
        def _matches_query(row: dict) -> bool:
            haystack = [
                str(row.get("gref") or ""),
                str(row.get("zprava") or ""),
                str(row.get("source_name") or ""),
                str(row.get("tx_key") or ""),
                str(row.get("channel") or ""),
            ]
            for batch in row.get("drilldown_batches") or []:
                haystack.extend(
                    [
                        str(batch.get("batch_ref") or ""),
                        str(batch.get("batch_source_name") or ""),
                    ]
                )
                for item in (batch.get("items") or batch.get("reservations") or []):
                    haystack.extend(
                        [
                            str(item.get("confirmation_code") or ""),
                            str(item.get("guest_name") or ""),
                            str(item.get("listing_name") or ""),
                            str(item.get("property_id") or ""),
                        ]
                    )
            return q in " ".join(haystack).lower()

        filtered = [row for row in filtered if _matches_query(row)]

    return filtered


# --------------------------------------------------------------------------- #
#  Reconciliation (Srovnání) view                                              #
# --------------------------------------------------------------------------- #

def _load_reconciliation_view(
    conn,
    *,
    year: int,
    month: int,
    channel_filter: str = "",
    status_filter: str = "",
):
    from report.accounting import build_payout_aggregate, compute_l3_reconciliation
    from report.db import get_accounting_entries

    channels = []
    if not channel_filter or channel_filter.lower() == "all":
        channels = ["airbnb", "booking"]
    else:
        channels = [channel_filter.lower()]

    all_rows = []
    for ch in channels:
        channel_label = ch.capitalize()
        payout_agg = build_payout_aggregate(conn, ch, year, month)
        acct_entries = get_accounting_entries(conn, channel=channel_label, year=year, month=month)
        l3 = compute_l3_reconciliation(payout_agg, acct_entries, ch)
        for r in l3:
            r["channel"] = channel_label
        all_rows.extend(l3)

    # Apply status filter
    if status_filter:
        sf = status_filter.upper()
        all_rows = [r for r in all_rows if r.get("status") == sf]

    # Compute KPIs from unfiltered data (reload if filtered)
    if status_filter:
        kpi_rows = []
        for ch in (["airbnb", "booking"] if not channel_filter or channel_filter.lower() == "all" else [channel_filter.lower()]):
            channel_label = ch.capitalize()
            payout_agg = build_payout_aggregate(conn, ch, year, month)
            acct_entries = get_accounting_entries(conn, channel=channel_label, year=year, month=month)
            l3 = compute_l3_reconciliation(payout_agg, acct_entries, ch)
            kpi_rows.extend(l3)
    else:
        kpi_rows = all_rows

    matched = sum(1 for r in kpi_rows if r.get("status") == "MATCHED")
    partial = sum(1 for r in kpi_rows if r.get("status") == "PARTIAL")
    unmatched = sum(1 for r in kpi_rows if r.get("status") == "UNMATCHED")
    no_source = sum(1 for r in kpi_rows if r.get("status") == "NO_SOURCE")
    total_diff = sum(r.get("diff") or 0.0 for r in kpi_rows if r.get("diff") is not None)

    return {
        "rows": all_rows,
        "matched": matched,
        "partial": partial,
        "unmatched": unmatched,
        "no_source": no_source,
        "total_pairs": len(kpi_rows),
        "total_diff": round(total_diff, 2),
    }
