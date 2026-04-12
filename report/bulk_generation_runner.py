"""
Sequential background runner for month-wide report generation.

Uses generate_report_in_process() directly (in-process, single connection)
instead of spawning subprocesses. CSV data is loaded once and shared
across all properties.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import sqlite3
import sys
import time
import traceback

from report.config import (
    get_all_properties,
    get_booking_config,
    get_hostify_listing_names,
    load_runtime_config,
)
from report.db import (
    BULK_GENERATION_FAILED,
    BULK_GENERATION_SUCCEEDED,
    GENERATION_JOB_FAILED,
    GENERATION_JOB_SUCCEEDED,
    MONTH_DATA_STATE_EMPTY,
    MONTH_STATUS_LOCKED,
    create_report_generation_job,
    finish_bulk_generation_run,
    finish_report_generation_job,
    get_active_report_generation_job,
    get_bulk_generation_run,
    get_connection,
    get_hostify_reservation_counts,
    get_report_history,
    get_report_month_state,
    set_bulk_generation_run_running,
    set_report_generation_job_running,
    update_bulk_generation_run_progress,
)
from report.engine import build_csv_cache, generate_report_in_process


log = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db_retry(fn, *args, retries: int = 5, delay: float = 2.0, **kwargs):
    """Retry a DB operation on sqlite3.OperationalError (database is locked)."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == retries - 1:
                raise
            log.warning("DB locked (attempt %d/%d), retrying in %.1fs…", attempt + 1, retries, delay)
            time.sleep(delay)


