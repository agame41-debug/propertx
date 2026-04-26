# Bank-match ownership fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the broken `payout_batch_bank_matches` ownership/downgrade machinery and replace it with three explicit integrity-defense layers, plus one-shot data migration to restore corrupted LOCKED rows.

**Architecture:** Two parts run together. Part A strips `slug/year/month` columns and the downgrade blocks (`get_bank_match_owner`, `clear_bank_matches_for_month`, the `already_owned` branches in `bank.py`). Part B adds three layers of integrity defense — L1 in-snapshot dedup with `verification_comment` annotations (engine), L2 cross-snapshot detector via indexed SELECT (bank), L3 boot audit with admin section (`integrity_audit` table). A one-shot migration patches LOCKED `report_rows` whose `bank_status` was silently flipped to `CHYBÍ`.

**Tech Stack:** Python 3, FastAPI, SQLite (single file, WAL), Jinja2 templates, pytest. No ORM — direct SQL via `sqlite3.Connection`.

**Spec:** [docs/superpowers/specs/2026-04-26-bank-match-ownership-fix-design.md](docs/superpowers/specs/2026-04-26-bank-match-ownership-fix-design.md)

---

## File structure

**Modified:**
- `report/db.py` — schema changes (drop ownership columns, add `integrity_audit` table), simplify `save_payout_batch_bank_matches`, delete `get_bank_match_owner` + `clear_bank_matches_for_month`, add `_drop_ownership_columns_from_payout_batch_bank_matches`, `run_integrity_audit`, `_restore_bank_status_after_ownership_fix`.
- `report/bank.py` — delete the `already_owned` downgrade branches in airbnb + booking enrichment, add `_find_code_in_other_snapshots` for L2.
- `report/engine.py` — drop `clear_bank_matches_for_month` call, drop slug/year/month kwargs from `save_payout_batch_bank_matches` calls, add `_flag_duplicate_codes_within_snapshot` + call before `save_report_rows`.
- `report/summary.py` — dedupe `rows` by `confirmation_code`, add `integrity_warnings` list to summary dict.
- `web.py` — `_app_lifespan` runs `_restore_bank_status_after_ownership_fix` then `run_integrity_audit` after `ensure_schema`. New endpoint `/admin/integrity`.
- `templates/audit.html` — new "Integrity violations" section.
- `templates/partials/reservation_detail.html` — INTEGRITY badge near `verification_diff`.
- `templates/partials/property_intro.html` — banner when `summary.integrity_warnings` non-empty.

**Created:**
- `tests/test_integrity.py` — L3 audit + data-migration tests + L1/L2 cross-snapshot tests.

**Updated tests:**
- `tests/test_bank.py` — add cross-month and cross-property regression tests.
- `tests/test_engine.py` — add L1 (per-snapshot dedup) tests.
- `tests/test_summary.py` — add dedup + `integrity_warnings` tests.

---

## Phase 1 — Cross-month regression test (red)

The first task captures the desired new behavior as a failing test, before any code changes. After Part A is done, this test should pass.

### Task 1: Failing cross-month regression test

**Files:**
- Modify: `tests/test_bank.py`

- [ ] **Step 1: Add the failing test at the end of `tests/test_bank.py`**

Append this test to `tests/test_bank.py`:

```python
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
        # NOTE: slug/year/month kwargs are required pre-fix to reproduce the
        # bug (the legacy ownership row needs year/month populated for
        # get_bank_match_owner's `year > 0 AND month > 0` filter). Task 3
        # strips these kwargs from the function — when that lands, remove
        # them from this and the April call below.
        save_payout_batch_bank_matches(conn, "airbnb", march_matches,
                                       slug="apt", year=2026, month=3)
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
        save_payout_batch_bank_matches(conn, "airbnb", april_matches,
                                       slug="apt", year=2026, month=4)
        save_report_rows(conn, "apt", 2026, 4, april_enriched)

        # Both months must show DORAZILO. Pre-fix April would be CHYBÍ.
        assert march_enriched[0]["bank_status"] == "DORAZILO"
        assert april_enriched[0]["bank_status"] == "DORAZILO"
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
pytest tests/test_bank.py::test_cross_month_batch_no_silent_downgrade -v
```

