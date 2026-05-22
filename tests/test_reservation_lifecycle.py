"""Regression tests for the two-stage reservation normalization.

Background
----------
Before this fix, the `_normalize_reservation` function was used both for
report generation AND as a pre-filter before persisting Hostify data into the
`hostify_reservations` snapshot table. That meant cancelled reservations with
payout_price=0 were dropped *before* they could overwrite a previous "accepted"
snapshot row. Result: a guest who cancelled after their reservation was
already synced as accepted stayed visible in the report forever.

The fix introduces a second normalizer, `_minimal_filter_for_snapshot`, that
keeps the cancellation signal so the snapshot UPSERT corrects itself. The
engine continues to filter cancelled+payout=0 from the actual report.

Guarantees verified here
------------------------
1. cancelled+payout=0 is KEPT by the snapshot filter (so UPSERT can correct
   the prior accepted state).
2. cancelled+payout>0 is KEPT by both filters (so late-paid cancellations
   stay in the report — accounting needs them).
3. SKIP_STATUSES (inquiry, voided, ...) are dropped at both layers.
4. Structurally invalid rows (no dates, no confirmation_code) are dropped.
5. The engine-side filter `_normalize_reservation` is unchanged: still drops
   cancelled+payout=0.
6. End-to-end: writing a cancelled+payout=0 row through the snapshot filter
   into the snapshot table overwrites a prior accepted payload.
"""
from __future__ import annotations

import json
import sqlite3

from report.loader import (
    _minimal_filter_for_snapshot,
    normalize_reservation,
    normalize_reservations_for_snapshot,
)


