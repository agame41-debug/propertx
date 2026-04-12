"""
report/engine.py — In-process report generation engine.

Replaces the subprocess-based generation for web-triggered flows.
Reads all inputs from SQLite (Hostify snapshots, payout CSV data, CNB rates).
Writes results to report_rows, report_history, report_month_state.
Does NOT write Excel files.

Public API:
    generate_report_in_process(conn, slug, year, month, config, *, cutoff_day=7) -> dict
    run_generation_background(db_path, slug, year, month, config) -> None  (for BackgroundTasks)
"""
from __future__ import annotations

import json
import logging
import os
import re
from calendar import monthrange
from collections import Counter
from datetime import date as date_cls, datetime as datetime_cls

from report.bank import (
    build_bank_index,
    enrich_booking_rows_with_bank,
    enrich_rows_with_bank,
    filter_bank_by_cutoff,
    load_bank_csv,
    load_booking_bank_transactions,
    resolve_pending_against_bank,
)
from report.calculator import calculate_all_rows
from report.checkin import apply_checkin_city_tax_overrides, hydrate_checkin_groups_from_db
from report.cnb import CnbRateError, get_rate_for_reservation, preload_rates_for_month
from report.config import get_booking_config, get_hostify_listing_names, get_all_properties
from report.db import (
    MONTH_STATUS_LOCKED,
    apply_overrides_to_rows,
    get_active_source_files,
    get_active_exclusions,
    get_codes_assigned_to_month,
    get_hostify_reservations_by_codes,
    get_hostify_reservations_for_listing_month,
    get_pending_payments,
    get_report_month_state,
    get_report_row_by_code,
    get_split_transactions,
    list_checkin_reservations,
    log_report_generated,
    mark_report_month_has_data,
    mark_report_month_stale,
    replace_checkin_match_audit,
    resolve_pending_payment,
    save_pending_payments,
    save_payout_batch_bank_matches,
    save_report_rows,
    touch_report_month_generation,
    get_reservation_month_assignments,
)
from report.loader import (
    _normalize_reservation,
    filter_for_property_month,
)
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
log = logging.getLogger(__name__)


def _build_adjustment_reservation(past_row: dict, batch_info: dict, suffix: str = "__ADJ") -> dict:
    """
    Build a synthetic reservation dict for a payout adjustment.
    Represents money arriving this month for a reservation from a past month.
    Cleaning, city tax, and balíčky are zeroed out to avoid double-counting.
    """
    source = past_row.get("source", "")
    parent_code = past_row.get("confirmation_code", "")
    payout_eur = float(
        batch_info.get("payout_eur")
        or batch_info.get("payout_czk", 0) / max(float(batch_info.get("airbnb_rate") or 25.0), 1.0)
        or 0.0
    )
    return {
        "confirmation_code": f"{parent_code}{suffix}",
        "adjustment_parent_code": parent_code,
        "guest_name": past_row.get("guest_name", ""),
        "check_in": past_row.get("check_in", ""),
        "check_out": past_row.get("check_out", ""),
        "nights": past_row.get("nights") or 0,
        "adults": past_row.get("adults") or 0,
        "children": 0,
        "infants": 0,
        "source": source,
        "status": "adjustment",
        "is_cancelled": False,
        "is_payout_adjustment": True,
        "adjustment_original_year": past_row.get("year"),
        "adjustment_original_month": past_row.get("month"),
        "listing_nickname": past_row.get("listing_nickname", ""),
        "listing_id": past_row.get("listing_id"),
        "confirmed_at": past_row.get("check_in", ""),
        "cleaning_fee_eur": 0.0,
        "city_tax_eur": 0.0,
        "channel_commission_eur": float(batch_info.get("commission_eur") or 0.0),
        "payout_price_eur": payout_eur,
        "effective_payout_eur": payout_eur,
        "airbnb_batch_rate": float(batch_info.get("airbnb_rate") or 0.0),
        "airbnb_payout_date": batch_info.get("payout_date", ""),
        "batch_ref": batch_info.get("gref") or batch_info.get("batch_ref", ""),
        "batch_payout_date": batch_info.get("payout_date", ""),
        "batch_amount_czk": batch_info.get("payout_czk"),
        "czk_booked": batch_info.get("payout_czk") if "booking" in source.lower() else None,
    }


