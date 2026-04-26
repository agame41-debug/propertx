"""When an airbnb or booking CSV is imported, payout_batches and
payout_batch_items are populated immediately — same lifecycle as
bank_transactions on bank-CSV import."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA
from report.source_registry import import_uploaded_source


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---------------------------------------------------------------------------
# Airbnb payout CSV fixture
#
# build_airbnb_payout_data() expects:
#   - A row where Typ == "Payout" with columns: Referenční kód, Datum,
#     Datum připsání na účet, Vyplaceno (payout batch header)
#   - Followed by item rows (e.g. Typ == "Rezervace") that belong to that batch
#
# Required columns (from _AIRBNB_REQUIRED_COLS):
#   Typ, Potvrzující kód, Datum rezervace, Datum zahájení, Datum ukončení,
#   Počet nocí, Host, Nabídka, Částka, Hrubé výdělky, Servisní poplatek,
#   Poplatek za úklid
#
# Additional payout-specific columns (not in required set but read by parser):
#   Referenční kód, Datum, Datum připsání na účet, Vyplaceno
# ---------------------------------------------------------------------------

_AIRBNB_PAYOUT_CSV = (
    "Typ,Potvrzující kód,Datum rezervace,Datum zahájení,Datum ukončení,"
    "Počet nocí,Host,Nabídka,Částka,Hrubé výdělky,Servisní poplatek,"
    "Poplatek za úklid,Referenční kód,Datum,Datum připsání na účet,Vyplaceno\n"
    "Payout,,,,,,,,,,,,"
    "G-AB-TEST1,2026-03-15,2026-03-16,25000.00\n"
    "Rezervace,HMTEST1,2026-01-10,2026-03-01,2026-03-04,"
    "3,Test Guest,28. Pluku 58,900.00,1000.00,100.00,65.00,"
    ",,,"
    "\n"
)


def _airbnb_fixture_bytes() -> bytes:
    return _AIRBNB_PAYOUT_CSV.encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Booking payout CSV fixture
#
# build_booking_payout_data() expects:
#   - A row where Typ / typ transakce == "(Payout)" with Deskriptor výpisu as
#     batch_ref, Datum vyplacení částky, Vyplacená částka
#   - Followed by "Rezervace" rows for items
#
# Required columns (from _BOOKING_REQUIRED_COLS):
#   Typ / typ transakce, Referenční číslo, Datum příjezdu, Datum odjezdu,
#   Název ubytování, ID ubytování, Datum vyplacení částky,
#   Hrubá částka, Provize, Hodnota transakce, Směnný kurz, Splatná částka,
#   Poplatek za\xa0platební služby
#
# Additional column used by payout parser: Vyplacená částka, Deskriptor výpisu
# ---------------------------------------------------------------------------

_BOOKING_PAYOUT_CSV = (
    "Typ / typ transakce,Referenční číslo,Datum příjezdu,Datum odjezdu,"
    "Název ubytování,ID ubytování,Datum vyplacení částky,"
    "Hrubá částka,Provize,Hodnota transakce,Směnný kurz,Splatná částka,"
    "Poplatek za\xa0platební služby,Deskriptor výpisu,Vyplacená částka\n"
    "(Payout),,-,-,-,-,2026-03-20,"
    "0.00,0.00,0.00,25.20,0.00,"
    "0.00,BK-TEST-BATCH,12500.00\n"
    "Rezervace,BKTEST1,2026-03-10,2026-03-13,"
    "28. Pluku 58,12860254,2026-03-20,"
    "100.00,-20.00,80.00,25.20,2016.00,"
    "-2.00,BK-TEST-BATCH,\n"
)


def _booking_fixture_bytes() -> bytes:
    return _BOOKING_PAYOUT_CSV.encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_airbnb_import_populates_payout_batches(conn):
    body = _airbnb_fixture_bytes()
    import_uploaded_source(
        conn, "airbnb", "test_airbnb.csv", body, imported_by="test"
    )

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()[0]
    assert n > 0, "airbnb import did not populate payout_batches"


def test_booking_import_populates_payout_batches(conn):
    body = _booking_fixture_bytes()
    import_uploaded_source(
        conn, "booking", "test_booking.csv", body, imported_by="test"
    )

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'booking'"
    ).fetchone()[0]
    assert n > 0, "booking import did not populate payout_batches"


def test_duplicate_airbnb_import_does_not_double_count_batches(conn):
    """SHA dedup prevents the second import; UPSERT keeps existing rows."""
    body = _airbnb_fixture_bytes()
    import_uploaded_source(conn, "airbnb", "f.csv", body, imported_by="t")
    before = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]

    import_uploaded_source(conn, "airbnb", "f.csv", body, imported_by="t")
    after = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]

    assert before == after