def _raw(**overrides) -> dict:
    """A reasonable Hostify reservation dict; tests override individual fields."""
    base = {
        "id": 9999,
        "channel_reservation_id": "AIR-TEST",
        "guest_name": "Test Guest",
        "checkIn": "2026-04-11",
        "checkOut": "2026-04-15",
        "nights": 4,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "cleaning_fee": 0,
        "city_tax": 0,
        "channel_commission": 0,
        "transaction_fee": 0,
        "payout_price": 0,
        "source": "Booking.com",
        "status": "cancelled",
        "confirmed_at": "2026-04-10 21:42:06",
        "cancelled_at": "2026-04-11 10:31:48",
        "listing_id": 184991,
        "listing_nickname": "Vinohradská 208/14 - Bcom",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
#  Snapshot filter — keeps cancellations                                       #
# --------------------------------------------------------------------------- #

def test_snapshot_filter_keeps_cancelled_with_zero_payout():
    """The bug we're fixing: cancelled+payout=0 must reach snapshot UPSERT."""
    row = _minimal_filter_for_snapshot(_raw(status="cancelled", payout_price=0))
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["is_cancelled"] is True
    assert row["payout_price_eur"] == 0.0
    assert row["cancelled_at"] == "2026-04-11 10:31:48"


def test_snapshot_filter_keeps_late_cancellation_with_payout():
    """Late cancellations (payout already collected) — KEEP for the report."""
    row = _minimal_filter_for_snapshot(_raw(status="cancelled", payout_price=237.44))
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["is_cancelled"] is True
    assert row["payout_price_eur"] == 237.44


def test_snapshot_filter_keeps_accepted():
    row = _minimal_filter_for_snapshot(_raw(status="accepted", payout_price=350.0))
    assert row is not None
    assert row["status"] == "accepted"
    assert row["is_cancelled"] is False


def test_snapshot_filter_drops_skip_statuses():
    """inquiry/voided/timedout/... are never useful, even at the snapshot layer."""
    for bad_status in ("inquiry", "voided", "timedout", "declined", "expired"):
        row = _minimal_filter_for_snapshot(_raw(status=bad_status))
        assert row is None, f"status={bad_status} should be dropped"


def test_snapshot_filter_drops_missing_dates_or_code():
    assert _minimal_filter_for_snapshot(_raw(status="accepted", checkIn="")) is None
    assert _minimal_filter_for_snapshot(_raw(status="accepted", checkOut="")) is None
    assert _minimal_filter_for_snapshot(
        _raw(status="accepted", checkIn="not-a-date")
    ) is None
    no_code = _raw(status="accepted")
    no_code.pop("channel_reservation_id")
    no_code["confirmation_code"] = ""
    assert _minimal_filter_for_snapshot(no_code) is None


# --------------------------------------------------------------------------- #
#  Engine filter unchanged — still drops cancelled+payout=0                    #
# --------------------------------------------------------------------------- #

def test_engine_filter_still_drops_cancelled_zero_payout():
    """The fix must NOT change the report-time behavior."""
    row = normalize_reservation(_raw(status="cancelled", payout_price=0))
    assert row is None


def test_engine_filter_keeps_late_cancellation_with_payout():
    """Same scenario as the snapshot test — must still appear in the report."""
    row = normalize_reservation(_raw(status="cancelled", payout_price=237.44))
    assert row is not None
    assert row["is_cancelled"] is True
    assert row["payout_price_eur"] == 237.44


# --------------------------------------------------------------------------- #
#  End-to-end: snapshot UPSERT overwrites stale accepted payload              #
# --------------------------------------------------------------------------- #

def _make_snapshot_db(tmp_path):
    """Create a DB with just the hostify_reservations table."""
    db_path = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE hostify_reservations (
            confirmation_code TEXT PRIMARY KEY,
            reservation_id    TEXT,
            source            TEXT,
            status            TEXT,
            guest_name        TEXT,
            check_in          TEXT,
            check_out         TEXT,
            assigned_year     INTEGER,
            assigned_month    INTEGER,
            listing_nickname  TEXT,
            payload_json      TEXT,
            first_seen_at     TEXT,
            last_seen_at      TEXT
        )
        """
    )
    conn.commit()
    return conn


def test_upsert_overwrites_stale_accepted_with_cancelled(tmp_path):
    """
    Simulate the bug: snapshot has 6333693395 marked 'accepted', then a fresh
    sync brings the same code as 'cancelled+payout=0'. After save_hostify_
    reservations + the new snapshot filter, the row should reflect cancelled.
    """
    from report.db import save_hostify_reservations

    conn = _make_snapshot_db(tmp_path)
    try:
        # Stale state: previously stored as accepted.
        accepted_payload = {
            "confirmation_code": "6333693395",
            "status": "accepted",
            "is_cancelled": False,
            "payout_price_eur": 0.0,
        }
        conn.execute(
            "INSERT INTO hostify_reservations "
            "(confirmation_code, reservation_id, source, status, guest_name, "
            " check_in, check_out, assigned_year, assigned_month, "
            " listing_nickname, payload_json, first_seen_at, last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "6333693395", "4052592", "Booking.com", "accepted", "Test Guest",
                "2026-04-11", "2026-04-15", 2026, 4,
                "Vinohradská 208/14 - Bcom", json.dumps(accepted_payload),
                "2026-04-10T22:08:43+00:00", "2026-04-11T10:53:14+00:00",
            ),
        )
        conn.commit()

        # Fresh sync now brings it as cancelled+payout=0.
        fresh_raw = _raw(
            channel_reservation_id="6333693395",
            status="cancelled",
            payout_price=0,
        )
        normalized = normalize_reservations_for_snapshot([fresh_raw])
        assert len(normalized) == 1, "snapshot filter must NOT drop this row"

        save_hostify_reservations(conn, normalized)

        row = conn.execute(
            "SELECT status, payload_json FROM hostify_reservations "
            "WHERE confirmation_code = ?",
            ("6333693395",),
        ).fetchone()
        assert row["status"] == "cancelled"
        payload = json.loads(row["payload_json"])
        assert payload["is_cancelled"] is True
        assert payload["payout_price_eur"] == 0.0
        assert payload["status"] == "cancelled"
    finally:
        conn.close()


def test_normalize_reservations_for_snapshot_filters_list():
    raw_list = [
        _raw(channel_reservation_id="A", status="accepted", payout_price=100),
        _raw(channel_reservation_id="B", status="cancelled", payout_price=0),
        _raw(channel_reservation_id="C", status="voided"),  # dropped
        _raw(channel_reservation_id="D", status="cancelled", payout_price=237),
    ]
    result = normalize_reservations_for_snapshot(raw_list)
    codes = {r["confirmation_code"] for r in result}
    assert codes == {"A", "B", "D"}