def _build_aircover_reservation(parent_row: dict, ac_item: dict, suffix: str = "__AC") -> dict:
    """
    Build a synthetic reservation for an AirCover compensation item.
    These are separate payouts from Airbnb for guest damages, not regular stays.
    Auto-excluded from financial calculations, marked KE KONTROLE.

    Uses a modified confirmation_code ({code}__AC) to avoid DB collision with
    the main reservation (UNIQUE on slug+year+month+code).
    batch_ref is cleared so bank enrichment doesn't steal the main row's match.
    """
    parent_code = parent_row.get("confirmation_code", "")
    amount_eur = abs(float(ac_item.get("amount_eur", 0)))
    return {
        "confirmation_code": f"{parent_code}{suffix}",
        "aircover_parent_code": parent_code,
        "guest_name": ac_item.get("guest_name") or parent_row.get("guest_name", ""),
        "check_in": ac_item.get("check_in") or parent_row.get("check_in", ""),
        "check_out": ac_item.get("check_out") or parent_row.get("check_out", ""),
        "nights": ac_item.get("nights") or parent_row.get("nights") or 0,
        "adults": parent_row.get("adults") or 0,
        "children": 0,
        "infants": 0,
        "source": "Airbnb",
        "status": "aircover",
        "is_cancelled": False,
        "is_aircover": True,
        "is_excluded": True,
        "aircover_details": ac_item.get("details", ""),
        "aircover_item_type": ac_item.get("item_type", ""),
        "listing_nickname": parent_row.get("listing_nickname", ""),
        "listing_id": parent_row.get("listing_id"),
        "confirmed_at": parent_row.get("check_in", ""),
        "cleaning_fee_eur": 0.0,
        "city_tax_eur": 0.0,
        "channel_commission_eur": 0.0,
        "payout_price_eur": amount_eur,
        "effective_payout_eur": amount_eur,
        "airbnb_batch_rate": float(ac_item.get("airbnb_rate") or 0.0),
        "airbnb_payout_date": ac_item.get("payout_date", ""),
        "batch_ref": ac_item.get("gref") or ac_item.get("batch_ref", ""),
        "batch_payout_date": ac_item.get("payout_date", ""),
        "batch_amount_czk": ac_item.get("batch_czk") or ac_item.get("amount_czk"),
    }


def _build_split_reservation(parent_row: dict, batch_info: dict, suffix: str = "__SP") -> dict:
    """
    Build a synthetic reservation for a manually split transaction.
    Represents an individual payout batch separated from the parent for
    independent month management. Cleaning, city tax, and balíčky are
    zeroed out to avoid double-counting (same as adjustments).
    """
    parent_code = parent_row.get("confirmation_code", "")
    source = parent_row.get("source", "")
    payout_eur = float(batch_info.get("payout_eur") or 0.0)
    return {
        "confirmation_code": f"{parent_code}{suffix}",
        "split_parent_code": parent_code,
        "guest_name": parent_row.get("guest_name", ""),
        "check_in": parent_row.get("check_in", ""),
        "check_out": parent_row.get("check_out", ""),
        "nights": parent_row.get("nights") or 0,
        "adults": parent_row.get("adults") or 0,
        "children": 0,
        "infants": 0,
        "source": source,
        "status": "split",
        "is_cancelled": False,
        "is_split_transaction": True,
        "listing_nickname": parent_row.get("listing_nickname", ""),
        "listing_id": parent_row.get("listing_id"),
        "confirmed_at": parent_row.get("check_in", ""),
        "cleaning_fee_eur": 0.0,
        "city_tax_eur": 0.0,
        "channel_commission_eur": float(batch_info.get("commission_eur") or 0.0),
        "payout_price_eur": payout_eur,
        "effective_payout_eur": payout_eur,
        "airbnb_batch_rate": float(batch_info.get("airbnb_rate") or 0.0),
        "airbnb_payout_date": batch_info.get("payout_date", ""),
        "batch_ref": batch_info.get("gref") or batch_info.get("batch_ref", ""),
        "batch_payout_date": batch_info.get("payout_date", ""),
        "batch_amount_czk": batch_info.get("payout_czk"),
    }


