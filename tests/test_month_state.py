from fastapi import HTTPException

from report.db import (
    BULK_GENERATION_FAILED,
    BULK_GENERATION_PENDING,
    BULK_GENERATION_RUNNING,
    BULK_GENERATION_SUCCEEDED,
    GENERATION_JOB_FAILED,
    GENERATION_JOB_PENDING,
    GENERATION_JOB_RUNNING,
    GENERATION_JOB_SUCCEEDED,
    LockedReportMonthError,
    MONTH_DATA_STATE_EMPTY,
    MONTH_DATA_STATE_GENERATED,
    MONTH_DATA_STATE_READY,
    MONTH_DATA_STATE_STALE,
    MONTH_STATUS_LOCKED,
    MONTH_STATUS_OPEN,
    add_expense,
    create_bulk_generation_run,
    create_report_generation_job,
    create_report_month_notification,
    delete_expense,
    finish_bulk_generation_run,
    finish_report_generation_job,
    get_active_bulk_generation_run,
    get_latest_bulk_generation_run,
    get_active_report_generation_job,
    get_connection,
    get_hostify_reservation_counts,
    get_latest_report_generation_job,
    get_report_month_state,
    get_report_month_states,
    is_report_month_locked,
    mark_report_month_stale,
    save_report_rows,
    save_hostify_reservations,
    set_bulk_generation_run_running,
    set_report_generation_job_running,
    set_report_month_locked,
    touch_report_month_generation,
    update_bulk_generation_run_progress,
    update_expense,
)
from report.web import _build_dashboard_maps, _ensure_month_open


def test_report_month_state_defaults_to_open_and_can_be_locked_and_unlocked():
    conn = get_connection(":memory:")
    try:
        state = get_report_month_state(conn, "28_Pluku_58", 2026, 3)
        assert state["status"] == MONTH_STATUS_OPEN
        assert state["last_generated_at"] is None
        assert state["data_state"] == MONTH_DATA_STATE_EMPTY

        locked = set_report_month_locked(conn, "28_Pluku_58", 2026, 3, locked=True, actor="admin")
        assert locked["status"] == MONTH_STATUS_LOCKED
        assert locked["locked_at"] is not None
        assert locked["locked_by"] == "admin"
        assert is_report_month_locked(conn, "28_Pluku_58", 2026, 3) is True

        unlocked = set_report_month_locked(conn, "28_Pluku_58", 2026, 3, locked=False, actor="admin")
        assert unlocked["status"] == MONTH_STATUS_OPEN
        assert unlocked["unlocked_at"] is not None
        assert unlocked["unlocked_by"] == "admin"
        assert is_report_month_locked(conn, "28_Pluku_58", 2026, 3) is False
    finally:
        conn.close()


def test_touch_report_month_generation_updates_timestamps():
    conn = get_connection(":memory:")
    try:
        touched = touch_report_month_generation(conn, "28_Pluku_58", 2026, 3)
        assert touched["status"] == MONTH_STATUS_OPEN
        assert touched["last_generated_at"] is not None
        assert touched["last_recalculated_at"] is not None
        assert touched["data_state"] == MONTH_DATA_STATE_GENERATED
        assert touched["has_new_data_since_generation"] == 0
    finally:
        conn.close()


def test_mark_report_month_stale_only_applies_after_generation():
    conn = get_connection(":memory:")
    try:
        untouched = mark_report_month_stale(conn, "28_Pluku_58", 2026, 3)
        assert untouched["data_state"] == MONTH_DATA_STATE_READY
        assert untouched["has_new_data_since_generation"] == 0

        touch_report_month_generation(conn, "28_Pluku_58", 2026, 3)
        stale = mark_report_month_stale(conn, "28_Pluku_58", 2026, 3)
        assert stale["data_state"] == MONTH_DATA_STATE_STALE
        assert stale["has_new_data_since_generation"] == 1
    finally:
        conn.close()


def test_get_report_month_states_filters_by_slug_and_month():
    conn = get_connection(":memory:")
    try:
        set_report_month_locked(conn, "A", 2026, 3, locked=True, actor="admin")
        set_report_month_locked(conn, "B", 2026, 4, locked=True, actor="admin")

        states = get_report_month_states(conn, slugs=["A"], months=[(2026, 3), (2026, 4)])
        assert len(states) == 1
        assert states[0]["slug"] == "A"
        assert states[0]["month"] == 3
    finally:
        conn.close()


