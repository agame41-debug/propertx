"""
Tests for report/verifier.py — CSV loading and cross-verification.

Covers:
  - load_airbnb_csv / load_booking_csv: parsing, column validation (P1.4), deduplication
  - verify_reservation: MATCHED / ROZDÍL / CHYBÍ_V_CSV / ZRUŠENO statuses
  - P1.5: month assignment rules are exercised via assign_report_month (in loader.py)
    and via find_csv_only_rows month filtering
"""
import io
import pytest
from datetime import date

from report.verifier import (
    CsvFormatError,
    load_airbnb_csv,
    load_booking_csv,
    verify_reservation,
    find_csv_only_rows,
    TOLERANCE_EUR,
    STATUS_MATCHED,
    STATUS_ROZDIL,
    STATUS_CHYBI_CSV,
    STATUS_ZRUSENO,
    STATUS_CHYBI_HOSTIFY,
)


# ---------------------------------------------------------------------------
# Helpers — build in-memory CSV sources
# ---------------------------------------------------------------------------

def _airbnb_source(rows_csv: str) -> dict:
    """Wrap CSV string as a DB-backed source blob."""
    content = rows_csv.encode("utf-8-sig")
    return {"original_name": "airbnb_test.csv", "content": content, "id": 1}


def _airbnb_source_cp1250_tab(rows_csv: str) -> dict:
    content = rows_csv.encode("cp1250")
    return {"original_name": "airbnb_cp1250_tab.csv", "content": content, "id": 11}


def _booking_source(rows_csv: str) -> dict:
    content = rows_csv.encode("utf-8-sig")
    return {"original_name": "booking_test.csv", "content": content, "id": 2}


AIRBNB_HEADER = (
    "Typ,Potvrzující kód,Datum rezervace,Datum zahájení,Datum ukončení,"
    "Počet nocí,Host,Nabídka,Částka,Hrubé výdělky,Servisní poplatek,Poplatek za úklid\n"
)

BOOKING_HEADER = (
    "Typ / typ transakce,Referenční číslo,Datum příjezdu,Datum odjezdu,"
    "Název ubytování,ID ubytování,Datum vyplacení částky,"
    "Hrubá částka,Provize,Hodnota transakce,Směnný kurz,Splatná částka\n"
    "Deskriptor výpisu\n"  # extra column present in real exports — must not break parser
)


# ---------------------------------------------------------------------------
# load_airbnb_csv
# ---------------------------------------------------------------------------

