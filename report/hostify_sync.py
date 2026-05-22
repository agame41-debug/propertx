"""
report/hostify_sync.py — Daily Hostify data sync background task.

Fetches 5 months of Hostify reservations (prev, current, +1, +2, +3),
updates hostify_reservations snapshots, then re-generates all open months
that have existing report data.

Usage (started from web.py lifespan):
    task = HostifySyncTask(db_path=..., config=..., config_path=...)
    asyncio_task = asyncio.create_task(task.run_loop())
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from report.db import (
    get_connection,
    get_report_month_state,
    record_orphan_listings,
    save_hostify_reservations,
    MONTH_STATUS_LOCKED,
)
from report.engine import build_csv_cache, generate_report_in_process
from report.loader import (
    fetch_raw_reservations_for_period,
    normalize_reservations_for_snapshot,
)

log = logging.getLogger(__name__)

_SYNC_INTERVAL_SECONDS = 24 * 3600  # 24 hours
_STARTUP_DELAY_SECONDS = 60          # brief delay after app start


def compute_sync_months(*, reference_date: date | None = None) -> list[tuple[int, int]]:
    """Return [(year, month)] for: prev, current, +1, +2, +3 months."""
    today = reference_date or date.today()
    y, m = today.year, today.month
    months = []
    for delta in range(-1, 4):   # -1, 0, 1, 2, 3
        total_month = m - 1 + delta   # zero-based
        my = y + total_month // 12
        mm = total_month % 12 + 1
        months.append((my, mm))
    return months


class HostifySyncTask:
    def __init__(self, *, db_path: str, config: dict, config_path: str | None):
        self._db_path = db_path
        self._config = config
        self._config_path = config_path

    def _sync_once(
        self,
        *,
        conn=None,
        reference_date: date | None = None,
    ) -> None:
        """Synchronous core: fetch Hostify for 5 months, regen open months."""
        from report.config import get_all_properties, load_runtime_config

        own_conn = conn is None
        if own_conn:
            conn = get_connection(self._db_path)
        try:
            config = self._config
            if not config:
                config = load_runtime_config(self._config_path, db_conn=conn)

            target_months = compute_sync_months(reference_date=reference_date)
            log.info("Hostify sync starting for months: %s", target_months)

            # 1. Fetch and persist Hostify snapshots for all target months
            for year, month in target_months:
                try:
                    all_raw = fetch_raw_reservations_for_period(
                        year, month, db_conn=conn
                    )
                    save_hostify_reservations(
                        conn, normalize_reservations_for_snapshot(all_raw)
                    )
                    log.info(
                        "Hostify sync: fetched %d reservations for %d/%d",
                        len(all_raw), month, year,
                    )
                except Exception as exc:
                    log.warning(
                        "Hostify sync: fetch failed for %d/%d: %s", month, year, exc
                    )

            # 1.5. Detect orphan listing nicknames — Hostify reservations
            # whose listing_nickname has no active alias never reach
            # report_rows. We log a warning the first time each appears so
            # the operator can add an alias via /inventory.
            try:
                detection = record_orphan_listings(conn)
                for orphan in detection["newly_detected"]:
                    log.warning(
                        "Hostify sync: NEW orphan listing %r — %d reservations "
                        "from %s, check-ins %s..%s, hostify listing_id=%s. "
                        "Add a hostify alias via /inventory to include in reports.",
                        orphan["listing_nickname"],
                        orphan["reservation_count"],
                        orphan["sources"] or "?",
                        orphan["first_check_in"] or "?",
                        orphan["last_check_in"] or "?",
                        orphan["example_listing_id"],
                    )
                if detection["resolved"]:
                    log.info(
                        "Hostify sync: %d orphan listings resolved: %s",
                        len(detection["resolved"]), detection["resolved"],
                    )
            except Exception as exc:
                log.warning(
                    "Hostify sync: orphan listing detection failed: %s", exc
                )

            # 2. Re-generate all open months with existing data
            try:
                properties = get_all_properties(config)
            except Exception:
                properties = []

            active_props = [p for p in properties if p.get("active", False)]
            # Build the CSV cache once and share it across all regens. Without
            # this, each generate_report_in_process call would re-parse 9k+
            # rows of CSV and re-issue hundreds of UPSERTs into payout_batches,
            # which holds the SQLite write lock long enough to starve any
            # concurrent bulk_generation_runner subprocess (DB-locked errors).
            try:
                csv_cache = build_csv_cache(conn)
            except Exception as exc:
                log.warning("Hostify sync: build_csv_cache failed, falling back to per-call parse: %s", exc)
                csv_cache = None

            for prop in active_props:
                slug = prop["slug"]
                for year, month in target_months:
                    try:
                        state = get_report_month_state(conn, slug, year, month)
                        if state.get("status") == MONTH_STATUS_LOCKED:
                            continue
                        if not state.get("last_generated_at"):
                            continue  # never generated → skip
                        generate_report_in_process(
                            conn, slug, year, month, config,
                            csv_cache=csv_cache,
                        )
                    except Exception as exc:
                        log.warning(
                            "Hostify sync regen failed %s %d/%d: %s",
                            slug, month, year, exc,
                        )
        finally:
            if own_conn:
                conn.close()

    async def run_loop(self) -> None:
        """Asyncio loop: wait for startup delay, then sync every 24h."""
        await asyncio.sleep(_STARTUP_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(self._sync_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("Hostify sync loop error: %s", exc, exc_info=True)
            await asyncio.sleep(_SYNC_INTERVAL_SECONDS)
