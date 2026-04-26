"""Tests for integrity-defense layers (L1 dedup, L2 cross-report, L3 audit)
and ownership-removal data migration."""
from __future__ import annotations

import json
from datetime import date

import pytest

from report.db import (
    get_connection,
    save_bank_transactions,
    save_payout_batch_bank_matches,
)


def test_drop_ownership_columns_migration():
    """Old payout_batch_bank_matches table with slug/year/month should be
    rebuilt without those columns; row data preserved."""
    import sqlite3
    from report.db import _drop_ownership_columns_from_payout_batch_bank_matches

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Hand-build an OLD-shape table with extra columns.
    conn.executescript("""
        CREATE TABLE payout_batch_bank_matches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel         TEXT NOT NULL,
            batch_ref       TEXT NOT NULL,
            tx_key          TEXT NOT NULL,
            match_method    TEXT,
            matched_amount_czk REAL,
            matched_at      TEXT NOT NULL,
            slug            TEXT DEFAULT '',
            year            INTEGER DEFAULT 0,
            month           INTEGER DEFAULT 0,
            UNIQUE(channel, batch_ref, tx_key)
        );
        INSERT INTO payout_batch_bank_matches
            (channel, batch_ref, tx_key, match_method, matched_amount_czk,
             matched_at, slug, year, month)
        VALUES
            ('airbnb', 'G-A', 'TX1', 'gref', 1000.0, '2026-04-01', 'aptA', 2026, 3),
            ('booking', 'JR1', 'TX2', 'descriptor_ref', 500.0, '2026-04-02', 'aptB', 2026, 4);
    """)
    conn.commit()

    _drop_ownership_columns_from_payout_batch_bank_matches(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(payout_batch_bank_matches)")}
    assert "slug" not in cols
    assert "year" not in cols
    assert "month" not in cols
    assert {"id", "channel", "batch_ref", "tx_key", "match_method",
            "matched_amount_czk", "matched_at"}.issubset(cols)

    rows = conn.execute(
        "SELECT channel, batch_ref, tx_key, match_method, matched_amount_czk "
        "FROM payout_batch_bank_matches ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["channel"] == "airbnb"
    assert rows[0]["batch_ref"] == "G-A"
    assert rows[1]["channel"] == "booking"
    conn.close()


def test_drop_ownership_columns_migration_idempotent():
    """Calling the migration twice is a no-op on the already-clean table."""
    import sqlite3
    from report.db import _drop_ownership_columns_from_payout_batch_bank_matches

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE payout_batch_bank_matches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel         TEXT NOT NULL,
            batch_ref       TEXT NOT NULL,
            tx_key          TEXT NOT NULL,
            match_method    TEXT,
            matched_amount_czk REAL,
            matched_at      TEXT NOT NULL,
            UNIQUE(channel, batch_ref, tx_key)
        );
    """)
    conn.commit()
    _drop_ownership_columns_from_payout_batch_bank_matches(conn)
    _drop_ownership_columns_from_payout_batch_bank_matches(conn)  # no-op
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(payout_batch_bank_matches)")}
    assert {"id", "channel", "batch_ref", "tx_key", "match_method",
            "matched_amount_czk", "matched_at"} == cols
    conn.close()
