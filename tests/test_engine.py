from __future__ import annotations

import json
from datetime import date

import pytest

from report.db import get_connection, MONTH_STATUS_LOCKED
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


def test_generate_report_in_process_returns_dict_with_rows_count():
    conn = get_connection(":memory:")
    try:
        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, _make_config()
        )
        assert isinstance(result, dict)
        assert "rows_count" in result
        assert result["rows_count"] == 0  # no hostify data in empty DB
    finally:
        conn.close()


def test_generate_report_in_process_skips_locked_month():
    from report.config import sync_json_config_to_db
    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)
        # Lock the month
        conn.execute(
            """INSERT OR REPLACE INTO report_month_state
               (slug, year, month, status, data_state)
               VALUES (?, ?, ?, 'LOCKED', 'READY')""",
            ("test_prop", 2026, 3),
        )
        conn.commit()

        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, config
        )
        assert result["skipped"] is True
        assert result["reason"] == "locked"
    finally:
        conn.close()


def test_generate_report_in_process_saves_rows_to_db():
    from report.db import get_report_rows, save_hostify_reservations
    from report.config import sync_json_config_to_db
    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)
        # Seed one Hostify reservation — save normalized form but with camelCase dates
        # so that filter_for_property_month (which calls _normalize_reservation) can parse them.
        # save_hostify_reservations stores payload_json; get_hostify_reservations_for_listing_month
        # returns those payloads which are then re-normalized by filter_for_property_month.
        raw = {
            "channel_reservation_id": "HM123456789",
            "guest_name": "Test Guest",
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
        from report.loader import normalize_reservations
        normalized = normalize_reservations([raw])
        # Re-attach camelCase date keys so filter_for_property_month can re-normalize
        for n in normalized:
            n["checkIn"] = n["check_in"]
            n["checkOut"] = n["check_out"]
        save_hostify_reservations(conn, normalized)

        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, config
        )
        rows = get_report_rows(conn, "test_prop", 2026, 3)
        assert result["rows_count"] >= 1
        assert any(r.get("confirmation_code") == "HM123456789" for r in rows)
    finally:
        conn.close()


def test_generate_report_in_process_matches_extra_hostify_child_listing_alias():
    from report.db import get_report_rows, save_hostify_reservations
    from report.config import sync_json_config_to_db

    conn = get_connection(":memory:")
    try:
        config = _make_config()
        config["properties"]["test_prop"]["channels"]["hostify"]["listing_names"] = [
            "Test Listing - Marriott"
        ]
        sync_json_config_to_db(conn, config)

        raw = {
            "channel_reservation_id": "MR123456",
            "guest_name": "Marriott Guest",
            "listing_nickname": "Test Listing - Marriott",
            "checkIn": "2026-03-10",
            "checkOut": "2026-03-12",
            "status": "confirmed",
            "source": "Marriott",
            "adults": 2,
            "children": 0,
            "infants": 0,
            "cleaning_fee": 0.0,
            "city_tax": 0.0,
            "channel_commission": 15.0,
            "payout_price": 85.0,
            "confirmedAt": "2026-01-15T10:00:00",
        }
        from report.loader import normalize_reservations

        normalized = normalize_reservations([raw])
        for row in normalized:
            row["checkIn"] = row["check_in"]
            row["checkOut"] = row["check_out"]
        save_hostify_reservations(conn, normalized)

        result = generate_report_in_process(conn, "test_prop", 2026, 3, config)
        rows = get_report_rows(conn, "test_prop", 2026, 3)

        assert result["rows_count"] >= 1
        marriott_rows = [row for row in rows if row.get("confirmation_code") == "MR123456"]
        assert len(marriott_rows) == 1
        assert marriott_rows[0]["source"] == "Marriott"
        assert marriott_rows[0]["verification_status"] == "CHYBÍ_V_CSV"
    finally:
        conn.close()


