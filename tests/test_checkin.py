from __future__ import annotations

from report.calculator import calculate_row
from report.checkin import apply_checkin_city_tax_overrides, load_checkin_groups
from report.db import (
    get_connection,
    get_report_history,
    log_report_generated,
    list_checkin_match_audit,
    list_checkin_reservations,
    replace_checkin_match_audit,
)
from report.status import effective_verification_status
from report.source_registry import analyze_import_delta, import_uploaded_source, validate_source_type
import report.web as web_module


# Current Hostify check-in export format (Birth Date based; the 14-col Nationality/
# Guest-Age format was removed in 0dd6072 "only Birth Date supported"). Age is derived
# from Birth Date at check-in, so fixtures provide a birth date for a desired age.
_CHECKIN_HEADER = (
    "Property Name;Full Name;Check-Out Date;Reservation ID;Check-In Date;"
    "Name;Surname;Birth Date;Nights of Stay;Booking Reference;Reservation External ID"
)


def _birth_date_for_age(age, checkin_ddmmyyyy: str) -> str:
    """Birth date (DD-MM-YYYY) that yields `age` at the given check-in; '' if age is None."""
    if age is None:
        return ""
    checkin_year = int(checkin_ddmmyyyy.split("-")[2])
    return f"01-01-{checkin_year - age}"


def _checkin_row(prop, full, name, surname, checkout, reservation_id, checkin, age) -> str:
    return ";".join([
        prop, full, checkout, reservation_id, checkin, name, surname,
        _birth_date_for_age(age, checkin), "", "", "",
    ])


def _checkin_csv(*rows: str) -> bytes:
    return ("\n".join([_CHECKIN_HEADER, *rows]) + "\n").encode("utf-8")


def _checkin_csv_bytes() -> bytes:
    return _checkin_csv(
        _checkin_row("28. Pluku 58", "John Adult", "John", "Adult", "12-03-2026", "chk-002", "10-03-2026", 35),
        _checkin_row("28. Pluku 58", "Jane Minor", "Jane", "Minor", "12-03-2026", "chk-002", "10-03-2026", 16),
        _checkin_row("28. Pluku 58", "Long Stay", "Long", "Stay", "20-05-2026", "chk-100", "10-03-2026", 40),
    )


def test_validate_source_type_accepts_checkin():
    assert validate_source_type("checkin") == "checkin"


def test_load_checkin_groups_parses_semicolon_export_and_applies_rules():
    groups = load_checkin_groups([{"original_name": "Guest Report.csv", "content": _checkin_csv_bytes()}])

    assert [group["reservation_id"] for group in groups] == ["chk-002", "chk-100"]
    assert groups[0]["paying_guests"] == 1
    assert groups[0]["exempt_guests"] == 1
    assert groups[0]["total_guests"] == 2
    assert groups[1]["stay_nights"] == 71
    assert groups[1]["paying_guests"] == 0
    assert groups[1]["exempt_guests"] == 1


def test_apply_checkin_city_tax_overrides_matches_by_property_dates_and_guest_name():
    groups = load_checkin_groups([{"original_name": "Guest Report.csv", "content": _checkin_csv_bytes()}])
    reservations = [
        {
            "reservation_id": "host-1",
            "confirmation_code": "ABC123",
            "guest_name": "John Adult",
            "check_in": "2026-03-10",
            "check_out": "2026-03-12",
            "nights": 2,
            "adults": 2,
            "children": 0,
            "infants": 0,
            "source": "Booking.com",
            "listing_nickname": "28. Pluku 58",
        }
    ]
    prop = {"slug": "28_Pluku_58", "display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58"}

    updated, stats = apply_checkin_city_tax_overrides(reservations, groups, prop, year=2026, month=3)

    assert stats["matched"] == 1
    assert updated[0]["checkin_verified"] is True
    assert updated[0]["city_tax_paying_guests"] == 1
    assert updated[0]["city_tax_exempt_guests"] == 1
    assert updated[0]["checkin_reservation_id"] == "chk-002"


def test_calculate_row_uses_checkin_city_tax_counts_for_balicky():
    reservation = {
        "reservation_id": "host-1",
        "confirmation_code": "ABC123",
        "guest_name": "John Adult",
        "check_in": "2026-03-10",
        "check_out": "2026-03-12",
        "nights": 2,
        "adults": 2,
        "children": 1,
        "infants": 0,
        "occupancy_adults": 2,
        "occupancy_children": 3,
        "occupancy_infants": 0,
        "city_tax_paying_guests": 1,
        "city_tax_exempt_guests": 2,
        "checkin_verified": True,
        "source": "Booking.com",
        "effective_payout_eur": 100,
        "czk_booked": 2500,
        "booking_rate": 25,
        "channel_commission_eur": 10,
        "cleaning_fee_eur": 5,
    }
    property_config = {
        "city_tax_rate": 50,
        "balicky_per_person": 100,
        "vat_rate": 0.21,
    }

    row = calculate_row(reservation, {"rate": 25, "valid_for": "2026-03-10"}, property_config, order=1)

    assert row["adults"] == 1
    assert row["children_infants"] == 2
    assert row["city_tax_czk"] == 100.0
    assert row["balicky_czk"] == 300.0
    assert row["checkin_verified"] is True


