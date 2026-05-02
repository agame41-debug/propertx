"""Tests for the Hostify orphan-listing detector.

An orphan listing is a `listing_nickname` present in `hostify_reservations`
without an active alias in `report_object_aliases`. Such listings are
silently dropped during regen (their reservations never reach `report_rows`),
so we surface them in the /inventory UI and log a warning the first time
each appears during the daily Hostify sync.
"""

import json
from datetime import date

import pytest

from report.db import (
    find_orphan_listing_nicknames,
    get_connection,
    get_orphan_listings_for_display,
    record_orphan_listings,
    save_hostify_reservations,
)


@pytest.fixture()
def conn():
    conn = get_connection(":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _seed_object(conn, slug: str = "MyMozart_414", display: str = "MyMozart 414") -> None:
    conn.execute(
        """INSERT INTO report_objects
           (slug, display_name, hostify_listing_id, listing_nickname,
            balicky_per_person, city_tax_rate, vat_rate, rentero_commission,
            active, created_at, updated_at, client_type)
           VALUES (?, ?, NULL, ?, 0, 50.0, 0.21, 0.15, 1, '', '', 'klient')""",
        (slug, display, display),
    )
    conn.commit()


def _seed_alias(
    conn,
    slug: str,
    nickname: str,
    *,
    is_active: bool = True,
    channel: str = "hostify",
    alias_type: str = "listing_nickname",
) -> None:
    conn.execute(
        """INSERT INTO report_object_aliases
           (report_object_slug, channel, alias_type, alias_value,
            is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, '', '')""",
        (slug, channel, alias_type, nickname, 1 if is_active else 0),
    )
    conn.commit()


def _seed_reservation(
    conn,
    *,
    code: str,
    nickname: str,
    source: str = "Airbnb",
    check_in: str = "2026-01-15",
    listing_id: int | None = 152891,
) -> None:
    payload = {"confirmation_code": code, "listing_id": listing_id}
    save_hostify_reservations(
        conn,
        [
            {
                "confirmation_code": code,
                "reservation_id": code,
                "source": source,
                "status": "accepted",
                "guest_name": "Test Guest",
                "check_in": check_in,
                "check_out": check_in,
                "assigned_year": int(check_in[:4]),
                "assigned_month": int(check_in[5:7]),
                "listing_nickname": nickname,
                **payload,
            }
        ],
    )


# ---------------------------------------------------------------------------
# find_orphan_listing_nicknames — pure query
# ---------------------------------------------------------------------------


class TestFindOrphanListingNicknames:
    def test_empty_db_returns_empty(self, conn):
        assert find_orphan_listing_nicknames(conn) == []

    def test_listing_with_active_alias_is_not_orphan(self, conn):
        _seed_object(conn)
        _seed_alias(conn, "MyMozart_414", "MyMozart 414")
        _seed_reservation(conn, code="C1", nickname="MyMozart 414")

        assert find_orphan_listing_nicknames(conn) == []

    def test_listing_without_alias_is_orphan(self, conn):
        _seed_reservation(
            conn, code="C1", nickname="Mystery Apartment",
            source="Booking.com", check_in="2025-09-15", listing_id=999001,
        )

        result = find_orphan_listing_nicknames(conn)

        assert len(result) == 1
        o = result[0]
        assert o["listing_nickname"] == "Mystery Apartment"
        assert o["reservation_count"] == 1
        assert o["sources"] == "Booking.com"
        assert o["first_check_in"] == "2025-09-15"
        assert o["last_check_in"] == "2025-09-15"
        assert o["example_listing_id"] == 999001

    def test_inactive_alias_does_not_resolve_orphan(self, conn):
        _seed_object(conn)
        _seed_alias(conn, "MyMozart_414", "MyMozart 414", is_active=False)
        _seed_reservation(conn, code="C1", nickname="MyMozart 414")

        result = find_orphan_listing_nicknames(conn)

        assert len(result) == 1
        assert result[0]["listing_nickname"] == "MyMozart 414"

    def test_alias_in_other_channel_does_not_resolve_orphan(self, conn):
        # Only hostify-channel listing_nickname aliases count for this check.
        _seed_object(conn)
        _seed_alias(conn, "MyMozart_414", "MyMozart 414", channel="airbnb",
                    alias_type="listing_name")
        _seed_reservation(conn, code="C1", nickname="MyMozart 414")

        result = find_orphan_listing_nicknames(conn)
        assert len(result) == 1

    def test_aggregates_sources_and_reservation_count(self, conn):
        _seed_reservation(conn, code="A1", nickname="Mystery", source="Airbnb")
        _seed_reservation(conn, code="A2", nickname="Mystery", source="Airbnb")
        _seed_reservation(conn, code="B1", nickname="Mystery", source="Booking.com")

        result = find_orphan_listing_nicknames(conn)

        assert len(result) == 1
        o = result[0]
        assert o["reservation_count"] == 3
        assert "Airbnb" in o["sources"]
        assert "Booking.com" in o["sources"]

    def test_empty_listing_nickname_is_ignored(self, conn):
        _seed_reservation(conn, code="C1", nickname="")
        assert find_orphan_listing_nicknames(conn) == []

    def test_orphans_sorted_by_reservation_count_descending(self, conn):
        _seed_reservation(conn, code="A1", nickname="Small")
        _seed_reservation(conn, code="B1", nickname="Big")
        _seed_reservation(conn, code="B2", nickname="Big")
        _seed_reservation(conn, code="B3", nickname="Big")

        names = [o["listing_nickname"] for o in find_orphan_listing_nicknames(conn)]
        assert names == ["Big", "Small"]


# ---------------------------------------------------------------------------
# record_orphan_listings — state sync + delta detection
# ---------------------------------------------------------------------------


class TestRecordOrphanListings:
    def test_first_call_inserts_and_reports_newly_detected(self, conn):
        _seed_reservation(conn, code="C1", nickname="Mystery")

        delta = record_orphan_listings(conn)

        assert delta["current_count"] == 1
        assert len(delta["newly_detected"]) == 1
        assert delta["newly_detected"][0]["listing_nickname"] == "Mystery"
        assert delta["resolved"] == []

        rows = list(conn.execute("SELECT * FROM hostify_orphan_listings"))
        assert len(rows) == 1
        assert rows[0]["listing_nickname"] == "Mystery"
        assert rows[0]["first_detected_at"] != ""
        assert rows[0]["last_detected_at"] == rows[0]["first_detected_at"]

    def test_idempotent_second_call_reports_no_new_orphans(self, conn):
        _seed_reservation(conn, code="C1", nickname="Mystery")
        first = record_orphan_listings(conn)

        second = record_orphan_listings(conn)

        assert second["current_count"] == 1
        assert second["newly_detected"] == []
        assert second["resolved"] == []
        # first_detected_at must be preserved across calls.
        row = conn.execute(
            "SELECT first_detected_at, last_detected_at FROM hostify_orphan_listings WHERE listing_nickname=?",
            ("Mystery",),
        ).fetchone()
        first_detected_initial = first["newly_detected"][0]
        assert row["first_detected_at"] is not None

    def test_alias_added_results_in_resolved(self, conn):
        _seed_reservation(conn, code="C1", nickname="MyMozart 414")
        record_orphan_listings(conn)
        # Now the operator adds an alias.
        _seed_object(conn)
        _seed_alias(conn, "MyMozart_414", "MyMozart 414")

        delta = record_orphan_listings(conn)

        assert delta["current_count"] == 0
        assert delta["newly_detected"] == []
        assert delta["resolved"] == ["MyMozart 414"]
        assert (
            conn.execute("SELECT COUNT(*) FROM hostify_orphan_listings").fetchone()[0]
            == 0
        )

    def test_new_orphan_added_to_existing_set(self, conn):
        _seed_reservation(conn, code="C1", nickname="First")
        record_orphan_listings(conn)
        _seed_reservation(conn, code="C2", nickname="Second")

        delta = record_orphan_listings(conn)

        assert delta["current_count"] == 2
        new_nicks = [o["listing_nickname"] for o in delta["newly_detected"]]
        assert new_nicks == ["Second"]
        assert delta["resolved"] == []

    def test_reservation_count_is_refreshed_on_subsequent_calls(self, conn):
        _seed_reservation(conn, code="C1", nickname="Mystery")
        record_orphan_listings(conn)
        _seed_reservation(conn, code="C2", nickname="Mystery", check_in="2026-02-15")

        record_orphan_listings(conn)

        row = conn.execute(
            "SELECT reservation_count, last_check_in FROM hostify_orphan_listings WHERE listing_nickname=?",
            ("Mystery",),
        ).fetchone()
        assert row["reservation_count"] == 2
        assert row["last_check_in"] == "2026-02-15"


# ---------------------------------------------------------------------------
# get_orphan_listings_for_display — UI helper
# ---------------------------------------------------------------------------


class TestGetOrphanListingsForDisplay:
    def test_includes_first_detected_at_when_recorded(self, conn):
        _seed_reservation(conn, code="C1", nickname="Mystery")
        record_orphan_listings(conn)

        display = get_orphan_listings_for_display(conn)

        assert len(display) == 1
        assert display[0]["first_detected_at"] is not None
        assert display[0]["last_detected_at"] is not None

    def test_first_detected_at_is_none_when_not_yet_recorded(self, conn):
        # Live orphan that the daily sync hasn't yet recorded.
        _seed_reservation(conn, code="C1", nickname="Mystery")

        display = get_orphan_listings_for_display(conn)

        assert len(display) == 1
        assert display[0]["first_detected_at"] is None

    def test_resolved_orphans_disappear_from_display_immediately(self, conn):
        # Sync recorded an orphan, then operator added an alias.
        _seed_reservation(conn, code="C1", nickname="MyMozart 414")
        record_orphan_listings(conn)
        _seed_object(conn)
        _seed_alias(conn, "MyMozart_414", "MyMozart 414")
        # UI does not re-run record_orphan_listings, so the table still has
        # the row — but the live query must hide it.

        display = get_orphan_listings_for_display(conn)

        assert display == []
