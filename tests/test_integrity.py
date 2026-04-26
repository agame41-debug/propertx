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


def test_drop_ownership_columns_recovers_from_partial_rename():
    """If a previous run was interrupted between DROP and RENAME, leaving
    behind payout_batch_bank_matches__new with no main table, the next
    migration should still complete cleanly. Reproduces the crash-recovery
    edge case."""
    import sqlite3
    from report.db import _drop_ownership_columns_from_payout_batch_bank_matches

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Simulate the post-DROP-pre-RENAME state: __new exists, main does not.
    conn.executescript("""
        CREATE TABLE payout_batch_bank_matches__new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel         TEXT NOT NULL,
            batch_ref       TEXT NOT NULL,
            tx_key          TEXT NOT NULL,
            match_method    TEXT,
            matched_amount_czk REAL,
            matched_at      TEXT NOT NULL,
            UNIQUE(channel, batch_ref, tx_key)
        );
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
    """)
    conn.commit()

    # Should not raise — the leftover __new is dropped first.
    _drop_ownership_columns_from_payout_batch_bank_matches(conn)

    # The renamed table should exist with the canonical schema.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(payout_batch_bank_matches)")}
    assert "slug" not in cols
    # The leftover __new table should be gone.
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='payout_batch_bank_matches__new'"
    ).fetchone()
    assert leftover is None
    conn.close()


def test_save_payout_batch_bank_matches_no_ownership_columns():
    """Function signature should not accept slug/year/month, and the
    persisted row should contain only the new columns."""
    conn = get_connection(":memory:")
    try:
        save_payout_batch_bank_matches(
            conn,
            "airbnb",
            [{
                "batch_ref": "G-XYZ",
                "tx_key": "TX-XYZ",
                "match_method": "gref",
                "matched_amount_czk": 1234.5,
            }],
        )
        rows = conn.execute(
            "SELECT * FROM payout_batch_bank_matches WHERE batch_ref = 'G-XYZ'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["match_method"] == "gref"
        assert rows[0]["matched_amount_czk"] == 1234.5
        # Ownership columns must not exist.
        cols = rows[0].keys()
        assert "slug" not in cols
        assert "year" not in cols
        assert "month" not in cols
    finally:
        conn.close()


def test_save_payout_batch_bank_matches_signature_has_no_ownership_kwargs():
    import inspect
    from report.db import save_payout_batch_bank_matches
    sig = inspect.signature(save_payout_batch_bank_matches)
    assert "slug" not in sig.parameters
    assert "year" not in sig.parameters
    assert "month" not in sig.parameters


def test_find_code_in_other_snapshots_finds_cross_report_dupe():
    from report.bank import _find_code_in_other_snapshots
    from report.db import save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt", 2026, 3, [
            {"confirmation_code": "DUPE", "data_marker": "march"},
        ])
        save_report_rows(conn, "apt", 2026, 4, [
            {"confirmation_code": "DUPE", "data_marker": "april"},
        ])
        # Looking from April's perspective, March is "other"
        result = _find_code_in_other_snapshots(conn, "DUPE", "apt", 2026, 4)
        assert result == [("apt", 2026, 3)]
    finally:
        conn.close()


def test_find_code_in_other_snapshots_returns_empty_when_unique():
    from report.bank import _find_code_in_other_snapshots
    from report.db import save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt", 2026, 3, [
            {"confirmation_code": "ONLY"},
        ])
        result = _find_code_in_other_snapshots(conn, "ONLY", "apt", 2026, 3)
        assert result == []
    finally:
        conn.close()


def test_find_code_in_other_snapshots_ignores_empty_code():
    from report.bank import _find_code_in_other_snapshots

    conn = get_connection(":memory:")
    try:
        result = _find_code_in_other_snapshots(conn, "", "apt", 2026, 3)
        assert result == []
    finally:
        conn.close()


def test_l2_annotates_cross_report_duplicate_in_airbnb_enrichment():
    """When the same confirmation_code already lives in another snapshot
    and we run enrich_rows_with_bank, the new row gets an INTEGRITY: note."""
    from datetime import date
    from report.bank import enrich_rows_with_bank, build_bank_index
    from report.db import save_report_rows, save_bank_transactions

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt", 2026, 3, [
            {"confirmation_code": "DUPE-CODE"},
        ])
        save_bank_transactions(conn, "airbnb", [{
            "tx_key": "2026-04-05|5000.00|G-DUPE|",
            "tx_id": "TX-DUPE",
            "datum": date(2026, 4, 5),
            "amount_czk": 5000.0,
            "gref": "G-DUPE",
            "zprava": "G-DUPE payout",
            "source_name": "bank.csv",
        }])
        bank_rows = [{
            "datum": date(2026, 4, 5),
            "amount_czk": 5000.0,
            "gref": "G-DUPE",
            "booking_ref": "",
            "tx_id": "TX-DUPE",
            "tx_key": "2026-04-05|5000.00|G-DUPE|",
            "zprava": "G-DUPE payout",
            "source_name": "bank.csv",
        }]
        index_by_gref, no_ref_rows = build_bank_index(bank_rows)
        gref_map = {"DUPE-CODE": {"gref": "G-DUPE", "payout_date": "2026-04-05",
                                  "payout_czk": 5000.0}}
        all_batches_map = {"DUPE-CODE": [{"gref": "G-DUPE",
                                          "payout_date": "2026-04-05",
                                          "payout_czk": 5000.0}]}
        rows = [{"confirmation_code": "DUPE-CODE", "source": "Airbnb",
                 "batch_ref": "G-DUPE", "batch_payout_date": "2026-04-05",
                 "batch_amount_czk_expected": 5000.0}]
        enriched, _ = enrich_rows_with_bank(
            rows, gref_map, index_by_gref, no_ref_rows,
            all_batches_map=all_batches_map,
            conn=conn, slug="apt", year=2026, month=4,
        )
        # The matched row must reference the other snapshot.
        comment = enriched[0].get("verification_comment") or ""
        assert "INTEGRITY:" in comment
        assert "apt/2026-3" in comment or "apt/2026-03" in comment
        # And bank_status must still be DORAZILO (not silently flipped).
        assert enriched[0]["bank_status"] == "DORAZILO"
    finally:
        conn.close()


