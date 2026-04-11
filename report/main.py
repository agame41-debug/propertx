"""
report/main.py — CLI entry point for the Rentero property financial report system.

Usage:
    python -m report.main --year 2026 --month 3 [options]

Options:
    --year INT           Year (e.g. 2026)
    --month INT          Month (1-12)
    --property SLUG      Process only this property slug (repeatable)
    --airbnb-csv PATH    Explicit Airbnb CSV file path (repeatable)
    --booking-csv PATH   Explicit Booking CSV file path (repeatable)
    --output-dir PATH    Output directory [default: output/reports/]
    --config PATH        Path to properties.json [default: config/properties.json]
    --overwrite          Overwrite existing output files
    --dry-run            Print raw Hostify data and exit, no files written
    --verbose            Extra logging
"""

# Load .env at module import time
import os
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass  # dotenv not installed, skip

import argparse
import inspect
import logging
import sys
from calendar import monthrange
from datetime import date as date_cls, datetime as datetime_cls

# Ensure project root is on sys.path when run as `python -m report.main`
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from report.config import (
    get_all_properties,
    get_booking_config,
    get_hostify_listing_names,
    load_runtime_config,
)
from report.checkin import apply_checkin_city_tax_overrides, hydrate_checkin_groups_from_db, load_checkin_groups
from report.cnb import preload_rates_for_month, get_rate_for_reservation, CnbRateError
from report.loader import (fetch_raw_reservations_for_period, filter_for_property_month,
                           normalize_reservations, HostifyFetchIncompleteError)
from report.verifier import (
    load_airbnb_csv,
    load_booking_csv,
    build_verification_index,
    build_airbnb_payout_data,
    build_booking_payout_data,
    STATUS_CHYBI_HOSTIFY,
    STATUS_KE_KONTROLE,
)
from report.calculator import calculate_all_rows, calculate_totals_with_config
from report.bank import (load_bank_csv, build_bank_index, enrich_rows_with_bank,
                         load_booking_bank_transactions, enrich_booking_rows_with_bank,
                         filter_bank_by_cutoff, resolve_pending_against_bank)
from report.db import (get_connection, log_report_generated, save_report_rows,
                       save_pending_payments, get_pending_payments, resolve_pending_payment,
                       get_active_source_files, save_payout_batches, save_payout_batch_items,
                       save_bank_transactions, save_payout_batch_bank_matches,
                       fill_missing_payout_item_guest_names,
                       save_hostify_reservations, get_hostify_reservations_by_codes,
                       list_checkin_reservations, replace_checkin_match_audit,
                       get_expenses, get_report_month_state, touch_report_month_generation,
                       mark_report_month_has_data, mark_report_month_stale,
                       apply_overrides_to_rows,
                       MONTH_STATUS_LOCKED,
                       get_reservation_month_assignments, get_codes_assigned_to_month,
                       get_active_exclusions, get_report_row_by_code)
from report.excel import write_property_report
# db imports merged above


def _get_active_properties(config: dict) -> list[dict]:
    try:
        signature = inspect.signature(get_all_properties)
    except (TypeError, ValueError):
        signature = None
    if signature and "active_only" in signature.parameters:
        return get_all_properties(config, active_only=True)
    return get_all_properties(config)


