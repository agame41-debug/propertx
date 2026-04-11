"""
Read-only preview builder for a single property-month.
"""

from __future__ import annotations

import inspect
import os
from calendar import monthrange
from datetime import date as date_cls

from report.bank import (
    build_bank_index,
    enrich_booking_rows_with_bank,
    enrich_rows_with_bank,
    filter_bank_by_cutoff,
    load_bank_csv,
    load_booking_bank_transactions,
    resolve_pending_against_bank,
)
from report.calculator import calculate_all_rows, calculate_totals_with_config
from report.cnb import CnbRateError, get_rate_for_reservation, preload_rates_for_month
from report.config import (
    get_all_properties,
    get_booking_config,
    get_hostify_listing_names,
    load_runtime_config,
)
from report.db import (
    get_active_source_files,
    get_expenses,
    get_hostify_reservations_by_codes,
    get_hostify_reservations_for_listing_month,
    get_pending_payments,
)
from report.loader import fetch_raw_reservations_for_period, filter_for_property_month
from report.summary import build_report_summary
from report.verifier import (
    STATUS_CHYBI_HOSTIFY,
    STATUS_KE_KONTROLE,
    build_airbnb_payout_data,
    build_booking_payout_data,
    build_verification_index,
    load_airbnb_csv,
    load_booking_csv,
)


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "properties.json")


def _get_active_properties(config: dict) -> list[dict]:
    try:
        signature = inspect.signature(get_all_properties)
    except (TypeError, ValueError):
        signature = None
    if signature and "active_only" in signature.parameters:
        return get_all_properties(config, active_only=True)
    return get_all_properties(config)


def _auto_find_csvs(source_dir: str, subdir: str, extensions: tuple) -> list[str]:
    path = os.path.join(source_dir, subdir)
    if not os.path.isdir(path):
        return []
    return [
        os.path.join(path, f)
        for f in os.listdir(path)
        if f.lower().endswith(extensions) and not f.startswith("~")
    ]


def _resolve_sources(
    db_conn,
    source_dir: str,
    subdir: str,
    extensions: tuple,
    source_type: str,
    *,
    legacy_autodiscover: bool,
) -> list:
    if db_conn is not None:
        db_sources = get_active_source_files(db_conn, source_type)
        if db_sources:
            return db_sources
    if legacy_autodiscover:
        return _auto_find_csvs(source_dir, subdir, extensions)
    return []


