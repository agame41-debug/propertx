"""Engine helper that writes parsed CSV payout artifacts to SQLite.

The helper is the single point that calls save_payout_batches,
save_payout_batch_items, fill_missing_payout_item_guest_names, and
save_bank_transactions for the airbnb + booking channels. Both the
engine (defensive, on every regen) and source_registry (on every
import) call it.
"""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA
from report.engine import _persist_csv_payout_artifacts


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _airbnb_data() -> dict:
    return {
        "batches": [
            {
                "batch_ref": "G-AB-001",
                "payout_date": "2026-04-15",
                "amount_czk": 25000.0,
                "amount_eur": 1000.0,
                "implied_rate": 25.0,
                "source_name": "airbnb_03_2026.csv",
            }
        ],
        "items": [
            {
                "batch_ref": "G-AB-001",
                "item_index": 0,
                "item_type": "reservation",
                "confirmation_code": "HMA001",
                "guest_name": "Test Guest",
                "amount_eur": 1000.0,
            }
        ],
    }


def _booking_data() -> dict:
    return {
        "batches": [
            {
                "batch_ref": "BK-001",
                "payout_date": "2026-04-20",
                "amount_czk": 12500.0,
                "amount_eur": 500.0,
                "implied_rate": 25.0,
                "source_name": "booking_03_2026.csv",
            }
        ],
        "items": [
            {
                "batch_ref": "BK-001",
                "item_index": 0,
                "item_type": "reservation",
                "confirmation_code": "BK001",
                "guest_name": "",
                "amount_eur": 500.0,
            }
        ],
    }


def test_persists_airbnb_batches(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT batch_ref, amount_czk FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()
    assert row is not None
    assert row["batch_ref"] == "G-AB-001"
    assert row["amount_czk"] == 25000.0


def test_persists_booking_batches(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT batch_ref FROM payout_batches WHERE channel = 'booking'"
    ).fetchone()
    assert row is not None
    assert row["batch_ref"] == "BK-001"


def test_persists_payout_items_for_both_channels(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    counts = conn.execute(
        "SELECT channel, COUNT(*) AS n FROM payout_batch_items GROUP BY channel"
    ).fetchall()
    by_channel = {r["channel"]: r["n"] for r in counts}
    assert by_channel == {"airbnb": 1, "booking": 1}


def test_fills_missing_booking_guest_names_from_index(conn):
    """Booking payout items lack guest_name; the booking_index from the
    CSV provides it. The helper must call fill_missing_payout_item_guest_names
    after persisting the items."""
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data=_booking_data(),
        booking_index={"BK001": {"guest_name": "Anna Nováková"}},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT guest_name FROM payout_batch_items WHERE confirmation_code = 'BK001'"
    ).fetchone()
    assert row["guest_name"] == "Anna Nováková"


def test_fills_missing_booking_guest_names_from_hostify_when_index_is_empty(conn):
    """Booking payout CSVs do not carry the guest name at all (the export
    has no "Host" column), so the only source is hostify_reservations.
    The helper must call fill_missing_payout_item_guest_names regardless of
    whether the booking_index supplied any names — otherwise booking items
    permanently render as 'Neznámý host' even when Hostify has the data."""
    conn.execute(
        """INSERT INTO hostify_reservations
           (confirmation_code, guest_name, listing_nickname, check_in, check_out,
            source, payload_json, first_seen_at, last_seen_at)
           VALUES ('BK001', 'Margarita Grigorenko', 'Test', '2026-04-15',
                   '2026-04-18', 'Booking.com', '{}', '2026-04-01T00:00:00',
                   '2026-04-01T00:00:00')""",
    )
    conn.commit()
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data=_booking_data(),
        booking_index={},  # CSV had no host column
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT guest_name FROM payout_batch_items WHERE confirmation_code = 'BK001'"
    ).fetchone()
    assert row["guest_name"] == "Margarita Grigorenko"


def _airbnb_data_with_blank_guest() -> dict:
    return {
        "batches": [
            {
                "batch_ref": "G-AB-002",
                "payout_date": "2025-12-31",
                "amount_czk": 25000.0,
                "amount_eur": 1000.0,
                "implied_rate": 25.0,
                "source_name": "airbnb_12_2025.csv",
            }
        ],
        "items": [
            {
                "batch_ref": "G-AB-002",
                "item_index": 0,
                "item_type": "Rezervace",
                "confirmation_code": "HMABNB1",
                "guest_name": "",
                "amount_eur": 1000.0,
            }
        ],
    }


def test_fills_missing_airbnb_guest_names_from_index(conn):
    """Airbnb payout items with empty guest_name must be filled from the
    airbnb_index supplied by the CSV loader. This is the bank-UI fix:
    bank-match found (DORAZILO) but UI shows 'Neznámý host' because
    payout_batch_items.guest_name was never enriched for airbnb."""
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=_airbnb_data_with_blank_guest(),
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        airbnb_index={"HMABNB1": {"guest_name": "Andi Zielecki"}},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT guest_name FROM payout_batch_items WHERE confirmation_code = 'HMABNB1'"
    ).fetchone()
    assert row["guest_name"] == "Andi Zielecki"