def test_hostify_counts_and_dashboard_maps_show_data_presence_and_stale_flag():
    conn = get_connection(":memory:")
    try:
        save_hostify_reservations(
            conn,
            [
                {
                    "confirmation_code": "ABC123",
                    "reservation_id": "1",
                    "source": "Airbnb",
                    "status": "accepted",
                    "guest_name": "Guest",
                    "check_in": "2026-03-10",
                    "check_out": "2026-03-12",
                    "assigned_year": 2026,
                    "assigned_month": 3,
                    "listing_nickname": "28. Pluku 58",
                }
            ],
        )
        counts = get_hostify_reservation_counts(
            conn,
            listing_nicknames=["28. Pluku 58"],
            months=[(2026, 3)],
        )
        assert counts[0]["reservation_count"] == 1

        touch_report_month_generation(conn, "28_Pluku_58", 2026, 3)
        mark_report_month_stale(conn, "28_Pluku_58", 2026, 3)

        create_report_month_notification(
            conn,
            slug="28_Pluku_58",
            year=2026,
            month=3,
            event_type="IMPORT_IMPACT_AUTO_REGENERATE_STARTED",
            source_type="airbnb",
            message="Import changed this month.",
            payload={"change_lines": ["Airbnb: +1 rezervací, +1 payout batchů"]},
        )

        history_map, month_state_map, data_exists_map, notification_map = _build_dashboard_maps(
            conn,
            [{"slug": "28_Pluku_58", "listing_nickname": "28. Pluku 58"}],
            [(2026, 3)],
        )
        assert history_map["28_Pluku_58"] == {}
        assert data_exists_map["28_Pluku_58"][(2026, 3)] is True
        assert month_state_map["28_Pluku_58"][(2026, 3)]["data_state"] == MONTH_DATA_STATE_STALE
        assert month_state_map["28_Pluku_58"][(2026, 3)]["has_new_data_since_generation"] == 1
        assert notification_map[("28_Pluku_58", 2026, 3)]["payload"]["change_lines"][0].startswith("Airbnb:")
    finally:
        conn.close()


def test_dashboard_maps_use_persisted_month_state_when_live_counts_are_empty():
    conn = get_connection(":memory:")
    try:
        mark_report_month_stale(conn, "28_Pluku_58", 2026, 5)

        history_map, month_state_map, data_exists_map, notification_map = _build_dashboard_maps(
            conn,
            [{"slug": "28_Pluku_58", "listing_nickname": "28. Pluku 58"}],
            [(2026, 5)],
        )

        assert history_map["28_Pluku_58"] == {}
        assert month_state_map["28_Pluku_58"][(2026, 5)]["data_state"] == MONTH_DATA_STATE_READY
        assert data_exists_map["28_Pluku_58"][(2026, 5)] is True
        assert notification_map == {}
    finally:
        conn.close()


def test_ensure_month_open_raises_for_locked_month():
    conn = get_connection(":memory:")
    try:
        set_report_month_locked(conn, "28_Pluku_58", 2026, 3, locked=True, actor="admin")
        try:
            _ensure_month_open(conn, "28_Pluku_58", 2026, 3)
        except HTTPException as exc:
            assert exc.status_code == 423
        else:
            raise AssertionError("Expected HTTPException for locked month")
    finally:
        conn.close()


def test_db_month_scoped_mutations_reject_locked_month():
    conn = get_connection(":memory:")
    try:
        set_report_month_locked(conn, "28_Pluku_58", 2026, 3, locked=True, actor="admin")

        try:
            add_expense(
                conn,
                {
                    "property_slug": "28_Pluku_58",
                    "year": 2026,
                    "month": 3,
                    "date": "2026-03-10",
                    "category_id": None,
                    "description": "Locked expense",
                    "amount_czk": 100,
                },
            )
        except LockedReportMonthError:
            pass
        else:
            raise AssertionError("Expected LockedReportMonthError for add_expense")

        try:
            save_report_rows(conn, "28_Pluku_58", 2026, 3, [{"confirmation_code": "ABC123"}])
        except LockedReportMonthError:
            pass
        else:
            raise AssertionError("Expected LockedReportMonthError for save_report_rows")
    finally:
        conn.close()


