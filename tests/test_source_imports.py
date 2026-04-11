from __future__ import annotations

import asyncio
import csv
import io
import os
import tempfile
from types import SimpleNamespace

from starlette.datastructures import UploadFile

from report.db import (
    create_report_month_notification,
    get_connection,
    import_source_file_with_result,
    list_checkin_reservations,
    list_import_runs,
    list_report_month_notifications,
    list_source_files,
)
from report.db_months import touch_report_month_generation
from report.source_registry import analyze_import_delta, import_uploaded_source, import_local_file
import report.web as web_module


def _airbnb_csv_bytes() -> bytes:
    rows = [
        {
            "Typ": "Payout",
            "Potvrzující kód": "",
            "Datum rezervace": "",
            "Datum zahájení": "",
            "Datum ukončení": "",
            "Počet nocí": "",
            "Host": "",
            "Nabídka": "",
            "Částka": "0",
            "Hrubé výdělky": "0",
            "Servisní poplatek": "0",
            "Poplatek za úklid": "0",
            "Referenční kód": "Transfer G-AAA111",
            "Datum": "03/15/2026",
            "Datum připsání na účet": "03/16/2026",
            "Vyplaceno": "1000",
        },
        {
            "Typ": "Rezervace",
            "Potvrzující kód": "ABC123",
            "Datum rezervace": "03/01/2026",
            "Datum zahájení": "03/10/2026",
            "Datum ukončení": "03/12/2026",
            "Počet nocí": "2",
            "Host": "John Doe",
            "Nabídka": "Modern APT City Hideaway",
            "Částka": "40",
            "Hrubé výdělky": "50",
            "Servisní poplatek": "-10",
            "Poplatek za úklid": "0",
            "Referenční kód": "",
            "Datum": "",
            "Datum připsání na účet": "",
            "Vyplaceno": "",
        },
    ]
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def test_analyze_import_delta_for_airbnb_counts_new_items():
    conn = get_connection(":memory:")
    try:
        summary = analyze_import_delta(conn, "airbnb", "airbnb.csv", _airbnb_csv_bytes())
        assert summary["detected_rows_count"] == 1
        assert summary["new_reservations_count"] == 1
        assert summary["new_batches_count"] == 1
        assert summary["affected_months"] == [(2026, 3)]
        assert ("28_Pluku_58", 2026, 3) in summary["affected_month_keys"]
    finally:
        conn.close()


def test_import_uploaded_source_deduplicates_and_logs_runs():
    conn = get_connection(":memory:")
    try:
        first = import_uploaded_source(conn, "airbnb", "airbnb.csv", _airbnb_csv_bytes(), imported_by="admin")
        second = import_uploaded_source(conn, "airbnb", "airbnb-copy.csv", _airbnb_csv_bytes(), imported_by="admin")

        files = list_source_files(conn)
        runs = list_import_runs(conn)

        assert first["is_duplicate"] is False
        assert second["is_duplicate"] is True
        assert len(files) == 1
        assert len(runs) == 2
        assert runs[0]["duplicate_of_source_file_id"] == files[0]["id"]
    finally:
        conn.close()


def test_import_uploaded_source_deduplicates_per_source_type():
    conn = get_connection(":memory:")
    try:
        first = import_uploaded_source(conn, "airbnb", "shared.csv", _airbnb_csv_bytes(), imported_by="admin")
        second = import_uploaded_source(conn, "accounting", "shared.csv", _airbnb_csv_bytes(), imported_by="admin")

        files = list_source_files(conn)

        assert first["is_duplicate"] is False
        assert second["is_duplicate"] is False
        assert len(files) == 2
        assert {row["source_type"] for row in files} == {"airbnb", "accounting"}
    finally:
        conn.close()