def test_fills_missing_airbnb_guest_names_from_hostify_when_index_lacks_guest(conn):
    """When the airbnb CSV's "Host" column was blank but a Hostify reservation
    with the same confirmation_code carries the guest, the JOIN inside
    fill_missing_payout_item_guest_names must surface it."""
    # Seed a hostify_reservations row with the guest name. The schema for
    # this table is created via _SCHEMA in the conn fixture.
    conn.execute(
        """INSERT INTO hostify_reservations
           (confirmation_code, guest_name, listing_nickname, check_in, check_out,
            source, payload_json, first_seen_at, last_seen_at)
           VALUES ('HMABNB2', 'Joel Santos', 'Test', '2025-12-15', '2025-12-18',
                   'Airbnb', '{}', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
    )
    conn.commit()

    blank_data = _airbnb_data_with_blank_guest()
    blank_data["items"][0]["confirmation_code"] = "HMABNB2"
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=blank_data,
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        airbnb_index={},  # CSV had no guest_name either
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT guest_name FROM payout_batch_items WHERE confirmation_code = 'HMABNB2'"
    ).fetchone()
    assert row["guest_name"] == "Joel Santos"


def test_persists_airbnb_bank_transactions(conn):
    bank_row = {
        "tx_key": "abnb-tx-1",
        "tx_id": "T1",
        "datum": "2026-04-15",
        "amount_czk": 25000.0,
        "gref": "G-AB-001",
        "property_id": "",
        "zprava": "Airbnb payout",
        "source_name": "bank.csv",
    }
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[bank_row],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT tx_key, channel FROM bank_transactions WHERE tx_key = 'abnb-tx-1'"
    ).fetchone()
    assert row is not None
    assert row["channel"] == "airbnb"


def test_persists_booking_bank_transactions_flattened(conn):
    """booking_bank_idx_all is dict[property_id, list[row]]; the helper
    must flatten it before calling save_bank_transactions."""
    booking_idx = {
        "PROP-1": [
            {
                "tx_key": "bk-tx-1",
                "tx_id": "T2",
                "datum": "2026-04-20",
                "amount_czk": 12500.0,
                "gref": "",
                "property_id": "PROP-1",
                "zprava": "Booking payout",
                "source_name": "bank.csv",
            }
        ]
    }
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all=booking_idx,
    )

    row = conn.execute(
        "SELECT tx_key, channel FROM bank_transactions WHERE tx_key = 'bk-tx-1'"
    ).fetchone()
    assert row is not None
    assert row["channel"] == "booking"


def test_idempotent_on_repeat_call(conn):
    """The underlying SQL is UPSERT; calling twice must not duplicate rows
    or change row count."""
    args = dict(
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )
    _persist_csv_payout_artifacts(conn, **args)
    _persist_csv_payout_artifacts(conn, **args)

    batches = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    items = conn.execute("SELECT COUNT(*) FROM payout_batch_items").fetchone()[0]
    assert batches == 2
    assert items == 2


def test_empty_inputs_are_no_op(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    counts = {
        "batches": conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0],
        "items": conn.execute("SELECT COUNT(*) FROM payout_batch_items").fetchone()[0],
        "tx": conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0],
    }
    assert counts == {"batches": 0, "items": 0, "tx": 0}


def test_generate_report_in_process_calls_helper_once(monkeypatch, tmp_path):
    """When generate_report_in_process runs, _persist_csv_payout_artifacts
    is invoked at least once with the expected kwargs structure."""
    import report.engine as engine_mod
    from report.db import get_connection

    seen: list[dict] = []

    def _spy(conn, **kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(engine_mod, "_persist_csv_payout_artifacts", _spy)

    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    try:
        # Minimal config with one property
        config = {
            "properties": {
                "test_slug": {
                    "display_name": "Test",
                    "listing_nickname": "Test",
                    "listing_id": "1",
                    "active": True,
                }
            }
        }
        # Will fail somewhere downstream because there's no Hostify data,
        # but the helper should have been invoked before that point.
        try:
            engine_mod.generate_report_in_process(conn, "test_slug", 2026, 4, config)
        except Exception:
            pass
    finally:
        conn.close()

    assert len(seen) >= 1, (
        "Expected _persist_csv_payout_artifacts to be called at least once during "
        f"generate_report_in_process; saw {len(seen)} calls."
    )
    # Verify expected kwargs structure on the first call
    assert "airbnb_payout_data" in seen[0]
    assert "booking_payout_data" in seen[0]
    assert "booking_index" in seen[0]
    assert "bank_rows_all" in seen[0]
    assert "booking_bank_idx_all" in seen[0]