class TestLoadAirbnbCsv:
    def _make_row(self, code="HM1", amount="100.00", typ="Rezervace"):
        return (
            f"{typ},{code},01/15/2026,03/05/2026,03/08/2026,"
            f"3,Jan Novák,28. Pluku 58,{amount},110.00,10.00,20.00\n"
        )

    def test_loads_reservation_row(self):
        src = _airbnb_source(AIRBNB_HEADER + self._make_row())
        index = load_airbnb_csv([src])
        assert "HM1" in index

    def test_skips_payout_rows(self):
        src = _airbnb_source(AIRBNB_HEADER + self._make_row(typ="Payout"))
        index = load_airbnb_csv([src])
        assert len(index) == 0

    def test_aggregates_split_payout_rows_for_same_confirmation_code(self):
        rows = self._make_row(code="DUP", amount="100") + self._make_row(code="DUP", amount="999")
        src = _airbnb_source(AIRBNB_HEADER + rows)
        index = load_airbnb_csv([src])
        assert index["DUP"]["amount_eur"] == 1099.0

    def test_does_not_double_count_identical_rows_from_overlapping_exports(self):
        row = self._make_row(code="DUP", amount="100")
        src1 = _airbnb_source(AIRBNB_HEADER + row)
        src2 = {"original_name": "airbnb_test_2.csv", "content": (AIRBNB_HEADER + row).encode("utf-8-sig"), "id": 2}
        index = load_airbnb_csv([src1, src2])
        assert index["DUP"]["amount_eur"] == 100.0

    def test_parses_numeric_fields(self):
        src = _airbnb_source(AIRBNB_HEADER + self._make_row(code="X", amount="123.45"))
        index = load_airbnb_csv([src])
        assert index["X"]["amount_eur"] == 123.45
        assert index["X"]["nights"] == 3

    def test_parses_dates(self):
        src = _airbnb_source(AIRBNB_HEADER + self._make_row())
        index = load_airbnb_csv([src])
        assert index["HM1"]["check_in"] == date(2026, 3, 5)
        assert index["HM1"]["check_out"] == date(2026, 3, 8)

    def test_missing_confirmation_code_skipped(self):
        row = f"Rezervace,,01/15/2026,03/05/2026,03/08/2026,3,Guest,Prop,100.00,110.00,10.00,20.00\n"
        src = _airbnb_source(AIRBNB_HEADER + row)
        index = load_airbnb_csv([src])
        assert len(index) == 0

    def test_empty_file_returns_empty(self):
        src = _airbnb_source(AIRBNB_HEADER)
        index = load_airbnb_csv([src])
        assert index == {}

    def test_no_sources_returns_empty(self):
        assert load_airbnb_csv([]) == {}

    def test_european_comma_decimal(self):
        """Handles comma as decimal separator (European locale exports)."""
        row = "Rezervace,EUR1,01/15/2026,03/05/2026,03/08/2026,3,Guest,Prop,100,50,10,20\n"
        src = _airbnb_source(AIRBNB_HEADER + row)
        index = load_airbnb_csv([src])
        # "100" parsed as 100.0
        assert index["EUR1"]["amount_eur"] == 100.0

    def test_tab_delimited_cp1250_airbnb_export(self):
        header = (
            "Datum\tBude připsán do dne\tTyp\tPotvrzující kód\tDatum rezervace\tDatum zahájení\t"
            "Datum ukončení\tPočet nocí\tHost\tNabídka\tPodrobnosti\tReferenční kód\tMěna\tČástka\t"
            "Vyplaceno\tServisní poplatek\tPoplatek za Rychlou platbu\tPoplatek za úklid\tHrubé výdělky\t"
            "Daně z obsazenosti\tVýdělky za kalendářní rok\n"
        )
        row = (
            "12/31/2025\t\tRezervace\tHMXKX5NBJE\t12/08/2025\t12/30/2025\t01/03/2026\t4\tRory Skillen\t"
            "Charles Bridge APT\t\t\tEUR\t1384.41\t\t319.59\t\t65.00\t1704.00\t0.00\t2025\n"
        )
        src = _airbnb_source_cp1250_tab(header + row)
        index = load_airbnb_csv([src])
        assert index["HMXKX5NBJE"]["guest"] == "Rory Skillen"
        assert index["HMXKX5NBJE"]["amount_eur"] == 1384.41


# ---------------------------------------------------------------------------
# load_booking_csv  (P1.4: column validation)
# ---------------------------------------------------------------------------

BOOKING_SIMPLE_HEADER = (
    "Typ / typ transakce,Referenční číslo,Datum příjezdu,Datum odjezdu,"
    "Název ubytování,ID ubytování,Datum vyplacení částky,"
    "Hrubá částka,Provize,Hodnota transakce,Směnný kurz,Splatná částka,Poplatek za\xa0platební služby,Deskriptor výpisu\n"
)