def test_sources_import_route_redirects_with_success_flash():
    conn = get_connection(":memory:")
    try:
        request = SimpleNamespace(session={})
        upload = UploadFile(filename="airbnb.csv", file=io.BytesIO(_airbnb_csv_bytes()))
        original_apply = web_module._apply_import_impacts
        web_module._apply_import_impacts = lambda *args, **kwargs: {
            "auto_started": [("28_Pluku_58", 2026, 3)],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
        }

        try:
            response = asyncio.run(
                web_module.sources_import(
                    request=request,
                    background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                    source_type="airbnb",
                    upload=upload,
                    conn=conn,
                    config={},
                )
            )
        finally:
            web_module._apply_import_impacts = original_apply

        assert response.status_code == 303
        assert response.headers["location"] == "/sources?source_type=airbnb"
        assert request.session["_flash"]["level"] == "success"
        assert len(list_source_files(conn)) == 1
        assert len(list_import_runs(conn)) == 1
        assert list_import_runs(conn)[0]["summary"]["impact_result"]["auto_started"] == [["28_Pluku_58", 2026, 3]]
    finally:
        conn.close()


def test_sources_import_route_keeps_import_when_impact_orchestration_fails():
    conn = get_connection(":memory:")
    try:
        request = SimpleNamespace(session={})
        upload = UploadFile(filename="airbnb.csv", file=io.BytesIO(_airbnb_csv_bytes()))
        original_apply = web_module._apply_import_impacts
        web_module._apply_import_impacts = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("impact failed"))

        try:
            response = asyncio.run(
                web_module.sources_import(
                    request=request,
                    background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                    source_type="airbnb",
                    upload=upload,
                    conn=conn,
                    config={},
                )
            )
        finally:
            web_module._apply_import_impacts = original_apply

        assert response.status_code == 303
        assert request.session["_flash"]["level"] == "error"
        assert "Import byl uložen" in request.session["_flash"]["detail"]
        assert len(list_source_files(conn)) == 1
        assert len(list_import_runs(conn)) == 1
        assert list_import_runs(conn)[0]["summary"]["orchestration_error"] == "impact failed"
    finally:
        conn.close()


def test_sources_import_route_redirects_with_duplicate_flash():
    conn = get_connection(":memory:")
    try:
        first_request = SimpleNamespace(session={})
        first_upload = UploadFile(filename="airbnb.csv", file=io.BytesIO(_airbnb_csv_bytes()))
        asyncio.run(
            web_module.sources_import(
                request=first_request,
                background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                source_type="airbnb",
                upload=first_upload,
                conn=conn,
                config={},
            )
        )

        second_request = SimpleNamespace(session={})
        second_upload = UploadFile(filename="airbnb-again.csv", file=io.BytesIO(_airbnb_csv_bytes()))
        response = asyncio.run(
            web_module.sources_import(
                request=second_request,
                background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                source_type="airbnb",
                upload=second_upload,
                conn=conn,
                config={},
            )
        )

        assert response.status_code == 303
        assert second_request.session["_flash"]["level"] == "info"
        assert "už v archivu existuje" in second_request.session["_flash"]["message"]
        assert len(list_source_files(conn)) == 1
        assert len(list_import_runs(conn)) == 2
    finally:
        conn.close()


def _checkin_csv_bytes() -> bytes:
    return (
        "Property Name;Full Name;Nationality;ID Type;ID Number;Phone Number;Check-Out Date;Reservation ID;Check-In Date;Name;Surname;Birth Country;Residence Country;Guest Age\n"
        "28. Pluku 58;John Adult;CZ;P;1;;12-03-2026;chk-001;10-03-2026;John;Adult;CZ;CZ;35\n"
    ).encode("utf-8")


def _bank_csv_bytes() -> bytes:
    return (
        "\"Datum zaúčtování\",\"Název protiúčtu\",\"IBAN\",\"BIC\",\"Protiúčet\",\"Bankovní kód protiúčtu\",\"Částka\",\"Typ transakce\",\"Zpráva pro mě\",\"Zpráva pro příjemce\",\"Poznámka\",\"Kategorie\",\"ID transakce\",\"Reference platby\"\n"
        "\"02.04.2026\",\"CITIBANK EUROPE PLC\",\"\",\"\",\"\",\"\",\"14502.28\",\"Příchozí úhrada\",\"\",\"G-C7BGRWXCW24X5/ROC/G-C7BGRWXCW24X5\",\"\",\"\",\"TX-A1\",\"\"\n"
        "\"02.04.2026\",\"BOOKING.COM B.V.\",\"\",\"\",\"\",\"\",\"16691.47\",\"Příchozí úhrada\",\"\",\"NO.VE6GE8X4GAXJG6TQ/12762704\",\"\",\"\",\"TX-B1\",\"\"\n"
    ).encode("utf-16")


