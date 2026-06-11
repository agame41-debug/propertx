"""Generation atomicity: a mid-pipeline failure must not leave the month empty.

The engine DELETEs report_rows early (so cross-month ADJ lookups don't see
stale data of the same month) and later steps commit that delete. Without the
restore guard in generate_report_in_process, any exception after that point
(CSV format drift, CNB outage, "database is locked") left the month showing a
zero-row report until the next successful regen.
"""
from __future__ import annotations

import pytest

import report.engine as engine_module
from report.cnb import CnbRateError
from report.db import get_connection, get_report_rows, save_report_rows
from report.engine import generate_report_in_process


def _make_config(slug: str = "test_prop") -> dict:
    return {
        "properties": {
            slug: {
                "listing_id": 1,
                "listing_nickname": "Test Listing",
                "display_name": "Test Property",
                "active": True,
                "balicky_per_person": 0,
                "city_tax_rate": 45.0,
                "vat_rate": 21.0,
                "channels": {
                    "hostify": {"listing_names": []},
                    "airbnb": {"listing_names": ["Test Listing"]},
                    "booking": {"listing_nickname": "", "property_id": ""},
                },
            }
        }
    }


_OLD_ROW = {
    "confirmation_code": "HMOLD111",
    "guest_name": "Previous Guest",
    "payout_czk": 12345.0,
    "verification_status": "MATCHED",
}


def test_failure_mid_generation_restores_previous_rows(monkeypatch):
    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "test_prop", 2026, 3, [_OLD_ROW])

        def boom(*args, **kwargs):
            raise CnbRateError("CNB API down")

        # preload_rates_for_month runs after the early DELETE was committed
        # by materialize_templates_for_month — the exact failure window.
        monkeypatch.setattr(engine_module, "preload_rates_for_month", boom)

        with pytest.raises(CnbRateError):
            generate_report_in_process(conn, "test_prop", 2026, 3, _make_config())

        rows = get_report_rows(conn, "test_prop", 2026, 3)
        assert [r["confirmation_code"] for r in rows] == ["HMOLD111"]
        assert rows[0]["payout_czk"] == 12345.0
    finally:
        conn.close()


def test_failure_after_successful_save_keeps_new_snapshot(monkeypatch):
    from report.config import sync_json_config_to_db
    from report.db import save_hostify_reservations
    from report.loader import normalize_reservations

    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)
        save_report_rows(conn, "test_prop", 2026, 3, [_OLD_ROW])

        raw = {
            "channel_reservation_id": "HMNEW2222",
            "guest_name": "New Guest",
            "listing_nickname": "Test Listing",
            "checkIn": "2026-03-10",
            "checkOut": "2026-03-12",
            "status": "confirmed",
            "adults": 2,
            "children": 0,
            "infants": 0,
            "cleaning_fee": 0.0,
            "city_tax": 0.0,
            "channel_commission": 15.0,
            "payout_price": 85.0,
            "confirmedAt": "2026-01-15T10:00:00",
        }
        normalized = normalize_reservations([raw])
        for n in normalized:
            n["checkIn"] = n["check_in"]
            n["checkOut"] = n["check_out"]
        save_hostify_reservations(conn, normalized)

        def boom(*args, **kwargs):
            raise RuntimeError("history write failed")

        # log_report_generated runs AFTER save_report_rows — the new snapshot
        # exists, so the restore guard must NOT clobber it with the old rows.
        monkeypatch.setattr(engine_module, "log_report_generated", boom)

        with pytest.raises(RuntimeError):
            generate_report_in_process(conn, "test_prop", 2026, 3, config)

        rows = get_report_rows(conn, "test_prop", 2026, 3)
        codes = [r["confirmation_code"] for r in rows]
        assert "HMNEW2222" in codes
        assert "HMOLD111" not in codes
    finally:
        conn.close()


def test_failure_with_no_previous_rows_leaves_month_empty(monkeypatch):
    conn = get_connection(":memory:")
    try:
        def boom(*args, **kwargs):
            raise CnbRateError("CNB API down")

        monkeypatch.setattr(engine_module, "preload_rates_for_month", boom)

        with pytest.raises(CnbRateError):
            generate_report_in_process(conn, "test_prop", 2026, 3, _make_config())

        assert get_report_rows(conn, "test_prop", 2026, 3) == []
    finally:
        conn.close()


def test_generations_are_serialized_process_wide(monkeypatch):
    """Exactly one generation at a time per process — route worker threads,
    import daemon threads and BackgroundTasks all funnel through
    engine._generation_serial_lock."""
    import threading
    import time

    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    def fake_unguarded(conn, slug, year, month, config, **kwargs):
        with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(0.05)
        with lock:
            active["count"] -= 1
        return {"rows_count": 0, "status_counts": {}}

    monkeypatch.setattr(engine_module, "_generate_report_unguarded", fake_unguarded)

    def worker():
        conn = get_connection(":memory:")
        try:
            generate_report_in_process(conn, "test_prop", 2026, 3, _make_config())
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert active["max"] == 1, "two generations overlapped despite the serial lock"


def test_failed_generation_skips_restore_when_active_job_exists(monkeypatch):
    """A PENDING/RUNNING generation job (bulk runner — separate process,
    outside our serial lock) is about to write fresh rows; restoring the old
    snapshot could interleave with its early DELETE and mix old+new rows."""
    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "test_prop", 2026, 3, [_OLD_ROW])
        conn.execute(
            "INSERT INTO report_generation_jobs (slug, year, month, status, created_at, updated_at) "
            "VALUES ('test_prop', 2026, 3, 'RUNNING', "
            "'2026-03-01T00:00:00+00:00', '2026-03-01T00:00:00+00:00')"
        )
        conn.commit()

        def boom(*args, **kwargs):
            raise CnbRateError("CNB API down")

        monkeypatch.setattr(engine_module, "preload_rates_for_month", boom)

        with pytest.raises(CnbRateError):
            generate_report_in_process(conn, "test_prop", 2026, 3, _make_config())

        # Restore deferred to the running job — month stays empty for it.
        assert get_report_rows(conn, "test_prop", 2026, 3) == []
    finally:
        conn.close()


def test_restore_still_runs_for_own_process_job(monkeypatch):
    """The bulk runner registers a RUNNING job (with its own pid) BEFORE
    generating — its own job must NOT suppress the restore of its own
    failed run."""
    import os as _os

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "test_prop", 2026, 3, [_OLD_ROW])
        conn.execute(
            "INSERT INTO report_generation_jobs "
            "(slug, year, month, status, pid, created_at, updated_at) "
            "VALUES ('test_prop', 2026, 3, 'RUNNING', ?, "
            "'2026-03-01T00:00:00+00:00', '2026-03-01T00:00:00+00:00')",
            (_os.getpid(),),
        )
        conn.commit()

        def boom(*args, **kwargs):
            raise CnbRateError("CNB API down")

        monkeypatch.setattr(engine_module, "preload_rates_for_month", boom)

        with pytest.raises(CnbRateError):
            generate_report_in_process(conn, "test_prop", 2026, 3, _make_config())

        rows = get_report_rows(conn, "test_prop", 2026, 3)
        assert [r["confirmation_code"] for r in rows] == ["HMOLD111"]
    finally:
        conn.close()