def test_generate_report_in_process_does_not_readd_moved_out_reservation_as_csv_only(monkeypatch):
    from report.config import sync_json_config_to_db
    from report.db import get_report_rows, save_hostify_reservations
    from report.db_controls import create_reservation_month_assignment
    from report.loader import normalize_reservations

    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)

        raw = {
            "channel_reservation_id": "HM_MOVE",
            "guest_name": "Moved Guest",
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
        for row in normalized:
            row["checkIn"] = row["check_in"]
            row["checkOut"] = row["check_out"]
        save_hostify_reservations(conn, normalized)

        create_reservation_month_assignment(conn, {
            "slug": "test_prop",
            "confirmation_code": "HM_MOVE",
            "target_year": 2026,
            "target_month": 2,
            "original_year": 2026,
            "original_month": 3,
            "reason": "Guest requested move",
            "actor": "test",
        })

        monkeypatch.setattr(
            "report.engine._resolve_sources",
            lambda _conn, source_type: [{"id": 1}] if source_type == "airbnb" else [],
        )
        monkeypatch.setattr(
            "report.engine.load_airbnb_csv",
            lambda _sources: {
                "HM_MOVE": {
                    "guest": "Moved Guest",
                    "listing": "Test Listing",
                    "check_in": date(2026, 3, 10),
                    "check_out": date(2026, 3, 12),
                    "nights": 2,
                    "amount_eur": 85.0,
                    "cleaning_fee_eur": 0.0,
                    "service_fee_eur": 15.0,
                    "date_reserved": date(2026, 1, 15),
                    "source_file": "airbnb.csv",
                }
            },
        )
        monkeypatch.setattr(
            "report.engine.build_airbnb_payout_data",
            lambda _sources: {"reservation_map": {}, "batches": [], "items": []},
        )
        monkeypatch.setattr("report.engine.load_booking_csv", lambda _sources: {})
        monkeypatch.setattr(
            "report.engine.build_booking_payout_data",
            lambda _sources: {"reservation_map": {}, "batches": [], "items": []},
        )
        monkeypatch.setattr("report.engine.preload_rates_for_month", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "report.engine.get_rate_for_reservation",
            lambda *_args, **_kwargs: {"rate": 25.0, "valid_for": "2026-03-10"},
        )

        result = generate_report_in_process(conn, "test_prop", 2026, 3, config)
        rows = get_report_rows(conn, "test_prop", 2026, 3)

        assert result["rows_count"] == 0
        assert rows == []
    finally:
        conn.close()


def test_flag_duplicate_codes_within_snapshot_annotates_violators():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "AAA", "verification_comment": ""},
        {"confirmation_code": "BBB", "verification_comment": "preexisting"},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 2
    assert rows[0]["verification_comment"].startswith("INTEGRITY:")
    assert rows[2]["verification_comment"].startswith("INTEGRITY:")
    assert rows[1]["verification_comment"] == "preexisting"  # untouched


def test_flag_duplicate_codes_ignores_empty_codes():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "", "verification_comment": ""},
        {"confirmation_code": "", "verification_comment": ""},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 0  # empty codes do not trigger
    assert all("INTEGRITY:" not in r["verification_comment"] for r in rows)


def test_flag_duplicate_codes_ignores_suffixed_synthetic_codes():
    """__ADJ, __AC, __SP[N] suffixes are part of the stored code, so they
    are distinct from their parents and don't trigger as duplicates."""
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "HMRA54", "verification_comment": ""},
        {"confirmation_code": "HMRA54__ADJ", "verification_comment": ""},
        {"confirmation_code": "HMRA54__SP1", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 0


def test_flag_duplicate_codes_preserves_existing_comment():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "AAA", "verification_comment": "RECOVERED: prior"},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    _flag_duplicate_codes_within_snapshot(rows)
    assert "RECOVERED: prior" in rows[0]["verification_comment"]
    assert rows[0]["verification_comment"].startswith("INTEGRITY:")


def test_generate_report_in_process_flags_no_duplicate_in_clean_data():
    """Smoke: empty DB → no duplicates → no INTEGRITY: comments."""
    conn = get_connection(":memory:")
    try:
        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, _make_config()
        )
        assert "rows_count" in result
        rows = conn.execute(
            "SELECT data FROM report_rows WHERE slug = 'test_prop' AND year = 2026 AND month = 3"
        ).fetchall()
        for r in rows:
            data = json.loads(r["data"])
            assert "INTEGRITY:" not in (data.get("verification_comment") or "")
    finally:
        conn.close()