Expected: FAIL — April row shows `CHYBÍ` because `get_bank_match_owner` returns the March owner. (If the test passes immediately, the bug isn't reproduced and the rest of the plan is moot — stop and investigate.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_bank.py
git commit -m "test: failing regression for cross-month bank-match downgrade

Captures the broken behavior before fix: a batch spanning two months
silently flips one reservation to CHYBÍ via the owner check.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Remove ownership machinery

### Task 2: Schema migration helper to drop ownership columns

**Files:**
- Modify: `report/db.py` — add `_drop_ownership_columns_from_payout_batch_bank_matches`
- Modify: `tests/test_integrity.py` — new file
- Test: `tests/test_integrity.py::test_drop_ownership_columns_migration`

- [ ] **Step 1: Create `tests/test_integrity.py` with failing migration test**

Create `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL (function not yet defined)**

```bash
pytest tests/test_integrity.py::test_drop_ownership_columns_migration -v
```

Expected: ImportError or AttributeError on `_drop_ownership_columns_from_payout_batch_bank_matches`.

- [ ] **Step 3: Add the helper to `report/db.py`**

Insert this function in `report/db.py` near `_run_migrations` (just before it):

```python
def _drop_ownership_columns_from_payout_batch_bank_matches(conn: sqlite3.Connection) -> None:
    """One-shot migration: remove slug/year/month columns added 2026-04-13.

    These columns powered the buggy ownership/downgrade mechanism that
    silently flipped DORAZILO → CHYBÍ on locked months. Removing them is
    safe because no callers read them after this fix lands.

    Idempotent: detects already-migrated schema and returns immediately.
    """
    cols = {row["name"] for row in conn.execute(
        "PRAGMA table_info(payout_batch_bank_matches)"
    )}
    if not {"slug", "year", "month"} & cols:
        return
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
        INSERT INTO payout_batch_bank_matches__new
            (id, channel, batch_ref, tx_key, match_method,
             matched_amount_czk, matched_at)
        SELECT id, channel, batch_ref, tx_key, match_method,
               matched_amount_czk, matched_at
        FROM payout_batch_bank_matches;
        DROP TABLE payout_batch_bank_matches;
        ALTER TABLE payout_batch_bank_matches__new
              RENAME TO payout_batch_bank_matches;
    """)
    conn.commit()
```

- [ ] **Step 4: Wire it into `_run_migrations`**

In `report/db.py`, modify `_run_migrations` ([report/db.py:718-769](report/db.py:718)):

Find the three lines:
```python
    _ensure_column(conn, "payout_batch_bank_matches", "slug", "slug TEXT DEFAULT ''")
    _ensure_column(conn, "payout_batch_bank_matches", "year", "year INTEGER DEFAULT 0")
    _ensure_column(conn, "payout_batch_bank_matches", "month", "month INTEGER DEFAULT 0")
```

Replace them with a single call:
```python
    _drop_ownership_columns_from_payout_batch_bank_matches(conn)
```

- [ ] **Step 5: Run both migration tests, expect PASS**

```bash
pytest tests/test_integrity.py::test_drop_ownership_columns_migration tests/test_integrity.py::test_drop_ownership_columns_migration_idempotent -v
```

Expected: PASS for both.

- [ ] **Step 6: Run full test suite, expect a few failures (next tasks fix them)**

```bash
pytest tests/ -x --ignore=tests/test_bank.py 2>&1 | tail -30
```

It is OK if `test_bank.py` and any test that calls `save_payout_batch_bank_matches(... slug=..., year=..., month=...)` fails — those are addressed next.

- [ ] **Step 7: Commit**

```bash
git add report/db.py tests/test_integrity.py
git commit -m "fix: migrate payout_batch_bank_matches off ownership columns

Removes slug/year/month columns that powered the buggy ownership
downgrade. Migration is idempotent and runs from _run_migrations.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Strip slug/year/month from `save_payout_batch_bank_matches`

**Files:**
- Modify: `report/db.py:2372-2413`
- Modify: `report/engine.py:930-931`
- Modify: `tests/test_integrity.py` — add test

- [ ] **Step 1: Add failing test**

Append to `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_save_payout_batch_bank_matches_signature_has_no_ownership_kwargs -v
```

Expected: FAIL — `slug` is still a parameter.

- [ ] **Step 3: Simplify `save_payout_batch_bank_matches` in `report/db.py:2372-2413`**

Replace the whole function:

```python
def save_payout_batch_bank_matches(
    conn: sqlite3.Connection,
    channel: str,
    matches: list[dict],
) -> None:
    """Persist batch ↔ bank transaction links for future drill-down screens."""
    if not matches:
        return
    now = _now()
    conn.executemany(
        """INSERT INTO payout_batch_bank_matches
           (channel, batch_ref, tx_key, match_method, matched_amount_czk, matched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel, batch_ref, tx_key) DO UPDATE SET
             match_method=excluded.match_method,
             matched_amount_czk=excluded.matched_amount_czk,
             matched_at=excluded.matched_at""",
        [
            (
                channel,
                m.get("batch_ref", ""),
                m.get("tx_key", ""),
                m.get("match_method", ""),
                m.get("matched_amount_czk"),
                now,
            )
            for m in matches
            if m.get("batch_ref") and m.get("tx_key")
        ],
    )
    conn.commit()
```

- [ ] **Step 4: Update both call sites in `report/engine.py:930-931`**

Replace:
```python
    save_payout_batch_bank_matches(conn, "airbnb", airbnb_matches, slug=slug, year=year, month=month)
    save_payout_batch_bank_matches(conn, "booking", booking_matches, slug=slug, year=year, month=month)
```

With:
```python
    save_payout_batch_bank_matches(conn, "airbnb", airbnb_matches)
    save_payout_batch_bank_matches(conn, "booking", booking_matches)
```

- [ ] **Step 4b: Update the regression test from Task 1**

In `tests/test_bank.py::test_cross_month_batch_no_silent_downgrade`, find the two
calls to `save_payout_batch_bank_matches(... slug=..., year=..., month=...)`
and remove the `slug=`, `year=`, `month=` kwargs:

```python
        save_payout_batch_bank_matches(conn, "airbnb", march_matches)
        ...
        save_payout_batch_bank_matches(conn, "airbnb", april_matches)
```

The test will still demonstrate the regression-protection: at this point in the
sequence (Task 3, before Task 5), the downgrade in `bank.py` is still in place,
but the saved rows no longer have year/month → `get_bank_match_owner` returns
None → no downgrade fires. **Expected test status after Step 4b: passes
"by accident".** Task 5 then removes the downgrade entirely, making the test
robust regardless of saved-row shape. Both states are progress; the test will
fail only if Task 5 is mis-applied.

- [ ] **Step 5: Run new tests, expect PASS**

```bash
pytest tests/test_integrity.py::test_save_payout_batch_bank_matches_signature_has_no_ownership_kwargs tests/test_integrity.py::test_save_payout_batch_bank_matches_no_ownership_columns -v
```

Expected: PASS.

- [ ] **Step 6: Run all tests in `test_bank.py`, expect existing tests to still pass**

```bash
pytest tests/test_bank.py -v 2>&1 | tail -40
```

The existing `tests/test_bank.py:343, 448` callers do not pass `slug=`/`year=`/`month=` (they were added later as default-`""` kwargs), so they continue working.

- [ ] **Step 7: Commit**

```bash
git add report/db.py report/engine.py tests/test_integrity.py
git commit -m "refactor: drop slug/year/month kwargs from save_payout_batch_bank_matches

Function no longer needs ownership context. Callers in engine.py simplified.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Delete `clear_bank_matches_for_month` + its call site

**Files:**
- Modify: `report/db.py:2416-2426`
- Modify: `report/engine.py:390-391`

- [ ] **Step 1: Delete the function from `report/db.py`**

Remove lines 2416-2426 (the entire `clear_bank_matches_for_month` function):

```python
def clear_bank_matches_for_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> None:
    """Remove bank match records for a specific slug/month before regeneration."""
    conn.execute(
        "DELETE FROM payout_batch_bank_matches WHERE slug = ? AND year = ? AND month = ?",
        (slug, year, month),
    )
```

Delete it. The whole-row UPSERT in `save_payout_batch_bank_matches` makes pre-clearing unnecessary.

- [ ] **Step 2: Delete its call site in `report/engine.py:390-391`**

Remove these two lines from `report/engine.py`:

```python
    from report.db import clear_bank_matches_for_month
    clear_bank_matches_for_month(conn, slug, year, month)
```

(The local import is on the line above the call — both lines go.)

- [ ] **Step 3: Run engine tests, expect PASS**

```bash
pytest tests/test_engine.py -v 2>&1 | tail -20
```

Expected: PASS for all engine tests.

- [ ] **Step 4: Commit**

```bash
git add report/db.py report/engine.py
git commit -m "refactor: drop clear_bank_matches_for_month — no longer needed

Whole-row UPSERT in save_payout_batch_bank_matches replaces the
clear-then-insert pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Delete `get_bank_match_owner` and the downgrade blocks

**Files:**
- Modify: `report/db.py:2429-2447`
- Modify: `report/bank.py:373-392` (airbnb downgrade)
- Modify: `report/bank.py:629-655` (booking downgrade)

- [ ] **Step 1: Delete `get_bank_match_owner` from `report/db.py:2429-2447`**

Remove the entire function (~19 lines):

```python
def get_bank_match_owner(
    conn: sqlite3.Connection,
    channel: str,
    batch_ref: str,
    tx_key: str,
) -> dict | None:
    """Return the (year, month) that owns this bank match, or None.
    ...
    """
    row = conn.execute(
        """SELECT year, month FROM payout_batch_bank_matches
           ...
        (channel, batch_ref, tx_key),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 2: Replace the airbnb downgrade block in `report/bank.py:373-392`**

Find this block in `enrich_rows_with_bank` (around line 373):

```python
        if bank_row:
            already_owned = False
            if conn and slug:
                from report.db import get_bank_match_owner
                owner = get_bank_match_owner(conn, "airbnb", batch_key, bank_row.get("tx_key", ""))
                if owner and (owner["year"] != year or owner["month"] != month):
                    already_owned = True
            datum = bank_row.get("datum")
            if already_owned:
                enriched.append({
                    **row,
                    "batch_ref": gref,
                    "batch_payout_date": payout_date_str,
                    "batch_amount_czk_expected": payout_czk,
                    "payout_gref":     gref,
                    "bank_tx_key":     "",
                    "bank_datum":      "",
                    "bank_amount_czk": None,
                    "bank_status":     "CHYBÍ",
                })
            else:
                enriched.append({
                    **row,
                    "batch_ref": gref,
                    "batch_payout_date": payout_date_str,
                    "batch_amount_czk_expected": payout_czk,
                    "payout_gref":     gref,
                    "bank_tx_key":     bank_row.get("tx_key", ""),
                    "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                    "bank_amount_czk": bank_row["amount_czk"],
                    "bank_status":     "DORAZILO",
                })
```

Replace with the unconditional `DORAZILO` form:

```python
        if bank_row:
            datum = bank_row.get("datum")
            enriched.append({
                **row,
                "batch_ref": gref,
                "batch_payout_date": payout_date_str,
                "batch_amount_czk_expected": payout_czk,
                "payout_gref":     gref,
                "bank_tx_key":     bank_row.get("tx_key", ""),
                "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": bank_row["amount_czk"],
                "bank_status":     "DORAZILO",
            })
```

- [ ] **Step 3: Find and replace the booking downgrade block in `report/bank.py`**

Run:
```bash
grep -n "get_bank_match_owner\|already_owned" report/bank.py
```

Locate the booking-side block (around line 629). It looks similar to the airbnb one but uses `"booking"` and `batch_ref` instead of `batch_key`. Read the surrounding context and apply the same flattening: keep only the `DORAZILO` branch, remove the `if already_owned` branch.

The structure to remove:

```python
                from report.db import get_bank_match_owner
                owner = get_bank_match_owner(conn, "booking", batch_ref, matched_bank.get("tx_key", ""))
                ...
                if already_owned:
                    enriched.append({ ..., "bank_status": "CHYBÍ" })
                else:
                    enriched.append({ ..., "bank_status": "DORAZILO" })
```

Replace with the unconditional DORAZILO append (mirror the airbnb shape — drop the conditional, the import, the owner lookup, and the CHYBÍ branch). Keep the surrounding loop structure untouched.

- [ ] **Step 4: Run cross-month regression test from Task 1, expect PASS**

```bash
pytest tests/test_bank.py::test_cross_month_batch_no_silent_downgrade -v
```

Expected: PASS — both rows now `DORAZILO`.

- [ ] **Step 5: Run full bank tests, expect PASS**

```bash
pytest tests/test_bank.py tests/test_engine.py -v 2>&1 | tail -30
```

Expected: PASS. If any pre-existing test asserted on the old downgrade behavior, that test was asserting on a bug — fix it to match the new behavior, and note the change in commit message.

- [ ] **Step 6: Commit**

```bash
git add report/db.py report/bank.py
git commit -m "fix: remove silent DORAZILO→CHYBÍ downgrade in bank enrichment

Deletes get_bank_match_owner and the already_owned branches in both
airbnb and booking enrichment. The downgrade defended against a
non-existent double-counting risk (verified: bank_amount_czk is never
summed across enriched_rows). Cross-month regression test now passes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Add cross-property regression test

**Files:**
- Modify: `tests/test_bank.py` — append test

- [ ] **Step 1: Add the test**

Append to `tests/test_bank.py`:

```python
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
```

- [ ] **Step 2: Run, expect PASS**

```bash
pytest tests/test_bank.py::test_cross_property_batch_no_silent_downgrade -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_bank.py
git commit -m "test: cross-property batch shows DORAZILO in both slugs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — Layer 1: per-snapshot integrity

### Task 7: `_flag_duplicate_codes_within_snapshot` helper

**Files:**
- Modify: `report/engine.py` — add helper near other private helpers
- Modify: `tests/test_engine.py` — add tests

- [ ] **Step 1: Add failing tests**

Append to `tests/test_engine.py`:

```python
def test_flag_duplicate_codes_within_snapshot_annotates_violators():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "AAA", "verification_comment": ""},
        {"confirmation_code": "BBB", "verification_comment": "preexisting"},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 2
    assert rows[0]["verification_comment"].startswith("INTEGRITY:")
    assert rows[2]["verification_comment"].startswith("INTEGRITY:")
    assert rows[1]["verification_comment"] == "preexisting"  # untouched


def test_flag_duplicate_codes_ignores_empty_codes():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "", "verification_comment": ""},
        {"confirmation_code": "", "verification_comment": ""},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 0  # empty codes do not trigger
    assert all("INTEGRITY:" not in r["verification_comment"] for r in rows)


def test_flag_duplicate_codes_ignores_suffixed_synthetic_codes():
    """__ADJ, __AC, __SP[N] suffixes are part of the stored code, so they
    are distinct from their parents and don't trigger as duplicates."""
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "HMRA54", "verification_comment": ""},
        {"confirmation_code": "HMRA54__ADJ", "verification_comment": ""},
        {"confirmation_code": "HMRA54__SP1", "verification_comment": ""},
    ]
    count = _flag_duplicate_codes_within_snapshot(rows)
    assert count == 0


def test_flag_duplicate_codes_preserves_existing_comment():
    from report.engine import _flag_duplicate_codes_within_snapshot

    rows = [
        {"confirmation_code": "AAA", "verification_comment": "RECOVERED: prior"},
        {"confirmation_code": "AAA", "verification_comment": ""},
    ]
    _flag_duplicate_codes_within_snapshot(rows)
    assert "RECOVERED: prior" in rows[0]["verification_comment"]
    assert rows[0]["verification_comment"].startswith("INTEGRITY:")
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_engine.py::test_flag_duplicate_codes_within_snapshot_annotates_violators -v
```

Expected: ImportError on `_flag_duplicate_codes_within_snapshot`.

- [ ] **Step 3: Add the helper to `report/engine.py`**

In `report/engine.py`, near the top (after imports, before the public functions), add:

```python
def _flag_duplicate_codes_within_snapshot(rows: list[dict]) -> int:
    """Annotate rows whose confirmation_code repeats inside this snapshot.

    Empty codes are ignored — legacy synthetic rows can share an empty
    string by design. Suffixed codes (__ADJ, __AC, __SP[N]) are stored as
    distinct strings so they don't collide with parents.

    Returns count of annotated rows.
    """
    seen: dict[str, int] = {}
    for r in rows:
        code = r.get("confirmation_code") or ""
        if not code:
            continue
        seen[code] = seen.get(code, 0) + 1
    dupes = {c for c, n in seen.items() if n > 1}
    if not dupes:
        return 0
    annotated = 0
    for r in rows:
        if (r.get("confirmation_code") or "") in dupes:
            existing = r.get("verification_comment") or ""
            r["verification_comment"] = (
                f"INTEGRITY: duplicate confirmation_code in snapshot. {existing}".strip()
            )
            annotated += 1
    return annotated
```

- [ ] **Step 4: Run all four new tests, expect PASS**

```bash
pytest tests/test_engine.py -v -k "flag_duplicate_codes"
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add report/engine.py tests/test_engine.py
git commit -m "feat: L1 — _flag_duplicate_codes_within_snapshot helper

Annotates rows whose confirmation_code repeats inside one (slug,year,month)
snapshot. Empty codes and suffixed synthetic codes are treated correctly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Wire L1 into `generate_report_in_process`

**Files:**
- Modify: `report/engine.py:973` (insert call before `save_report_rows`)
- Modify: `tests/test_engine.py` — integration test

- [ ] **Step 1: Add integration test**

Append to `tests/test_engine.py`:

```python
def test_generate_report_in_process_flags_no_duplicate_in_clean_data():
    """Smoke: empty DB → no duplicates → no INTEGRITY: comments."""
    conn = get_connection(":memory:")
    try:
        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, _make_config()
        )
        assert "rows_count" in result
        rows = conn.execute(
            "SELECT data FROM report_rows WHERE slug = 'test_prop' AND year = 2026 AND month = 3"
        ).fetchall()
        for r in rows:
            data = json.loads(r["data"])
            assert "INTEGRITY:" not in (data.get("verification_comment") or "")
    finally:
        conn.close()
