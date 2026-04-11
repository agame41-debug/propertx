from report.loader import normalize_reservation


def test_normalize_reservation_keeps_hostify_city_tax():
    row = normalize_reservation(
        {
            "id": 1,
            "channel_reservation_id": "BKG1",
            "guest_name": "Guest",
            "checkIn": "2026-03-11",
            "checkOut": "2026-03-17",
            "nights": 6,
            "adults": 2,
            "children": 0,
            "infants": 0,
            "cleaning_fee": 65,
            "city_tax": 24,
            "channel_commission": 59.16,
            "transaction_fee": 5.92,
            "payout_price": 353.32,
            "source": "Booking.com",
            "status": "accepted",
            "confirmed_at": "2026-03-09 20:18:55",
            "listing_id": 184991,
            "listing_nickname": "28. Pluku 58 - Bcom",
        }
    )
    assert row is not None
    assert row["city_tax_eur"] == 24.0


def test_normalize_reservation_skips_timedout_status():
    row = normalize_reservation(
        {
            "id": 2,
            "channel_reservation_id": "AIR1",
            "guest_name": "Guest",
            "checkIn": "2026-03-11",
            "checkOut": "2026-03-17",
            "nights": 6,
            "adults": 2,
            "children": 0,
            "infants": 0,
            "cleaning_fee": 65,
            "city_tax": 0,
            "channel_commission": 59.16,
            "transaction_fee": 0,
            "payout_price": 353.32,
            "source": "Airbnb",
            "status": "timedout",
            "confirmed_at": "2026-03-09 20:18:55",
            "listing_id": 184991,
            "listing_nickname": "28. Pluku 58",
        }
    )
    assert row is None