def test_save_report_rows_replaces_month_snapshot_instead_of_accumulating():
    conn = get_connection(":memory:")
    try:
        save_report_rows(
            conn,
            "28_Pluku_58",
            2026,
            3,
            [
                {"confirmation_code": "OLD-1", "check_in": "2025-07-10"},
                {"confirmation_code": "OLD-2", "check_in": "2025-08-10"},
            ],
        )
        save_report_rows(
            conn,
            "28_Pluku_58",
            2026,
            3,
            [
                {"confirmation_code": "NEW-1", "check_in": "2026-03-11"},
            ],
        )

        rows = conn.execute(
            """SELECT confirmation_code FROM report_rows
               WHERE slug = ? AND year = ? AND month = ?
               ORDER BY confirmation_code""",
            ("28_Pluku_58", 2026, 3),
        ).fetchall()

        assert [row["confirmation_code"] for row in rows] == ["NEW-1"]
    finally:
        conn.close()


def test_update_and_delete_expense_reject_locked_month():
    conn = get_connection(":memory:")
    try:
        expense_id = add_expense(
            conn,
            {
                "property_slug": "28_Pluku_58",
                "year": 2026,
                "month": 3,
                "date": "2026-03-10",
                "category_id": None,
                "description": "Editable",
                "amount_czk": 100,
            },
        )
        set_report_month_locked(conn, "28_Pluku_58", 2026, 3, locked=True, actor="admin")

        try:
            update_expense(
                conn,
                expense_id,
                {
                    "property_slug": "28_Pluku_58",
                    "year": 2026,
                    "month": 3,
                    "date": "2026-03-11",
                    "category_id": None,
                    "description": "Updated",
                    "amount_czk": 200,
                },
            )
        except LockedReportMonthError:
            pass
        else:
            raise AssertionError("Expected LockedReportMonthError for update_expense")

        try:
            delete_expense(conn, expense_id)
        except LockedReportMonthError:
            pass
        else:
            raise AssertionError("Expected LockedReportMonthError for delete_expense")
    finally:
        conn.close()


def test_report_generation_job_lifecycle_allows_only_one_active_job():
    conn = get_connection(":memory:")
    try:
        job = create_report_generation_job(conn, "28_Pluku_58", 2026, 4, requested_by="admin")
        assert job["status"] == GENERATION_JOB_PENDING

        duplicate = create_report_generation_job(conn, "28_Pluku_58", 2026, 4, requested_by="admin")
        assert duplicate["id"] == job["id"]
        assert get_active_report_generation_job(conn, "28_Pluku_58", 2026, 4)["id"] == job["id"]

        running = set_report_generation_job_running(conn, job["id"], pid=12345)
        assert running["status"] == GENERATION_JOB_RUNNING
        assert running["pid"] == 12345

        succeeded = finish_report_generation_job(
            conn,
            job["id"],
            status=GENERATION_JOB_SUCCEEDED,
            message="ok",
            detail="done",
        )
        assert succeeded["status"] == GENERATION_JOB_SUCCEEDED
        assert succeeded["finished_at"] is not None
        assert get_active_report_generation_job(conn, "28_Pluku_58", 2026, 4) is None

        next_job = create_report_generation_job(conn, "28_Pluku_58", 2026, 4, requested_by="admin")
        assert next_job["id"] != job["id"]
        failed = finish_report_generation_job(
            conn,
            next_job["id"],
            status=GENERATION_JOB_FAILED,
            message="boom",
            detail="trace",
        )
        assert failed["status"] == GENERATION_JOB_FAILED
        assert get_latest_report_generation_job(conn, "28_Pluku_58", 2026, 4)["id"] == next_job["id"]
    finally:
        conn.close()


def test_stale_pending_generation_job_is_expired_automatically():
    conn = get_connection(":memory:")
    try:
        job = create_report_generation_job(conn, "28_Pluku_58", 2026, 4, requested_by="admin")
        conn.execute(
            """UPDATE report_generation_jobs
               SET created_at = '2000-01-01T00:00:00+00:00',
                   updated_at = '2000-01-01T00:00:00+00:00'
               WHERE id = ?""",
            (job["id"],),
        )
        conn.commit()

        assert get_active_report_generation_job(conn, "28_Pluku_58", 2026, 4) is None
        latest = get_latest_report_generation_job(conn, "28_Pluku_58", 2026, 4)
        assert latest["status"] == GENERATION_JOB_FAILED
        assert "automaticky ukončen" in latest["message"]
    finally:
        conn.close()