def _resolve_sources(conn, source_type: str) -> list:
    """Return active DB-backed source file records for the given type."""
    return get_active_source_files(conn, source_type)


def build_csv_cache(conn) -> dict:
    """
    Load and parse all CSV source data once. Return a cache dict that can
    be passed to generate_report_in_process() to avoid redundant I/O
    when generating multiple properties in a loop.
    """
    airbnb_csvs = _resolve_sources(conn, "airbnb")
    booking_csvs = _resolve_sources(conn, "booking")
    bank_csvs = _resolve_sources(conn, "bank")
    return {
        "airbnb_csvs": airbnb_csvs,
        "booking_csvs": booking_csvs,
        "bank_csvs": bank_csvs,
        "airbnb_index": load_airbnb_csv(airbnb_csvs) if airbnb_csvs else {},
        "booking_index": load_booking_csv(booking_csvs) if booking_csvs else {},
        "airbnb_payout_data": build_airbnb_payout_data(airbnb_csvs) if airbnb_csvs else {
            "reservation_map": {}, "all_batches_map": {}, "batches": [], "items": []
        },
        "booking_payout_data": build_booking_payout_data(booking_csvs) if booking_csvs else {
            "reservation_map": {}, "batches": [], "items": []
        },
        "bank_csvs_loaded": True,
        "bank_rows_all": load_bank_csv(bank_csvs) if bank_csvs else [],
        "booking_bank_idx_all": load_booking_bank_transactions(bank_csvs) if bank_csvs else {},
    }