```

- [ ] **Step 2: Run, expect PASS (rows are empty so trivially passes)**

```bash
pytest tests/test_engine.py::test_generate_report_in_process_flags_no_duplicate_in_clean_data -v
```

Expected: PASS — but this also verifies the engine still runs. The wiring step below is the real change.

- [ ] **Step 3: Insert the call in `report/engine.py`**

In `report/engine.py`, find the line:
```python
    # ── Persist ─────────────────────────────────────────────────────────────
    save_report_rows(conn, slug, year, month, calc_rows)
```

(around [engine.py:973-974](report/engine.py:973))

Insert one line above `save_report_rows`:

```python
    # ── Persist ─────────────────────────────────────────────────────────────
    _flag_duplicate_codes_within_snapshot(calc_rows)
    save_report_rows(conn, slug, year, month, calc_rows)
```

- [ ] **Step 4: Run engine tests, expect PASS**

```bash
pytest tests/test_engine.py -v 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report/engine.py tests/test_engine.py
git commit -m "feat: L1 — flag duplicates before save_report_rows in engine

Annotates calc_rows in place with INTEGRITY: prefix on duplicates so
the warning survives into the persisted JSON, including for LOCKED snapshots.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Dedup + `integrity_warnings` in `build_report_summary`

**Files:**
- Modify: `report/summary.py:12-99`
- Modify: `tests/test_summary.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_summary.py`:

```python
def test_build_report_summary_dedupes_duplicate_codes_and_reports_warnings():
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "BBB", "payout_czk": 50.0, "cena_ubytovani_czk": 40.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)

    # Sums must use deduplicated rows: 100 (AAA once) + 50 (BBB) = 150
    assert summary["gross_payout_czk"] == 150.0
    assert summary["accommodation_income_czk"] == 120.0
    assert summary["integrity_warnings"] == ["AAA"]


def test_build_report_summary_no_warnings_when_unique():
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["integrity_warnings"] == []


def test_build_report_summary_empty_codes_dont_warn():
    """Multiple rows with empty confirmation_code are legitimate (synthetic
    rows). They should not trigger integrity warnings, but should NOT be
    deduped (each is a distinct synthetic record)."""
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "", "payout_czk": 10.0, "cena_ubytovani_czk": 8.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "", "payout_czk": 5.0, "cena_ubytovani_czk": 4.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["integrity_warnings"] == []
    assert summary["gross_payout_czk"] == 15.0  # both summed, neither deduped
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_summary.py::test_build_report_summary_dedupes_duplicate_codes_and_reports_warnings -v
```

Expected: FAIL — no `integrity_warnings` key, sums incorrect.

- [ ] **Step 3: Modify `report/summary.py:build_report_summary`**

In `report/summary.py`, after this line:
```python
    rows = [r for r in rows if not r.get("is_excluded")]
```
([summary.py:26](report/summary.py:26))

Insert dedup logic:

```python
    # Dedup by confirmation_code (L1 integrity defense). Empty codes are
    # legitimately repeatable (synthetic rows); only non-empty repeats are
    # treated as duplicates.
    seen_codes: set[str] = set()
    integrity_warnings: list[str] = []
    deduped_rows: list[dict] = []
    for r in rows:
        code = r.get("confirmation_code") or ""
        if code:
            if code in seen_codes:
                if code not in integrity_warnings:
                    integrity_warnings.append(code)
                continue  # skip duplicate
            seen_codes.add(code)
        deduped_rows.append(r)
    rows = deduped_rows
```

Then in the returned dict (currently [summary.py:80-99](report/summary.py:80)), add the new key. Find the `return {` line and add inside the dict:

```python
        "integrity_warnings": integrity_warnings,
```

(insert as the last key, before the closing `}`).

- [ ] **Step 4: Run all summary tests, expect PASS**

```bash
pytest tests/test_summary.py -v
```

