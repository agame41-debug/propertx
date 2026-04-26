"""When cena_ubytovani would be negative, the clamp must log a warning so
operators can investigate rather than silently producing 0 CZK."""

from __future__ import annotations

import logging

from report.calculator import calculate_row


def _build_reservation(*, payout_eur: float, cleaning_fee_eur: float) -> dict:
    return {
        "confirmation_code": "HMA_NEG",
        "source": "airbnb",
        "effective_payout_eur": payout_eur,
        "airbnb_batch_rate": 25.0,
        "channel_commission_eur": 0.0,
        "cleaning_fee_eur": cleaning_fee_eur,
        "nights": 1,
        "occupancy_adults": 2,
        "occupancy_children": 0,
        "occupancy_infants": 0,
        "city_tax_paying_guests": 2,
        "city_tax_exempt_guests": 0,
        "check_in": "2026-04-10",
        "check_out": "2026-04-11",
    }


def _property_config() -> dict:
    return {
        "city_tax_rate": 50,
        "balicky_per_person": 0,
        "vat_rate": 0.21,
    }


def test_calculate_row_warns_when_cena_ubytovani_clamped(caplog):
    res = _build_reservation(payout_eur=10.0, cleaning_fee_eur=100.0)
    cnb = {"rate": 25.0, "valid_for": "2026-04-10"}

    with caplog.at_level(logging.WARNING, logger="report.calculator"):
        row = calculate_row(res, cnb, _property_config(), order=1)

    assert row["cena_ubytovani_czk"] == 0.0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("cena_ubytovani < 0 clamped to 0" in r.getMessage() for r in warnings), (
        f"Expected clamp warning. Got: {[r.getMessage() for r in warnings]}"
    )
    assert any("HMA_NEG" in r.getMessage() for r in warnings)


def test_calculate_row_silent_when_cena_ubytovani_positive(caplog):
    res = _build_reservation(payout_eur=200.0, cleaning_fee_eur=10.0)
    cnb = {"rate": 25.0, "valid_for": "2026-04-10"}

    with caplog.at_level(logging.WARNING, logger="report.calculator"):
        row = calculate_row(res, cnb, _property_config(), order=1)

    assert row["cena_ubytovani_czk"] > 0
    clamp_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "cena_ubytovani" in r.getMessage()
    ]
    assert clamp_warnings == []