def generate_report_in_process(
    conn,
    slug: str,
    year: int,
    month: int,
    config: dict,
    *,
    cutoff_day: int = 7,
    csv_cache: dict | None = None,
) -> dict:
    """
    Run a full report generation in-process (no subprocess, no Excel).

    Pass csv_cache (from build_csv_cache()) to avoid re-parsing CSV sources
    on every call — critical for bulk generation of many properties.

    Returns:
        {"rows_count": int, "status_counts": dict}           on success
        {"skipped": True, "reason": "locked"}                if month is locked
    """
    props = {p["slug"]: p for p in get_all_properties(config)}
    if slug not in props:
        raise ValueError(f"Unknown property slug: {slug}")
    prop = props[slug]

    # ── Check month lock ────────────────────────────────────────────────────
    month_state = get_report_month_state(conn, slug, year, month)
    if month_state.get("status") == MONTH_STATUS_LOCKED:
        mark_report_month_stale(conn, slug, year, month)
        log.info("Skipping locked month %s %d/%d", slug, month, year)
        return {"skipped": True, "reason": "locked"}

    # ── Clear stale rows for THIS month early, so get_report_row_by_code()
    #    in the adjustment logic below won't find leftover data from a
    #    previous (possibly buggy) generation of this same month.
    conn.execute(
        "DELETE FROM report_rows WHERE slug = ? AND year = ? AND month = ?",
        (slug, year, month),
    )

    # ── Cutoff date ─────────────────────────────────────────────────────────
    if month == 12:
        cutoff_year, cutoff_month_num = year + 1, 1
    else:
        cutoff_year, cutoff_month_num = year, month + 1
    _, last_day_cutoff = monthrange(cutoff_year, cutoff_month_num)
    cutoff_date = date_cls(
        cutoff_year, cutoff_month_num, min(cutoff_day, last_day_cutoff)
    )

    # ── Load CSV payout data (from cache or DB-backed source files) ──────────
    if csv_cache:
        airbnb_index = csv_cache["airbnb_index"]
        booking_index = csv_cache["booking_index"]
        airbnb_payout_data = csv_cache["airbnb_payout_data"]
        booking_payout_data = csv_cache["booking_payout_data"]
        bank_rows_all = csv_cache["bank_rows_all"]
        booking_bank_idx_all = csv_cache["booking_bank_idx_all"]
        bank_csvs = csv_cache["bank_csvs"]
    else:
        airbnb_csvs = _resolve_sources(conn, "airbnb")
        booking_csvs = _resolve_sources(conn, "booking")
        bank_csvs = _resolve_sources(conn, "bank")
        airbnb_index = load_airbnb_csv(airbnb_csvs) if airbnb_csvs else {}
        booking_index = load_booking_csv(booking_csvs) if booking_csvs else {}
        airbnb_payout_data = build_airbnb_payout_data(airbnb_csvs) if airbnb_csvs else {
            "reservation_map": {}, "all_batches_map": {}, "batches": [], "items": []
        }
        booking_payout_data = build_booking_payout_data(booking_csvs) if booking_csvs else {
            "reservation_map": {}, "batches": [], "items": []
        }
        bank_rows_all = load_bank_csv(bank_csvs) if bank_csvs else []
        booking_bank_idx_all = load_booking_bank_transactions(bank_csvs) if bank_csvs else {}

    gref_map = airbnb_payout_data["reservation_map"]
    airbnb_all_batches = airbnb_payout_data.get("all_batches_map", {})
    booking_batch_map = booking_payout_data["reservation_map"]

    # ── Bank data ───────────────────────────────────────────────────────────
    if bank_rows_all or bank_csvs:
        bank_rows = filter_bank_by_cutoff(bank_rows_all, cutoff_date)
        bank_index, bank_no_ref = build_bank_index(bank_rows)
        bank_index_full, bank_no_ref_full = build_bank_index(bank_rows_all)
        booking_bank_idx = {
            pid: [r for r in rows if r.get("datum") and r["datum"] <= cutoff_date]
            for pid, rows in booking_bank_idx_all.items()
        }
    else:
        bank_rows_all, bank_index, bank_no_ref = [], {}, []
        bank_index_full, bank_no_ref_full = {}, []
        booking_bank_idx, booking_bank_idx_all = {}, {}

    # ── Checkin groups ──────────────────────────────────────────────────────
    checkin_groups = hydrate_checkin_groups_from_db(
        list_checkin_reservations(
            conn=conn,
            active_only=True,
            overlap_year=year,
            overlap_month=month,
            latest_only=True,
        )
    )

    # ── CNB rates ───────────────────────────────────────────────────────────
    preload_rates_for_month(year, month, persist=True)

    # ── Hostify data from DB snapshot ───────────────────────────────────────
    booking_cfg = get_booking_config(prop, year=year, month=month)
    booking_nickname = booking_cfg.get("listing_nickname") or None
    hostify_nicknames = get_hostify_listing_names(prop, year=year, month=month)
    primary_nickname = hostify_nicknames[0] if hostify_nicknames else prop["listing_nickname"]
    extra_hostify_nicknames = [n for n in hostify_nicknames[1:] if n]

    listing_nicknames_all = [
        primary_nickname,
        *extra_hostify_nicknames,
        *([booking_nickname] if booking_nickname else []),
    ]
    all_raw = get_hostify_reservations_for_listing_month(
        conn,
        listing_nicknames=[n for n in listing_nicknames_all if n],
        year=year,
        month=month,
    )
    if all_raw:
        mark_report_month_has_data(conn, slug, year, month)

    reservations = filter_for_property_month(
        all_raw, primary_nickname, year, month,
        extra_nicknames=(
            extra_hostify_nicknames + ([booking_nickname] if booking_nickname else [])
        ) or None,
    )

    # ── Month assignments (month-scoped) ──────────────────────────────────────
    all_assignments = get_reservation_month_assignments(conn, slug)
    hidden_confirmation_codes: set[str] = set()

    # Codes whose MAIN reservation is moved OUT of this month
    codes_main_out: set[str] = set()
    # (code, gref) pairs for adjustments moved OUT of this month
    adj_grefs_out: set[tuple[str, str]] = set()
    # Codes for adjustments moved OUT (for hidden_confirmation_codes)
    codes_adj_out: set[str] = set()

    for asgn in all_assignments:
        if asgn["original_year"] == year and asgn["original_month"] == month:
            raw_code = asgn["confirmation_code"]
            if asgn.get("is_adjustment"):
                # Strip __ADJ/__ADJ2 suffix to match original code in all_batches_map
                base_code = re.sub(r"__(ADJ|SP)\d*$", "", raw_code)
                adj_grefs_out.add((base_code, asgn.get("batch_ref", "")))
                codes_adj_out.add(raw_code)
            else:
                codes_main_out.add(raw_code)

    hidden_confirmation_codes = codes_main_out | codes_adj_out

    # Filter main reservations moved out of this month
    if codes_main_out:
        reservations = [
            r for r in reservations
            if r["confirmation_code"] not in codes_main_out
        ]

    # Pull in main reservations moved INTO this month
    moved_in = get_codes_assigned_to_month(conn, slug, year, month)
    current_codes = {r["confirmation_code"] for r in reservations}
    # Codes for adjustments moved into this month (bypass date window later)
    adj_codes_in: set[str] = set()
    adj_grefs_in: set[tuple[str, str]] = set()
    for asgn in moved_in:
        raw_code = asgn["confirmation_code"]
        code = raw_code
        if asgn.get("is_adjustment"):
            # Strip __ADJ/__ADJ2 suffix to match original code in all_batches_map
            base_code = re.sub(r"__(ADJ|SP)\d*$", "", raw_code)
            adj_codes_in.add(base_code)
            adj_grefs_in.add((base_code, asgn.get("batch_ref", "")))
        elif code not in current_codes:
            # Pull main reservation from hostify_reservations
            row = conn.execute(
                "SELECT payload_json FROM hostify_reservations WHERE confirmation_code = ?",
                (code,),
            ).fetchone()
            if row:
                raw = json.loads(row["payload_json"])
                normalized = _normalize_reservation(raw)
                if normalized:
                    normalized["assigned_year"] = year
                    normalized["assigned_month"] = month
                    reservations.append(normalized)
                    log.info("Pulled in moved reservation %s for %s %d/%d", code, slug, month, year)

    # ── Exclusions ──────────────────────────────────────────────────────────
    excluded_codes = get_active_exclusions(conn, slug)
    for r in reservations:
        if r["confirmation_code"] in excluded_codes:
            r["is_excluded"] = True

    # ── Payout adjustments (cross-month codes) ──────────────────────────────
    month_start = date_cls(year, month, 1)
    current_codes = {r["confirmation_code"] for r in reservations}
    booking_pid = booking_cfg.get("property_id", "")
    after_prev_cutoff = date_cls(year, month, cutoff_day + 1)

    def _payout_date_in_window(payout_date_str: str) -> bool:
        """Check if payout date falls within the current month's adjustment window."""
        payout_date_str = (payout_date_str or "").strip()
        if not payout_date_str:
            return True  # no date info → include conservatively
        payout_dt = None
        for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                payout_dt = datetime_cls.strptime(payout_date_str, fmt).date()
                break
            except ValueError:
                pass
        if payout_dt is None:
            return True
        return after_prev_cutoff <= payout_dt <= cutoff_date

    # Collect all Airbnb codes with ANY batch in this month's window
    # adj_grefs_out: suppress adjustments moved OUT of this month
    # adj_grefs_in / adj_codes_in: pull in adjustments moved INTO this month
    #   (bypass date-window check for their batches)
    seen_adjustment_grefs: set[str] = set()
    adj_suffix_counter: dict[str, int] = {}  # code -> next suffix number
    def _next_adj_suffix(code: str) -> str:
        n = adj_suffix_counter.get(code, 0) + 1
        adj_suffix_counter[code] = n
        return "__ADJ" if n == 1 else f"__ADJ{n}"

    for code, batch_list in airbnb_all_batches.items():
        # Skip codes already in this month UNLESS they have adjustments moved here
        if code in current_codes and code not in adj_codes_in:
            continue
        past_row = get_report_row_by_code(conn, code)
        # If past_row not found in DB (cleared during this generation), try in-memory
        if past_row is None or past_row.get("slug") != slug:
            past_row = next(
                (r for r in reservations if r.get("confirmation_code") == code),
                None,
            )
        if past_row is None:
            continue
        # For codes already in current_codes (main reservation here), use current
        # reservation as past_row reference for building adjustment
        if code in current_codes:
            # Main reservation is here — only process moved-in adjustment grefs
            for batch_info in batch_list:
                gref = batch_info.get("gref", "")
                if gref in seen_adjustment_grefs:
                    continue
                if (code, gref) in adj_grefs_out:
                    continue
                if (code, gref) not in adj_grefs_in:
                    continue
                seen_adjustment_grefs.add(gref)
                reservations.append(_build_adjustment_reservation(past_row, batch_info, suffix=_next_adj_suffix(code)))
            continue
        if past_row.get("year") == year and past_row.get("month") == month:
            continue
        # Only adjustments from past months (not future reservations with early payouts)
        # But allow codes moved into this month regardless of chronology
        if code not in adj_codes_in and (past_row.get("year"), past_row.get("month")) > (year, month):
            continue
        for batch_info in batch_list:
            gref = batch_info.get("gref", "")
            if gref in seen_adjustment_grefs:
                continue
            # Skip if this specific gref was moved OUT of this month
            if (code, gref) in adj_grefs_out:
                continue
            # For codes moved IN: bypass date window; otherwise enforce it
            if code in adj_codes_in:
                # Only include the specific gref(s) that were moved here
                if adj_grefs_in and (code, gref) not in adj_grefs_in:
                    if not _payout_date_in_window(batch_info.get("payout_date", "")):
                        continue
            elif not _payout_date_in_window(batch_info.get("payout_date", "")):
                continue
            seen_adjustment_grefs.add(gref)
            reservations.append(_build_adjustment_reservation(past_row, batch_info, suffix=_next_adj_suffix(code)))

    # Booking adjustments (single batch per code)
    for code, pinfo in booking_batch_map.items():
        if code in current_codes:
            continue
        if pinfo.get("property_id", "") != booking_pid:
            continue
        past_row = get_report_row_by_code(conn, code)
        if past_row is None or past_row.get("slug") != slug:
            continue
        if past_row.get("year") == year and past_row.get("month") == month:
            continue
        if code not in adj_codes_in and (past_row.get("year"), past_row.get("month")) > (year, month):
            continue
        bref = pinfo.get("gref", "") or pinfo.get("batch_ref", "")
        if (code, bref) in adj_grefs_out:
            continue
        if code not in adj_codes_in and not _payout_date_in_window(pinfo.get("payout_date", "")):
            continue
        reservations.append(_build_adjustment_reservation(past_row, pinfo, suffix=_next_adj_suffix(code)))

    # Fallback: codes in gref_map but not in all_batches (shouldn't happen, but safe)
    for code in gref_map:
        if code in current_codes or code in airbnb_all_batches:
            continue
        past_row = get_report_row_by_code(conn, code)
        if past_row is None or past_row.get("slug") != slug:
            continue
        if past_row.get("year") == year and past_row.get("month") == month:
            continue
        if code not in adj_codes_in and (past_row.get("year"), past_row.get("month")) > (year, month):
            continue
        batch_info = gref_map[code]
        bref = batch_info.get("gref", "") or batch_info.get("batch_ref", "")
        if (code, bref) in adj_grefs_out:
            continue
        if code not in adj_codes_in and not _payout_date_in_window(batch_info.get("payout_date", "")):
            continue
        reservations.append(_build_adjustment_reservation(past_row, batch_info, suffix=_next_adj_suffix(code)))

    # ── AirCover items (separate compensation rows) ──────────────────────────
    aircover_map = airbnb_payout_data.get("aircover_map", {})
    for code, ac_items in aircover_map.items():
        # Find parent reservation — either in this month or a past month
        parent_row = None
        if code in current_codes:
            parent_row = next((r for r in reservations if r.get("confirmation_code") == code and not r.get("is_aircover")), None)
        if parent_row is None:
            db_row = get_report_row_by_code(conn, code)
            if db_row is not None and db_row.get("slug") == slug:
                parent_row = db_row
        if parent_row is None:
            continue
        ac_count = 0
        for ac_item in ac_items:
            if not _payout_date_in_window(ac_item.get("payout_date", "")):
                continue
            ac_count += 1
            suffix = "__AC" if ac_count == 1 else f"__AC{ac_count}"
            reservations.append(_build_aircover_reservation(parent_row, ac_item, suffix=suffix))
            log.info("AirCover item for %s: %.2f EUR (%s)",
                     code, ac_item.get("amount_eur", 0), ac_item.get("details", ""))

    # ── Split transaction rows ─────────────────────────────────────────────
    split_records = get_split_transactions(conn, slug)
    splits_by_code: dict[str, list[dict]] = {}
    for sr in split_records:
        splits_by_code.setdefault(sr["confirmation_code"], []).append(sr)

    split_batch_refs_by_code: dict[str, set[str]] = {}
    for code, splits in splits_by_code.items():
        parent_res = next(
            (r for r in reservations if r.get("confirmation_code") == code),
            None,
        )
        if parent_res is None:
            continue
        all_batches = airbnb_all_batches.get(code, [])
        if not all_batches:
            continue
        sp_count = 0
        for sr in splits:
            batch_ref = sr["batch_ref"]
            batch_info = next(
                (b for b in all_batches if b.get("gref", "") == batch_ref),
                None,
            )
            if batch_info is None:
                continue
            sp_count += 1
            suffix = "__SP" if sp_count == 1 else f"__SP{sp_count}"
            reservations.append(_build_split_reservation(parent_res, batch_info, suffix=suffix))
            split_batch_refs_by_code.setdefault(code, set()).add(batch_ref)
            log.info(
                "Split transaction for %s: batch %s, %.2f EUR",
                code, batch_ref, batch_info.get("payout_eur", 0),
            )

    # ── Verify against CSV ──────────────────────────────────────────────────
    booking_codes = [
        code for code, row in booking_index.items()
        if row.get("property_id", "").strip() == booking_pid
    ]
    late_hostify_lookup = get_hostify_reservations_by_codes(
        conn,
        booking_codes,
        listing_nicknames=[n for n in listing_nicknames_all if n],
        year=year,
        month=month,
    )
    verified, csv_only = build_verification_index(
        reservations, airbnb_index, booking_index, prop,
        hostify_lookup=late_hostify_lookup,
        year=year, month=month,
        hidden_confirmation_codes=hidden_confirmation_codes,
    )
    all_verified = verified + csv_only

    # ── Checkin city-tax overrides ──────────────────────────────────────────
    checkin_stats: dict = {}
    if checkin_groups:
        all_verified, checkin_stats = apply_checkin_city_tax_overrides(
            all_verified, checkin_groups, prop, year=year, month=month
        )
    replace_checkin_match_audit(
        conn, slug, year, month,
        checkin_stats.get("audit_records", []) if checkin_groups else [],
    )

    # ── Attach Airbnb/Booking batch rate ────────────────────────────────────
    for row in all_verified:
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()
        # Payout adjustments / AirCover already have their own batch info — don't overwrite
        if row.get("is_payout_adjustment"):
            continue
        if row.get("is_aircover"):
            continue
        if row.get("is_split_transaction"):
            continue
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
            row["batch_payout_date"] = (
                pinfo.get("payout_date", "") or row.get("booking_payout_date", "")
            )
            row["batch_amount_czk"] = pinfo.get("payout_czk")
            row["batch_rate"] = pinfo.get("booking_batch_rate")

    # ── Split payout: limit effective_payout_eur to batches within window ──
    for row in all_verified:
        if row.get("is_payout_adjustment"):
            continue
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()
        if "airbnb" not in source:
            continue
        batches = airbnb_all_batches.get(code, [])
        if len(batches) <= 1:
            continue
        # Sum only batches with payout_date within this month's window
        window_eur = 0.0
        for b in batches:
            payout_date_str = (b.get("payout_date") or "").strip()
            payout_dt = None
            for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
                try:
                    payout_dt = datetime_cls.strptime(payout_date_str, fmt).date()
                    break
                except ValueError:
                    pass
            if payout_dt is not None and payout_dt <= cutoff_date:
                window_eur += b.get("payout_eur", 0.0)
        if window_eur > 0:
            row["effective_payout_eur"] = window_eur
            log.debug(
                "Split payout %s: limited to %.2f EUR (full=%.2f EUR)",
                code, window_eur, sum(b.get("payout_eur", 0) for b in batches),
            )

    # ── Reduce parent effective_payout_eur by split transaction amounts ────
    for row in all_verified:
        code = row.get("confirmation_code", "")
        if code not in split_batch_refs_by_code:
            continue
        if row.get("is_split_transaction") or row.get("is_payout_adjustment") or row.get("is_aircover"):
            continue
        split_refs = split_batch_refs_by_code[code]
        batches = airbnb_all_batches.get(code, [])
        split_eur = sum(
            b.get("payout_eur", 0.0) for b in batches
            if b.get("gref", "") in split_refs
        )
        if split_eur > 0:
            current = float(row.get("effective_payout_eur") or 0.0)
            row["effective_payout_eur"] = max(current - split_eur, 0.0)
            log.info(
                "Split deduction for %s: -%.2f EUR (new effective: %.2f EUR)",
                code, split_eur, row["effective_payout_eur"],
            )

    # ── CNB rates per reservation ───────────────────────────────────────────
    rate_cache: dict = {}
    for res in all_verified:
        confirmed_at = res.get("confirmed_at") or res.get("check_in") or ""
        rate_date = confirmed_at[:10]
        if rate_date and rate_date not in rate_cache:
            try:
                rate_cache[rate_date] = get_rate_for_reservation(confirmed_at, persist=True)
            except CnbRateError:
                rate_cache[rate_date] = {"rate": 0, "valid_for": rate_date}

    # ── Calculate ───────────────────────────────────────────────────────────
    calc_rows = calculate_all_rows(all_verified, rate_cache, prop)
    calc_rows = apply_overrides_to_rows(conn, calc_rows, slug, year, month)

    # ── Bank enrichment ─────────────────────────────────────────────────────
    calc_rows, airbnb_matches = enrich_rows_with_bank(
        calc_rows, gref_map, bank_index, bank_no_ref,
        all_batches_map=airbnb_all_batches,
        bank_index_full=bank_index_full,
        bank_no_ref_full=bank_no_ref_full,
    )
    calc_rows, booking_matches = enrich_booking_rows_with_bank(
        calc_rows, booking_bank_idx, prop, year=year, month=month,
        booking_bank_idx_all=booking_bank_idx_all,
    )
    save_payout_batch_bank_matches(conn, "airbnb", airbnb_matches)
    save_payout_batch_bank_matches(conn, "booking", booking_matches)

    # ── Booking CHYBÍ_V_HOSTIFY → KE_KONTROLE ──────────────────────────────
    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if (
            "booking" in source
            and row.get("verification_status") == STATUS_CHYBI_HOSTIFY
            and row.get("bank_status") == "CHYBÍ"
        ):
            row["verification_status"] = STATUS_KE_KONTROLE

    # ── Pending payments ────────────────────────────────────────────────────
    prev_pending = [
        p for p in get_pending_payments(conn, slug, status="PENDING")
        if p["original_year"] < year
        or (p["original_year"] == year and p["original_month"] < month)
    ]
    if prev_pending:
        bank_index_full, bank_no_ref_full = build_bank_index(bank_rows_all)
        resolved, _ = resolve_pending_against_bank(
            prev_pending, bank_index_full, bank_no_ref_full,
            booking_bank_idx_all, cutoff_date, booking_pid,
        )
        for r in resolved:
            resolve_pending_payment(
                conn, slug, r["confirmation_code"],
                year, month, r["bank_datum"], r["bank_amount_czk"],
            )
            if r.get("original_year") != year or r.get("original_month") != month:
                mark_report_month_stale(
                    conn, slug, int(r["original_year"]), int(r["original_month"])
                )

    chybi_rows = [r for r in calc_rows if r.get("bank_status") == "CHYBÍ"]
    save_pending_payments(conn, slug, year, month, chybi_rows)

    # ── Persist ─────────────────────────────────────────────────────────────
    save_report_rows(conn, slug, year, month, calc_rows)
    log_report_generated(conn, slug, year, month, "", calc_rows)  # no Excel path
    touch_report_month_generation(conn, slug, year, month)

    status_counts = dict(Counter(r.get("verification_status", "") for r in calc_rows))
    log.info(
        "Generated %s %d/%d: %d rows %s",
        slug, month, year, len(calc_rows), status_counts,
    )
    return {"rows_count": len(calc_rows), "status_counts": status_counts}


def run_generation_background(
    db_path: str,
    slug: str,
    year: int,
    month: int,
    config: dict,
) -> None:
    """
    Wrapper for use with FastAPI BackgroundTasks.
    Opens its own DB connection (background tasks run after the request connection closes).
    """
    from report.db import get_connection
    conn = get_connection(db_path)
    try:
        generate_report_in_process(conn, slug, year, month, config)
    except Exception as exc:
        log.error(
            "Background generation failed for %s %d/%d: %s", slug, month, year, exc,
            exc_info=True,
        )
    finally:
        conn.close()