def test_import_uploaded_checkin_rolls_back_when_snapshot_persistence_fails(monkeypatch):
    conn = get_connection(":memory:")
    try:
        monkeypatch.setattr(
            web_module,
            "_apply_import_impacts",
            lambda *args, **kwargs: {"auto_started": [], "already_running": [], "open_without_report": [], "locked_notified": []},
        )
        import report.source_registry as source_registry_module

        original = source_registry_module.save_checkin_source_snapshot
        monkeypatch.setattr(
            source_registry_module,
            "save_checkin_source_snapshot",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("persist failed")),
        )
        try:
            try:
                import_uploaded_source(conn, "checkin", "Guest Report.csv", _checkin_csv_bytes(), imported_by="admin")
            except RuntimeError as exc:
                assert "persist failed" in str(exc)
            else:
                raise AssertionError("Expected import failure")
        finally:
            monkeypatch.setattr(source_registry_module, "save_checkin_source_snapshot", original)

        assert list_source_files(conn) == []
        assert list_import_runs(conn) == []
    finally:
        conn.close()


def test_checkin_backfill_materializes_legacy_blob_only_source():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = get_connection(db_path)
        import_source_file_with_result(
            conn,
            "checkin",
            "Guest Report.csv",
            _checkin_csv_bytes(),
            active=True,
            commit=True,
        )
        conn.close()

        conn = get_connection(db_path)
        try:
            groups = list_checkin_reservations(conn, active_only=True, latest_only=True)
            assert len(groups) == 1
            assert groups[0]["reservation_id"] == "chk-001"
        finally:
            conn.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_import_uploaded_source_for_bank_persists_transactions_immediately():
    conn = get_connection(":memory:")
    try:
        summary = import_uploaded_source(conn, "bank", "bank.csv", _bank_csv_bytes(), imported_by="admin")

        rows = conn.execute(
            """SELECT channel, tx_key, source_name, zprava
                 FROM bank_transactions
                ORDER BY channel, tx_key"""
        ).fetchall()

        assert summary["is_duplicate"] is False
        assert summary["persisted_airbnb_transactions"] == 1
        assert summary["persisted_booking_transactions"] == 1
        assert len(rows) == 2
        assert [row["channel"] for row in rows] == ["airbnb", "booking"]
        assert all(row["source_name"] == "bank.csv" for row in rows)
    finally:
        conn.close()


def test_import_local_file_uses_full_import_pipeline_for_checkin():
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        with open(path, "wb") as fh:
            fh.write(_checkin_csv_bytes())
        file_id = import_local_file(path, "checkin", imported_by="cli", db_path=db_path)

        conn = get_connection(db_path)
        try:
            runs = list_import_runs(conn)
            groups = list_checkin_reservations(conn, active_only=True, latest_only=True)
            assert file_id > 0
            assert len(runs) == 1
            assert runs[0]["source_file_id"] == file_id
            assert len(groups) == 1
            assert groups[0]["reservation_id"] == "chk-001"
        finally:
            conn.close()
    finally:
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists(db_path):
            os.remove(db_path)