def test_analyze_import_delta_for_checkin_counts_grouped_reservations():
    conn = get_connection(":memory:")
    try:
        summary = analyze_import_delta(conn, "checkin", "Guest Report.csv", _checkin_csv_bytes())
        assert summary["detected_rows_count"] == 2
        assert summary["new_reservations_count"] == 2
        assert ("28_Pluku_58", 2026, 3) in summary["affected_month_keys"]
    finally:
        conn.close()


def test_display_verification_status_requires_tax_verification_for_full_match():
    status, note = web_module._display_verification_status(
        {
            "verification_status": "MATCHED",
            "tax_verification_required": True,
            "checkin_verified": False,
        }
    )

    assert status == "KE KONTROLE"
    assert "místní poplatky" in note.lower()


def test_import_uploaded_source_persists_checkin_groups_in_sqlite():
    conn = get_connection(":memory:")
    try:
        summary = import_uploaded_source(
            conn,
            "checkin",
            "Guest Report.csv",
            _checkin_csv_bytes(),
            imported_by="admin",
        )
        groups = list_checkin_reservations(conn, active_only=True)

        assert summary["is_duplicate"] is False
        assert summary["persisted_checkin_groups"] == 2
        assert summary["persisted_checkin_guest_rows"] == 3
        assert [group["reservation_id"] for group in groups] == ["chk-002", "chk-100"]
        assert groups[0]["property_slug"] == "28_Pluku_58"
        assert sorted(groups[0]["guest_names"]) == ["Jane Minor", "John Adult"]
    finally:
        conn.close()


def test_apply_checkin_city_tax_overrides_returns_audit_records():
    groups = load_checkin_groups([{"original_name": "Guest Report.csv", "content": _checkin_csv_bytes()}])
    reservations = [
        {
            "reservation_id": "host-1",
            "confirmation_code": "ABC123",
            "guest_name": "John Adult",
            "check_in": "2026-03-10",
            "check_out": "2026-03-12",
            "nights": 2,
            "adults": 2,
            "children": 0,
            "infants": 0,
            "source": "Booking.com",
            "listing_nickname": "28. Pluku 58",
        }
    ]
    prop = {"slug": "28_Pluku_58", "display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58"}

    _updated, stats = apply_checkin_city_tax_overrides(reservations, groups, prop, year=2026, month=3)

    reservation_records = [row for row in stats["audit_records"] if row["record_type"] == "reservation"]
    group_records = [row for row in stats["audit_records"] if row["record_type"] == "evidence_group"]

    assert any(row["match_status"] == "MATCHED" for row in reservation_records)
    assert any("city_tax_paying_guests" in row["overwritten_fields"] for row in reservation_records)
    assert any(row["match_status"] == "MATCHED" for row in group_records)


def test_replace_and_list_checkin_match_audit_roundtrip():
    conn = get_connection(":memory:")
    try:
        replace_checkin_match_audit(
            conn,
            "28_Pluku_58",
            2026,
            3,
            [
                {
                    "record_type": "reservation",
                    "confirmation_code": "ABC123",
                    "guest_name": "John Adult",
                    "source": "Booking.com",
                    "check_in": "2026-03-10",
                    "check_out": "2026-03-12",
                    "checkin_reservation_id": "chk-002",
                    "checkin_property_name": "28. Pluku 58",
                    "match_status": "MATCHED",
                    "overwritten_fields": {"city_tax_paying_guests": {"old": 2, "new": 1}},
                    "detail": {"reason": "unit test"},
                }
            ],
        )
        rows = list_checkin_match_audit(conn, slug="28_Pluku_58", year=2026, month=3)

        assert len(rows) == 1
        assert rows[0]["match_status"] == "MATCHED"
        assert rows[0]["overwritten_fields"]["city_tax_paying_guests"]["new"] == 1
        assert rows[0]["detail"]["reason"] == "unit test"
    finally:
        conn.close()


