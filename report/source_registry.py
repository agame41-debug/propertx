"""
report/source_registry.py — manual source-file registry backed by SQLite.
"""

from __future__ import annotations

import inspect
import os
from datetime import date

from report.bank import load_bank_csv, load_booking_bank_transactions, load_marriott_bank_transactions
from report.checkin import (
    load_checkin_groups,
    load_checkin_guest_rows,
    match_checkin_property_slug,
    prepare_checkin_groups_for_storage,
)
from report.config import (
    get_airbnb_listing_names,
    get_all_properties,
    get_booking_config,
    load_runtime_config,
)
from report.db import (
    get_source_file,
    get_active_source_files,
    get_connection,
    import_source_file,
    import_source_file_with_result,
    list_checkin_reservations,
    list_source_files,
    list_import_runs,
    log_import_run,
    save_bank_transactions,
    save_checkin_source_snapshot,
    set_source_file_active,
)
from report.loader import assign_report_month
from report.verifier import build_airbnb_payout_data, build_booking_payout_data, load_airbnb_csv, load_booking_csv

SOURCE_TYPES = {"airbnb", "booking", "bank", "accounting", "checkin", "objekty"}


def _get_active_properties(config: dict) -> list[dict]:
    try:
        signature = inspect.signature(get_all_properties)
    except (TypeError, ValueError):
        signature = None
    if signature and "active_only" in signature.parameters:
        return get_all_properties(config, active_only=True)
    return get_all_properties(config)


def validate_source_type(source_type: str) -> str:
    normalized = (source_type or "").strip().lower()
    if normalized not in SOURCE_TYPES:
        raise ValueError(
            f"Unknown source type '{source_type}'. Expected one of: {', '.join(sorted(SOURCE_TYPES))}."
        )
    return normalized


def import_local_file(
    path: str,
    source_type: str,
    *,
    active: bool = True,
    imported_by: str = "cli",
    db_path: str | None = None,
) -> int:
    """Read a local file and import it via the same pipeline the web UI uses."""
    source_type = validate_source_type(source_type)
    abs_path = os.path.abspath(path)
    with open(abs_path, "rb") as f:
        content = f.read()
    conn = get_connection(db_path)
    try:
        summary = import_uploaded_source(
            conn,
            source_type,
            os.path.basename(abs_path),
            content,
            imported_by=imported_by,
            active=active,
        )
        return int(summary["source_file_id"])
    finally:
        conn.close()


def fetch_active_sources(source_type: str) -> list[dict]:
    """Return active DB-backed source files for the given type."""
    source_type = validate_source_type(source_type)
    conn = get_connection()
    try:
        return get_active_source_files(conn, source_type)
    finally:
        conn.close()


def fetch_source_listing(source_type: str | None = None, *, active_only: bool = False) -> list[dict]:
    """List registered source files."""
    conn = get_connection()
    try:
        return list_source_files(conn, source_type, active_only=active_only)
    finally:
        conn.close()


def mark_source_active(file_id: int, is_active: bool) -> None:
    conn = get_connection()
    try:
        set_source_file_active(conn, file_id, is_active)
    finally:
        conn.close()


def fetch_source_file(file_id: int, *, include_content: bool = False) -> dict | None:
    conn = get_connection()
    try:
        return get_source_file(conn, file_id, include_content=include_content)
    finally:
        conn.close()