def test_apply_import_impacts_autostarts_open_months_and_notifies_locked(monkeypatch):
    conn = get_connection(":memory:")
    try:
        monkeypatch.setattr(
            web_module,
            "get_report_month_state",
            lambda _conn, slug, year, month: {"status": "LOCKED"} if slug == "Locked_Slug" else {"status": "OPEN"},
        )
        monkeypatch.setattr(web_module, "mark_report_month_stale", lambda *a, **kw: None)
        monkeypatch.setattr(
            web_module,
            "_enqueue_report_generation",
            lambda _conn, *, slug, year, month, requested_by: ("started", {"slug": slug, "year": year, "month": month}),
        )

        result = web_module._apply_import_impacts(
            conn,
            {
                "source_type": "airbnb",
                "import_run_id": 5,
                "message": "delta",
                "affected_month_keys": [
                    ("28_Pluku_58", 2026, 3),
                    ("No_Report_Yet", 2026, 3),
                    ("Locked_Slug", 2026, 3),
                ],
            },
            requested_by="admin",
            background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
            config={},
        )

        started_slugs = [slug for slug, year, month in result["auto_started"]]
        assert "28_Pluku_58" in started_slugs
        assert "No_Report_Yet" in started_slugs
        assert result["auto_started"] == [("28_Pluku_58", 2026, 3), ("No_Report_Yet", 2026, 3)]
        assert result["locked_notified"] == [("Locked_Slug", 2026, 3)]
        notifications = list_report_month_notifications(conn, slug="Locked_Slug", year=2026, month=3)
        assert len(notifications) == 1
        assert "uzamčený měsíc" in notifications[0]["message"]
        assert notifications[0]["payload"]["change_lines"][0].startswith("Airbnb:")
    finally:
        conn.close()


def test_source_activate_applies_month_impacts(monkeypatch):
    conn = get_connection(":memory:")
    try:
        summary = import_uploaded_source(conn, "airbnb", "airbnb.csv", _airbnb_csv_bytes(), imported_by="admin")
        request = SimpleNamespace(session={})
        original_apply = web_module._apply_import_impacts
        web_module._apply_import_impacts = lambda *args, **kwargs: {
            "auto_started": [("28_Pluku_58", 2026, 3)],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
        }

        try:
            response = asyncio.run(
                web_module.source_activate(
                    request=request,
                    background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                    file_id=int(summary["source_file_id"]),
                    source_type="airbnb",
                    conn=conn,
                    config={},
                )
            )
        finally:
            web_module._apply_import_impacts = original_apply

        assert response.status_code == 303
        assert request.session["_flash"]["level"] == "success"
        assert "Auto-regenerace spuštěna" in request.session["_flash"]["detail"]
    finally:
        conn.close()


def test_source_deactivate_applies_month_impacts(monkeypatch):
    conn = get_connection(":memory:")
    try:
        summary = import_uploaded_source(conn, "airbnb", "airbnb.csv", _airbnb_csv_bytes(), imported_by="admin")
        request = SimpleNamespace(session={})
        original_apply = web_module._apply_import_impacts
        web_module._apply_import_impacts = lambda *args, **kwargs: {
            "auto_started": [],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [("28_Pluku_58", 2026, 3)],
        }

        try:
            response = asyncio.run(
                web_module.source_deactivate(
                    request=request,
                    background_tasks=SimpleNamespace(add_task=lambda *a, **kw: None),
                    file_id=int(summary["source_file_id"]),
                    source_type="airbnb",
                    conn=conn,
                    config={},
                )
            )
        finally:
            web_module._apply_import_impacts = original_apply

        assert response.status_code == 303
        assert request.session["_flash"]["level"] == "success"
        assert "Uzamčené měsíce pouze notifikovány" in request.session["_flash"]["detail"]
    finally:
        conn.close()


def test_list_report_month_notifications_can_hide_notifications_older_than_last_generation():
    conn = get_connection(":memory:")
    try:
        create_report_month_notification(
            conn,
            slug="28_Pluku_58",
            year=2026,
            month=3,
            event_type="IMPORT_IMPACT_AUTO_REGENERATE_STARTED",
            source_type="booking",
            message="Older notice",
            payload={"change_lines": ["Booking: old"]},
        )
        state = touch_report_month_generation(conn, "28_Pluku_58", 2026, 3)
        create_report_month_notification(
            conn,
            slug="28_Pluku_58",
            year=2026,
            month=3,
            event_type="IMPORT_IMPACT_AUTO_REGENERATE_STARTED",
            source_type="booking",
            message="Newer notice",
            payload={"change_lines": ["Booking: new"]},
        )

        notifications = list_report_month_notifications(
            conn,
            slug="28_Pluku_58",
            year=2026,
            month=3,
            created_after=state["last_generated_at"],
        )

        assert len(notifications) == 1
        assert notifications[0]["message"] == "Newer notice"
    finally:
        conn.close()