def _build_adjustment_reservation(past_row: dict, batch_info: dict) -> dict:
    """
    Build a synthetic reservation dict for a payout adjustment.
    Represents money arriving this month for a reservation from a past month.
    Cleaning, city tax, and balíčky are zeroed out to avoid double-counting.
    """
    source = past_row.get("source", "")
    payout_eur = float(
        batch_info.get("payout_eur")
        or batch_info.get("payout_czk", 0) / max(float(batch_info.get("airbnb_rate") or 25.0), 1.0)
        or 0.0
    )
    return {
        "confirmation_code": past_row.get("confirmation_code", ""),
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


def _auto_find_csvs(source_dir: str, subdir: str, extensions: tuple) -> list[str]:
    """Auto-discover CSV files in source/airbnb/ or source/booking/."""
    path = os.path.join(source_dir, subdir)
    if not os.path.isdir(path):
        return []
    return [
        os.path.join(path, f)
        for f in os.listdir(path)
        if f.lower().endswith(extensions) and not f.startswith("~")
    ]


def _source_label(source) -> str:
    if isinstance(source, str):
        return os.path.basename(source)
    return str(source.get("original_name") or f"db:{source.get('id', '?')}")


def _resolve_sources(
    db_conn,
    args_paths: list[str] | None,
    source_dir: str,
    subdir: str,
    extensions: tuple,
    source_type: str,
    *,
    legacy_autodiscover: bool,
) -> list:
    """Priority: explicit CLI paths -> active DB sources -> legacy folder scan."""
    if args_paths:
        return args_paths
    if db_conn is not None:
        db_sources = get_active_source_files(db_conn, source_type)
        if db_sources:
            return db_sources
    if legacy_autodiscover:
        return _auto_find_csvs(source_dir, subdir, extensions)
    return []


def main():
    parser = argparse.ArgumentParser(
        description="Rentero Property Financial Report Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--year", type=int, required=True, help="Year (e.g. 2026)")
    parser.add_argument("--month", type=int, required=True, help="Month 1-12")
    parser.add_argument(
        "--property", dest="properties", action="append", metavar="SLUG",
        help="Process only this property slug (repeatable). Default: all."
    )
    parser.add_argument(
        "--airbnb-csv", dest="airbnb_csvs", action="append", metavar="PATH",
        help="Airbnb CSV file path (repeatable). Default: active files from SQLite source registry."
    )
    parser.add_argument(
        "--booking-csv", dest="booking_csvs", action="append", metavar="PATH",
        help="Booking CSV file path (repeatable). Default: active files from SQLite source registry."
    )
    parser.add_argument(
        "--bank-csv", dest="bank_csvs", action="append", metavar="PATH",
        help="Bank CSV file path (repeatable). Default: active files from SQLite source registry."
    )
    parser.add_argument(
        "--output-dir", default=os.path.join(_PROJECT_ROOT, "output", "reports"),
        help="Output directory [default: output/reports/]"
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to properties.json [default: config/properties.json]"
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", help="Print Hostify data only, no files")
    parser.add_argument("--verbose", action="store_true", help="Extra logging")
    parser.add_argument(
        "--legacy-autodiscover", action="store_true",
        help="Fallback to scanning source/* directories when no active DB-backed source files exist."
    )
    parser.add_argument(
        "--cutoff-day", type=int, default=7, metavar="N",
        help="Day of the month AFTER the report month used as payment cutoff [default: 7]. "
             "E.g. for March report: cutoff = April N. "
             "Payments arriving after cutoff are deferred to the next month's report."
    )

    args = parser.parse_args()

    # Logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s  %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("report.main")

    # Validate args
    if not (1 <= args.month <= 12):
        print(f"ERROR: --month must be 1-12, got {args.month}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------ #
    #  Step 0: Init SQLite DB                                            #
    # ------------------------------------------------------------------ #
    try:
        db_conn = get_connection()
        log.debug("SQLite database ready: cache/rentero.db")
    except Exception as e:
        log.warning("SQLite unavailable: %s. Running without DB-backed sources/cache.", e)
        db_conn = None

    persist_db = db_conn is not None and not args.dry_run

    # Load config (DB-first with JSON fallback)
    try:
        config = load_runtime_config(args.config, db_conn=db_conn)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine which properties to process
    all_props = _get_active_properties(config)
    if args.properties:
        props = [p for p in all_props if p["slug"] in args.properties]
        missing = set(args.properties) - {p["slug"] for p in props}
        if missing:
            print(f"ERROR: Unknown property slug(s): {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)
    else:
        props = all_props

    if not props:
        print("ERROR: No properties to process.", file=sys.stderr)
        sys.exit(1)

    year, month = args.year, args.month
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Rentero Report Generator", file=sys.stderr)
    print(f"  Period: {month:02d}/{year}", file=sys.stderr)
    print(f"  Properties: {len(props)}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # ------------------------------------------------------------------ #
    #  Step 1: Load CSV verification data (once for all properties)       #
    # ------------------------------------------------------------------ #
    source_dir = os.path.join(_PROJECT_ROOT, "source")

    airbnb_csvs = _resolve_sources(
        db_conn, args.airbnb_csvs, source_dir, "airbnb", (".csv",), "airbnb",
        legacy_autodiscover=args.legacy_autodiscover,
    )
    booking_csvs = _resolve_sources(
        db_conn, args.booking_csvs, source_dir, "booking", (".csv",), "booking",
        legacy_autodiscover=args.legacy_autodiscover,
    )

    if airbnb_csvs:
        log.info("Airbnb CSV files: %s", [_source_label(f) for f in airbnb_csvs])
    else:
        log.warning("No Airbnb source files found.")

    if booking_csvs:
        log.info("Booking CSV files: %s", [_source_label(f) for f in booking_csvs])
    else:
        log.warning("No Booking source files found.")

    airbnb_index = load_airbnb_csv(airbnb_csvs) if airbnb_csvs else {}
    booking_index = load_booking_csv(booking_csvs) if booking_csvs else {}

    airbnb_payout_data = build_airbnb_payout_data(airbnb_csvs) if airbnb_csvs else {
        "reservation_map": {}, "all_batches_map": {}, "batches": [], "items": []
    }
    booking_payout_data = build_booking_payout_data(booking_csvs) if booking_csvs else {
        "reservation_map": {}, "batches": [], "items": []
    }

    # confirmation_code -> batch info
    gref_map = airbnb_payout_data["reservation_map"]
    airbnb_all_batches = airbnb_payout_data.get("all_batches_map", {})
    booking_batch_map = booking_payout_data["reservation_map"]

    if persist_db:
        save_payout_batches(db_conn, "airbnb", airbnb_payout_data["batches"])
        save_payout_batch_items(db_conn, "airbnb", airbnb_payout_data["items"])
        save_payout_batches(db_conn, "booking", booking_payout_data["batches"])
        save_payout_batch_items(db_conn, "booking", booking_payout_data["items"])
        if hasattr(db_conn, "execute"):
            fill_missing_payout_item_guest_names(
                db_conn,
                "booking",
                guest_names_by_code={
                    str(code): str(row.get("guest_name") or "").strip()
                    for code, row in booking_index.items()
                    if str(code or "").strip() and str(row.get("guest_name") or "").strip()
                },
            )

    # Bank CSV — auto-discover source/bank/*.csv if not provided
    bank_csvs = _resolve_sources(
        db_conn, args.bank_csvs, source_dir, "bank", (".csv",), "bank",
        legacy_autodiscover=args.legacy_autodiscover,
    )
    checkin_sources = get_active_source_files(db_conn, "checkin") if db_conn else []
    # ------------------------------------------------------------------ #
    #  Cutoff date: Nth day of the month AFTER the report month           #
    #  Example: March 2026 report → cutoff = April 7, 2026               #
    # ------------------------------------------------------------------ #
    cutoff_day = args.cutoff_day
    if month == 12:
        cutoff_year, cutoff_month_num = year + 1, 1
    else:
        cutoff_year, cutoff_month_num = year, month + 1
    _, last_day_cutoff = monthrange(cutoff_year, cutoff_month_num)
    cutoff_date = date_cls(cutoff_year, cutoff_month_num, min(cutoff_day, last_day_cutoff))
    log.info("Payment cutoff: %s (day %d of following month)", cutoff_date.isoformat(), cutoff_day)

    if bank_csvs:
        log.info("Bank CSV files: %s", [_source_label(f) for f in bank_csvs])
        bank_rows_all = load_bank_csv(bank_csvs)
        # Filter to cutoff: only transactions that arrived by cutoff date count for this month
        bank_rows = filter_bank_by_cutoff(bank_rows_all, cutoff_date)
        bank_index, bank_no_ref = build_bank_index(bank_rows)
        log.info("Bank: %d transactions up to cutoff (%d total), %d G-ref, %d no-ref",
                 len(bank_rows), len(bank_rows_all), len(bank_index), len(bank_no_ref))
        booking_bank_idx_all = load_booking_bank_transactions(bank_csvs)
        booking_bank_idx = {
            pid: [r for r in rows if r.get("datum") and r["datum"] <= cutoff_date]
            for pid, rows in booking_bank_idx_all.items()
        }
        if persist_db:
            save_bank_transactions(db_conn, "airbnb", bank_rows_all)
            booking_bank_rows_all = [item for rows in booking_bank_idx_all.values() for item in rows]
            save_bank_transactions(db_conn, "booking", booking_bank_rows_all)
    else:
        log.warning("No bank CSV files found. Bank reconciliation will be skipped.")
        bank_rows_all = []
        bank_index, bank_no_ref = {}, []
        booking_bank_idx = {}
        booking_bank_idx_all = {}
    if checkin_sources:
        log.info("Evidence hostů source files: %s", [_source_label(f) for f in checkin_sources])
    if db_conn and hasattr(db_conn, "execute"):
        checkin_groups = hydrate_checkin_groups_from_db(
            list_checkin_reservations(
                conn=db_conn,
                active_only=True,
                overlap_year=year,
                overlap_month=month,
                latest_only=True,
            )
        )
    else:
        checkin_groups = load_checkin_groups(checkin_sources) if checkin_sources else []

    # ------------------------------------------------------------------ #
    #  Step 2: Warm CNB rate cache                                        #
    # ------------------------------------------------------------------ #
    try:
        cnb_rates = preload_rates_for_month(year, month, persist=not args.dry_run)
        log.info("CNB rates loaded: %d days in %d-%02d", len(cnb_rates), year, month)
    except CnbRateError as e:
        print(f"ERROR: CNB API unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------ #
    #  Step 3: Fetch Hostify reservations (one call for all properties)   #
    # ------------------------------------------------------------------ #
    log.info("Fetching Hostify reservations for %d-%02d...", year, month)
    try:
        all_raw = fetch_raw_reservations_for_period(
            year, month, use_cache=not args.dry_run, db_conn=db_conn
        )
    except HostifyFetchIncompleteError as e:
        print(
            f"\nERROR: Hostify fetch incomplete for {year}-{month:02d}.\n"
            f"  Window:   {e.from_date} → {e.to_date}\n"
            f"  Expected: {e.total} reservations\n"
            f"  Fetched:  {e.fetched} reservations\n"
            "  Report generation stopped — no files written, no data persisted.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to fetch Hostify data: {e}", file=sys.stderr)
        sys.exit(1)

    # Persist normalized snapshots only after a confirmed complete fetch
    if persist_db:
        try:
            save_hostify_reservations(db_conn, normalize_reservations(all_raw))
            if hasattr(db_conn, "execute"):
                fill_missing_payout_item_guest_names(db_conn, "booking")
        except Exception as e:
            log.warning("Could not persist Hostify reservation snapshots: %s", e)

    # ------------------------------------------------------------------ #
    #  Step 4: Process each property                                      #
    # ------------------------------------------------------------------ #
    success_count = 0
    error_count = 0

    for prop in props:
        slug = prop["slug"]
        nickname = prop["listing_nickname"]
        print(f"  Processing: {slug} ({nickname})", file=sys.stderr)

        try:
            # 4a. Filter reservations for this property (incl. Booking sub-listing)
            booking_cfg = get_booking_config(prop, year=year, month=month)
            booking_nickname = booking_cfg.get("listing_nickname") or None
            hostify_nicknames = get_hostify_listing_names(prop, year=year, month=month)
            primary_nickname = hostify_nicknames[0] if hostify_nicknames else nickname
            extra_hostify_nicknames = [n for n in hostify_nicknames[1:] if n]
            reservations = filter_for_property_month(
                all_raw, primary_nickname, year, month,
                extra_nicknames=(
                    extra_hostify_nicknames + ([booking_nickname] if booking_nickname else [])
                ) or None,
            )
            log.info("  %d reservations found", len(reservations))
            if persist_db and reservations:
                mark_report_month_has_data(db_conn, slug, year, month)

            if persist_db:
                month_state = get_report_month_state(db_conn, slug, year, month)
                if month_state.get("status") == MONTH_STATUS_LOCKED:
                    if reservations:
                        mark_report_month_stale(db_conn, slug, year, month)
                    print(
                        f"    SKIP: month {month:02d}/{year} is LOCKED for {slug}. Unlock it before generation.",
                        file=sys.stderr,
                    )
                    error_count += 1
                    continue

            if not reservations:
                log.warning("  No reservations for %s in %d-%02d", slug, year, month)

            # ── Apply manual month assignments ─────────────────────────────
            hidden_confirmation_codes: set[str] = set()
            if persist_db:
                assignments = get_reservation_month_assignments(db_conn, slug)
                if assignments:
                    hidden_confirmation_codes = {
                        code
                        for code, assignment in assignments.items()
                        if assignment["target_year"] != year or assignment["target_month"] != month
                    }
                    reservations = [
                        r for r in reservations
                        if r["confirmation_code"] not in assignments
                        or (
                            assignments[r["confirmation_code"]]["target_year"] == year
                            and assignments[r["confirmation_code"]]["target_month"] == month
                        )
                    ]
                    codes_moved_in = get_codes_assigned_to_month(db_conn, slug, year, month)
                    current_codes = {r["confirmation_code"] for r in reservations}
                    for code in codes_moved_in - current_codes:
                        matched = [
                            r for r in all_raw
                            if str(r.get("channel_reservation_id") or r.get("confirmation_code") or "") == code
                        ]
                        if matched:
                            from report.loader import _normalize_reservation
                            normalized = _normalize_reservation(matched[0])
                            if normalized:
                                normalized["assigned_year"] = year
                                normalized["assigned_month"] = month
                                reservations.append(normalized)
                                log.info("  Pulled in moved reservation %s from month assignment", code)

            # ── Apply exclusions ───────────────────────────────────────────
            if persist_db:
                excluded_codes = get_active_exclusions(db_conn, slug)
                if excluded_codes:
                    for r in reservations:
                        if r["confirmation_code"] in excluded_codes:
                            r["is_excluded"] = True
                    log.info(
                        "  %d reservation(s) marked as excluded",
                        sum(1 for r in reservations if r.get("is_excluded")),
                    )

            # ── Clear stale rows for THIS month early, so get_report_row_by_code()
            #    in the adjustment logic below won't find leftover data from a
            #    previous (possibly buggy) generation of this same month.
            if persist_db:
                db_conn.execute(
                    "DELETE FROM report_rows WHERE slug = ? AND year = ? AND month = ?",
                    (slug, year, month),
                )

            # ── Detect payout adjustments for cross-month codes ────────────
            if persist_db:
                current_codes = {r["confirmation_code"] for r in reservations}
                month_start = date_cls(year, month, 1)
                after_prev_cutoff = date_cls(year, month, cutoff_day + 1)
                booking_pid = booking_cfg.get("property_id", "")

                def _payout_date_in_window(payout_date_str: str) -> bool:
                    payout_date_str = (payout_date_str or "").strip()
                    if not payout_date_str:
                        return True
                    payout_dt = None
                    for _fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
                        try:
                            payout_dt = datetime_cls.strptime(payout_date_str, _fmt).date()
                            break
                        except ValueError:
                            pass
                    if payout_dt is None:
                        return True
                    return after_prev_cutoff <= payout_dt <= cutoff_date

                # Airbnb: iterate all batches (not just gref_map which has only one per code)
                seen_adjustment_grefs: set[str] = set()
                for code, batch_list in airbnb_all_batches.items():
                    if code in current_codes:
                        continue
                    past_row = get_report_row_by_code(db_conn, code)
                    if past_row is None or past_row.get("slug") != slug:
                        continue
                    if past_row.get("year") == year and past_row.get("month") == month:
                        continue
                    if (past_row.get("year"), past_row.get("month")) > (year, month):
                        continue
                    for batch_info in batch_list:
                        gref = batch_info.get("gref", "")
                        if gref in seen_adjustment_grefs:
                            continue
                        if not _payout_date_in_window(batch_info.get("payout_date", "")):
                            continue
                        seen_adjustment_grefs.add(gref)
                        reservations.append(_build_adjustment_reservation(past_row, batch_info))
                        log.info(
                            "  Payout adjustment detected: %s gref=%s (originally %d-%02d)",
                            code, gref, past_row.get("year", 0), past_row.get("month", 0),
                        )

                # Booking adjustments (single batch per code)
                for code, pinfo in booking_batch_map.items():
                    if code in current_codes:
                        continue
                    if pinfo.get("property_id", "") != booking_pid:
                        continue
                    past_row = get_report_row_by_code(db_conn, code)
                    if past_row is None or past_row.get("slug") != slug:
                        continue
                    if past_row.get("year") == year and past_row.get("month") == month:
                        continue
                    if (past_row.get("year"), past_row.get("month")) > (year, month):
                        continue
                    if not _payout_date_in_window(pinfo.get("payout_date", "")):
                        continue
                    reservations.append(_build_adjustment_reservation(past_row, pinfo))
                    log.info(
                        "  Payout adjustment detected: %s (originally %d-%02d)",
                        code, past_row.get("year", 0), past_row.get("month", 0),
                    )

            # 4b. Verify against CSV
            late_hostify_lookup = {}
            if db_conn:
                booking_pid = booking_cfg.get("property_id", "")
                booking_codes = [
                    code for code, row in booking_index.items()
                    if row.get("property_id", "").strip() == booking_pid
                ]
                listing_nicknames = [
                    *hostify_nicknames,
                    booking_nickname,
                ]
                late_hostify_lookup = get_hostify_reservations_by_codes(
                    db_conn,
                    booking_codes,
                    listing_nicknames=[n for n in listing_nicknames if n],
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
            if checkin_groups:
                all_verified, checkin_stats = apply_checkin_city_tax_overrides(
                    all_verified,
                    checkin_groups,
                    prop,
                    year=year,
                    month=month,
                )
                if checkin_stats["matched"] or checkin_stats["ambiguous_buckets"]:
                    log.info(
                        "  Checkin city-tax overlay: matched=%d unmatched_groups=%d ambiguous_buckets=%d",
                        checkin_stats["matched"],
                        checkin_stats["unmatched_groups"],
                        checkin_stats["ambiguous_buckets"],
                    )
            if persist_db:
                replace_checkin_match_audit(
                    db_conn,
                    slug,
                    year,
                    month,
                    checkin_stats.get("audit_records", []) if checkin_groups else [],
                )

            # 4b+. Attach Airbnb batch rate to each Airbnb row
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

            # 4b++. Split payout: limit effective_payout_eur to batches within window
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

            # 4c. Fetch CNB rates for each reservation's confirmed_at date
            rate_cache: dict[str, dict] = {}
            for res in all_verified:
                confirmed_at = res.get("confirmed_at") or res.get("check_in") or ""
                rate_date = confirmed_at[:10]
                if rate_date and rate_date not in rate_cache:
                    try:
                        rate_cache[rate_date] = get_rate_for_reservation(
                            confirmed_at,
                            persist=not args.dry_run,
                        )
                    except CnbRateError as e:
                        log.warning("    CNB rate unavailable for %s: %s", rate_date, e)
                        rate_cache[rate_date] = {"rate": 0, "valid_for": rate_date}

            # 4d. Calculate
            calc_rows = calculate_all_rows(all_verified, rate_cache, prop)

            # 4e. Enrich with bank arrival data (Airbnb + Booking, cutoff-filtered)
            calc_rows, airbnb_batch_matches = enrich_rows_with_bank(
                calc_rows, gref_map, bank_index, bank_no_ref
            )
            calc_rows, booking_batch_matches = enrich_booking_rows_with_bank(
                calc_rows, booking_bank_idx, prop, year=year, month=month
            )
            if persist_db:
                save_payout_batch_bank_matches(db_conn, "airbnb", airbnb_batch_matches)
                save_payout_batch_bank_matches(db_conn, "booking", booking_batch_matches)

            # Mark Booking CHYBÍ_V_HOSTIFY rows with no bank confirmation as KE KONTROLE
            for row in calc_rows:
                source = (row.get("source") or "").lower()
                if (
                    "booking" in source
                    and row.get("verification_status") == STATUS_CHYBI_HOSTIFY
                    and row.get("bank_status") == "CHYBÍ"
                ):
                    row["verification_status"] = STATUS_KE_KONTROLE

            if db_conn and hasattr(db_conn, "execute"):
                calc_rows = apply_overrides_to_rows(db_conn, calc_rows, slug, year, month)

            totals = calculate_totals_with_config(calc_rows, prop)
            expenses = get_expenses(db_conn, slug, year, month) if db_conn else []

            # 4f. Resolve pending payments from previous months
            transferred_rows = []
            if persist_db:
                prev_pending = [
                    p for p in get_pending_payments(db_conn, slug, status="PENDING")
                    if p["original_year"] < year or
                    (p["original_year"] == year and p["original_month"] < month)
                ]
                if prev_pending:
                    # Build full (unfiltered) bank index for resolution lookup
                    bank_index_full, bank_no_ref_full = build_bank_index(bank_rows_all)
                    resolved, _ = resolve_pending_against_bank(
                        prev_pending, bank_index_full, bank_no_ref_full,
                        booking_bank_idx_all, cutoff_date, booking_cfg.get("property_id", ""),
                    )
                    for r in resolved:
                        resolve_pending_payment(
                            db_conn, slug, r["confirmation_code"],
                            year, month, r["bank_datum"], r["bank_amount_czk"],
                        )
                        if (
                            r.get("original_year") != year
                            or r.get("original_month") != month
                        ):
                            mark_report_month_stale(
                                db_conn,
                                slug,
                                int(r["original_year"]),
                                int(r["original_month"]),
                            )
                    transferred_rows = resolved
                    if transferred_rows:
                        log.info("  Resolved %d pending payment(s) from previous months",
                                 len(transferred_rows))

            # 4g. Save new CHYBÍ rows as pending payments
            if persist_db:
                chybi_rows = [r for r in calc_rows if r.get("bank_status") == "CHYBÍ"]
                save_pending_payments(db_conn, slug, year, month, chybi_rows)
                if chybi_rows:
                    log.info("  Saved %d pending payment(s) to DB (CHYBÍ)", len(chybi_rows))

            # 4h. Log verification + bank summary
            from collections import Counter
            status_counts = Counter(r["verification_status"] for r in calc_rows)
            bank_counts = Counter(r.get("bank_status", "") for r in calc_rows
                                  if (r.get("source") or "").lower() in ("airbnb", "booking.com"))
            print(f"    Verification: {dict(status_counts)}", file=sys.stderr)
            if bank_counts:
                print(f"    Banka: {dict(bank_counts)}", file=sys.stderr)
            if transferred_rows:
                print(f"    Přeneseno z min. měsíců: {len(transferred_rows)} plateb", file=sys.stderr)

            if args.dry_run:
                # Dry-run: full preview only; no files or report-related DB artifacts are written
                print(f"\n{'─'*80}")
                print(f"DRY RUN — {slug} ({nickname}) — {month:02d}/{year}")
                print(f"{'─'*80}")
                print(f"  {'Гость':<24} {'Заезд':<12} {'Источник':<14} {'Статус':<18} {'Банк':<12} {'Выплата CZK':>12}")
                print(f"  {'─'*24} {'─'*12} {'─'*14} {'─'*18} {'─'*12} {'─'*12}")
                for r in calc_rows:
                    guest = (r.get("guest_name") or "")[:24]
                    checkin = (r.get("check_in") or "")[:10]
                    src = (r.get("source") or "")[:14]
                    vstatus = (r.get("verification_status") or "")[:18]
                    bstatus = (r.get("bank_status") or "N/A")[:12]
                    payout = r.get("payout_czk") or 0.0
                    print(f"  {guest:<24} {checkin:<12} {src:<14} {vstatus:<18} {bstatus:<12} {payout:>12.2f}")
                print(f"  {'─'*80}")
                print(f"  Итого строк: {len(calc_rows)}")
                success_count += 1
                continue

            # 4i. Write Excel
            output_path = write_property_report(
                calc_rows, totals, prop, year, month,
                args.output_dir, overwrite=args.overwrite,
                transferred_rows=transferred_rows,
                expenses=expenses,
            )
            print(f"    OK: {os.path.relpath(output_path)} ({len(calc_rows)} rows)", file=sys.stderr)

            # Save to DB
            if db_conn:
                try:
                    save_report_rows(db_conn, slug, year, month, calc_rows)
                    log_report_generated(db_conn, slug, year, month, output_path, calc_rows)
                    touch_report_month_generation(db_conn, slug, year, month)
                except Exception as e:
                    log.warning("Could not save to DB: %s", e)

            success_count += 1

        except FileExistsError as e:
            print(f"    SKIP: {e}", file=sys.stderr)
            error_count += 1
        except Exception as e:
            print(f"    ERROR processing {slug}: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc(file=sys.stderr)
            error_count += 1

    # ------------------------------------------------------------------ #
    #  Summary                                                            #
    # ------------------------------------------------------------------ #
    print(f"\n{'='*60}", file=sys.stderr)
    if args.dry_run:
        print(f"  DRY RUN complete. Processed: {success_count}. Errors: {error_count}. No files written.", file=sys.stderr)
    else:
        print(f"  Generated: {success_count} file(s). Errors: {error_count}.", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
