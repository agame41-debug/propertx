"""Deduplication guards for re-imported accounting & bank source files.

Two distinct real-world traps (found on prod 2026-05-24):

1. Účetnictví (315) re-exports overlap: two active accounting source files
   contain the SAME documents (e.g. file #17 covers 2026-01..03, file #29
   covers 2026-01..04). `get_accounting_entries` returned both → the 315 side
   of Srovnání double-counted (Opletalova 8 Feb showed 43 400 = 2×21 700).

2. Bank statement re-exports overlap: the SAME Airbnb payout exported from two
   different statements gets a different bank-internal tx_id → different tx_key
   → a plain tx_key UPSERT stored it twice.
"""

from __future__ import annotations

import os

os.environ.setdefault("RENTERO_ALLOW_INSECURE_DEFAULTS", "1")

from report.db import (
    get_accounting_entries,
    get_connection,
    save_accounting_entries,
    save_bank_transactions,
)


def _add_source_file(conn, sf_id: int, source_type: str, sha: str, active: int = 1) -> None:
    conn.execute(
        """INSERT INTO source_files (id, source_type, original_name, content, sha256, imported_at, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (sf_id, source_type, f"{source_type}_{sf_id}.csv", b"x", sha, "2026-05-01T00:00:00+00:00", active),
    )
    conn.commit()


def test_get_accounting_entries_dedups_reimported_document_by_newest_source_file():
    conn = get_connection(":memory:")
    try:
        _add_source_file(conn, 100, "accounting", "sha-old")
        _add_source_file(conn, 200, "accounting", "sha-new")
        entry = {
            "doc": "FKV260017", "doc_type": "FKV", "datum": "2026-02-10",
            "popis": "", "castka": 21700.0, "objekt": "Opletalova 8",
            "objekt_raw": "Opletalova 8", "mesic": "2026-02",
            "channel": "Airbnb", "stredisko": "", "ucet": "315001",
        }
        # Same document imported from an OLD and a NEW active source file.
        save_accounting_entries(conn, [entry], source_file_id=100)
        save_accounting_entries(conn, [dict(entry)], source_file_id=200)

        rows = get_accounting_entries(conn, channel="Airbnb", year=2026, month=2)
        # Must NOT double-count: only the newest source file's row survives.
        assert len(rows) == 1, [r["source_file_id"] for r in rows]
        assert rows[0]["source_file_id"] == 200
        assert sum(r["castka"] for r in rows) == 21700.0
    finally:
        conn.close()


def test_get_accounting_entries_keeps_distinct_documents():
    conn = get_connection(":memory:")
    try:
        _add_source_file(conn, 300, "accounting", "sha-distinct")
        save_accounting_entries(conn, [
            {"doc": "FKV260017", "doc_type": "FKV", "datum": "2026-02-10", "popis": "",
             "castka": 21700.0, "objekt": "Opletalova 8", "objekt_raw": "Opletalova 8",
             "mesic": "2026-02", "channel": "Airbnb", "stredisko": "", "ucet": "315001"},
            {"doc": "FKV260018", "doc_type": "FKV", "datum": "2026-02-11", "popis": "",
             "castka": 17266.0, "objekt": "Opletalova 8_4P nova", "objekt_raw": "x",
             "mesic": "2026-02", "channel": "Airbnb", "stredisko": "", "ucet": "315001"},
        ], source_file_id=300)
        rows = get_accounting_entries(conn, channel="Airbnb", year=2026, month=2)
        assert len(rows) == 2
    finally:
        conn.close()


def _bank_row(tx_key, gref, amt, datum="2026-04-01", tx_id=None):
    return {
        "tx_key": tx_key, "tx_id": tx_id or tx_key, "datum": datum,
        "amount_czk": amt, "gref": gref, "property_id": "",
        "zprava": f"{gref}/ROC/{gref}", "source_name": "stmt.csv",
    }


def _bank_count(conn, gref):
    return conn.execute(
        "SELECT count(*) FROM bank_transactions WHERE gref = ?", (gref,)
    ).fetchone()[0]


def test_save_bank_transactions_skips_cross_export_duplicate():
    conn = get_connection(":memory:")
    try:
        # Same payout (gref+date+amount) from statement A...
        save_bank_transactions(conn, "airbnb", [_bank_row("0007600957058", "G-QVPQPRB6DN6EP", 18216.0)])
        # ...then from statement B with a DIFFERENT bank tx_id → must NOT duplicate.
        save_bank_transactions(conn, "airbnb", [_bank_row("2000025532423093", "G-QVPQPRB6DN6EP", 18216.0)])
        assert _bank_count(conn, "G-QVPQPRB6DN6EP") == 1

        # A genuinely different payout is still stored.
        save_bank_transactions(conn, "airbnb", [_bank_row("TXOTHER", "G-OTHER123", 999.0)])
        assert _bank_count(conn, "G-OTHER123") == 1

        # Same gref but a different amount = a separate transfer, keep both.
        save_bank_transactions(conn, "airbnb", [_bank_row("TX-split", "G-OTHER123", 12.0)])
        assert _bank_count(conn, "G-OTHER123") == 2
    finally:
        conn.close()


def test_save_bank_transactions_same_tx_key_still_upserts():
    conn = get_connection(":memory:")
    try:
        save_bank_transactions(conn, "airbnb", [_bank_row("TXSAME", "G-AAA", 100.0)])
        # Re-importing the exact same file (same tx_key) updates in place, not skipped.
        row = _bank_row("TXSAME", "G-AAA", 100.0)
        row["zprava"] = "updated"
        save_bank_transactions(conn, "airbnb", [row])
        assert _bank_count(conn, "G-AAA") == 1
        z = conn.execute("SELECT zprava FROM bank_transactions WHERE tx_key='TXSAME'").fetchone()[0]
        assert z == "updated"
    finally:
        conn.close()