def test_stale_running_generation_job_is_expired_when_pid_is_gone():
    conn = get_connection(":memory:")
    try:
        job = create_report_generation_job(conn, "28_Pluku_58", 2026, 4, requested_by="admin")
        set_report_generation_job_running(conn, job["id"], pid=999999)
        conn.execute(
            """UPDATE report_generation_jobs
               SET started_at = '2000-01-01T00:00:00+00:00',
                   updated_at = '2000-01-01T00:00:00+00:00'
               WHERE id = ?""",
            (job["id"],),
        )
        conn.commit()

        assert get_active_report_generation_job(conn, "28_Pluku_58", 2026, 4) is None
        latest = get_latest_report_generation_job(conn, "28_Pluku_58", 2026, 4)
        assert latest["status"] == GENERATION_JOB_FAILED
        assert "automaticky" in latest["message"]
    finally:
        conn.close()


def test_bulk_generation_run_lifecycle_allows_only_one_active_run():
    conn = get_connection(":memory:")
    try:
        run = create_bulk_generation_run(conn, 2026, 4, total_objects=10, requested_by="admin")
        assert run["status"] == BULK_GENERATION_PENDING

        duplicate = create_bulk_generation_run(conn, 2026, 5, total_objects=4, requested_by="admin")
        assert duplicate["id"] == run["id"]
        assert get_active_bulk_generation_run(conn)["id"] == run["id"]

        running = set_bulk_generation_run_running(conn, run["id"], pid=12345)
        assert running["status"] == BULK_GENERATION_RUNNING
        assert running["pid"] == 12345

        progress = update_bulk_generation_run_progress(
            conn,
            run["id"],
            current_slug="28_Pluku_58",
            processed_objects=3,
            succeeded_objects=2,
            failed_objects=1,
            skipped_locked_objects=0,
            skipped_no_data_objects=0,
            skipped_running_objects=0,
            message="Hotovo 3/10",
        )
        assert progress["processed_objects"] == 3
        assert progress["current_slug"] == "28_Pluku_58"

        finished = finish_bulk_generation_run(
            conn,
            run["id"],
            status=BULK_GENERATION_SUCCEEDED,
            message="done",
            detail="ok",
            current_slug="",
        )
        assert finished["status"] == BULK_GENERATION_SUCCEEDED
        assert finished["finished_at"] is not None
        assert get_active_bulk_generation_run(conn) is None

        next_run = create_bulk_generation_run(conn, 2026, 5, total_objects=2, requested_by="admin")
        assert next_run["id"] != run["id"]
        latest = get_latest_bulk_generation_run(conn)
        assert latest["id"] == next_run["id"]
    finally:
        conn.close()


def test_stale_pending_bulk_generation_run_is_expired_automatically():
    conn = get_connection(":memory:")
    try:
        run = create_bulk_generation_run(conn, 2026, 4, total_objects=3, requested_by="admin")
        conn.execute(
            """UPDATE bulk_generation_runs
               SET created_at = '2000-01-01T00:00:00+00:00',
                   updated_at = '2000-01-01T00:00:00+00:00'
               WHERE id = ?""",
            (run["id"],),
        )
        conn.commit()

        assert get_active_bulk_generation_run(conn) is None
        latest = get_latest_bulk_generation_run(conn)
        assert latest["status"] == BULK_GENERATION_FAILED
        assert "automaticky ukončen" in latest["message"]
    finally:
        conn.close()


def test_stale_running_bulk_generation_run_is_expired_when_pid_is_gone():
    conn = get_connection(":memory:")
    try:
        run = create_bulk_generation_run(conn, 2026, 4, total_objects=3, requested_by="admin")
        set_bulk_generation_run_running(conn, run["id"], pid=999999)
        update_bulk_generation_run_progress(conn, run["id"], current_slug="Reznicka_21")
        conn.execute(
            """UPDATE bulk_generation_runs
               SET started_at = '2000-01-01T00:00:00+00:00',
                   updated_at = '2000-01-01T00:00:00+00:00'
               WHERE id = ?""",
            (run["id"],),
        )
        conn.commit()

        assert get_active_bulk_generation_run(conn) is None
        latest = get_latest_bulk_generation_run(conn)
        assert latest["status"] == BULK_GENERATION_FAILED
        assert "automaticky" in latest["message"]
        assert latest["current_slug"] == ""
    finally:
        conn.close()