def test_l2_annotates_cross_report_duplicate_in_booking_enrichment():
    """Same as the airbnb test but for the booking enrichment path."""
    from datetime import date
    from report.bank import enrich_booking_rows_with_bank
    from report.db import save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt", 2026, 3, [
            {"confirmation_code": "BDUPE"},
        ])
        # Pre-populate matched bank row directly via a constructed booking idx.
        booking_bank_idx = {"normalized_ref": [{
            "datum": date(2026, 4, 10),
            "amount_czk": 3000.0,
            "tx_key": "2026-04-10|3000.00||",
            "booking_ref": "REFXYZ",
            "zprava": "REFXYZ payout",
        }]}
        rows = [{
            "confirmation_code": "BDUPE",
            "source": "Booking.com",
            "batch_ref": "REFXYZ",
            "batch_payout_date": "2026-04-10",
            "batch_amount_czk_expected": 3000.0,
        }]
        prop = {"channels": {"booking": {"property_id": "PID"}}}

        enriched, _ = enrich_booking_rows_with_bank(
            rows, booking_bank_idx, prop, year=2026, month=4,
            booking_bank_idx_all=booking_bank_idx,
            conn=conn, slug="apt",
        )
        comment = enriched[0].get("verification_comment") or ""
        # The synthetic data is constructed so booking enrichment matches
        # successfully → bank_status DORAZILO. If a future change breaks
        # this assumption, the test fails loudly and prompts a revisit.
        assert enriched[0]["bank_status"] == "DORAZILO"
        assert "INTEGRITY:" in comment
        assert "apt/2026-3" in comment or "apt/2026-03" in comment
    finally:
        conn.close()


def test_integrity_audit_table_exists_after_ensure_schema():
    conn = get_connection(":memory:")
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(integrity_audit)")}
        assert {"id", "confirmation_code", "occurrences", "detected_at"}.issubset(cols)
        idx = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_integrity_audit_detected_at" in idx
    finally:
        conn.close()


def test_run_integrity_audit_finds_cross_snapshot_dupe():
    from report.db import run_integrity_audit, save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt_A", 2026, 3, [{"confirmation_code": "X"}])
        save_report_rows(conn, "apt_B", 2026, 4, [{"confirmation_code": "X"}])
        save_report_rows(conn, "apt_C", 2026, 5, [{"confirmation_code": "Y"}])  # unique

        findings = run_integrity_audit(conn)
        assert len(findings) == 1
        assert findings[0]["confirmation_code"] == "X"
        # Occurrences string contains both snapshots
        occ = findings[0]["occurrences"]
        assert "apt_A/2026-03" in occ
        assert "apt_B/2026-04" in occ

        # And one row was inserted into integrity_audit
        rows = conn.execute(
            "SELECT confirmation_code, occurrences FROM integrity_audit"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["confirmation_code"] == "X"
    finally:
        conn.close()


def test_run_integrity_audit_ignores_empty_codes():
    from report.db import run_integrity_audit, save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt_A", 2026, 3, [{"confirmation_code": ""}])
        save_report_rows(conn, "apt_B", 2026, 4, [{"confirmation_code": ""}])
        findings = run_integrity_audit(conn)
        assert findings == []
        rows = conn.execute("SELECT * FROM integrity_audit").fetchall()
        assert rows == []
    finally:
        conn.close()


def test_run_integrity_audit_appends_new_findings_each_call():
    """The audit table is an event log; each call appends new detected_at
    rows even for the same dupe."""
    from report.db import run_integrity_audit, save_report_rows

    conn = get_connection(":memory:")
    try:
        save_report_rows(conn, "apt_A", 2026, 3, [{"confirmation_code": "X"}])
        save_report_rows(conn, "apt_B", 2026, 4, [{"confirmation_code": "X"}])
        run_integrity_audit(conn)
        run_integrity_audit(conn)
        rows = conn.execute("SELECT * FROM integrity_audit").fetchall()
        assert len(rows) == 2  # two events for same dupe
    finally:
        conn.close()