def test_cross_month_checkin_group_is_available_via_overlap_lookup():
    conn = get_connection(":memory:")
    try:
        content = _checkin_csv(
            _checkin_row("28. Pluku 58", "John Adult", "John", "Adult", "10-04-2026", "chk-overlap", "30-03-2026", 35),
        )
        import_uploaded_source(conn, "checkin", "Guest Report overlap.csv", content, imported_by="admin")

        april_rows = list_checkin_reservations(conn, active_only=True, overlap_year=2026, overlap_month=4, latest_only=True)

        assert [row["reservation_id"] for row in april_rows] == ["chk-overlap"]
    finally:
        conn.close()


def test_latest_only_checkin_rows_prefer_newest_active_source():
    conn = get_connection(":memory:")
    try:
        first = _checkin_csv(
            _checkin_row("28. Pluku 58", "John Adult", "John", "Adult", "12-03-2026", "chk-dup", "10-03-2026", 35),
        )
        second = _checkin_csv(
            _checkin_row("28. Pluku 58", "John Adult", "John", "Adult", "12-03-2026", "chk-dup", "10-03-2026", 35),
            _checkin_row("28. Pluku 58", "Jane Minor", "Jane", "Minor", "12-03-2026", "chk-dup", "10-03-2026", 16),
        )
        import_uploaded_source(conn, "checkin", "dup-1.csv", first, imported_by="admin")
        import_uploaded_source(conn, "checkin", "dup-2.csv", second, imported_by="admin")

        rows = list_checkin_reservations(conn, active_only=True, latest_only=True)

        assert len(rows) == 1
        assert rows[0]["reservation_id"] == "chk-dup"
        assert rows[0]["total_guests"] == 2
    finally:
        conn.close()


def test_missing_age_keeps_row_under_review():
    groups = load_checkin_groups(
        [{
            "original_name": "Guest Report.csv",
            "content": _checkin_csv(
                _checkin_row("28. Pluku 58", "Unknown Age", "Unknown", "Age", "12-03-2026", "chk-missing", "10-03-2026", None),
            ),
        }]
    )
    reservations = [
        {
            "reservation_id": "host-1",
            "confirmation_code": "ABC123",
            "guest_name": "Unknown Age",
            "check_in": "2026-03-10",
            "check_out": "2026-03-12",
            "nights": 2,
            "adults": 1,
            "children": 0,
            "infants": 0,
            "source": "Booking.com",
            "listing_nickname": "28. Pluku 58",
            "verification_status": "MATCHED",
        }
    ]
    prop = {"slug": "28_Pluku_58", "display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58"}

    updated, _stats = apply_checkin_city_tax_overrides(reservations, groups, prop, year=2026, month=3)
    status, _note = effective_verification_status(updated[0])

    assert updated[0]["checkin_verified"] is False
    assert updated[0]["city_tax_paying_guests"] == 0
    assert updated[0]["city_tax_exempt_guests"] == 1
    assert status == "KE KONTROLE"


def test_apply_checkin_city_tax_overrides_skips_cancelled_reservations():
    groups = load_checkin_groups([{"original_name": "Guest Report.csv", "content": _checkin_csv_bytes()}])
    reservations = [
        {
            "reservation_id": "host-1",
            "confirmation_code": "ABC123",
            "guest_name": "John Adult",
            "check_in": "2026-03-10",
            "check_out": "2026-03-12",
            "nights": 2,
            "adults": 2,
            "children": 0,
            "infants": 0,
            "source": "Booking.com",
            "listing_nickname": "28. Pluku 58",
            "is_cancelled": True,
            "checkin_verified": True,
            "checkin_reservation_id": "stale-id",
        }
    ]
    prop = {"slug": "28_Pluku_58", "display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58"}

    updated, stats = apply_checkin_city_tax_overrides(reservations, groups, prop, year=2026, month=3)

    assert updated[0]["tax_verification_required"] is False
    assert updated[0]["checkin_verified"] is False
    assert updated[0]["checkin_reservation_id"] == ""
    assert stats["matched"] == 0
    assert stats["reservations_without_evidence"] == 0
    reservation_records = [row for row in stats["audit_records"] if row["record_type"] == "reservation"]
    assert reservation_records[0]["match_status"] == "SKIPPED_CANCELLED"


def test_log_report_generated_uses_effective_verification_status():
    conn = get_connection(":memory:")
    try:
        log_report_generated(
            conn,
            "28_Pluku_58",
            2026,
            3,
            "/tmp/report.xlsx",
            [
                {
                    "verification_status": "MATCHED",
                    "tax_verification_required": True,
                    "checkin_verified": False,
                    "checkin_missing_age_guests": 0,
                }
            ],
        )
        history = get_report_history(conn, slug="28_Pluku_58", limit=1)[0]

        assert history["matched"] == 0
        assert history["ke_kontrole"] == 1
    finally:
        conn.close()