class TestLoadBookingCsv:
    def _make_row(self, ref="BDC1", net="80.00", czk="2016.00"):
        return (
            f"Rezervace,{ref},2026-03-10,2026-03-13,"
            f"28. Pluku 58,12860254,2026-03-20,"
            f"100.00,-20.00,{net},25.20,{czk},-2.00,BATCH-REF\n"
        )

    def test_loads_reservation_row(self):
        src = _booking_source(BOOKING_SIMPLE_HEADER + self._make_row())
        index = load_booking_csv([src])
        assert "BDC1" in index

    def test_skips_payout_rows(self):
        payout_row = "(Payout),,-,-,-,-,2026-03-20,2016.00,0.00,0.00,25.20,2016.00,0.00,BATCH-REF\n"
        src = _booking_source(BOOKING_SIMPLE_HEADER + payout_row)
        index = load_booking_csv([src])
        assert len(index) == 0

    def test_parses_czk_booked(self):
        src = _booking_source(BOOKING_SIMPLE_HEADER + self._make_row())
        index = load_booking_csv([src])
        assert index["BDC1"]["czk_booked"] == 2016.0

    def test_parses_booking_rate(self):
        src = _booking_source(BOOKING_SIMPLE_HEADER + self._make_row())
        index = load_booking_csv([src])
        assert index["BDC1"]["booking_rate"] == 25.20

    def test_parses_dates(self):
        src = _booking_source(BOOKING_SIMPLE_HEADER + self._make_row())
        index = load_booking_csv([src])
        assert index["BDC1"]["check_in"] == date(2026, 3, 10)
        assert index["BDC1"]["check_out"] == date(2026, 3, 13)

    def test_deduplication_keeps_first(self):
        rows = self._make_row(ref="DUP", net="80") + self._make_row(ref="DUP", net="999")
        src = _booking_source(BOOKING_SIMPLE_HEADER + rows)
        index = load_booking_csv([src])
        assert index["DUP"]["net_eur"] == 80.0

    def test_missing_required_columns_raises(self):
        broken_header = BOOKING_SIMPLE_HEADER.replace(",Poplatek za\xa0platební služby", "")
        src = _booking_source(broken_header + self._make_row())
        with pytest.raises(CsvFormatError):
            load_booking_csv([src])


# ---------------------------------------------------------------------------
# verify_reservation
# ---------------------------------------------------------------------------

PROP = {"listing_nickname": "28. Pluku 58", "booking_property_id": "12860254"}


def _hostify_res(**kw) -> dict:
    base = {
        "confirmation_code": "HM1",
        "source": "Airbnb",
        "payout_price_eur": 100.0,
        "is_cancelled": False,
    }
    base.update(kw)
    return base


