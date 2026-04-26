"""Negative payout_eur (refund flow) must not yield a negative implied rate."""

from __future__ import annotations

from report.calculator import calculate_row


def _booking_reservation(*, payout_eur: float, czk_booked: float, booking_rate: float = 0.0) -> dict:
    return {
        "confirmation_code": "BK_REFUND",
        "source": "booking",
        "effective_payout_eur": payout_eur,
        "czk_booked": czk_booked,
        "booking_rate": booking_rate,
        "channel_commission_eur": 0.0,
        "cleaning_fee_eur": 0.0,
        "nights": 1,
        "occupancy_adults": 1,
        "occupancy_children": 0,
        "occupancy_infants": 0,
        "city_tax_paying_guests": 1,
        "city_tax_exempt_guests": 0,
        "check_in": "2026-04-10",
        "check_out": "2026-04-11",
    }


def _config() -> dict:
    return {"city_tax_rate": 50, "balicky_per_person": 0, "vat_rate": 0.21}


def _cnb() -> dict:
    return {"rate": 25.0, "valid_for": "2026-04-10"}


def test_negative_payout_eur_does_not_yield_negative_kurz():
    """Refund flow: payout_eur < 0 must not produce a negative implied rate
    that would then poison every CZK calculation downstream."""
    res = _booking_reservation(payout_eur=-100.0, czk_booked=2500.0, booking_rate=24.5)
    row = calculate_row(res, _cnb(), _config(), order=1)

    assert row["kurz"] >= 0.0, f"kurz must not be negative, got {row['kurz']}"


def test_negative_czk_booked_does_not_yield_negative_kurz():
    res = _booking_reservation(payout_eur=100.0, czk_booked=-2500.0, booking_rate=24.5)
    row = calculate_row(res, _cnb(), _config(), order=1)

    assert row["kurz"] >= 0.0


def test_falls_back_to_booking_rate_when_derived_is_invalid():
    """If derived_booking_rate would be <= 0 but booking_rate is positive,
    use booking_rate."""
    res = _booking_reservation(payout_eur=-100.0, czk_booked=2500.0, booking_rate=24.5)
    row = calculate_row(res, _cnb(), _config(), order=1)

    assert row["kurz"] == 24.5


def test_positive_values_still_compute_derived_rate_normally():
    """The fix must not regress the happy path."""
    res = _booking_reservation(payout_eur=100.0, czk_booked=2500.0, booking_rate=24.5)
    row = calculate_row(res, _cnb(), _config(), order=1)

    # 2500 / 100 = 25.0 — derived takes priority over booking_rate
    assert row["kurz"] == 25.0
