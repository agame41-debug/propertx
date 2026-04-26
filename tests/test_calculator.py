"""
Tests for report/calculator.py — pure financial calculation engine.

Covers:
  - calculate_row: all CZK field formulas
  - Exchange rate selection: Airbnb batch rate / Booking derived rate / CNB fallback
  - _stay_label formatting
  - _null_row when kurz=0
  - calculate_all_rows: sort order, per-row CNB rate lookup
"""
import pytest
from report.calculator import (
    calculate_row,
    calculate_all_rows,
    _stay_label,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROP = {
    "city_tax_rate": 50,
    "balicky_per_person": 249,
    "vat_rate": 0.21,
    "rentero_commission": 0.15,
}

CNB_RATE = {"rate": 25.0, "valid_for": "2026-03-01"}


def _airbnb_res(**overrides) -> dict:
    base = {
        "confirmation_code": "HM123",
        "guest_name": "Jan Novák",
        "check_in": "2026-03-05",
        "check_out": "2026-03-08",
        "nights": 3,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "source": "Airbnb",
        "payout_price_eur": 100.0,
        "effective_payout_eur": 100.0,
        "cleaning_fee_eur": 20.0,
        "channel_commission_eur": 10.0,
        "confirmed_at": "2026-02-01",
        "is_cancelled": False,
        "listing_id": 184988,
        "listing_nickname": "28. Pluku 58",
        "airbnb_batch_rate": 25.5,
        "airbnb_payout_date": "2026-03-10",
        "booking_rate": 0,
        "czk_booked": 0,
        "verification_status": "MATCHED",
        "verification_diff": 0.0,
        "csv_payout_eur": 100.0,
        "month_comment": None,
        "verification_comment": "",
        "batch_ref": "G-H1234",
        "batch_payout_date": "2026-03-10",
        "batch_amount_czk": 2550.0,
        "batch_rate": 25.5,
    }
    base.update(overrides)
    return base


def _booking_res(**overrides) -> dict:
    base = {
        "confirmation_code": "BDC456",
        "guest_name": "Eva Svobodová",
        "check_in": "2026-03-10",
        "check_out": "2026-03-13",
        "nights": 3,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "source": "Booking.com",
        "payout_price_eur": 80.0,
        "effective_payout_eur": 80.0,
        "cleaning_fee_eur": 15.0,
        "channel_commission_eur": 8.0,
        "confirmed_at": "2026-02-15",
        "is_cancelled": False,
        "listing_id": 184988,
        "listing_nickname": "28. Pluku 58",
        "airbnb_batch_rate": 0,
        "booking_rate": 25.2,
        "czk_booked": 2016.0,   # 80.0 * 25.2
        "booking_payout_date": "2026-03-15",
        "verification_status": "MATCHED",
        "verification_diff": 0.0,
        "csv_payout_eur": 80.0,
        "month_comment": None,
        "verification_comment": "",
        "batch_ref": "",
        "batch_payout_date": "",
        "batch_amount_czk": None,
        "batch_rate": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _stay_label
# ---------------------------------------------------------------------------

class TestStayLabel:
    def test_normal(self):
        assert _stay_label("2026-03-05", "2026-03-08") == "05.03.-08.03."

    def test_cross_month(self):
        assert _stay_label("2026-01-30", "2026-02-02") == "30.01.-02.02."

    def test_invalid_date_fallback(self):
        label = _stay_label("bad", "also-bad")
        assert "→" in label

    def test_none_fallback(self):
        label = _stay_label(None, None)
        assert "→" in label


# ---------------------------------------------------------------------------
# Exchange rate selection
# ---------------------------------------------------------------------------

class TestExchangeRateSelection:
    """P1.7 — verify which kurz is chosen for each source type."""

    def test_airbnb_uses_batch_rate(self):
        row = calculate_row(_airbnb_res(), CNB_RATE, PROP, order=1)
        assert row["kurz"] == 25.5
        assert row["kurz_date"] == "2026-03-10"

    def test_airbnb_falls_back_to_cnb_when_no_batch_rate(self):
        row = calculate_row(_airbnb_res(airbnb_batch_rate=0), CNB_RATE, PROP, order=1)
        assert row["kurz"] == 25.0

    def test_airbnb_commission_uses_cnb_even_when_batch_rate_exists(self):
        row = calculate_row(_airbnb_res(), CNB_RATE, PROP, order=1)
        assert row["provize_czk"] == 250.0

    def test_booking_derives_rate_from_czk_booked(self):
        # derived = czk_booked / payout_eur = 2016 / 80 = 25.2
        row = calculate_row(_booking_res(), CNB_RATE, PROP, order=1)
        assert abs(row["kurz"] - 25.2) < 1e-6

    def test_booking_falls_back_to_booking_rate_when_czk_zero(self):
        row = calculate_row(
            _booking_res(czk_booked=0, booking_rate=24.8),
            CNB_RATE, PROP, order=1,
        )
        assert row["kurz"] == 24.8

    def test_unknown_source_uses_cnb(self):
        row = calculate_row(
            _airbnb_res(source="Other", airbnb_batch_rate=0),
            CNB_RATE, PROP, order=1,
        )
        assert row["kurz"] == 25.0

    def test_null_row_when_no_rate(self):
        row = calculate_row(
            _airbnb_res(source="Other", airbnb_batch_rate=0),
            {"rate": 0, "valid_for": ""},
            PROP, order=1,
        )
        assert row["kurz"] is None
        assert row["city_tax_czk"] is None
        assert "CNB kurz nedostupný" in row["comment"]


# ---------------------------------------------------------------------------
# Core formula correctness
# ---------------------------------------------------------------------------

class TestCalculateRowAirbnb:
    """
    reservation: 3 nights, 2 adults, 0 children, Airbnb batch rate 25.5
    payout_eur=100, cleaning=20, commission=10
    city_tax_rate=50, balicky_per_person=249, vat_rate=0.21
    """

    def setup_method(self):
        self.row = calculate_row(_airbnb_res(), CNB_RATE, PROP, order=1)

    def test_city_tax(self):
        # 50 × 3 nights × 2 adults = 300
        assert self.row["city_tax_czk"] == 300.0

    def test_provize_czk(self):
        # 10 EUR × 25.0 (CNB ke dni rezervace) = 250
        assert self.row["provize_czk"] == 250.0

    def test_dph_provize(self):
        # 250 × 0.21 = 52.5
        assert self.row["dph_provize_czk"] == 52.5

    def test_payout_czk(self):
        # Airbnb: payout_eur × kurz = 100 × 25.5 = 2550
        assert self.row["payout_czk"] == 2550.0

    def test_uklid_czk(self):
        # 20 × 25.5 = 510
        assert self.row["uklid_czk"] == 510.0

    def test_balicky(self):
        # 249 × (2 adults + 0 children) = 498
        assert self.row["balicky_czk"] == 498.0

    def test_dph_uklid_balicky(self):
        # (510 + 498) × 0.21 = 1008 × 0.21 = 211.68
        assert self.row["dph_uklid_balicky_czk"] == pytest.approx(211.68, abs=0.01)

    def test_priprava_pokoje(self):
        # 510 + 498 = 1008
        assert self.row["priprava_pokoje_czk"] == 1008.0

    def test_cena_ubytovani(self):
        # 2550 - 1008 - 300 - 52.5 - 211.68 = 977.82
        expected = 2550.0 - 1008.0 - 300.0 - 52.5 - 211.68
        assert self.row["cena_ubytovani_czk"] == pytest.approx(expected, abs=0.01)

    def test_identity_fields(self):
        assert self.row["order"] == 1
        assert self.row["nights"] == 3
        assert self.row["adults"] == 2
        assert self.row["confirmation_code"] == "HM123"

    def test_stay_label(self):
        assert self.row["stay_label"] == "05.03.-08.03."


class TestCalculateRowBooking:
    """
    Booking: czk_booked=2016, so payout_czk should be czk_booked directly.
    """

    def setup_method(self):
        self.row = calculate_row(_booking_res(), CNB_RATE, PROP, order=2)

    def test_payout_czk_uses_czk_booked(self):
        # Booking: payout_czk = czk_booked = 2016
        assert self.row["payout_czk"] == 2016.0

    def test_uklid_czk_uses_derived_rate(self):
        # cleaning_fee_eur=15, kurz=25.2 → 378
        assert self.row["uklid_czk"] == pytest.approx(15.0 * 25.2, abs=0.01)


class TestCalculateRowWithChildren:
    def test_children_counted_in_balicky(self):
        res = _airbnb_res(adults=2, children=1, infants=1)
        row = calculate_row(res, CNB_RATE, PROP, order=1)
        # balicky_per_person × (2 adults + 1 child + 1 infant) = 249 × 4 = 996
        assert row["balicky_czk"] == 996.0
        assert row["children_infants"] == 2

    def test_children_not_counted_in_city_tax(self):
        res = _airbnb_res(adults=2, children=2, infants=0)
        row = calculate_row(res, CNB_RATE, PROP, order=1)
        # city_tax only counts adults
        assert row["city_tax_czk"] == 50 * 3 * 2  # 300

    def test_cancelled_reservation_has_no_stay_costs(self):
        row = calculate_row(_airbnb_res(is_cancelled=True), CNB_RATE, PROP, order=1)
        assert row["city_tax_czk"] == 0.0
        assert row["uklid_czk"] == 0.0
        assert row["balicky_czk"] == 0.0
        assert row["dph_uklid_balicky_czk"] == 0.0
        assert row["priprava_pokoje_czk"] == 0.0
        expected = row["payout_czk"] - row["dph_provize_czk"]
        assert row["cena_ubytovani_czk"] == pytest.approx(expected, abs=0.01)

    def test_cena_ubytovani_is_clamped_to_zero_when_negative(self):
        row = calculate_row(
            _airbnb_res(
                payout_price_eur=20.0,
                effective_payout_eur=20.0,
                cleaning_fee_eur=20.0,
                adults=2,
                children=1,
                infants=0,
            ),
            CNB_RATE,
            PROP,
            order=1,
        )
        assert row["payout_czk"] == 510.0
        assert row["cena_ubytovani_czk"] == 0.0


# ---------------------------------------------------------------------------
# calculate_all_rows
# ---------------------------------------------------------------------------

class TestCalculateAllRows:
    def test_sorted_by_check_in(self):
        res_list = [
            _airbnb_res(check_in="2026-03-20", check_out="2026-03-23", confirmation_code="C"),
            _airbnb_res(check_in="2026-03-05", check_out="2026-03-08", confirmation_code="A"),
            _airbnb_res(check_in="2026-03-10", check_out="2026-03-13", confirmation_code="B"),
        ]
        cnb_rates = {
            "2026-02-01": CNB_RATE,
        }
        rows = calculate_all_rows(res_list, cnb_rates, PROP)
        check_ins = [r["check_in"] for r in rows]
        assert check_ins == sorted(check_ins)

    def test_order_is_1_based(self):
        res_list = [_airbnb_res(), _booking_res()]
        cnb_rates = {"2026-02-01": CNB_RATE, "2026-02-15": CNB_RATE}
        rows = calculate_all_rows(res_list, cnb_rates, PROP)
        orders = [r["order"] for r in rows]
        assert orders == list(range(1, len(rows) + 1))

    def test_empty_input(self):
        rows = calculate_all_rows([], {}, PROP)
        assert rows == []


# ---------------------------------------------------------------------------
# is_payout_adjustment
# ---------------------------------------------------------------------------

def test_payout_adjustment_has_zero_cleaning_citytax_balicky():
    """Adjustment rows must not double-count cleaning, city tax, or balíčky."""
    reservation = _airbnb_res(
        is_payout_adjustment=True,
        status="adjustment",
        cleaning_fee_eur=50.0,
        channel_commission_eur=10.0,
        payout_price_eur=300.0,
        effective_payout_eur=300.0,
        airbnb_batch_rate=25.0,
    )
    cnb_rate = CNB_RATE
    row = calculate_row(reservation, cnb_rate, PROP, order=1)

    assert row["uklid_czk"] == 0.0, "adjustment must not include cleaning fee"
    assert row["city_tax_czk"] == 0.0, "adjustment must not include city tax"
    assert row["balicky_czk"] == 0.0, "adjustment must not include balíčky"
    assert row["dph_uklid_balicky_czk"] == 0.0
    assert row["payout_czk"] > 0, "payout must be present"
    assert row.get("is_payout_adjustment") is True