class TestVerifyReservation:
    def _airbnb_index(self, code="HM1", amount=100.0) -> dict:
        return {code: {"amount_eur": amount, "source_file": "airbnb.csv"}}

    def _booking_index(self, code="BDC1", net=80.0, czk=2016.0) -> dict:
        return {
            code: {
                "net_eur": net,
                "czk_booked": czk,
                "booking_rate": 25.2,
                "payout_date": "2026-03-20",
                "source_file": "booking.csv",
            }
        }

    # --- MATCHED ---
    def test_matched_within_tolerance(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=100.05),  # diff = -0.05, within tolerance
            {},
        )
        assert res["verification_status"] == STATUS_MATCHED
        assert res["effective_payout_eur"] == 100.0  # uses Hostify value

    def test_matched_exact(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=100.0),
            {},
        )
        assert res["verification_status"] == STATUS_MATCHED

    # --- ROZDÍL ---
    def test_rozdil_outside_tolerance(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=90.0),  # diff = 10.0
            {},
        )
        assert res["verification_status"] == STATUS_ROZDIL
        assert res["effective_payout_eur"] == 90.0  # uses CSV value
        assert res["verification_diff"] == pytest.approx(10.0, abs=0.001)

    def test_rozdil_at_tolerance_boundary_is_matched(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=100.0 - TOLERANCE_EUR),  # diff = exactly tolerance
            {},
        )
        assert res["verification_status"] == STATUS_MATCHED

    def test_rozdil_just_over_boundary(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=100.0 - TOLERANCE_EUR - 0.01),
            {},
        )
        assert res["verification_status"] == STATUS_ROZDIL

    def test_diff_under_one_eur_is_matched(self):
        res = verify_reservation(
            _hostify_res(payout_price_eur=100.0),
            self._airbnb_index(amount=99.02),
            {},
        )
        assert res["verification_status"] == STATUS_MATCHED

    # --- CHYBÍ_V_CSV ---
    def test_chybi_csv_when_not_in_index(self):
        res = verify_reservation(_hostify_res(), {}, {})
        assert res["verification_status"] == STATUS_CHYBI_CSV
        assert res["effective_payout_eur"] == 100.0  # uses Hostify

    # --- ZRUŠENO ---
    def test_zruseno_cancelled(self):
        res = verify_reservation(_hostify_res(is_cancelled=True), self._airbnb_index(), {})
        assert res["verification_status"] == STATUS_ZRUSENO

    # --- Booking source ---
    def test_booking_matched(self):
        res = verify_reservation(
            _hostify_res(confirmation_code="BDC1", source="Booking.com", payout_price_eur=80.0),
            {},
            self._booking_index(code="BDC1", net=80.0),
        )
        assert res["verification_status"] == STATUS_MATCHED

    def test_booking_fields_attached(self):
        res = verify_reservation(
            _hostify_res(confirmation_code="BDC1", source="Booking.com", payout_price_eur=80.0),
            {},
            self._booking_index(code="BDC1", net=80.0),
        )
        assert res["czk_booked"] == 2016.0
        assert res["booking_rate"] == 25.2
        assert res["booking_payout_date"] == "2026-03-20"

    def test_booking_city_tax_is_subtracted_before_matching(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=353.32,
                city_tax_eur=24.0,
            ),
            {},
            self._booking_index(code="BDC1", net=329.32, czk=8052.37),
        )
        assert res["verification_status"] == STATUS_MATCHED
        assert res["effective_payout_eur"] == pytest.approx(329.32, abs=0.001)

    def test_booking_missing_csv_uses_hostify_net_of_city_tax(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=353.32,
                city_tax_eur=24.0,
            ),
            {},
            {},
        )
        assert res["verification_status"] == STATUS_CHYBI_CSV
        assert res["effective_payout_eur"] == pytest.approx(329.32, abs=0.001)

    def test_booking_does_not_use_inferred_city_tax_for_matching(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=213.38,
                nights=3,
                adults=1,
            ),
            {},
            self._booking_index(code="BDC1", net=207.38, czk=5069.3),
            property_config={"city_tax_rate": 50},
        )
        assert res["verification_status"] == STATUS_ROZDIL
        assert res["effective_payout_eur"] == pytest.approx(207.38, abs=0.001)

    def test_booking_uses_hostify_city_tax_even_when_inferred_differs(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=213.38,
                city_tax_eur=4.0,
                nights=3,
                adults=1,
            ),
            {},
            self._booking_index(code="BDC1", net=207.38, czk=5069.3),
            property_config={"city_tax_rate": 50},
        )
        assert res["verification_status"] == STATUS_ROZDIL
        assert res["effective_payout_eur"] == pytest.approx(207.38, abs=0.001)
        assert res["inferred_city_tax_eur"] == pytest.approx(5.9524, abs=0.001)

    def test_booking_matching_uses_hostify_city_tax_not_checkin_override(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=236.0,
                city_tax_eur=24.0,
                nights=3,
                adults=2,
                city_tax_paying_guests=1,
            ),
            {},
            self._booking_index(code="BDC1", net=212.0, czk=5300.0),
            property_config={"city_tax_rate": 50},
        )
        assert res["verification_status"] == STATUS_MATCHED
        assert res["effective_payout_eur"] == pytest.approx(212.0, abs=0.001)
        assert res["inferred_city_tax_eur"] == pytest.approx(5.9524, abs=0.001)

    def test_booking_city_tax_inference_does_not_mask_unrelated_diff(self):
        res = verify_reservation(
            _hostify_res(
                confirmation_code="BDC1",
                source="Booking.com",
                payout_price_eur=120.0,
                nights=3,
                adults=1,
            ),
            {},
            self._booking_index(code="BDC1", net=90.0, czk=2205.0),
            property_config={"city_tax_rate": 50},
        )
        assert res["verification_status"] == STATUS_ROZDIL


