"""Multi-room Booking.com group reservations.

A single guest can book several rooms in one Booking.com booking. Hostify
returns these as SEPARATE reservations — each with its own internal
``reservation_id`` and its own listing — but they all share ONE
``confirmation_code`` (the Booking.com number).

``hostify_reservations`` must therefore be keyed by ``reservation_id`` (unique
per room), NOT by ``confirmation_code``. Keying by ``confirmation_code`` made
the sibling rooms collide on the primary key, so all but one room were silently
dropped and never reached the per-object reports (regression that forced manual
Excel splits).
"""

import pytest

from report.db import (
    get_connection,
    get_hostify_reservations_for_listing_month,
    save_hostify_reservations,
)


@pytest.fixture()
def conn():
    conn = get_connection(":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _room(reservation_id: str, code: str, nickname: str) -> dict:
    return {
        "confirmation_code": code,
        "reservation_id": reservation_id,
        "source": "Booking.com",
        "status": "accepted",
        "guest_name": "Dávid Kapitány",
        "check_in": "2026-05-01",
        "check_out": "2026-05-03",
        "assigned_year": 2026,
        "assigned_month": 5,
        "listing_nickname": nickname,
        "listing_id": int(reservation_id[-3:]) if reservation_id[-3:].isdigit() else None,
    }


GROUP_CODE = "6369981330"
ROOMS = [
    _room("3940865", GROUP_CODE, "Vaclavske namesti 3101 - Bcom"),
    _room("3940866", GROUP_CODE, "Vaclavske namesti 3102 - Bcom"),
    _room("3940867", GROUP_CODE, "Václavské náměstí 2001 - Bcom"),
]


class TestGroupReservationPersistence:
    def test_all_rooms_persist_despite_shared_confirmation_code(self, conn):
        save_hostify_reservations(conn, ROOMS)

        n = conn.execute(
            "SELECT COUNT(*) FROM hostify_reservations WHERE confirmation_code = ?",
            (GROUP_CODE,),
        ).fetchone()[0]
        assert n == 3, "all sibling rooms of a Booking group must persist"

        ids = {
            r["reservation_id"]
            for r in conn.execute(
                "SELECT reservation_id FROM hostify_reservations WHERE confirmation_code = ?",
                (GROUP_CODE,),
            )
        }
        assert ids == {"3940865", "3940866", "3940867"}

    def test_each_room_resolves_to_its_own_listing_month(self, conn):
        save_hostify_reservations(conn, ROOMS)

        for nick in (
            "Vaclavske namesti 3101 - Bcom",
            "Vaclavske namesti 3102 - Bcom",
            "Václavské náměstí 2001 - Bcom",
        ):
            rows = get_hostify_reservations_for_listing_month(
                conn, listing_nicknames=[nick], year=2026, month=5
            )
            assert len(rows) == 1, f"room {nick} must be retrievable on its own listing"
            assert rows[0]["listing_nickname"] == nick

    def test_group_split_divides_payout_by_base_plus_cleaning_share(self, conn):
        """Booking group payout splits per room by (base+cleaning) share,
        matching the manual spreadsheet; CZK still sums to the bank total."""
        from report.engine import _split_group_booking_payouts

        rooms = [
            dict(_room("3940867", GROUP_CODE, "Václavské náměstí 2001 - Bcom"),
                 base_price_eur=893.92, cleaning_fee_eur=55.0, channel_commission_eur=142.34),
            dict(_room("3940865", GROUP_CODE, "Vaclavske namesti 3101 - Bcom"),
                 base_price_eur=938.94, cleaning_fee_eur=55.0, channel_commission_eur=188.63),
            dict(_room("3940866", GROUP_CODE, "Vaclavske namesti 3102 - Bcom"),
                 base_price_eur=637.93, cleaning_fee_eur=55.0, channel_commission_eur=103.94),
        ]
        save_hostify_reservations(conn, rooms)

        GROUP_EUR, GROUP_CZK = 2200.89, 53634.57
        booking_batch_map = {
            GROUP_CODE: {"total_amount_eur": GROUP_EUR, "total_amount_czk": GROUP_CZK}
        }
        # Each room row currently carries the GROUP total (as batch attribution leaves it).
        # Each room came out of verify_reservation as a bogus ROZDÍL (per-room
        # Hostify payout vs the whole group's CSV total).
        all_verified = [
            {"confirmation_code": GROUP_CODE, "source": "Booking.com",
             "listing_nickname": nick, "effective_payout_eur": GROUP_EUR,
             "czk_booked": GROUP_CZK, "channel_commission_eur": 434.91,
             "verification_status": "ROZDÍL", "verification_diff": -1394.31}
            for nick in ("Václavské náměstí 2001 - Bcom",
                         "Vaclavske namesti 3101 - Bcom",
                         "Vaclavske namesti 3102 - Bcom")
        ]
        _split_group_booking_payouts(conn, all_verified, booking_batch_map)

        weights = {
            "Václavské náměstí 2001 - Bcom": 948.92,
            "Vaclavske namesti 3101 - Bcom": 993.94,
            "Vaclavske namesti 3102 - Bcom": 692.93,
        }
        total_w = sum(weights.values())
        by_nick = {r["listing_nickname"]: r for r in all_verified}
        for nick, w in weights.items():
            share = w / total_w
            assert by_nick[nick]["effective_payout_eur"] == pytest.approx(GROUP_EUR * share, abs=0.01)
            assert by_nick[nick]["channel_commission_eur"] == pytest.approx(434.91 * share, abs=0.01)
            assert by_nick[nick]["is_group_split"] is True
            # The bogus per-room-vs-group ROZDÍL must be cleared.
            assert by_nick[nick]["verification_status"] == "MATCHED"
            assert by_nick[nick]["verification_diff"] is None
            assert by_nick[nick]["csv_payout_eur"] == pytest.approx(GROUP_EUR * share, abs=0.01)
        # CZK must still sum to the bank total exactly (no leakage).
        assert sum(r["czk_booked"] for r in all_verified) == pytest.approx(GROUP_CZK, abs=0.01)

    def test_no_split_without_base_prices(self, conn):
        """Until base prices are populated (resync), the split is skipped."""
        from report.engine import _split_group_booking_payouts

        # rooms WITHOUT base_price_eur in payload
        save_hostify_reservations(conn, ROOMS)
        all_verified = [
            {"confirmation_code": GROUP_CODE, "source": "Booking.com",
             "listing_nickname": "Vaclavske namesti 3101 - Bcom",
             "effective_payout_eur": 2200.89, "czk_booked": 53634.57}
        ]
        _split_group_booking_payouts(
            conn, all_verified, {GROUP_CODE: {"total_amount_eur": 2200.89, "total_amount_czk": 53634.57}}
        )
        # untouched
        assert all_verified[0]["effective_payout_eur"] == 2200.89
        assert "is_group_split" not in all_verified[0]

    def test_single_room_booking_not_split(self, conn):
        from report.engine import _split_group_booking_payouts

        save_hostify_reservations(conn, [
            dict(_room("5000001", "SOLO123", "Solo Listing - Bcom"),
                 base_price_eur=500.0, cleaning_fee_eur=55.0, channel_commission_eur=80.0),
        ])
        all_verified = [
            {"confirmation_code": "SOLO123", "source": "Booking.com",
             "listing_nickname": "Solo Listing - Bcom",
             "effective_payout_eur": 475.0, "czk_booked": 11500.0}
        ]
        _split_group_booking_payouts(
            conn, all_verified, {"SOLO123": {"total_amount_eur": 475.0, "total_amount_czk": 11500.0}}
        )
        assert all_verified[0]["effective_payout_eur"] == 475.0
        assert "is_group_split" not in all_verified[0]

    def test_resaving_same_room_updates_in_place(self, conn):
        """Re-sync of the same reservation_id must UPDATE, not duplicate."""
        save_hostify_reservations(conn, ROOMS)
        # Same reservation_id, now cancelled — must overwrite the accepted row.
        updated = dict(ROOMS[0])
        updated["status"] = "cancelled"
        save_hostify_reservations(conn, [updated])

        rows = conn.execute(
            "SELECT status FROM hostify_reservations WHERE reservation_id = ?",
            ("3940865",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "cancelled"