def _truncate_generation_detail(detail: str, max_chars: int = 40000) -> str:
    text = str(detail or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n... output truncated"


def _summarize_generation_error(detail: str) -> str:
    lines = [line.strip() for line in str(detail or "").splitlines() if line.strip()]
    if not lines:
        return "Generování reportu selhalo."
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line
    return lines[-1]


def _get_active_properties(config: dict) -> list[dict]:
    try:
        signature = inspect.signature(get_all_properties)
    except (TypeError, ValueError):
        signature = None
    if signature and "active_only" in signature.parameters:
        return get_all_properties(config, active_only=True)
    return get_all_properties(config)


def _property_listing_names(prop: dict, *, year: int, month: int) -> list[str]:
    names = []
    names.extend(get_hostify_listing_names(prop, year=year, month=month))
    booking_nickname = (get_booking_config(prop, year=year, month=month).get("listing_nickname") or "").strip()
    if booking_nickname:
        names.append(booking_nickname)
    return [name for name in dict.fromkeys(str(name).strip() for name in names if str(name).strip())]


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


def _latest_report_for_month(conn, slug: str, year: int, month: int) -> dict | None:
    history = get_report_history(conn, slug=slug, limit=50)
    for row in history:
        if int(row.get("year") or 0) == int(year) and int(row.get("month") or 0) == int(month):
            return row
    return None


def _final_message(*, succeeded: int, failed: int, skipped_locked: int, skipped_no_data: int, skipped_running: int, total: int, year: int, month: int) -> str:
    return (
        f"Sekvenční generování {month:02d}/{year} dokončeno. "
        f"OK {succeeded}/{total}, chyby {failed}, zamčeno {skipped_locked}, "
        f"bez dat {skipped_no_data}, obsazeno {skipped_running}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sequential month-wide Rentero generation.")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")

    conn = get_connection(args.db_path)
    try:
        _db_retry(set_bulk_generation_run_running, conn, args.run_id, pid=os.getpid())
        run = get_bulk_generation_run(conn, args.run_id)
        if not run:
            return 1
        config = load_runtime_config(args.config, db_conn=conn)
        properties = _get_active_properties(config)
        total = len(properties)
        _db_retry(update_bulk_generation_run_progress,
            conn,
            args.run_id,
            message=f"Sekvenční generování {args.month:02d}/{args.year} — načítání CSV…",
            detail="",
        )

        # Load all CSV data ONCE — shared across all properties
        cache = build_csv_cache(conn)
        log.info("CSV cache loaded: airbnb=%d booking=%d bank=%d rows",
                 len(cache["airbnb_index"]), len(cache["booking_index"]),
                 len(cache["bank_rows_all"]))

        _db_retry(update_bulk_generation_run_progress,
            conn,
            args.run_id,
            message=f"Sekvenční generování {args.month:02d}/{args.year} běží.",
            detail="",
        )

        succeeded = 0
        failed = 0
        skipped_locked = 0
        skipped_no_data = 0
        skipped_running = 0
        failure_lines: list[str] = []

        for index, prop in enumerate(properties, start=1):
            slug = str(prop.get("slug") or "").strip()
            if not slug:
                continue
            _db_retry(update_bulk_generation_run_progress,
                conn,
                args.run_id,
                current_slug=slug,
                processed_objects=index - 1,
                succeeded_objects=succeeded,
                failed_objects=failed,
                skipped_locked_objects=skipped_locked,
                skipped_no_data_objects=skipped_no_data,
                skipped_running_objects=skipped_running,
                message=f"Zpracovává se {slug} ({index}/{total})",
                detail="\n".join(failure_lines[-8:]),
            )

            state = get_report_month_state(conn, slug, args.year, args.month)
            latest_report = _latest_report_for_month(conn, slug, args.year, args.month)
            has_data = _month_has_data(conn, prop, args.year, args.month)
            if state.get("status") == MONTH_STATUS_LOCKED:
                skipped_locked += 1
            elif not has_data and not latest_report:
                skipped_no_data += 1
            elif get_active_report_generation_job(conn, slug, args.year, args.month):
                skipped_running += 1
            else:
                job = _db_retry(
                    create_report_generation_job,
                    conn,
                    slug,
                    args.year,
                    args.month,
                    requested_by=str(run.get("requested_by") or ""),
                )
                _db_retry(set_report_generation_job_running, conn, int(job["id"]), pid=os.getpid())
                try:
                    result = generate_report_in_process(
                        conn,
                        slug,
                        args.year,
                        args.month,
                        config,
                        csv_cache=cache,
                    )
                    if result.get("skipped"):
                        skipped_locked += 1
                        _db_retry(
                            finish_report_generation_job,
                            conn,
                            int(job["id"]),
                            status=GENERATION_JOB_SUCCEEDED,
                            message=f"Přeskočeno: {result.get('reason', 'locked')}",
                            detail="",
                        )
                    else:
                        succeeded += 1
                        detail = f"rows={result.get('rows_count', 0)} {result.get('status_counts', {})}"
                        _db_retry(
                            finish_report_generation_job,
                            conn,
                            int(job["id"]),
                            status=GENERATION_JOB_SUCCEEDED,
                            message=f"Report pro {args.month:02d}/{args.year} byl úspěšně vygenerován.",
                            detail=detail,
                        )
                except Exception as exc:
                    failed += 1
                    detail = _truncate_generation_detail(traceback.format_exc())
                    failure_lines.append(f"{slug}: {_summarize_generation_error(str(exc))}")
                    _db_retry(
                        finish_report_generation_job,
                        conn,
                        int(job["id"]),
                        status=GENERATION_JOB_FAILED,
                        message=_summarize_generation_error(str(exc)),
                        detail=detail,
                    )

            _db_retry(update_bulk_generation_run_progress,
                conn,
                args.run_id,
                current_slug=slug,
                processed_objects=index,
                succeeded_objects=succeeded,
                failed_objects=failed,
                skipped_locked_objects=skipped_locked,
                skipped_no_data_objects=skipped_no_data,
                skipped_running_objects=skipped_running,
                message=f"Hotovo {index}/{total}",
                detail="\n".join(failure_lines[-8:]),
            )

        final_message = _final_message(
            succeeded=succeeded,
            failed=failed,
            skipped_locked=skipped_locked,
            skipped_no_data=skipped_no_data,
            skipped_running=skipped_running,
            total=total,
            year=args.year,
            month=args.month,
        )
        _db_retry(
            finish_bulk_generation_run,
            conn,
            args.run_id,
            status=BULK_GENERATION_FAILED if failed else BULK_GENERATION_SUCCEEDED,
            current_slug="",
            message=final_message,
            detail="\n".join(failure_lines[-12:]),
        )
        return 1 if failed else 0
    except Exception as exc:
        try:
            _db_retry(
                finish_bulk_generation_run,
                conn,
                args.run_id,
                status=BULK_GENERATION_FAILED,
                current_slug="",
                message="Sekvenční generování selhalo.",
                detail=_truncate_generation_detail(traceback.format_exc()),
            )
        except Exception:
            log.error("Could not mark run %d as FAILED: %s", args.run_id, traceback.format_exc())
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