def fetch_import_runs(source_type: str | None = None, *, limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        return list_import_runs(conn, source_type=source_type, limit=limit)
    finally:
        conn.close()


def _source_blob(original_name: str, content: bytes) -> dict:
    return {
        "id": None,
        "original_name": original_name,
        "content": content,
    }


def _normalize_month_keys(month_keys: list[tuple]) -> list[tuple[str, int, int]]:
    seen: set[tuple[str, int, int]] = set()
    normalized: list[tuple[str, int, int]] = []
    for slug, year, month in month_keys:
        key = (str(slug), int(year), int(month))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    normalized.sort()
    return normalized


def _iter_overlap_months(check_in_iso: str, check_out_iso: str) -> list[tuple[int, int]]:
    try:
        check_in = date.fromisoformat(check_in_iso)
        check_out = date.fromisoformat(check_out_iso)
    except ValueError:
        return []
    if check_out <= check_in:
        return [(check_in.year, check_in.month)]
    cursor = date(check_in.year, check_in.month, 1)
    last_inclusive = date((check_out - date.resolution).year, (check_out - date.resolution).month, 1)
    months: list[tuple[int, int]] = []
    while cursor <= last_inclusive:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _load_properties_for_matching(conn) -> list[dict]:
    config = load_runtime_config(None, db_conn=conn)
    return _get_active_properties(config)


def _match_airbnb_slug(properties: list[dict], listing_name: str, year: int, month: int) -> str:
    normalized = (listing_name or "").strip()
    if not normalized:
        return ""
    for prop in properties:
        names = [name.strip() for name in get_airbnb_listing_names(prop, year=year, month=month) if name and name.strip()]
        if normalized in names:
            return prop["slug"]
    return ""


def _match_booking_slug(properties: list[dict], property_id: str, year: int, month: int) -> str:
    pid = str(property_id or "").strip()
    if not pid:
        return ""
    for prop in properties:
        booking_cfg = get_booking_config(prop, year=year, month=month)
        if str(booking_cfg.get("property_id") or "").strip() == pid:
            return prop["slug"]
    return ""


def _estimate_report_month_from_bank_date(tx_date, *, cutoff_day: int = 7) -> tuple[int, int] | None:
    if not tx_date:
        return None
    if int(tx_date.day) <= int(cutoff_day):
        if tx_date.month == 1:
            return tx_date.year - 1, 12
        return tx_date.year, tx_date.month - 1
    return tx_date.year, tx_date.month


def _airbnb_delta_summary(conn, source_name: str, content: bytes) -> dict:
    source = _source_blob(source_name, content)
    reservation_map = load_airbnb_csv([source])
    payout_data = build_airbnb_payout_data([source])
    properties = _load_properties_for_matching(conn)

    existing_codes = {
        row["confirmation_code"]
        for row in conn.execute(
            """SELECT DISTINCT confirmation_code
               FROM payout_batch_items
               WHERE channel = 'airbnb' AND confirmation_code <> ''"""
        ).fetchall()
    }
    existing_batches = {
        row["batch_ref"]
        for row in conn.execute(
            "SELECT DISTINCT batch_ref FROM payout_batches WHERE channel = 'airbnb'"
        ).fetchall()
    }

    new_codes = sorted(code for code in reservation_map if code not in existing_codes)
    new_batches = sorted(
        batch["batch_ref"]
        for batch in payout_data["batches"]
        if batch.get("batch_ref") and batch["batch_ref"] not in existing_batches
    )
    affected_months = sorted({
        assign_report_month(row["check_in"], row["check_out"], row["nights"], "Airbnb")
        for row in reservation_map.values()
        if row.get("check_in") and row.get("check_out")
    })
    affected_month_keys = []
    for row in reservation_map.values():
        if not row.get("check_in") or not row.get("check_out"):
            continue
        assign_year, assign_month = assign_report_month(row["check_in"], row["check_out"], row["nights"], "Airbnb")
        slug = _match_airbnb_slug(properties, row.get("listing", ""), assign_year, assign_month)
        if slug:
            affected_month_keys.append((slug, assign_year, assign_month))

    return {
        "duplicate": False,
        "detected_rows_count": len(reservation_map),
        "new_rows_count": len(new_codes),
        "new_reservations_count": len(new_codes),
        "new_payout_rows_count": len(new_codes),
        "new_batches_count": len(new_batches),
        "new_confirmation_codes": new_codes[:50],
        "new_batch_refs": new_batches[:50],
        "affected_months": affected_months,
        "affected_month_keys": _normalize_month_keys(affected_month_keys),
        "message": f"+{len(new_codes)} new Airbnb reservations, +{len(new_batches)} new payout batches",
    }


def _booking_delta_summary(conn, source_name: str, content: bytes) -> dict:
    source = _source_blob(source_name, content)
    reservation_map = load_booking_csv([source])
    payout_data = build_booking_payout_data([source])
    properties = _load_properties_for_matching(conn)

    existing_codes = {
        row["confirmation_code"]
        for row in conn.execute(
            """SELECT DISTINCT confirmation_code
               FROM payout_batch_items
               WHERE channel = 'booking' AND confirmation_code <> ''"""
        ).fetchall()
    }
    existing_batches = {
        row["batch_ref"]
        for row in conn.execute(
            "SELECT DISTINCT batch_ref FROM payout_batches WHERE channel = 'booking'"
        ).fetchall()
    }

    new_codes = sorted(code for code in reservation_map if code not in existing_codes)
    new_batches = sorted(
        batch["batch_ref"]
        for batch in payout_data["batches"]
        if batch.get("batch_ref") and batch["batch_ref"] not in existing_batches
    )
    affected_months = sorted({
        assign_report_month(row["check_in"], row["check_out"], 1, "Booking.com")
        for row in reservation_map.values()
        if row.get("check_in") and row.get("check_out")
    })
    affected_month_keys = []
    for row in reservation_map.values():
        if not row.get("check_in") or not row.get("check_out"):
            continue
        assign_year, assign_month = assign_report_month(row["check_in"], row["check_out"], 1, "Booking.com")
        slug = _match_booking_slug(properties, row.get("property_id", ""), assign_year, assign_month)
        if slug:
            affected_month_keys.append((slug, assign_year, assign_month))

    return {
        "duplicate": False,
        "detected_rows_count": len(reservation_map),
        "new_rows_count": len(new_codes),
        "new_reservations_count": len(new_codes),
        "new_payout_rows_count": len(new_codes),
        "new_batches_count": len(new_batches),
        "new_confirmation_codes": new_codes[:50],
        "new_batch_refs": new_batches[:50],
        "affected_months": affected_months,
        "affected_month_keys": _normalize_month_keys(affected_month_keys),
        "message": f"+{len(new_codes)} new Booking reservations, +{len(new_batches)} new payout batches",
    }


def _bank_delta_summary(conn, source_name: str, content: bytes) -> dict:
    source = _source_blob(source_name, content)
    airbnb_rows = load_bank_csv([source])
    booking_map = load_booking_bank_transactions([source])
    booking_rows = [row for rows in booking_map.values() for row in rows]
    marriott_rows = load_marriott_bank_transactions([source])
    properties = _load_properties_for_matching(conn)

    existing_tx_keys = {
        row["tx_key"]
        for row in conn.execute("SELECT tx_key FROM bank_transactions").fetchall()
    }

    all_rows = []
    seen: set[str] = set()
    for row in airbnb_rows + booking_rows + marriott_rows:
        tx_key = row.get("tx_key") or ""
        if not tx_key or tx_key in seen:
            continue
        seen.add(tx_key)
        all_rows.append(row)

    new_airbnb = sorted(
        row["tx_key"] for row in airbnb_rows
        if row.get("tx_key") and row["tx_key"] not in existing_tx_keys
    )
    new_booking = sorted(
        row["tx_key"] for row in booking_rows
        if row.get("tx_key") and row["tx_key"] not in existing_tx_keys
    )
    new_total = sorted(
        row["tx_key"] for row in all_rows
        if row.get("tx_key") and row["tx_key"] not in existing_tx_keys
    )
    affected_month_keys = []
    pending_by_gref = {}
    for row in conn.execute(
        """SELECT slug, original_year, original_month, gref
           FROM pending_payments
           WHERE status = 'PENDING' AND gref <> ''"""
    ).fetchall():
        pending_by_gref.setdefault(row["gref"], []).append(
            (row["slug"], int(row["original_year"]), int(row["original_month"]))
        )

    for row in airbnb_rows:
        if not row.get("tx_key") or row["tx_key"] in existing_tx_keys:
            continue
        for key in pending_by_gref.get(row.get("gref", ""), []):
            affected_month_keys.append(key)

    for row in booking_rows:
        if not row.get("tx_key") or row["tx_key"] in existing_tx_keys:
            continue
        for key in pending_by_gref.get(row.get("booking_ref", ""), []):
            affected_month_keys.append(key)
        estimated = _estimate_report_month_from_bank_date(row.get("datum"))
        if estimated:
            slug = _match_booking_slug(properties, row.get("property_id", ""), estimated[0], estimated[1])
            if slug:
                affected_month_keys.append((slug, estimated[0], estimated[1]))

    return {
        "duplicate": False,
        "detected_rows_count": len(all_rows),
        "new_rows_count": len(new_total),
        "new_transactions_count": len(new_total),
        "new_airbnb_transactions_count": len(new_airbnb),
        "new_booking_transactions_count": len(new_booking),
        "new_transaction_keys": new_total[:50],
        "affected_months": [],
        "affected_month_keys": _normalize_month_keys(affected_month_keys),
        "message": (
            f"+{len(new_total)} new bank transactions "
            f"(Airbnb {len(new_airbnb)}, Booking {len(new_booking)}, "
            f"Marriott {len(marriott_rows)})"
        ),
    }


def _accounting_delta_summary(conn, source_name: str, content: bytes) -> dict:
    from report.accounting import load_hlavni_kniha_from_bytes
    from report.db import get_stredisko_map
    stredisko_map = get_stredisko_map(conn)
    try:
        entries = load_hlavni_kniha_from_bytes(content, stredisko_map or None)
    except Exception:
        entries = []
    affected_months = set()
    for e in entries:
        m = e.get("mesic")
        if m and len(m) == 7:
            try:
                affected_months.add((int(m[:4]), int(m[5:7])))
            except ValueError:
                pass
    return {
        "duplicate": False,
        "detected_rows_count": len(entries),
        "new_rows_count": len(entries),
        "new_transactions_count": 0,
        "new_reservations_count": 0,
        "affected_months": sorted(affected_months),
        "affected_month_keys": [],
        "message": f"+{len(entries)} účetních záznamů z '{source_name}'",
    }


def _stredisko_delta_summary(source_name: str, content: bytes) -> dict:
    from report.accounting import load_stredisko_from_bytes
    try:
        entries = load_stredisko_from_bytes(content)
    except Exception:
        entries = []
    return {
        "duplicate": False,
        "detected_rows_count": len(entries),
        "new_rows_count": len(entries),
        "new_transactions_count": 0,
        "new_reservations_count": 0,
        "affected_months": [],
        "affected_month_keys": [],
        "message": f"+{len(entries)} středisek z '{source_name}'",
    }


def _checkin_delta_summary(conn, source_name: str, content: bytes) -> dict:
    source = _source_blob(source_name, content)
    groups = load_checkin_groups([source])
    properties = _load_properties_for_matching(conn)
    existing_groups = {
        str(row.get("reservation_id") or ""): (
            str(row.get("property_name") or ""),
            str(row.get("check_in") or ""),
            str(row.get("check_out") or ""),
            int(row.get("total_guests") or 0),
            int(row.get("paying_guests") or 0),
            int(row.get("exempt_guests") or 0),
            int(row.get("missing_age_guests") or 0),
            tuple(row.get("guest_names") or []),
        )
        for row in list_checkin_reservations(conn, active_only=True, latest_only=True)
    }

    new_groups = []
    changed_groups = []
    for group in groups:
        fingerprint = (
            str(group.get("property_name") or ""),
            str(group.get("check_in") or ""),
            str(group.get("check_out") or ""),
            int(group.get("total_guests") or 0),
            int(group.get("paying_guests") or 0),
            int(group.get("exempt_guests") or 0),
            int(group.get("missing_age_guests") or 0),
            tuple(group.get("guest_names") or []),
        )
        existing = existing_groups.get(str(group["reservation_id"]))
        if existing is None:
            new_groups.append(group)
        elif existing != fingerprint:
            changed_groups.append(group)
    affected_month_keys = []
    affected_months = set()
    for group in groups:
        for assign_year, assign_month in _iter_overlap_months(group["check_in"], group["check_out"]):
            affected_months.add((assign_year, assign_month))
            slug = match_checkin_property_slug(
                properties,
                group.get("property_name", ""),
                year=assign_year,
                month=assign_month,
            )
            if slug:
                affected_month_keys.append((slug, assign_year, assign_month))

    return {
        "duplicate": False,
        "detected_rows_count": len(groups),
        "new_rows_count": len(new_groups) + len(changed_groups),
        "new_reservations_count": len(new_groups),
        "changed_groups_count": len(changed_groups),
        "affected_months": sorted(affected_months),
        "affected_month_keys": _normalize_month_keys(affected_month_keys),
        "new_checkin_reservation_ids": [group["reservation_id"] for group in new_groups[:50]],
        "changed_checkin_reservation_ids": [group["reservation_id"] for group in changed_groups[:50]],
        "message": (
            f"+{len(new_groups)} nových skupin z Checkin reportu"
            + (f", {len(changed_groups)} změněných" if changed_groups else "")
        ),
    }


def _default_effective_ym() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def analyze_import_delta(conn, source_type: str, original_name: str, content: bytes,
                         *, effective_ym: str | None = None) -> dict:
    source_type = validate_source_type(source_type)
    if source_type == "airbnb":
        return _airbnb_delta_summary(conn, original_name, content)
    if source_type == "booking":
        return _booking_delta_summary(conn, original_name, content)
    if source_type == "bank":
        return _bank_delta_summary(conn, original_name, content)
    if source_type == "checkin":
        return _checkin_delta_summary(conn, original_name, content)
    if source_type == "objekty":
        from report.objekty_import import objekty_delta_summary
        return objekty_delta_summary(conn, content, effective_ym or _default_effective_ym())
    return _accounting_delta_summary(conn, original_name, content)


def import_uploaded_source(
    conn,
    source_type: str,
    original_name: str,
    content: bytes,
    *,
    imported_by: str = "",
    active: bool = True,
    effective_ym: str | None = None,
) -> dict:
    source_type = validate_source_type(source_type)
    if source_type == "objekty":
        effective_ym = effective_ym or _default_effective_ym()
    conn.execute("BEGIN")
    try:
        delta = analyze_import_delta(conn, source_type, original_name, content,
                                     effective_ym=effective_ym)
        stored = import_source_file_with_result(
            conn,
            source_type,
            original_name,
            content,
            active=active,
            commit=False,
        )

        summary = {
            **delta,
            "source_name": original_name,
            "source_type": source_type,
            "is_duplicate": bool(stored["is_duplicate"]),
            "source_file_id": stored["source_file_id"],
            "duplicate_of_source_file_id": stored["duplicate_of_source_file_id"],
        }
        if source_type == "checkin" and not stored["is_duplicate"]:
            source = _source_blob(original_name, content)
            properties = _load_properties_for_matching(conn)
            guest_rows = load_checkin_guest_rows([source])
            groups = prepare_checkin_groups_for_storage(load_checkin_groups([source]), properties)
            guest_property_by_reservation = {
                str(group.get("reservation_id") or ""): str(group.get("property_slug") or "")
                for group in groups
            }
            enriched_guest_rows = [
                {
                    **row,
                    "property_slug": guest_property_by_reservation.get(str(row.get("reservation_id") or ""), ""),
                }
                for row in guest_rows
            ]
            save_checkin_source_snapshot(
                conn,
                int(stored["source_file_id"]),
                enriched_guest_rows,
                groups,
                commit=False,
            )
            summary["persisted_checkin_groups"] = len(groups)
            summary["persisted_checkin_guest_rows"] = len(enriched_guest_rows)
        elif source_type == "airbnb" and not stored["is_duplicate"]:
            from report.engine import _persist_csv_payout_artifacts
            from report.verifier import build_airbnb_payout_data, load_airbnb_csv
            source = _source_blob(original_name, content)
            airbnb_payout = build_airbnb_payout_data([source])
            airbnb_index = load_airbnb_csv([source])
            _persist_csv_payout_artifacts(
                conn,
                airbnb_payout_data=airbnb_payout,
                booking_payout_data={"batches": [], "items": []},
                booking_index={},
                airbnb_index=airbnb_index,
                bank_rows_all=[],
                booking_bank_idx_all={},
            )
            summary["persisted_airbnb_batches"] = len(airbnb_payout.get("batches") or [])
            summary["persisted_airbnb_items"] = len(airbnb_payout.get("items") or [])
        elif source_type == "booking" and not stored["is_duplicate"]:
            from report.engine import _persist_csv_payout_artifacts
            from report.verifier import build_booking_payout_data, load_booking_csv
            source = _source_blob(original_name, content)
            booking_payout = build_booking_payout_data([source])
            booking_index = load_booking_csv([source])
            _persist_csv_payout_artifacts(
                conn,
                airbnb_payout_data={"batches": [], "items": []},
                booking_payout_data=booking_payout,
                booking_index=booking_index,
                bank_rows_all=[],
                booking_bank_idx_all={},
            )
            summary["persisted_booking_batches"] = len(booking_payout.get("batches") or [])
            summary["persisted_booking_items"] = len(booking_payout.get("items") or [])
        elif source_type == "bank" and not stored["is_duplicate"]:
            source = _source_blob(original_name, content)
            airbnb_rows = load_bank_csv([source])
            booking_map = load_booking_bank_transactions([source])
            booking_rows = [row for rows in booking_map.values() for row in rows]
            marriott_rows = load_marriott_bank_transactions([source])
            save_bank_transactions(conn, "airbnb", airbnb_rows, commit=False)
            save_bank_transactions(conn, "booking", booking_rows, commit=False)
            save_bank_transactions(conn, "marriott", marriott_rows, commit=False)
            summary["persisted_airbnb_transactions"] = len(airbnb_rows)
            summary["persisted_booking_transactions"] = len(booking_rows)
            summary["persisted_marriott_transactions"] = len(marriott_rows)
        elif source_type == "accounting" and not stored["is_duplicate"]:
            from report.accounting import load_hlavni_kniha_from_bytes
            from report.db import get_stredisko_map, save_accounting_entries
            stredisko_map = get_stredisko_map(conn)
            entries = load_hlavni_kniha_from_bytes(content, stredisko_map or None)
            save_accounting_entries(conn, entries, int(stored["source_file_id"]), commit=False)
            summary["persisted_accounting_entries"] = len(entries)
        if stored["is_duplicate"]:
            summary["message"] = f"Duplicate import: '{original_name}' already exists in archive."
            summary["duplicate"] = True
            summary["new_rows_count"] = 0
            summary["new_transactions_count"] = 0
            summary["new_reservations_count"] = 0

        if source_type == "objekty":
            # Idempotent apply: runs regardless of byte-level duplicate, because
            # the same TSV re-imported for a new effective month legitimately
            # writes new profile segments. The apply result is the source of
            # truth for the summary (updated_count, affected_month_keys).
            from report.objekty_import import apply_objekty_import
            # commit=False: the outer BEGIN/commit in this function owns the
            # transaction so the whole objekty import is atomic.
            applied = apply_objekty_import(conn, content, effective_ym, commit=False)
            summary.update(applied)
            # Let downstream impacts run even on a byte-duplicate re-import.
            summary["is_duplicate"] = False

        import_run = log_import_run(
            conn,
            source_type=source_type,
            source_file_id=None if stored["is_duplicate"] else stored["source_file_id"],
            imported_by=imported_by,
            duplicate_of_source_file_id=stored["duplicate_of_source_file_id"] if stored["is_duplicate"] else None,
            new_rows_count=int(summary.get("new_rows_count") or 0),
            new_transactions_count=int(summary.get("new_transactions_count") or 0),
            new_reservations_count=int(summary.get("new_reservations_count") or 0),
            summary=summary,
            commit=False,
        )
        summary["import_run_id"] = import_run.get("id")
        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise
