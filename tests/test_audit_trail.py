from __future__ import annotations

import csv
import io

from report.db import get_connection
from report.routes import audit_routes
from report.source_registry import import_uploaded_source


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
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def test_audit_load_events_includes_import_run_with_document_details():
    conn = get_connection(":memory:")
    try:
        import_uploaded_source(
            conn,
            "airbnb",
            "airbnb_march.csv",
            _airbnb_csv_bytes(),
            imported_by="nikita",
        )

        events = audit_routes._load_events(conn, slug="", year=0, month=0)
        import_events = [event for event in events if event["event_type"] == "import"]

        assert len(import_events) == 1
        event = import_events[0]
        assert event["source_name"] == "airbnb_march.csv"
        assert event["source_type"] == "airbnb"
        assert event["imported_by"] == "nikita"
        assert event["primary_slug"] == "28_Pluku_58"
        assert event["primary_year"] == 2026
        assert event["primary_month"] == 3
        assert any("Airbnb:" in line for line in event["detail_lines"])
    finally:
        conn.close()


def test_audit_load_events_filters_imports_by_slug_year_month():
    conn = get_connection(":memory:")
    try:
        import_uploaded_source(
            conn,
            "airbnb",
            "airbnb_march.csv",
            _airbnb_csv_bytes(),
            imported_by="nikita",
        )

        matching = audit_routes._load_events(conn, slug="28_Pluku_58", year=2026, month=3)
        non_matching = audit_routes._load_events(conn, slug="Other_Property", year=2026, month=3)

        assert any(event["event_type"] == "import" for event in matching)
        assert not any(event["event_type"] == "import" for event in non_matching)
    finally:
        conn.close()


def test_build_import_event_keeps_multiple_affected_objects_visible():
    event = audit_routes._build_import_event(
        {
            "id": 7,
            "source_type": "booking",
            "imported_by": "nikita",
            "imported_at": "2026-04-10T09:00:00+00:00",
            "summary_json": (
                '{"source_name":"booking.csv","message":"ok","affected_month_keys":'
                '[["28_Pluku_58",2026,3],["Second_Property",2026,3],["Third_Property",2026,4]]}'
            ),
            "source_file_name": "booking.csv",
            "duplicate_source_file_name": "",
            "duplicate_of_source_file_id": None,
        },
        slug="",
        year=0,
        month=0,
    )

    assert event is not None
    assert event["link_slug"] == ""
    assert event["display_slugs"] == ["28_Pluku_58", "Second_Property", "Third_Property"]
    assert event["display_periods"] == [(2026, 3), (2026, 4)]