# ---------------------------------------------------------------------------
# P1.5 — Month assignment rules (assign_report_month via loader.py)
# ---------------------------------------------------------------------------

class TestMonthAssignment:
    """
    Covers assign_report_month directly — all rule branches and edge cases.
    """
    from report.loader import assign_report_month

    @pytest.fixture(autouse=True)
    def _import(self):
        from report.loader import assign_report_month
        self.assign = assign_report_month

    # --- Airbnb ---
    def test_airbnb_default_checkin_month(self):
        y, m = self.assign(date(2026, 3, 5), date(2026, 3, 8), nights=3, source="Airbnb")
        assert (y, m) == (2026, 3)

    def test_airbnb_short_stay_stays_in_checkin_month(self):
        # 20 nights exactly → check-in month (rule: nights > 20, not >=)
        y, m = self.assign(date(2026, 3, 1), date(2026, 3, 21), nights=20, source="Airbnb")
        assert (y, m) == (2026, 3)

    def test_airbnb_long_stay_uses_checkout_month(self):
        # 21 nights → checkout month
        y, m = self.assign(date(2026, 3, 1), date(2026, 3, 22), nights=21, source="Airbnb")
        assert (y, m) == (2026, 3)

    def test_airbnb_long_stay_crosses_month_boundary(self):
        # 25 nights, checkin March, checkout April → April
        y, m = self.assign(date(2026, 3, 5), date(2026, 3, 30), nights=25, source="Airbnb")
        # checkout is still in March, so result is March
        assert m == 3

    def test_airbnb_long_stay_checkout_in_next_month(self):
        y, m = self.assign(date(2026, 2, 8), date(2026, 3, 3), nights=23, source="Airbnb")
        assert (y, m) == (2026, 3)

    # --- Booking ---
    def test_booking_default_checkin_month(self):
        # checkout in same month → check-in month
        y, m = self.assign(date(2026, 3, 10), date(2026, 3, 13), nights=3, source="Booking.com")
        assert (y, m) == (2026, 3)

    def test_booking_checkout_different_month_day_gt5_uses_checkout(self):
        # checkout March, day=10 > 5 → checkout month
        y, m = self.assign(date(2026, 2, 26), date(2026, 3, 10), nights=12, source="Booking.com")
        assert (y, m) == (2026, 3)

    def test_booking_checkout_different_month_day_lte5_uses_checkin(self):
        # checkout March 3 → day=3 ≤ 5 → check-in month (Feb)
        y, m = self.assign(date(2026, 2, 26), date(2026, 3, 3), nights=5, source="Booking.com")
        assert (y, m) == (2026, 2)

    def test_booking_checkout_day_exactly_5_uses_checkin(self):
        # day=5, rule is > 5 so → check-in month
        y, m = self.assign(date(2026, 2, 26), date(2026, 3, 5), nights=7, source="Booking.com")
        assert (y, m) == (2026, 2)

    def test_booking_checkout_day_6_uses_checkout(self):
        y, m = self.assign(date(2026, 2, 26), date(2026, 3, 6), nights=8, source="Booking.com")
        assert (y, m) == (2026, 3)

    def test_booking_same_month_regardless_of_day(self):
        # Even if checkout.day > 5, if same month → check-in month
        y, m = self.assign(date(2026, 3, 1), date(2026, 3, 10), nights=9, source="Booking.com")
        assert (y, m) == (2026, 3)

    # --- Unknown source ---
    def test_unknown_source_checkin_month(self):
        y, m = self.assign(date(2026, 3, 10), date(2026, 3, 20), nights=10, source="VRBO")
        assert (y, m) == (2026, 3)


def test_find_csv_only_rows_skips_codes_hidden_by_manual_month_move():
    rows = find_csv_only_rows(
        reservations=[],
        airbnb_index={
            "HM_MOVE": {
                "guest": "Moved Guest",
                "listing": "28. Pluku 58",
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
        booking_index={},
        property_config=PROP,
        year=2026,
        month=3,
        hidden_confirmation_codes={"HM_MOVE"},
    )
    assert rows == []
