"""
Tests for report/bank.py — bank CSV loading and reconciliation.

Covers:
  - load_bank_csv: parsing, filtering (Airbnb / CITIBANK only), G-ref extraction
  - build_bank_index: split into gref / no-ref pools
  - match_bank_transaction: G-ref strict match only (no amount/date fallback)
  - _normalize_booking_ref: normalization rules
  - match_booking_by_ref: descriptor-reference strict match
"""
import io
import pytest
from datetime import date

import report.web as web_module
from report.bank import (
    MATCH_METHOD_AMOUNT_FALLBACK,
    load_bank_csv,
    build_bank_index,
    match_bank_amount_date_fallback,
    match_bank_transaction,
    match_booking_by_ref,
    enrich_booking_rows_with_bank,
    enrich_rows_with_bank,
    _normalize_booking_ref,
)
from report.db import (
    fill_missing_payout_item_guest_names,
    get_connection,
    save_bank_transactions,
    save_hostify_reservations,
    save_payout_batch_bank_matches,
    save_payout_batch_items,
    save_payout_batches,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bank_source(rows_csv: str) -> dict:
    """Wrap UTF-16 CSV string as a DB-backed source blob."""
    content = rows_csv.encode("utf-16")
    return {"original_name": "bank_test.csv", "content": content, "id": 1}


# Bank CSV is UTF-16, comma-separated (per bank.py docstring)
BANK_HEADER = (
    "Datum zaúčtování,Typ transakce,Částka,Název protiúčtu,"
    "Zpráva pro příjemce,Reference platby,ID transakce\n"
)


def _make_bank_row(
    datum="01.03.2026",
    amount="2550.00",
    partner="CITIBANK EUROPE PLC",
    zprava="G-H1234 Payment",
    ref="",
    tx_id="TX001",
    typ="Příchozí úhrada",
) -> str:
    return f"{datum},{typ},{amount},{partner},{zprava},{ref},{tx_id}\n"


def _tx_row(datum: str, amount: float, gref: str = "", tx_id: str = "", booking_ref: str = "") -> dict:
    """Create a pre-built bank row dict (as returned by load_bank_csv)."""
    d = date.fromisoformat(datum)
    row = {
        "datum": d,
        "amount_czk": amount,
        "gref": gref,
        "booking_ref": booking_ref,
        "tx_id": tx_id or f"auto-{datum}-{amount}",
        "zprava": f"{gref} payment" if gref else "no ref",
        "source_name": "test",
    }
    row["tx_key"] = f"{datum}|{amount:.2f}|{gref}|"
    return row


# ---------------------------------------------------------------------------
# load_bank_csv
# ---------------------------------------------------------------------------

class TestLoadBankCsv:
    def test_loads_citibank_incoming(self):
        csv_data = BANK_HEADER + _make_bank_row()
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert len(rows) == 1
        assert rows[0]["amount_czk"] == 2550.0

    def test_extracts_gref(self):
        csv_data = BANK_HEADER + _make_bank_row(zprava="G-H1234 info")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert rows[0]["gref"] == "G-H1234"

    def test_extracts_gref_from_reference(self):
        csv_data = BANK_HEADER + _make_bank_row(zprava="no ref here", ref="G-ABCDE payment")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert rows[0]["gref"] == "G-ABCDE"

    def test_skips_non_incoming(self):
        csv_data = BANK_HEADER + _make_bank_row(typ="Odchozí platba")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert len(rows) == 0

    def test_skips_non_citibank(self):
        csv_data = BANK_HEADER + _make_bank_row(partner="SOME OTHER BANK")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert len(rows) == 0

    def test_skips_zero_amount(self):
        csv_data = BANK_HEADER + _make_bank_row(amount="0.00")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert len(rows) == 0

    def test_skips_negative_amount(self):
        csv_data = BANK_HEADER + _make_bank_row(amount="-100.00")
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert len(rows) == 0

    def test_adds_tx_key(self):
        csv_data = BANK_HEADER + _make_bank_row()
        src = _bank_source(csv_data)
        rows = load_bank_csv([src])
        assert "tx_key" in rows[0]
        assert rows[0]["tx_key"]  # non-empty

    def test_empty_csv_returns_empty(self):
        src = _bank_source(BANK_HEADER)
        rows = load_bank_csv([src])
        assert rows == []

    def test_no_sources_returns_empty(self):
        assert load_bank_csv([]) == []


# ---------------------------------------------------------------------------
# build_bank_index
# ---------------------------------------------------------------------------

class TestBuildBankIndex:
    def test_splits_gref_and_no_ref(self):
        rows = [
            _tx_row("2026-03-01", 2550.0, gref="G-A1"),
            _tx_row("2026-03-05", 1000.0, gref=""),
        ]
        idx, no_ref = build_bank_index(rows)
        assert "G-A1" in idx
        assert len(no_ref) == 1

    def test_no_ref_sorted_by_amount(self):
        rows = [
            _tx_row("2026-03-01", 3000.0),
            _tx_row("2026-03-02", 1000.0),
            _tx_row("2026-03-03", 2000.0),
        ]
        _, no_ref = build_bank_index(rows)
        amounts = [r["amount_czk"] for r in no_ref]
        assert amounts == sorted(amounts)

    def test_duplicate_gref_keeps_first(self):
        rows = [
            _tx_row("2026-03-01", 2550.0, gref="G-A1", tx_id="TX1"),
            _tx_row("2026-03-02", 9999.0, gref="G-A1", tx_id="TX2"),
        ]
        idx, _ = build_bank_index(rows)
        assert idx["G-A1"]["tx_id"] == "TX1"


# ---------------------------------------------------------------------------
# match_bank_transaction — strict G-ref only (§15.1)
# ---------------------------------------------------------------------------

class TestMatchBankTransaction:
    def _idx(self, gref="G-A1", amount=2550.0, datum="2026-03-10") -> tuple:
        rows = [_tx_row(datum, amount, gref=gref, tx_id="TX1")]
        return build_bank_index(rows)

    def test_primary_gref_match(self):
        idx, no_ref = self._idx()
        result = match_bank_transaction("G-A1", 2550.0, "2026-03-10", idx, no_ref)
        assert result is not None
        assert result["gref"] == "G-A1"

    def test_returns_none_when_gref_not_found(self):
        idx, no_ref = self._idx()
        result = match_bank_transaction("G-ZZZZZ", 2550.0, "2026-03-10", idx, no_ref)
        assert result is None

    def test_amount_only_does_not_match(self):
        """Strict G-ref matching: amount alone must NOT produce a match."""
        rows = [_tx_row("2026-03-10", 2550.0, gref="", tx_id="TX1")]
        idx, no_ref = build_bank_index(rows)
        result = match_bank_transaction("", 2550.0, "2026-03-10", idx, no_ref)
        assert result is None

    def test_amount_date_fallback_removed(self):
        """Even exact amount + date must not match without G-ref."""
        rows = [_tx_row("2026-03-15", 2550.0, gref="", tx_id="TX1")]
        idx, no_ref = build_bank_index(rows)
        result = match_bank_transaction("", 2550.0, "2026-03-10", idx, no_ref)
        assert result is None

    def test_gref_collision_avoidance(self):
        """Same G-ref must not match twice when tx_key in used_tx_keys."""
        idx, no_ref = self._idx()
        used: set[str] = set()

        first = match_bank_transaction("G-A1", 2550.0, "2026-03-10", idx, no_ref, used_tx_keys=used)
        assert first is not None
        used.add(first["tx_key"])

        second = match_bank_transaction("G-A1", 2550.0, "2026-03-10", idx, no_ref, used_tx_keys=used)
        assert second is None

    def test_empty_gref_returns_none(self):
        idx, no_ref = self._idx()
        result = match_bank_transaction("", 0.0, "", idx, no_ref)
        assert result is None


# ---------------------------------------------------------------------------
# match_bank_amount_date_fallback — conservative no-G-ref fallback
# ---------------------------------------------------------------------------

class TestAmountDateFallback:
    def test_unique_candidate_in_window_matches(self):
        # Empty-gref bank tx (CSV from Česká spořitelna sometimes drops
        # the description for Citibank payouts).
        no_ref = [_tx_row("2026-03-12", 91498.65, gref="", tx_id="TX1")]
        result = match_bank_amount_date_fallback(91498.65, "03/10/2026", no_ref)
        assert result is not None
        assert result["tx_id"] == "TX1"

    def test_amount_outside_tolerance_does_not_match(self):
        no_ref = [_tx_row("2026-03-12", 91498.66, gref="", tx_id="TX1")]
        result = match_bank_amount_date_fallback(91498.65, "03/10/2026", no_ref)
        assert result is None

    def test_date_outside_window_does_not_match(self):
        no_ref = [_tx_row("2026-03-25", 91498.65, gref="", tx_id="TX1")]
        result = match_bank_amount_date_fallback(91498.65, "03/10/2026", no_ref)
        assert result is None

    def test_multiple_candidates_rejected_as_ambiguous(self):
        no_ref = [
            _tx_row("2026-03-11", 91498.65, gref="", tx_id="TX1"),
            _tx_row("2026-03-13", 91498.65, gref="", tx_id="TX2"),
        ]
        result = match_bank_amount_date_fallback(91498.65, "03/10/2026", no_ref)
        assert result is None  # ambiguous → reject

    def test_used_tx_keys_excludes_consumed_rows(self):
        no_ref = [
            _tx_row("2026-03-12", 91498.65, gref="", tx_id="TX1"),
            _tx_row("2026-03-13", 91498.65, gref="", tx_id="TX2"),
        ]
        used = {no_ref[0]["tx_key"]}
        result = match_bank_amount_date_fallback(
            91498.65, "03/10/2026", no_ref, used_tx_keys=used,
        )
        assert result is not None
        assert result["tx_id"] == "TX2"

    def test_invalid_amount_returns_none(self):
        no_ref = [_tx_row("2026-03-12", 100.0, gref="", tx_id="TX1")]
        assert match_bank_amount_date_fallback(0.0, "03/10/2026", no_ref) is None
        assert match_bank_amount_date_fallback(-100.0, "03/10/2026", no_ref) is None
        assert match_bank_amount_date_fallback(None, "03/10/2026", no_ref) is None

    def test_invalid_date_returns_none(self):
        no_ref = [_tx_row("2026-03-12", 100.0, gref="", tx_id="TX1")]
        assert match_bank_amount_date_fallback(100.0, "", no_ref) is None
        assert match_bank_amount_date_fallback(100.0, "not-a-date", no_ref) is None

    def test_no_ref_pool_excludes_gref_rows(self):
        # build_bank_index puts G-ref rows in index_by_gref (not no_ref_rows),
        # so callers should never pass G-ref rows into the fallback pool.
        # Verify the helper itself is just a filter — pool selection is the
        # caller's responsibility, but a row with a gref should still match
        # purely on amount/date if passed in. This guards against accidental
        # leakage if a caller mis-routes rows.
        rows = [_tx_row("2026-03-12", 100.0, gref="G-XYZ", tx_id="TX1")]
        idx, no_ref = build_bank_index(rows)
        assert no_ref == [], "build_bank_index must keep G-ref rows out of the pool"


# ---------------------------------------------------------------------------
# enrich_rows_with_bank — fallback integration on the real call path
# ---------------------------------------------------------------------------

class TestEnrichRowsAmountFallback:
    def _calc_row(self, code: str, gref: str = "", payout_czk: float = 0.0) -> dict:
        return {
            "source": "Airbnb",
            "confirmation_code": code,
            "batch_ref": gref,
            "batch_payout_date": "03/10/2026" if gref else "",
            "batch_amount_czk_expected": payout_czk if gref else None,
        }

    def test_fallback_matches_when_csv_omits_gref(self):
        # Bank tx for the payout exists but its description was empty.
        bank_rows = [_tx_row("2026-03-12", 91498.65, gref="", tx_id="TX1")]
        idx, no_ref = build_bank_index(bank_rows)
        gref_map = {"R1": {"gref": "G-A1", "payout_date": "03/10/2026", "payout_czk": 91498.65}}

        enriched, matches = enrich_rows_with_bank(
            [self._calc_row("R1", gref="G-A1", payout_czk=91498.65)],
            gref_map, idx, no_ref,
        )

        assert enriched[0]["bank_status"] == "DORAZILO"
        assert enriched[0]["bank_match_method"] == MATCH_METHOD_AMOUNT_FALLBACK
        assert enriched[0]["bank_tx_key"] == bank_rows[0]["tx_key"]
        assert len(matches) == 1
        assert matches[0]["match_method"] == MATCH_METHOD_AMOUNT_FALLBACK

    def test_strict_gref_path_still_marks_method_as_gref(self):
        bank_rows = [_tx_row("2026-03-12", 91498.65, gref="G-A1", tx_id="TX1")]
        idx, no_ref = build_bank_index(bank_rows)
        gref_map = {"R1": {"gref": "G-A1", "payout_date": "03/10/2026", "payout_czk": 91498.65}}

        enriched, matches = enrich_rows_with_bank(
            [self._calc_row("R1", gref="G-A1", payout_czk=91498.65)],
            gref_map, idx, no_ref,
        )

        assert enriched[0]["bank_status"] == "DORAZILO"
        assert enriched[0]["bank_match_method"] == "gref"
        assert matches[0]["match_method"] == "gref"

    def test_fallback_does_not_fire_when_ambiguous(self):
        bank_rows = [
            _tx_row("2026-03-11", 91498.65, gref="", tx_id="TX1"),
            _tx_row("2026-03-13", 91498.65, gref="", tx_id="TX2"),
        ]
        idx, no_ref = build_bank_index(bank_rows)
        gref_map = {"R1": {"gref": "G-A1", "payout_date": "03/10/2026", "payout_czk": 91498.65}}

        enriched, matches = enrich_rows_with_bank(
            [self._calc_row("R1", gref="G-A1", payout_czk=91498.65)],
            gref_map, idx, no_ref,
        )

        assert enriched[0]["bank_status"] == "CHYBÍ"
        assert enriched[0]["bank_match_method"] == ""
        assert matches == []

    def test_no_gref_no_fallback(self):
        # If we don't even know which batch we're looking for (gref empty),
        # the fallback must not fire — there is no target.
        bank_rows = [_tx_row("2026-03-12", 91498.65, gref="", tx_id="TX1")]
        idx, no_ref = build_bank_index(bank_rows)

        enriched, matches = enrich_rows_with_bank(
            [self._calc_row("R1", gref="", payout_czk=0.0)],
            {}, idx, no_ref,
        )

        assert enriched[0]["bank_status"] == "CHYBÍ"
        assert enriched[0]["bank_match_method"] == ""
        assert matches == []


# ---------------------------------------------------------------------------
# _normalize_booking_ref
# ---------------------------------------------------------------------------

class TestNormalizeBookingRef:
    def test_strips_no_prefix(self):
        assert _normalize_booking_ref("NO.JR3ESA8TRKWKCGAT/10936099") == "JR3ESA8TRKWKCGAT"

    def test_uppercases(self):
        assert _normalize_booking_ref("jR3ESa8TRKwKcGAt") == "JR3ESA8TRKWKCGAT"

    def test_strips_property_id(self):
        assert _normalize_booking_ref("NO.ABCDEF/12860254") == "ABCDEF"

    def test_no_prefix_no_slash(self):
        assert _normalize_booking_ref("ABCDEF123") == "ABCDEF123"

    def test_empty_string(self):
        assert _normalize_booking_ref("") == ""

    def test_none_like(self):
        assert _normalize_booking_ref(None) == ""  # type: ignore


# ---------------------------------------------------------------------------
# match_booking_by_ref — strict descriptor reference (§15.2)
# ---------------------------------------------------------------------------

class TestMatchBookingByRef:
    def _pid_row(self, booking_ref: str, datum="2026-03-20", amount=2016.0, tx_id="TX_BK1") -> dict:
        row = _tx_row(datum, amount, tx_id=tx_id, booking_ref=booking_ref)
        return row

    def test_matches_by_descriptor_ref(self):
        """Booking CSV Deskriptor výpisu matches normalized bank Zpráva."""
        pid_rows = [self._pid_row("JR3ESA8TRKWKCGAT")]
        # Batch ref as stored in payout CSV (lowercase, no NO./property_id)
        result = match_booking_by_ref("jR3ESa8TRKwKcGAt", pid_rows)
        assert result is not None

    def test_matches_bank_format_ref(self):
        """Bank format NO.XXX/property_id also normalizes to a match."""
        pid_rows = [self._pid_row("JR3ESA8TRKWKCGAT")]
        result = match_booking_by_ref("NO.JR3ESA8TRKWKCGAT/10936099", pid_rows)
        assert result is not None

    def test_no_match_wrong_ref(self):
        pid_rows = [self._pid_row("ABCDEF123456")]
        result = match_booking_by_ref("ZZZZZZZZZZZ", pid_rows)
        assert result is None

    def test_empty_batch_ref_returns_none(self):
        pid_rows = [self._pid_row("ABCDEF")]
        result = match_booking_by_ref("", pid_rows)
        assert result is None


def test_load_bank_rows_with_drilldown_groups_batch_items_under_transaction():
    conn = get_connection(":memory:")
    try:
        save_bank_transactions(
            conn,
            "airbnb",
            [
                {
                    "tx_key": "2026-03-15|1000.00|G-AAA111|",
                    "tx_id": "TX1",
                    "datum": date(2026, 3, 15),
                    "amount_czk": 1000.0,
                    "gref": "G-AAA111",
                    "zprava": "G-AAA111 payout",
                    "source_name": "bank.csv",
                }
            ],
        )
        save_payout_batches(
            conn,
            "airbnb",
            [
                {
                    "batch_ref": "G-AAA111",
                    "payout_date": "2026-03-14",
                    "amount_czk": 1000.0,
                    "amount_eur": 40.0,
                    "source_name": "airbnb.csv",
                }
            ],
        )
        save_payout_batch_items(
            conn,
            "airbnb",
            [
                {
                    "batch_ref": "G-AAA111",
                    "item_index": 1,
                    "confirmation_code": "ABC123",
                    "guest_name": "John Doe",
                    "listing_name": "28. Pluku 58",
                    "amount_czk": 600.0,
                    "check_in": "2026-03-10",
                    "check_out": "2026-03-12",
                },
                {
                    "batch_ref": "G-AAA111",
                    "item_index": 2,
                    "confirmation_code": "DEF456",
                    "guest_name": "Jane Doe",
                    "listing_name": "28. Pluku 58",
                    "amount_czk": 400.0,
                    "check_in": "2026-03-12",
                    "check_out": "2026-03-14",
                },
            ],
        )
        save_payout_batch_bank_matches(
            conn,
            "airbnb",
            [
                {
                    "batch_ref": "G-AAA111",
                    "tx_key": "2026-03-15|1000.00|G-AAA111|",
                    "match_method": "gref",
                    "matched_amount_czk": 1000.0,
                }
            ],
        )

        rows = web_module._load_bank_rows_with_drilldown(conn, year=2026, month=3)

        assert len(rows) == 1
        assert rows[0]["matched_batch_ref"] == "G-AAA111"
        assert rows[0]["matched_reservation_count"] == 2
        assert len(rows[0]["drilldown_batches"]) == 1
        assert [item["confirmation_code"] for item in rows[0]["drilldown_batches"][0]["items"]] == ["ABC123", "DEF456"]
    finally:
        conn.close()


def test_filter_bank_rows_filters_by_channel_match_state_and_query():
    rows = [
        {
            "channel": "airbnb",
            "matched_batch_ref": "G-AAA111",
            "gref": "G-AAA111",
            "zprava": "Airbnb payout",
            "source_name": "bank.csv",
            "tx_key": "1",
            "drilldown_batches": [
                {
                    "batch_ref": "G-AAA111",
                    "batch_source_name": "airbnb.csv",
                    "items": [{"confirmation_code": "ABC123", "guest_name": "John Doe", "listing_name": "28. Pluku 58", "property_id": ""}],
                }
            ],
        },
        {
            "channel": "booking",
            "matched_batch_ref": "",
            "gref": "",
            "zprava": "Booking payout",
            "source_name": "bank.csv",
            "tx_key": "2",
            "drilldown_batches": [],
        },
    ]

    filtered = web_module._filter_bank_rows(rows, channel="airbnb", match_state="matched", query="ABC123")

    assert len(filtered) == 1
    assert filtered[0]["channel"] == "airbnb"


def test_load_bank_rows_with_drilldown_populates_booking_guest_summary_from_hostify():
    conn = get_connection(":memory:")
    try:
        save_bank_transactions(
            conn,
            "booking",
            [
                {
                    "tx_key": "2026-03-17|8052.00||",
                    "tx_id": "TX-B1",
                    "datum": date(2026, 3, 17),
                    "amount_czk": 8052.0,
                    "gref": "",
                    "zprava": "NO.JR3ESA8TRKWKCGAT/10936099",
                    "source_name": "bank.csv",
                }
            ],
        )
        save_payout_batches(
            conn,
            "booking",
            [
                {
                    "batch_ref": "JR3ESA8TRKWKCGAT",
                    "payout_date": "2026-03-16",
                    "amount_czk": 8052.0,
                    "amount_eur": 320.0,
                    "source_name": "booking.csv",
                }
            ],
        )
        save_payout_batch_items(
            conn,
            "booking",
            [
                {
                    "batch_ref": "JR3ESA8TRKWKCGAT",
                    "item_index": 1,
                    "confirmation_code": "BDC1",
                    "guest_name": "",
                    "listing_name": "28. Pluku 58",
                    "amount_czk": 8052.0,
                    "check_in": "2026-03-11",
                    "check_out": "2026-03-17",
                }
            ],
        )
        save_payout_batch_bank_matches(
            conn,
            "booking",
            [
                {
                    "batch_ref": "JR3ESA8TRKWKCGAT",
                    "tx_key": "2026-03-17|8052.00||",
                    "match_method": "descriptor_ref",
                    "matched_amount_czk": 8052.0,
                }
            ],
        )
        save_hostify_reservations(
            conn,
            [
                {
                    "confirmation_code": "BDC1",
                    "reservation_id": "H-1",
                    "source": "Booking.com",
                    "status": "new",
                    "guest_name": "Valentyn Kalinskiy",
                    "check_in": "2026-03-11",
                    "check_out": "2026-03-17",
                    "assigned_year": 2026,
                    "assigned_month": 3,
                    "listing_nickname": "28. Pluku 58",
                }
            ],
        )

        rows = web_module._load_bank_rows_with_drilldown(conn, year=2026, month=3)

        assert len(rows) == 1
        assert rows[0]["display_guest_summary"] == "Valentyn Kalinskiy"
        assert rows[0]["drilldown_batches"][0]["items"][0]["guest_name"] == "Valentyn Kalinskiy"
    finally:
        conn.close()


def test_load_bank_rows_with_drilldown_infers_booking_batch_from_descriptor_reference():
    conn = get_connection(":memory:")
    try:
        save_bank_transactions(
            conn,
            "booking",
            [
                {
                    "tx_key": "2026-03-25|5143.00||",
                    "tx_id": "TX-B2",
                    "datum": date(2026, 3, 25),
                    "amount_czk": 5143.0,
                    "gref": "",
                    "zprava": "NO.ABCDEF123/10936099",
                    "source_name": "bank.csv",
                }
            ],
        )
        save_payout_batches(
            conn,
            "booking",
            [
                {
                    "batch_ref": "ABCDEF123",
                    "payout_date": "2026-03-24",
                    "amount_czk": 5143.0,
                    "amount_eur": 205.0,
                    "source_name": "booking.csv",
                }
            ],
        )
        save_payout_batch_items(
            conn,
            "booking",
            [
                {
                    "batch_ref": "ABCDEF123",
                    "item_index": 1,
                    "confirmation_code": "BDC2",
                    "guest_name": "Nataliia Naumenko",
                    "listing_name": "28. Pluku 58",
                    "amount_czk": 5143.0,
                    "check_in": "2026-03-22",
                    "check_out": "2026-03-24",
                }
            ],
        )

        rows = web_module._load_bank_rows_with_drilldown(conn, year=2026, month=3)

        assert len(rows) == 1
        assert rows[0]["matched_batch_ref"] == "ABCDEF123"
        assert rows[0]["drilldown_batches"][0]["match_method"] == "descriptor_ref_fallback"
        assert rows[0]["display_guest_summary"] == "Nataliia Naumenko"
    finally:
        conn.close()


def test_fill_missing_booking_payout_item_guest_names_backfills_from_hostify_db():
    conn = get_connection(":memory:")
    try:
        save_payout_batches(
            conn,
            "booking",
            [
                {
                    "batch_ref": "ABCDEF123",
                    "payout_date": "2026-03-24",
                    "amount_czk": 5143.0,
                    "amount_eur": 205.0,
                    "source_name": "booking.csv",
                }
            ],
        )
        save_payout_batch_items(
            conn,
            "booking",
            [
                {
                    "batch_ref": "ABCDEF123",
                    "item_index": 1,
                    "confirmation_code": "BDC2",
                    "guest_name": "",
                    "listing_name": "28. Pluku 58",
                    "amount_czk": 5143.0,
                    "check_in": "2026-03-22",
                    "check_out": "2026-03-24",
                }
            ],
        )
        save_hostify_reservations(
            conn,
            [
                {
                    "confirmation_code": "BDC2",
                    "reservation_id": "H-2",
                    "source": "Booking.com",
                    "status": "new",
                    "guest_name": "Nataliia Naumenko",
                    "check_in": "2026-03-22",
                    "check_out": "2026-03-24",
                    "assigned_year": 2026,
                    "assigned_month": 3,
                    "listing_nickname": "28. Pluku 58",
                }
            ],
        )

        updated = fill_missing_payout_item_guest_names(conn, "booking")

        row = conn.execute(
            """SELECT guest_name
                 FROM payout_batch_items
                WHERE channel = 'booking' AND batch_ref = 'ABCDEF123' AND item_index = 1"""
        ).fetchone()

        assert updated == 1
        assert row is not None
        assert row["guest_name"] == "Nataliia Naumenko"
    finally:
        conn.close()

    def test_amount_only_does_not_match(self):
        """Amount alone must not produce a match — different from old behavior."""
        pid_rows = [self._pid_row("ABCDEF")]
        # Pass a batch_ref that won't normalize to ABCDEF
        result = match_booking_by_ref("ZZZZZZZ", pid_rows)
        assert result is None

    def test_deduplication_via_used_tx_keys(self):
        pid_rows = [self._pid_row("JR3ESA8TRKWKCGAT")]
        used: set[str] = set()

        first = match_booking_by_ref("JR3ESA8TRKWKCGAT", pid_rows, used_tx_keys=used)
        assert first is not None
        used.add(first["tx_key"])

        second = match_booking_by_ref("JR3ESA8TRKWKCGAT", pid_rows, used_tx_keys=used)
        assert second is None

    def test_empty_pid_rows(self):
        result = match_booking_by_ref("JR3ESA8TRKWKCGAT", [])
        assert result is None


# ---------------------------------------------------------------------------
# enrich_booking_rows_with_bank
# ---------------------------------------------------------------------------

class TestEnrichBookingRowsWithBank:
    def _booking_row(self, batch_ref="jR3ESa8TRKwKcGAt", confirmation_code="BDC1") -> dict:
        return {
            "source": "Booking.com",
            "batch_ref": batch_ref,
            "confirmation_code": confirmation_code,
        }

    def test_uses_nested_channels_booking_property_id(self):
        calc_rows = [self._booking_row()]
        booking_bank_idx = {
            "12860254": [TestMatchBookingByRef()._pid_row("JR3ESA8TRKWKCGAT")]
        }
        property_config = {
            "channels": {
                "booking": {
                    "property_id": "12860254",
                }
            }
        }

        enriched, match_details = enrich_booking_rows_with_bank(
            calc_rows, booking_bank_idx, property_config
        )

        assert enriched[0]["bank_status"] == "DORAZILO"
        assert enriched[0]["bank_amount_czk"] == 2016.0
        assert enriched[0]["payout_gref"] == "jR3ESa8TRKwKcGAt"
        assert match_details[0]["match_method"] == "descriptor_ref"

    def test_keeps_legacy_root_level_booking_property_id(self):
        calc_rows = [self._booking_row()]
        booking_bank_idx = {
            "12860254": [TestMatchBookingByRef()._pid_row("JR3ESA8TRKWKCGAT")]
        }
        property_config = {
            "booking_property_id": "12860254",
        }

        enriched, _ = enrich_booking_rows_with_bank(
            calc_rows, booking_bank_idx, property_config
        )

        assert enriched[0]["bank_status"] == "DORAZILO"

    def test_falls_back_to_global_descriptor_ref_when_property_id_is_stale(self):
        calc_rows = [self._booking_row()]
        booking_bank_idx = {
            "12860254": [TestMatchBookingByRef()._pid_row("JR3ESA8TRKWKCGAT")],
            "99999999": [],
        }
        property_config = {
            "channels": {
                "booking": {
                    "property_id": "99999999",
                }
            }
        }

        enriched, match_details = enrich_booking_rows_with_bank(
            calc_rows, booking_bank_idx, property_config
        )

        assert enriched[0]["bank_status"] == "DORAZILO"
        assert enriched[0]["bank_amount_czk"] == 2016.0
        assert match_details[0]["match_method"] == "descriptor_ref_global"


def test_cross_month_batch_no_silent_downgrade():
    """Regression: a batch spanning two months must show DORAZILO in BOTH
    months regardless of regen order. Pre-fix this would downgrade one to
    CHYBÍ via get_bank_match_owner."""
    from report.bank import enrich_rows_with_bank, build_bank_index
    from report.db import save_report_rows

    conn = get_connection(":memory:")
    try:
        # One bank tx covers two reservations, different months, same slug.
        save_bank_transactions(
            conn,
            "airbnb",
            [
                {
                    "tx_key": "2026-04-05|18000.00|G-XMONTH|",
                    "tx_id": "TX-XM",
                    "datum": date(2026, 4, 5),
                    "amount_czk": 18000.0,
                    "gref": "G-XMONTH",
                    "zprava": "G-XMONTH payout",
                    "source_name": "bank.csv",
                }
            ],
        )
        bank_rows = [
            {
                "datum": date(2026, 4, 5),
                "amount_czk": 18000.0,
                "gref": "G-XMONTH",
                "booking_ref": "",
                "tx_id": "TX-XM",
                "tx_key": "2026-04-05|18000.00|G-XMONTH|",
                "zprava": "G-XMONTH payout",
                "source_name": "bank.csv",
            }
        ]
        index_by_gref, no_ref_rows = build_bank_index(bank_rows)

        gref_map = {
            "MARCH-CODE": {"gref": "G-XMONTH", "payout_date": "2026-04-05", "payout_czk": 10000.0},
            "APRIL-CODE": {"gref": "G-XMONTH", "payout_date": "2026-04-05", "payout_czk": 8000.0},
        }
        all_batches_map = {
            "MARCH-CODE": [{"gref": "G-XMONTH", "payout_date": "2026-04-05", "payout_czk": 10000.0}],
            "APRIL-CODE": [{"gref": "G-XMONTH", "payout_date": "2026-04-05", "payout_czk": 8000.0}],
        }

        # March regen: row for MARCH-CODE
        march_rows = [{
            "confirmation_code": "MARCH-CODE",
            "source": "Airbnb",
            "batch_ref": "G-XMONTH",
            "batch_payout_date": "2026-04-05",
            "batch_amount_czk_expected": 10000.0,
        }]
        march_enriched, march_matches = enrich_rows_with_bank(
            march_rows, gref_map, index_by_gref, no_ref_rows,
            all_batches_map=all_batches_map,
            conn=conn, slug="apt", year=2026, month=3,
        )
        from report.db import save_payout_batch_bank_matches
        # NOTE: Task 3 stripped slug/year/month kwargs — bank-match rows no
        # longer carry per-month ownership, so the silent downgrade can't fire.
        save_payout_batch_bank_matches(conn, "airbnb", march_matches)
        save_report_rows(conn, "apt", 2026, 3, march_enriched)

        # April regen: row for APRIL-CODE — same batch
        april_rows = [{
            "confirmation_code": "APRIL-CODE",
            "source": "Airbnb",
            "batch_ref": "G-XMONTH",
            "batch_payout_date": "2026-04-05",
            "batch_amount_czk_expected": 8000.0,
        }]
        april_enriched, april_matches = enrich_rows_with_bank(
            april_rows, gref_map, index_by_gref, no_ref_rows,
            all_batches_map=all_batches_map,
            conn=conn, slug="apt", year=2026, month=4,
        )
        save_payout_batch_bank_matches(conn, "airbnb", april_matches)
        save_report_rows(conn, "apt", 2026, 4, april_enriched)

        # Both months must show DORAZILO. Pre-fix April would be CHYBÍ.
        assert march_enriched[0]["bank_status"] == "DORAZILO"
        assert april_enriched[0]["bank_status"] == "DORAZILO"
    finally:
        conn.close()


def test_cross_property_batch_no_silent_downgrade():
    """Regression: a batch covering reservations from two different slugs
    must show DORAZILO in BOTH slugs."""
    from report.bank import enrich_rows_with_bank, build_bank_index
    from report.db import save_report_rows, save_payout_batch_bank_matches

    conn = get_connection(":memory:")
    try:
        save_bank_transactions(
            conn,
            "airbnb",
            [
                {
                    "tx_key": "2026-03-15|15000.00|G-XPROP|",
                    "tx_id": "TX-XP",
                    "datum": date(2026, 3, 15),
                    "amount_czk": 15000.0,
                    "gref": "G-XPROP",
                    "zprava": "G-XPROP payout",
                    "source_name": "bank.csv",
                }
            ],
        )
        bank_rows = [{
            "datum": date(2026, 3, 15),
            "amount_czk": 15000.0,
            "gref": "G-XPROP",
            "booking_ref": "",
            "tx_id": "TX-XP",
            "tx_key": "2026-03-15|15000.00|G-XPROP|",
            "zprava": "G-XPROP payout",
            "source_name": "bank.csv",
        }]
        index_by_gref, no_ref_rows = build_bank_index(bank_rows)

        gref_map = {
            "PROP-A-CODE": {"gref": "G-XPROP", "payout_date": "2026-03-15", "payout_czk": 9000.0},
            "PROP-B-CODE": {"gref": "G-XPROP", "payout_date": "2026-03-15", "payout_czk": 6000.0},
        }
        all_batches_map = {
            "PROP-A-CODE": [{"gref": "G-XPROP", "payout_date": "2026-03-15", "payout_czk": 9000.0}],
            "PROP-B-CODE": [{"gref": "G-XPROP", "payout_date": "2026-03-15", "payout_czk": 6000.0}],
        }

        rows_a = [{"confirmation_code": "PROP-A-CODE", "source": "Airbnb",
                   "batch_ref": "G-XPROP", "batch_payout_date": "2026-03-15",
                   "batch_amount_czk_expected": 9000.0}]
        enriched_a, matches_a = enrich_rows_with_bank(
            rows_a, gref_map, index_by_gref, no_ref_rows,
            all_batches_map=all_batches_map,
            conn=conn, slug="apt_A", year=2026, month=3,
        )
        save_payout_batch_bank_matches(conn, "airbnb", matches_a)
        save_report_rows(conn, "apt_A", 2026, 3, enriched_a)

        rows_b = [{"confirmation_code": "PROP-B-CODE", "source": "Airbnb",
                   "batch_ref": "G-XPROP", "batch_payout_date": "2026-03-15",
                   "batch_amount_czk_expected": 6000.0}]
        enriched_b, matches_b = enrich_rows_with_bank(
            rows_b, gref_map, index_by_gref, no_ref_rows,
            all_batches_map=all_batches_map,
            conn=conn, slug="apt_B", year=2026, month=3,
        )
        save_payout_batch_bank_matches(conn, "airbnb", matches_b)
        save_report_rows(conn, "apt_B", 2026, 3, enriched_b)

        assert enriched_a[0]["bank_status"] == "DORAZILO"
        assert enriched_b[0]["bank_status"] == "DORAZILO"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Money S3 format support
# ---------------------------------------------------------------------------
from report.bank import (
    _detect_bank_format,
    _source_bytes,
    load_booking_bank_transactions,
)
# load_marriott_bank_transactions imported lazily inside its tests (added in a later task).

# Money S3: cp1250-encoded, ';'-separated, Header / Detail 1 / Footer sections.
# Detail definition row (col1==0) names the columns; data rows have col1==1.
_MS3_DETAIL_DEF = (
    "Detail 1;0;Banka protiúčtu;Částka;Číslo bank. dokladu;"
    "Číslo hrazeného dokladu;Číslo protiúčtu;Datum splatnosti;"
    "Datum vstupu do banky;Datum zaúčtování;Doplňující údaj;"
    "Identifikátor položky;Konstantní symbol;Měna;Název protiúčtu;"
    "Příznak spárování;Specifický symbol;Typ položky;Variabilní symbol"
)


def _ms3_file(*detail_rows: str) -> dict:
    """Build a Money S3 cp1250 source blob: Header, Detail def, data rows, Footer."""
    lines = [
        "Header;0;Banka;Celkem;Číslo strany",
        "Header;1;0800;0;1",
        _MS3_DETAIL_DEF,
        *detail_rows,
        "Footer;0;Celkem;Číslo strany",
        "Footer;1;0;11;46148",
    ]
    content = ("\r\n".join(lines) + "\r\n").encode("cp1250")
    return {"original_name": "vypis_ms3.csv", "content": content, "id": 2}


def _ms3_detail(amount: str, datum: str, doplnujici: str, ident: str, partner: str) -> str:
    # Columns: 0 "Detail 1";1 marker;2 bank;3 amount;4-8;9 datum;10 doplnujici;
    #          11 ident;12-13;14 partner;15-18
    return (
        f"Detail 1;1;2600;{amount};;;4000230103;01.04.2026;;{datum};"
        f"{doplnujici};{ident};;CZK;{partner};0;;1;"
    )


def test_detect_format_legacy_utf16():
    raw = (BANK_HEADER + _make_bank_row()).encode("utf-16")
    assert _detect_bank_format(raw) == "legacy"


def test_detect_format_money_s3():
    raw = _ms3_file(
        _ms3_detail("100.00", "01.04.2026", "G-ABC payment", "TX9", "CITIBANK EUROPE PLC")
    )["content"]
    assert _detect_bank_format(raw) == "money_s3"


def test_source_bytes_from_blob():
    blob = {"original_name": "x.csv", "content": b"abc", "id": 7}
    assert _source_bytes(blob) == b"abc"