def build_property_preview(
    slug: str,
    year: int,
    month: int,
    *,
    db_conn,
    config_path: str | None = None,
    legacy_autodiscover: bool = True,
    cutoff_day: int = 7,
    allow_live_fetch: bool = False,
) -> dict:
    config = load_runtime_config(config_path or _DEFAULT_CONFIG_PATH, db_conn=db_conn)
    props = {p["slug"]: p for p in _get_active_properties(config)}
    if slug not in props:
        raise ValueError(f"Unknown property slug: {slug}")
    prop = props[slug]

    source_dir = os.path.join(_PROJECT_ROOT, "source")
    airbnb_csvs = _resolve_sources(
        db_conn, source_dir, "airbnb", (".csv",), "airbnb",
        legacy_autodiscover=legacy_autodiscover,
    )
    booking_csvs = _resolve_sources(
        db_conn, source_dir, "booking", (".csv",), "booking",
        legacy_autodiscover=legacy_autodiscover,
    )
    bank_csvs = _resolve_sources(
        db_conn, source_dir, "bank", (".csv",), "bank",
        legacy_autodiscover=legacy_autodiscover,
    )

    airbnb_index = load_airbnb_csv(airbnb_csvs) if airbnb_csvs else {}
    booking_index = load_booking_csv(booking_csvs) if booking_csvs else {}
    airbnb_payout_data = build_airbnb_payout_data(airbnb_csvs) if airbnb_csvs else {
        "reservation_map": {}, "batches": [], "items": []
    }
    booking_payout_data = build_booking_payout_data(booking_csvs) if booking_csvs else {
        "reservation_map": {}, "batches": [], "items": []
    }
    gref_map = airbnb_payout_data["reservation_map"]
    booking_batch_map = booking_payout_data["reservation_map"]

    if month == 12:
        cutoff_year, cutoff_month_num = year + 1, 1
    else:
        cutoff_year, cutoff_month_num = year, month + 1
    _, last_day_cutoff = monthrange(cutoff_year, cutoff_month_num)
    cutoff_date = date_cls(cutoff_year, cutoff_month_num, min(cutoff_day, last_day_cutoff))

    if bank_csvs:
        bank_rows_all = load_bank_csv(bank_csvs)
        bank_rows = filter_bank_by_cutoff(bank_rows_all, cutoff_date)
        bank_index, bank_no_ref = build_bank_index(bank_rows)
        booking_bank_idx_all = load_booking_bank_transactions(bank_csvs)
        booking_bank_idx = {
            pid: [r for r in rows if r.get("datum") and r["datum"] <= cutoff_date]
            for pid, rows in booking_bank_idx_all.items()
        }
    else:
        bank_rows_all = []
        bank_index, bank_no_ref = {}, []
        booking_bank_idx = {}
        booking_bank_idx_all = {}

    booking_cfg = get_booking_config(prop, year=year, month=month)
    booking_nickname = booking_cfg.get("listing_nickname") or None
    hostify_nicknames = get_hostify_listing_names(prop, year=year, month=month)
    primary_nickname = hostify_nicknames[0] if hostify_nicknames else prop["listing_nickname"]
    extra_hostify_nicknames = [n for n in hostify_nicknames[1:] if n]
    preview_listing_nicknames = [
        primary_nickname,
        *extra_hostify_nicknames,
        booking_nickname,
    ]

    all_raw = []
    if db_conn is not None:
        all_raw = get_hostify_reservations_for_listing_month(
            db_conn,
            listing_nicknames=[n for n in preview_listing_nicknames if n],
            year=year,
            month=month,
        )
    if not all_raw and allow_live_fetch:
        all_raw = fetch_raw_reservations_for_period(
            year,
            month,
            use_cache=True,
            db_conn=db_conn,
        )
    if not all_raw:
        raise ValueError(
            f"No cached Hostify data available for {slug} {month:02d}/{year}. "
            "Generate the month first or wait for a successful Hostify-backed run."
        )

    preload_rates_for_month(year, month, persist=False)
    reservations = filter_for_property_month(
        all_raw,
        primary_nickname,
        year,
        month,
        extra_nicknames=(
            extra_hostify_nicknames + ([booking_nickname] if booking_nickname else [])
        ) or None,
    )

    late_hostify_lookup = {}
    if db_conn:
        booking_pid = booking_cfg.get("property_id", "")
        booking_codes = [
            code for code, row in booking_index.items()
            if row.get("property_id", "").strip() == booking_pid
        ]
        listing_nicknames = [*hostify_nicknames, booking_nickname]
        late_hostify_lookup = get_hostify_reservations_by_codes(
            db_conn,
            booking_codes,
            listing_nicknames=[n for n in listing_nicknames if n],
            year=year,
            month=month,
        )

    verified, csv_only = build_verification_index(
        reservations,
        airbnb_index,
        booking_index,
        prop,
        hostify_lookup=late_hostify_lookup,
        year=year,
        month=month,
    )
    all_verified = verified + csv_only

    for row in all_verified:
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()
        if "airbnb" in source:
            pinfo = gref_map.get(code, {})
            if pinfo.get("airbnb_rate"):
                row["airbnb_batch_rate"] = pinfo["airbnb_rate"]
            row["airbnb_payout_date"] = pinfo.get("payout_date", "")
            row["batch_ref"] = pinfo.get("gref", "")
            row["batch_payout_date"] = pinfo.get("payout_date", "")
            row["batch_amount_czk"] = pinfo.get("payout_czk")
            row["batch_rate"] = pinfo.get("airbnb_rate")
        elif "booking" in source:
            pinfo = booking_batch_map.get(code, {})
            row["batch_ref"] = pinfo.get("batch_ref", "")
            row["batch_payout_date"] = pinfo.get("payout_date", "") or row.get("booking_payout_date", "")
            row["batch_amount_czk"] = pinfo.get("payout_czk")
            row["batch_rate"] = pinfo.get("booking_batch_rate")

    rate_cache: dict[str, dict] = {}
    for res in all_verified:
        confirmed_at = res.get("confirmed_at") or res.get("check_in") or ""
        rate_date = confirmed_at[:10]
        if rate_date and rate_date not in rate_cache:
            try:
                rate_cache[rate_date] = get_rate_for_reservation(confirmed_at, persist=False)
            except CnbRateError:
                rate_cache[rate_date] = {"rate": 0, "valid_for": rate_date}

    calc_rows = calculate_all_rows(all_verified, rate_cache, prop)
    calc_rows, _ = enrich_rows_with_bank(calc_rows, gref_map, bank_index, bank_no_ref)
    calc_rows, _ = enrich_booking_rows_with_bank(calc_rows, booking_bank_idx, prop, year=year, month=month)

    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if (
            "booking" in source
            and row.get("verification_status") == STATUS_CHYBI_HOSTIFY
            and row.get("bank_status") == "CHYBÍ"
        ):
            row["verification_status"] = STATUS_KE_KONTROLE

    transferred_rows = []
    if db_conn:
        prev_pending = [
            p for p in get_pending_payments(db_conn, slug, status="PENDING")
            if p["original_year"] < year or (p["original_year"] == year and p["original_month"] < month)
        ]
        if prev_pending:
            bank_index_full, bank_no_ref_full = build_bank_index(bank_rows_all)
            transferred_rows, _ = resolve_pending_against_bank(
                prev_pending,
                bank_index_full,
                bank_no_ref_full,
                booking_bank_idx_all,
                cutoff_date,
                booking_cfg.get("property_id", ""),
            )

    expenses = get_expenses(db_conn, slug, year, month) if db_conn else []
    totals = calculate_totals_with_config(calc_rows, prop)
    summary = build_report_summary(
        calc_rows,
        prop,
        expenses=expenses,
        transferred_rows=transferred_rows,
    )
    return {
        "prop": prop,
        "rows": calc_rows,
        "expenses": expenses,
        "summary": summary,
        "totals": totals,
        "transferred_rows": transferred_rows,
        "data_exists": bool(reservations or calc_rows),
    }