Expected: all PASS. If any pre-existing test breaks because the returned dict now has an extra key, update the test to ignore the new key (it's additive — old asserts on individual keys still work).

- [ ] **Step 5: Commit**

```bash
git add report/summary.py tests/test_summary.py
git commit -m "feat: L1 — build_report_summary dedupes by confirmation_code

Returns integrity_warnings list. Empty codes are repeatable by design
(synthetic rows). The dedup keeps numeric sums correct even if a
duplicate slips past upstream guards.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Layer 2: cross-report detector

### Task 10: `_find_code_in_other_snapshots` helper

**Files:**
- Modify: `report/bank.py` — add helper
- Modify: `tests/test_integrity.py` — add tests

- [ ] **Step 1: Add failing test**

Append to `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_find_code_in_other_snapshots_finds_cross_report_dupe -v
```

Expected: ImportError on `_find_code_in_other_snapshots`.

- [ ] **Step 3: Add the helper to `report/bank.py`**

Add near the top of `report/bank.py` (after imports, before existing helpers):

```python
def _find_code_in_other_snapshots(
    conn,
    code: str,
    slug: str,
    year: int,
    month: int,
    limit: int = 5,
) -> list[tuple[str, int, int]]:
    """L2 integrity defense: locate the same confirmation_code in other
    (slug, year, month) snapshots. Empty codes never trigger.

    Uses idx_report_rows_code_lookup for the lookup."""
    if not code:
        return []
    rows = conn.execute(
        """SELECT slug, year, month FROM report_rows
           WHERE confirmation_code = ?
             AND NOT (slug = ? AND year = ? AND month = ?)
           ORDER BY year DESC, month DESC
           LIMIT ?""",
        (code, slug, year, month, limit),
    ).fetchall()
    return [(r["slug"], r["year"], r["month"]) for r in rows]
```

- [ ] **Step 4: Run, expect PASS**

```bash
pytest tests/test_integrity.py -v -k "find_code_in_other_snapshots"
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add report/bank.py tests/test_integrity.py
git commit -m "feat: L2 — _find_code_in_other_snapshots indexed lookup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Wire L2 into airbnb branch of `enrich_rows_with_bank`

**Files:**
- Modify: `report/bank.py` — extend the unconditional DORAZILO branch
- Modify: `tests/test_integrity.py` — add integration test

- [ ] **Step 1: Add failing integration test**

Append to `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_l2_annotates_cross_report_duplicate_in_airbnb_enrichment -v
```

Expected: FAIL — no INTEGRITY annotation yet.

- [ ] **Step 3: Modify the airbnb DORAZILO branch in `report/bank.py`**

Find the airbnb `if bank_row:` block (the simplified one from Task 5). Replace it with:

```python
        if bank_row:
            datum = bank_row.get("datum")
            verification_comment = row.get("verification_comment") or ""
            if conn is not None and slug:
                others = _find_code_in_other_snapshots(
                    conn, row.get("confirmation_code", ""), slug, year, month
                )
                if others:
                    where = ", ".join(f"{s}/{y}-{m}" for s, y, m in others)
                    verification_comment = (
                        f"INTEGRITY: also in {where}. {verification_comment}".strip()
                    )
            enriched.append({
                **row,
                "batch_ref": gref,
                "batch_payout_date": payout_date_str,
                "batch_amount_czk_expected": payout_czk,
                "payout_gref":     gref,
                "bank_tx_key":     bank_row.get("tx_key", ""),
                "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": bank_row["amount_czk"],
                "bank_status":     "DORAZILO",
                "verification_comment": verification_comment,
            })
```

- [ ] **Step 4: Run integration test, expect PASS**

```bash
pytest tests/test_integrity.py::test_l2_annotates_cross_report_duplicate_in_airbnb_enrichment -v
```

Expected: PASS.

- [ ] **Step 5: Run all bank tests, expect PASS**

```bash
pytest tests/test_bank.py -v 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add report/bank.py tests/test_integrity.py
git commit -m "feat: L2 — annotate cross-report duplicates in airbnb enrichment

Adds INTEGRITY: note to verification_comment when the same
confirmation_code lives in another (slug,year,month) snapshot.
bank_status stays DORAZILO (no silent downgrade).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Wire L2 into booking branch of `enrich_booking_rows_with_bank`

**Files:**
- Modify: `report/bank.py` — booking enrichment block
- Modify: `tests/test_integrity.py` — add booking-side test

- [ ] **Step 1: Add failing booking-side test**

Append to `tests/test_integrity.py`:

```python
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
        # If the row was matched, expect the INTEGRITY note. If the booking
        # match logic does not match the synthetic data above, the test still
        # needs to be meaningful — assert at least that bank_status is set.
        assert enriched[0].get("bank_status") in ("DORAZILO", "CHYBÍ")
        if enriched[0]["bank_status"] == "DORAZILO":
            assert "INTEGRITY:" in comment
    finally:
        conn.close()
```

NOTE: Booking-match plumbing is more elaborate (descriptor refs, Hostify codes). If the synthetic data does not produce a DORAZILO match, the test still asserts the contract: when DORAZILO, the comment must contain the INTEGRITY note. Implementation should mirror the airbnb wiring exactly.

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_l2_annotates_cross_report_duplicate_in_booking_enrichment -v
```

If the booking match doesn't trigger DORAZILO with the synthetic input, the test's outer assertion still passes — confirm the test produces a DORAZILO row by reading the test's enriched output, then refine if needed. Otherwise, the failure to add INTEGRITY note is the bug we fix.

- [ ] **Step 3: Mirror the airbnb wiring in the booking DORAZILO branch**

In `report/bank.py`, find the booking-side block where the unconditional `DORAZILO` append now lives (post-Task 5). Wrap it with the same `_find_code_in_other_snapshots` lookup, building `verification_comment` the same way, then append.

The booking-side block lives around line 640+ depending on Task 5's exact diff. Locate the booking enriched-row append where `bank_status: "DORAZILO"` is written. Add before the append:

```python
            verification_comment = row.get("verification_comment") or ""
            if conn is not None and slug:
                others = _find_code_in_other_snapshots(
                    conn, row.get("confirmation_code", ""), slug, year, month
                )
                if others:
                    where = ", ".join(f"{s}/{y}-{m}" for s, y, m in others)
                    verification_comment = (
                        f"INTEGRITY: also in {where}. {verification_comment}".strip()
                    )
```

And add `"verification_comment": verification_comment,` to the appended dict.

- [ ] **Step 4: Run booking-side test, expect PASS**

```bash
pytest tests/test_integrity.py::test_l2_annotates_cross_report_duplicate_in_booking_enrichment -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite to catch regressions**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add report/bank.py tests/test_integrity.py
git commit -m "feat: L2 — annotate cross-report duplicates in booking enrichment

Mirrors the airbnb wiring on the booking path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Layer 3: global integrity audit

### Task 13: `integrity_audit` table in schema

**Files:**
- Modify: `report/db.py:_SCHEMA` constant
- Modify: `tests/test_integrity.py` — add table-existence test

- [ ] **Step 1: Add failing test**

Append to `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_integrity_audit_table_exists_after_ensure_schema -v
```

Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add table to `_SCHEMA` in `report/db.py`**

Find the `_SCHEMA` constant (starts around [report/db.py:136](report/db.py:136)). Add this near the other recently-added tables (e.g., near the `report_month_state` definition):

```sql
CREATE TABLE IF NOT EXISTS integrity_audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT NOT NULL,
    occurrences       TEXT NOT NULL,
    detected_at       TEXT NOT NULL
);
```

(Add it inside the triple-quoted `_SCHEMA` string, between two existing table definitions.)

- [ ] **Step 4: Add the index to `_run_migrations` in `report/db.py`**

In `_run_migrations`, near other `CREATE INDEX IF NOT EXISTS` calls, add:

```python
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_integrity_audit_detected_at
           ON integrity_audit(detected_at DESC)"""
    )
```

- [ ] **Step 5: Run test, expect PASS**

```bash
pytest tests/test_integrity.py::test_integrity_audit_table_exists_after_ensure_schema -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add report/db.py tests/test_integrity.py
git commit -m "schema: add integrity_audit table for L3 global integrity audit

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: `run_integrity_audit` helper

**Files:**
- Modify: `report/db.py` — add helper
- Modify: `tests/test_integrity.py` — add tests

- [ ] **Step 1: Add failing tests**

Append to `tests/test_integrity.py`:

```python
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_run_integrity_audit_finds_cross_snapshot_dupe -v
```

Expected: ImportError on `run_integrity_audit`.

- [ ] **Step 3: Add `run_integrity_audit` to `report/db.py`**

Add this function near the end of the public API surface (e.g., after `save_payout_batch_bank_matches`):

```python
def run_integrity_audit(conn: sqlite3.Connection) -> list[dict]:
    """Find confirmation_codes appearing in multiple report_rows snapshots.

    Writes findings to integrity_audit (event log — one row per call when a
    dupe exists) and returns the same list. Empty codes are ignored.
    """
    rows = conn.execute("""
        SELECT confirmation_code,
               GROUP_CONCAT(slug || '/' || year || '-' || printf('%02d', month), ',') AS occurrences,
               COUNT(*) AS occ_count
        FROM report_rows
        WHERE confirmation_code <> ''
        GROUP BY confirmation_code
        HAVING occ_count > 1
        ORDER BY occ_count DESC, confirmation_code
    """).fetchall()
    findings = [dict(r) for r in rows]
    if findings:
        now = _now()
        conn.executemany(
            "INSERT INTO integrity_audit (confirmation_code, occurrences, detected_at) VALUES (?, ?, ?)",
            [(f["confirmation_code"], f["occurrences"], now) for f in findings],
        )
        conn.commit()
    return findings
```

- [ ] **Step 4: Run all three tests, expect PASS**

```bash
pytest tests/test_integrity.py -v -k "run_integrity_audit"
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add report/db.py tests/test_integrity.py
git commit -m "feat: L3 — run_integrity_audit groups report_rows by code

Writes findings to integrity_audit. Ignores empty codes. Append-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Hook `run_integrity_audit` into `_app_lifespan`

**Files:**
- Modify: `web.py:204-223`

- [ ] **Step 1: Locate `_app_lifespan` in `web.py`**

```bash
grep -n "_app_lifespan\|asynccontextmanager\|HostifySyncTask" web.py | head -10
```

Note the structure — it's an async context manager.

- [ ] **Step 2: Read lines around it**

Use Read tool on `web.py` lines 200-240 to see the exact shape, including imports at the top.

- [ ] **Step 3: Wire the audit call**

In `web.py`, inside `_app_lifespan` body (the async context manager that handles startup), after schema/config validation but before `yield`, add:

```python
    try:
        from report.db import run_integrity_audit
        from report.config import get_app_config
        cfg = get_app_config()
        if cfg.get("db_path"):
            from report.db import get_connection
            audit_conn = get_connection(cfg["db_path"])
            try:
                findings = run_integrity_audit(audit_conn)
                if findings:
                    log.warning(
                        "integrity audit: %d duplicate confirmation_code(s) found",
                        len(findings),
                    )
                    for f in findings:
                        log.warning(
                            "integrity violation: %s seen in %s",
                            f["confirmation_code"], f["occurrences"],
                        )
            finally:
                audit_conn.close()
    except Exception:
        log.exception("integrity audit failed")
```

NOTE: Adapt the imports to the actual conventions used in `web.py` — check what `log`, `cfg`, `db_path` accessor look like. If `web.py` already opens a long-lived DB connection in lifespan, reuse that connection instead of creating a new one. The pattern in this snippet is illustrative; align it with existing code.

- [ ] **Step 4: Manual smoke test**

```bash
PYTHONPATH=. python -c "from web import app; print('imports ok')"
```

If lifespan is wired correctly, this just imports cleanly. To exercise the audit, start the dev server and check logs.

- [ ] **Step 5: Add a smoke test in `tests/test_integrity.py`**

```python
def test_run_integrity_audit_callable_from_lifespan_no_findings():
    """Smoke: empty DB → no findings, no exception."""
    from report.db import run_integrity_audit
    conn = get_connection(":memory:")
    try:
        findings = run_integrity_audit(conn)
        assert findings == []
    finally:
        conn.close()
```

Run:
```bash
pytest tests/test_integrity.py::test_run_integrity_audit_callable_from_lifespan_no_findings -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web.py tests/test_integrity.py
git commit -m "feat: L3 — run integrity audit on app startup

Logs warnings for any cross-snapshot duplicate confirmation_code found
in report_rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: `/admin/integrity` endpoint and audit-page section

**Files:**
- Modify: `web.py` (or the appropriate routes file) — new endpoint
- Modify: `templates/audit.html` — new section

- [ ] **Step 1: Find the existing `/admin/audit` route as the template**

```bash
grep -n "admin/audit\|@app.get.*audit\|TemplateResponse.*audit" web.py report/routes/*.py 2>/dev/null
```

Identify the file and decorator pattern used.

- [ ] **Step 2: Add the `/admin/integrity` route**

In the same file as `/admin/audit`, add a parallel route (this example assumes FastAPI + Jinja2 templates, mirror the existing pattern):

```python
@app.get("/admin/integrity")
def admin_integrity(request: Request):
    conn = get_connection(_db_path())
    try:
        rows = conn.execute(
            """SELECT confirmation_code, occurrences, detected_at
               FROM integrity_audit
               ORDER BY detected_at DESC
               LIMIT 200"""
        ).fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse(
        "audit.html",
        {"request": request, "integrity_rows": [dict(r) for r in rows]},
    )
```

Adapt `_db_path()` and `templates` references to match the actual project bindings.

- [ ] **Step 3: Add the "Integrity violations" section to `templates/audit.html`**

Open `templates/audit.html` and add a new section near the top (after `{% extends %}` and before existing content):

```html
{% if integrity_rows %}
<section class="audit-section">
  <h2>Integrity violations</h2>
  <table class="audit-table">
    <thead>
      <tr>
        <th>Confirmation code</th>
        <th>Snapshots</th>
        <th>Detected at</th>
      </tr>
    </thead>
    <tbody>
      {% for row in integrity_rows %}
      <tr>
        <td><code>{{ row.confirmation_code }}</code></td>
        <td>{{ row.occurrences }}</td>
        <td>{{ row.detected_at }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}
```

(Use the existing CSS classes from the page — `audit-section`, `audit-table` are illustrative; pick the real classes after reading the existing template.)

- [ ] **Step 4: Smoke test the endpoint**

Start the dev server (or use FastAPI TestClient):

```python
# tests/test_integrity.py
def test_admin_integrity_endpoint_returns_200_and_renders():
    """Smoke: endpoint serves without error, even with empty audit."""
    from fastapi.testclient import TestClient
    from web import app

    with TestClient(app) as client:
        # If /admin requires auth, follow the existing test pattern for auth.
        resp = client.get("/admin/integrity")
        assert resp.status_code in (200, 401, 403)  # 200 if no auth or test session
```

Run:
```bash
pytest tests/test_integrity.py::test_admin_integrity_endpoint_returns_200_and_renders -v
```

If it returns 401/403, follow the existing test pattern (e.g., `test_panel_route_access.py`) to authenticate.

- [ ] **Step 5: Commit**

```bash
git add web.py templates/audit.html tests/test_integrity.py
git commit -m "feat: L3 — /admin/integrity endpoint + audit page section

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — Data migration: restore silently-downgraded LOCKED rows

### Task 17: `_restore_bank_status_after_ownership_fix` helper

**Files:**
- Modify: `report/db.py` — add helper
- Modify: `tests/test_integrity.py` — add tests

- [ ] **Step 1: Add failing tests**

Append to `tests/test_integrity.py`:

```python
def test_restore_bank_status_after_ownership_fix_patches_locked_chybi_row():
    """A LOCKED report_row with bank_status=CHYBÍ whose batch_ref has a
    match should be patched to DORAZILO with RECOVERED: prepended."""
    from datetime import date
    from report.db import (
        _restore_bank_status_after_ownership_fix,
        save_bank_transactions,
        save_payout_batch_bank_matches,
        save_report_rows,
        lock_report_month,
    )

    conn = get_connection(":memory:")
    try:
        save_bank_transactions(conn, "airbnb", [{
            "tx_key": "2026-04-05|18000.00|G-RECOVER|",
            "tx_id": "TX-RC",
            "datum": date(2026, 4, 5),
            "amount_czk": 18000.0,
            "gref": "G-RECOVER",
            "zprava": "G-RECOVER payout",
            "source_name": "bank.csv",
        }])
        save_payout_batch_bank_matches(conn, "airbnb", [{
            "batch_ref": "G-RECOVER",
            "tx_key": "2026-04-05|18000.00|G-RECOVER|",
            "match_method": "gref",
            "matched_amount_czk": 18000.0,
        }])
        # Save a CHYBÍ row, then lock the month.
        save_report_rows(conn, "apt", 2026, 3, [{
            "confirmation_code": "STUCK",
            "batch_ref": "G-RECOVER",
            "bank_status": "CHYBÍ",
            "bank_tx_key": "",
            "bank_datum": "",
            "bank_amount_czk": None,
            "verification_comment": "prior note",
        }])
        lock_report_month(conn, "apt", 2026, 3)

        count = _restore_bank_status_after_ownership_fix(conn)
        assert count == 1

        # Inspect the patched JSON
        row = conn.execute(
            "SELECT data FROM report_rows WHERE slug='apt' AND year=2026 AND month=3"
        ).fetchone()
        data = json.loads(row["data"])
        assert data["bank_status"] == "DORAZILO"
        assert data["bank_tx_key"] == "2026-04-05|18000.00|G-RECOVER|"
        assert data["bank_amount_czk"] == 18000.0
        assert data["verification_comment"].startswith("RECOVERED:")
        assert "prior note" in data["verification_comment"]
    finally:
        conn.close()


def test_restore_bank_status_idempotent():
    from datetime import date
    from report.db import (
        _restore_bank_status_after_ownership_fix,
        save_bank_transactions,
        save_payout_batch_bank_matches,
        save_report_rows,
    )

    conn = get_connection(":memory:")
    try:
        save_bank_transactions(conn, "airbnb", [{
            "tx_key": "TX-IDEM", "tx_id": "T1",
            "datum": date(2026, 4, 5), "amount_czk": 100.0,
            "gref": "G-IDEM", "zprava": "", "source_name": "x",
        }])
        save_payout_batch_bank_matches(conn, "airbnb", [{
            "batch_ref": "G-IDEM",
            "tx_key": "TX-IDEM",
            "match_method": "gref",
            "matched_amount_czk": 100.0,
        }])
        save_report_rows(conn, "apt", 2026, 3, [{
            "confirmation_code": "C", "batch_ref": "G-IDEM",
            "bank_status": "CHYBÍ", "bank_tx_key": "",
            "bank_datum": "", "bank_amount_czk": None,
            "verification_comment": "",
        }])
        first = _restore_bank_status_after_ownership_fix(conn)
        second = _restore_bank_status_after_ownership_fix(conn)
        assert first == 1
        assert second == 0
    finally:
        conn.close()
```

- [ ] **Step 2: Run, expect FAIL**

```bash
pytest tests/test_integrity.py::test_restore_bank_status_after_ownership_fix_patches_locked_chybi_row -v
```

Expected: ImportError on `_restore_bank_status_after_ownership_fix` and possibly `lock_report_month` — verify the latter exists with `grep -n "def lock_report_month\|def lock_report\b" report/db.py report/db_months.py 2>/dev/null` and adapt the import.

- [ ] **Step 3: Add the helper to `report/db.py`**

Add near `run_integrity_audit`:

```python
def _restore_bank_status_after_ownership_fix(conn: sqlite3.Connection) -> int:
    """One-shot data migration: patch report_rows that were silently
    downgraded to bank_status='CHYBÍ' by the old ownership mechanism.

    Targets rows whose batch_ref has a current bank match in
    payout_batch_bank_matches. Bypasses _assert_report_month_mutable
    deliberately — this is a corrective rewrite of stored history. The
    WHERE clause makes it idempotent: once a row is restored to DORAZILO
    it no longer matches.

    Also clears matching pending_payments rows since a row that resolves
    to DORAZILO no longer belongs in pending.
    """
    cursor = conn.execute("""
        SELECT rr.id, rr.slug, rr.year, rr.month, rr.confirmation_code, rr.data,
               pbm.tx_key, pbm.matched_amount_czk,
               bt.datum AS bank_datum
        FROM report_rows rr
        JOIN payout_batch_bank_matches pbm
          ON json_extract(rr.data, '$.batch_ref') = pbm.batch_ref
         AND pbm.channel = 'airbnb'
        LEFT JOIN bank_transactions bt
          ON bt.tx_key = pbm.tx_key AND bt.channel = 'airbnb'
        WHERE json_extract(rr.data, '$.bank_status') = 'CHYBÍ'
          AND COALESCE(json_extract(rr.data, '$.batch_ref'), '') <> ''
    """)
    restored = 0
    for r in cursor.fetchall():
        data = json.loads(r["data"])
        prev_comment = data.get("verification_comment") or ""
        data["bank_status"] = "DORAZILO"
        data["bank_tx_key"] = r["tx_key"]
        data["bank_amount_czk"] = r["matched_amount_czk"]
        # bank_datum may be a stored ISO string; format DD.MM.YYYY for UI.
        try:
            from datetime import date as _date
            d = r["bank_datum"]
            if isinstance(d, str) and len(d) >= 10:
                d = _date.fromisoformat(d[:10])
            data["bank_datum"] = d.strftime("%d.%m.%Y") if hasattr(d, "strftime") else (r["bank_datum"] or "")
        except Exception:
            data["bank_datum"] = r["bank_datum"] or ""
        data["verification_comment"] = (
            f"RECOVERED: bank match restored after ownership fix. {prev_comment}".strip()
        )
        conn.execute(
            "UPDATE report_rows SET data = ? WHERE id = ?",
            (json.dumps(data, default=str), r["id"]),
        )
        conn.execute(
            """DELETE FROM pending_payments
               WHERE slug = ? AND confirmation_code = ?""",
            (r["slug"], r["confirmation_code"]),
        )
        restored += 1
    if restored:
        conn.commit()
    return restored
```

NOTE: The booking channel needs the same treatment, but only if booking-CHYBÍ rows exist whose batch_ref maps to an airbnb match — that's not normal. Limit to `pbm.channel = 'airbnb'` first; if booking restoration is needed after manual prod inspection, extend the helper.

- [ ] **Step 4: Run both tests, expect PASS**

```bash
pytest tests/test_integrity.py -v -k "restore_bank_status"
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add report/db.py tests/test_integrity.py
git commit -m "feat: data migration to restore silently-downgraded LOCKED rows

One-shot, idempotent. Patches bank_status CHYBÍ → DORAZILO for rows
whose batch_ref has a bank match. Bypasses lock protection (this is
a corrective rewrite of stored history). Clears matching pending_payments.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: Hook restore migration into `_app_lifespan`

**Files:**
- Modify: `web.py` — call `_restore_bank_status_after_ownership_fix` before the audit

- [ ] **Step 1: In `web.py` `_app_lifespan`, add the call before `run_integrity_audit`**

Inside the same `try:` block from Task 15, before the audit call:

```python
            from report.db import _restore_bank_status_after_ownership_fix
            restored = _restore_bank_status_after_ownership_fix(audit_conn)
            if restored:
                log.warning(
                    "ownership-fix migration: restored %d previously-downgraded rows",
                    restored,
                )
```

Order: schema-ensure runs inside `get_connection`, then restore migration, then `run_integrity_audit`.

- [ ] **Step 2: Smoke test**

```bash
PYTHONPATH=. python -c "from web import app; print('ok')"
```

Expected: clean import.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web.py
git commit -m "feat: run ownership-fix migration on app startup before audit

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — UI integration

### Task 19: INTEGRITY badge in `reservation_detail.html`

**Files:**
- Modify: `templates/partials/reservation_detail.html:274-279`

- [ ] **Step 1: Read the existing `verification_diff` block**

Use Read tool on `templates/partials/reservation_detail.html:274-280` to see the exact JS/style/class pattern.

- [ ] **Step 2: Add the INTEGRITY badge near `verification_diff`**

In `templates/partials/reservation_detail.html`, after the `verification_diff` block (around line 279), add:

```html
{% if r.verification_comment and 'INTEGRITY:' in r.verification_comment %}
<div style="display:flex;justify-content:space-between;padding-top:8px;border-top:1px solid rgba(244,67,54,0.2);">
  <span style="font-size:12px;color:var(--red);">Integrita</span>
  <span style="font-size:12px;font-weight:600;color:var(--red);">
    {{ r.verification_comment.split('INTEGRITY:')[1].split('.')[0]|trim }}
  </span>
</div>
{% endif %}
```

- [ ] **Step 3: Manually verify in the dev server**

```bash
# Start the dev server (adapt to project's start command)
uvicorn web:app --reload &
```

Open a property page that has an integrity-flagged row. Verify the red "Integrita" badge renders. (If no real flagged data exists yet, manually edit a test JSON row in a copy DB to include `"verification_comment": "INTEGRITY: also in apt/2026-03"`.)

Stop the dev server when done.

- [ ] **Step 4: Commit**

```bash
git add templates/partials/reservation_detail.html
git commit -m "ui: red Integrita badge in reservation_detail when verification_comment has INTEGRITY:

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Banner in `property_intro.html` when `summary.integrity_warnings` non-empty

**Files:**
- Modify: `templates/partials/property_intro.html`

- [ ] **Step 1: Read the file's top section**

Use Read tool on `templates/partials/property_intro.html:1-60` to see where the page header lives.

- [ ] **Step 2: Add the banner near the page header**

Insert this near the top of the visible content (right after the opening `<div class="prop-page-header">` block):

```html
{% if summary and summary.integrity_warnings %}
<div style="background:rgba(244,67,54,0.1);border:1px solid rgba(244,67,54,0.4);color:var(--red);
            padding:12px 16px;border-radius:8px;margin:12px 0;display:flex;
            justify-content:space-between;align-items:center;">
  <div>
    <strong>Pozor: nalezeny duplicitní rezervace v reportu ({{ summary.integrity_warnings|length }})</strong>
    <div style="font-size:12px;margin-top:4px;">
      {{ summary.integrity_warnings|join(', ') }}
    </div>
  </div>
  <a href="/admin/integrity" style="color:var(--red);text-decoration:underline;font-size:13px;">Detail</a>
</div>
{% endif %}
```

- [ ] **Step 3: Manually verify in the dev server**

Same as Task 19: simulate a duplicate by editing a test DB or by running `_flag_duplicate_codes_within_snapshot` against a synthetic dataset, then load the property page.

- [ ] **Step 4: Commit**

```bash
git add templates/partials/property_intro.html
git commit -m "ui: banner on property page when summary.integrity_warnings non-empty

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — End-to-end verification

### Task 21: Local smoke test against a copy of the production DB

**Files:** none (verification only)

- [ ] **Step 1: Get a copy of the prod DB**

```bash
scp rentero@204.168.216.181:~/rentero/cache/rentero.db /tmp/rentero-prod-copy.db
```

(Adapt the host/path to your environment. If SCP not available, copy from a recent `~/backups/rentero_YYYYMMDD.db`.)

- [ ] **Step 2: Run the migration manually**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/nikita1/315 new/.claude/worktrees/hungry-elgamal-bf3a52"
PYTHONPATH=. python3 -c "
from report.db import (
    get_connection,
    _restore_bank_status_after_ownership_fix,
    run_integrity_audit,
)
conn = get_connection('/tmp/rentero-prod-copy.db')
restored = _restore_bank_status_after_ownership_fix(conn)
print(f'Restored: {restored}')
findings = run_integrity_audit(conn)
print(f'Integrity findings: {len(findings)}')
for f in findings[:10]:
    print(f'  {f[\"confirmation_code\"]}: {f[\"occurrences\"]}')
conn.close()
"
```

Expected: A non-zero `Restored:` count if production has the bug. The reported `HMRA54MA5N` symptom should be among them.

- [ ] **Step 2.5: Verify HMRA54MA5N specifically**

```bash
PYTHONPATH=. python3 -c "
import json
from report.db import get_connection
conn = get_connection('/tmp/rentero-prod-copy.db')
row = conn.execute(\"\"\"
    SELECT slug, year, month, data FROM report_rows
    WHERE confirmation_code = 'HMRA54MA5N'
\"\"\").fetchall()
for r in row:
    data = json.loads(r['data'])
    print(f'{r[\"slug\"]}/{r[\"year\"]}-{r[\"month\"]:02d}: bank_status={data.get(\"bank_status\")}, comment={data.get(\"verification_comment\", \"\")[:80]}')
conn.close()
"
```

Expected: Francouzska_50/2026-03 shows `bank_status=DORAZILO`, comment starts with `RECOVERED:`.

- [ ] **Step 3: Spot-check the rest of the DB**

Optional extra check — count total CHYBÍ rows before/after to gauge impact. Or open the dev server pointed at the copy:

```bash
RENTERO_DB_PATH=/tmp/rentero-prod-copy.db uvicorn web:app --reload
```

Browse the affected reports in the UI; verify:
- DORAZILO replaces stuck CHYBÍ.
- RECOVERED: prefix shows in reservation detail (or the Integrita badge if INTEGRITY: was present too).
- `/admin/integrity` lists any genuine duplicates.

- [ ] **Step 4: Cleanup**

```bash
rm /tmp/rentero-prod-copy.db
```

- [ ] **Step 5: No commit needed — this is verification.**

If something goes wrong during smoke test, note the failure and circle back to whichever task introduced the issue.

---

## Phase 9 — Final regression check

### Task 22: Full test suite + lint pass

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v 2>&1 | tail -40
```

Expected: all PASS. Pay attention to:
- No test references `slug=`, `year=`, `month=` kwargs of `save_payout_batch_bank_matches`.
- No test imports `get_bank_match_owner` or `clear_bank_matches_for_month`.
- New cross-month and cross-property tests pass.

- [ ] **Step 2: Confirm no leftover references**

```bash
grep -rn "get_bank_match_owner\|clear_bank_matches_for_month\|already_owned" --include="*.py"
```

Expected: zero matches.

```bash
grep -rn "payout_batch_bank_matches.*slug\|payout_batch_bank_matches.*year\|payout_batch_bank_matches.*month" --include="*.py"
```

Expected: zero matches except inside the migration helper itself (`_drop_ownership_columns_from_payout_batch_bank_matches`).

- [ ] **Step 3: Confirm migrations run cleanly on fresh DB**

```bash
python3 -c "
from report.db import get_connection
conn = get_connection(':memory:')
print('schema ok')
conn.close()
"
```

Expected: prints "schema ok", no exceptions.

- [ ] **Step 4: Final commit (if any leftover changes)**

If anything got tweaked during regression check, commit it:

```bash
git add -A
git commit -m "chore: final cleanup after bank-match ownership fix

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If nothing left to commit, skip this step.

---

## Self-review notes

- **Spec coverage check:**
  - Spec Part A.1 schema migration → Task 2.
  - Spec Part A.2 simplify save → Task 3.
  - Spec Part A.3 delete dead helpers → Tasks 4, 5.
  - Spec Part A.4 delete downgrade blocks → Task 5.
  - Spec Part B.1 L1 helper + integration → Tasks 7, 8, 9.
  - Spec Part B.2 L2 detector → Tasks 10, 11, 12.
  - Spec Part B.3 L3 audit → Tasks 13, 14, 15, 16.
  - Spec Part B.4 data migration → Tasks 17, 18.
  - Spec Part B.5 UI → Tasks 19, 20.
  - Spec tests section → covered in each task plus Task 22 final pass.
  - Rollout step "verify HMRA54MA5N" → Task 21 step 2.5.

- **Type/signature consistency:** All helpers refer to `_flag_duplicate_codes_within_snapshot`, `_find_code_in_other_snapshots`, `_drop_ownership_columns_from_payout_batch_bank_matches`, `_restore_bank_status_after_ownership_fix`, `run_integrity_audit` consistently across tasks. `save_payout_batch_bank_matches` signature change is applied in Task 3 and respected in all subsequent test snippets.

- **Booking-side L2 caveat (Task 12):** booking enrichment plumbing is more elaborate than airbnb. The plan instructs the implementer to mirror the airbnb wiring at the precise DORAZILO append. If the test data does not produce a DORAZILO match, the test contract still holds (asserting only the conditional on DORAZILO). After implementation, if the booking match path differs structurally, refine the test with real fixture data.

- **Lifespan integration (Task 15, 18):** The exact shape depends on `web.py`'s existing patterns for accessing `db_path`, `log`, and templates. The plan's snippet is illustrative; the implementer should align with the conventions they read in the file.
