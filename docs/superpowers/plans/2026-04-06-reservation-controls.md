# Reservation Controls + Payout Adjustment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three reservation management capabilities: (1) manually move a reservation to a different month, (2) soft-exclude a reservation from financial calculations, (3) automatically detect and generate "Doplatek" rows when a payout batch references a reservation from a locked/past month.

**Architecture:** A new `report/db_controls.py` module owns the two new DB tables (`reservation_month_assignments`, `reservation_exclusions`) and their CRUD functions. The generation pipeline in `report/main.py` applies moves and exclusions immediately after `filter_for_property_month()`, then detects payout adjustments before verification. `report/calculator.py` handles the new `is_payout_adjustment` flag identically to `is_cancelled` (no cleaning/city tax/balíčky). Web routes in `property_routes.py` expose four POST endpoints for move/exclude actions. UI changes are isolated to `property_reservations.html` (table row styles) and `reservation_detail.html` (Akce section in panel).

**Tech Stack:** Python/FastAPI, SQLite, Jinja2, vanilla JS (existing pattern).

**Depends on:** Floating Panel plan (`2026-04-06-floating-panel.md`) must be implemented first — the Akce section in Task 7 renders inside the panel.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `report/db_controls.py` | **Create** | DB functions for move/exclude; 2 new table schemas |
| `report/db.py` | Modify | Import + expose `db_controls` functions; add tables to `_SCHEMA` |
| `report/calculator.py` | Modify | Handle `is_payout_adjustment` flag (zero out cleaning/city tax/balíčky) |
| `report/main.py` | Modify | Apply moves, exclusions, and adjustment detection in pipeline step 4 |
| `report/summary.py` | Modify | Skip `is_excluded` rows in all sums |
| `report/web_support.py` | Modify | Skip `is_excluded` rows in `_compute_row_breakdown`; expose new DB functions in state |
| `report/routes/property_routes.py` | Modify | Add 4 POST routes: move, move-revert, exclude, reinstate |
| `templates/partials/property_reservations.html` | Modify | Excluded row style; adjustment Doplatek sub-label in Kanál column |
| `templates/partials/base_styles.html` | Modify | Add `.row-excluded`, `.adjustment-sublabel` CSS |
| `templates/partials/reservation_detail.html` | Modify | Add Akce section (move + exclude forms) |
| `tests/test_controls.py` | **Create** | Unit tests for `db_controls.py` and calculator flag |

---

## Task 1: `report/db_controls.py` + schema

**Files:**
- Create: `report/db_controls.py`
- Modify: `report/db.py`
- Create: `tests/test_controls.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/test_controls.py
import sqlite3
import pytest
from datetime import datetime, timezone

from report.db import _SCHEMA


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


# ── Month assignments ──────────────────────────────────────────────────────

def test_create_and_get_month_assignment(conn):
    from report.db_controls import create_reservation_month_assignment, get_reservation_month_assignments
    create_reservation_month_assignment(conn, {
        "slug": "obj1",
        "confirmation_code": "HMA001",
        "target_year": 2026,
        "target_month": 4,
        "original_year": 2026,
        "original_month": 3,
        "reason": "Guest requested change",
        "actor": "admin",
    })
    assignments = get_reservation_month_assignments(conn, "obj1")
    assert "HMA001" in assignments
    a = assignments["HMA001"]
    assert a["target_year"] == 2026
    assert a["target_month"] == 4
    assert a["original_year"] == 2026
    assert a["original_month"] == 3


def test_revert_month_assignment(conn):
    from report.db_controls import (
        create_reservation_month_assignment,
        revert_reservation_month_assignment,
        get_reservation_month_assignments,
    )
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": "HMA002",
        "target_year": 2026, "target_month": 4,
        "original_year": 2026, "original_month": 3,
        "reason": "test", "actor": "admin",
    })
    revert_reservation_month_assignment(conn, "obj1", "HMA002", actor="admin")
    assignments = get_reservation_month_assignments(conn, "obj1")
    # Reverted assignments are excluded from active set
    assert "HMA002" not in assignments


def test_get_codes_assigned_to_month(conn):
    from report.db_controls import create_reservation_month_assignment, get_codes_assigned_to_month
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": "HMA003",
        "target_year": 2026, "target_month": 4,
        "original_year": 2026, "original_month": 3,
        "reason": "test", "actor": "admin",
    })
    codes = get_codes_assigned_to_month(conn, "obj1", 2026, 4)
    assert "HMA003" in codes
    codes_other = get_codes_assigned_to_month(conn, "obj1", 2026, 3)
    assert "HMA003" not in codes_other


# ── Exclusions ─────────────────────────────────────────────────────────────

def test_create_and_get_exclusion(conn):
    from report.db_controls import create_reservation_exclusion, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1",
        "confirmation_code": "HMA010",
        "reason": "duplicate",
        "actor": "admin",
    })
    exclusions = get_active_exclusions(conn, "obj1")
    assert "HMA010" in exclusions


def test_reinstate_removes_from_active_exclusions(conn):
    from report.db_controls import create_reservation_exclusion, reinstate_reservation, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1", "confirmation_code": "HMA011",
        "reason": "test", "actor": "admin",
    })
    reinstate_reservation(conn, "obj1", "HMA011", actor="admin")
    exclusions = get_active_exclusions(conn, "obj1")
    assert "HMA011" not in exclusions


def test_exclusion_is_slug_scoped(conn):
    from report.db_controls import create_reservation_exclusion, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1", "confirmation_code": "HMA020",
        "reason": "test", "actor": "admin",
    })
    exclusions_obj2 = get_active_exclusions(conn, "obj2")
    assert "HMA020" not in exclusions_obj2
```

- [ ] **Step 1.2: Run — expect FAIL (ImportError)**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python -m pytest tests/test_controls.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'create_reservation_month_assignment' from 'report.db_controls'` (module doesn't exist yet).

- [ ] **Step 1.3: Add tables to `report/db.py` `_SCHEMA`**

In `report/db.py`, find the end of `_SCHEMA` (the multi-line string ending with the last `CREATE TABLE` before the closing `"""`). Add these two tables right before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS reservation_month_assignments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    target_year       INTEGER NOT NULL,
    target_month      INTEGER NOT NULL,
    original_year     INTEGER NOT NULL,
    original_month    INTEGER NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    actor             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    reverted_at       TEXT,
    reverted_by       TEXT,
    UNIQUE(slug, confirmation_code)
);

CREATE TABLE IF NOT EXISTS reservation_exclusions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    actor             TEXT NOT NULL DEFAULT '',
    excluded_at       TEXT NOT NULL,
    reinstated_at     TEXT,
    reinstated_by     TEXT,
    UNIQUE(slug, confirmation_code)
);
```

- [ ] **Step 1.4: Create `report/db_controls.py`**

```python
"""
report/db_controls.py — DB functions for reservation move and exclude controls.

Two tables managed here:
  reservation_month_assignments — manual month reassignments (MOVE action)
  reservation_exclusions        — soft-exclusions from financial calculation
"""

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Month assignments ──────────────────────────────────────────────────────

def create_reservation_month_assignment(
    conn: sqlite3.Connection,
    data: dict,
) -> None:
    """
    Create or replace a month assignment for a reservation.
    Only one active assignment per (slug, confirmation_code).
    """
    conn.execute(
        """INSERT INTO reservation_month_assignments
               (slug, confirmation_code, target_year, target_month,
                original_year, original_month, reason, actor, created_at)
           VALUES (:slug, :confirmation_code, :target_year, :target_month,
                   :original_year, :original_month, :reason, :actor, :created_at)
           ON CONFLICT(slug, confirmation_code) DO UPDATE SET
               target_year    = excluded.target_year,
               target_month   = excluded.target_month,
               original_year  = excluded.original_year,
               original_month = excluded.original_month,
               reason         = excluded.reason,
               actor          = excluded.actor,
               created_at     = excluded.created_at,
               reverted_at    = NULL,
               reverted_by    = NULL""",
        {
            "slug": data["slug"],
            "confirmation_code": data["confirmation_code"],
            "target_year": int(data["target_year"]),
            "target_month": int(data["target_month"]),
            "original_year": int(data["original_year"]),
            "original_month": int(data["original_month"]),
            "reason": str(data.get("reason") or "").strip(),
            "actor": str(data.get("actor") or "").strip(),
            "created_at": _now(),
        },
    )
    conn.commit()


def revert_reservation_month_assignment(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    actor: str = "",
) -> None:
    """Mark an assignment as reverted (soft-delete)."""
    conn.execute(
        """UPDATE reservation_month_assignments
              SET reverted_at = ?, reverted_by = ?
            WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL""",
        (_now(), actor, slug, confirmation_code),
    )
    conn.commit()


def get_reservation_month_assignments(
    conn: sqlite3.Connection,
    slug: str,
) -> dict[str, dict]:
    """
    Return all active (non-reverted) month assignments for a property.
    Returns {confirmation_code: {target_year, target_month, original_year, original_month, reason}}.
    """
    rows = conn.execute(
        """SELECT confirmation_code, target_year, target_month,
                  original_year, original_month, reason, actor, created_at
             FROM reservation_month_assignments
            WHERE slug = ? AND reverted_at IS NULL""",
        (slug,),
    ).fetchall()
    return {
        row["confirmation_code"]: dict(row)
        for row in rows
    }


def get_codes_assigned_to_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> set[str]:
    """
    Return confirmation_codes that are actively assigned INTO (target) this month.
    Used by the pipeline to pull in reservations moved from other months.
    """
    rows = conn.execute(
        """SELECT confirmation_code
             FROM reservation_month_assignments
            WHERE slug = ? AND target_year = ? AND target_month = ?
              AND reverted_at IS NULL""",
        (slug, year, month),
    ).fetchall()
    return {row["confirmation_code"] for row in rows}


def get_assignment_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
) -> dict | None:
    """Return the active assignment for a single code, or None."""
    row = conn.execute(
        """SELECT * FROM reservation_month_assignments
            WHERE slug = ? AND confirmation_code = ? AND reverted_at IS NULL""",
        (slug, confirmation_code),
    ).fetchone()
    return dict(row) if row else None


# ── Exclusions ─────────────────────────────────────────────────────────────

def create_reservation_exclusion(
    conn: sqlite3.Connection,
    data: dict,
) -> None:
    """
    Exclude a reservation from financial calculations.
    Idempotent: re-excluding an already-excluded reservation resets reinstated_at.
    """
    conn.execute(
        """INSERT INTO reservation_exclusions
               (slug, confirmation_code, reason, actor, excluded_at)
           VALUES (:slug, :confirmation_code, :reason, :actor, :excluded_at)
           ON CONFLICT(slug, confirmation_code) DO UPDATE SET
               reason        = excluded.reason,
               actor         = excluded.actor,
               excluded_at   = excluded.excluded_at,
               reinstated_at = NULL,
               reinstated_by = NULL""",
        {
            "slug": data["slug"],
            "confirmation_code": data["confirmation_code"],
            "reason": str(data.get("reason") or "").strip(),
            "actor": str(data.get("actor") or "").strip(),
            "excluded_at": _now(),
        },
    )
    conn.commit()


def reinstate_reservation(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    *,
    actor: str = "",
) -> None:
    """Re-include a previously excluded reservation."""
    conn.execute(
        """UPDATE reservation_exclusions
              SET reinstated_at = ?, reinstated_by = ?
            WHERE slug = ? AND confirmation_code = ? AND reinstated_at IS NULL""",
        (_now(), actor, slug, confirmation_code),
    )
    conn.commit()


def get_active_exclusions(
    conn: sqlite3.Connection,
    slug: str,
) -> set[str]:
    """Return confirmation_codes that are currently excluded (not reinstated)."""
    rows = conn.execute(
        """SELECT confirmation_code FROM reservation_exclusions
            WHERE slug = ? AND reinstated_at IS NULL""",
        (slug,),
    ).fetchall()
    return {row["confirmation_code"] for row in rows}


def get_exclusion_for_code(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
) -> dict | None:
    """Return the active exclusion record for a single code, or None."""
    row = conn.execute(
        """SELECT * FROM reservation_exclusions
            WHERE slug = ? AND confirmation_code = ? AND reinstated_at IS NULL""",
        (slug, confirmation_code),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 1.5: Run tests — expect PASS**

```bash
python -m pytest tests/test_controls.py -v
```

Expected: 7 PASSED

- [ ] **Step 1.6: Export functions from `report/db.py`**

In `report/db.py`, add at the end of the imports section (after all existing imports):

```python
from report.db_controls import (
    create_reservation_month_assignment,
    revert_reservation_month_assignment,
    get_reservation_month_assignments,
    get_codes_assigned_to_month,
    get_assignment_for_code,
    create_reservation_exclusion,
    reinstate_reservation,
    get_active_exclusions,
    get_exclusion_for_code,
)
```

- [ ] **Step 1.7: Commit**

```bash
git add report/db_controls.py report/db.py tests/test_controls.py
git commit -m "feat: add db_controls.py — reservation_month_assignments and reservation_exclusions tables"
```

---

## Task 2: Calculator — `is_payout_adjustment` flag

**Files:**
- Modify: `report/calculator.py`
- Modify: `tests/test_calculator.py`

- [ ] **Step 2.1: Write failing test**

Add to `tests/test_calculator.py` (find the existing test file and append):

```python
def test_payout_adjustment_has_zero_cleaning_citytax_balicky():
    """Adjustment rows must not double-count cleaning, city tax, or balíčky."""
    from report.calculator import calculate_row
    reservation = {
        "confirmation_code": "HMA_ADJ_001",
        "guest_name": "Jan Novák",
        "check_in": "2026-01-10",
        "check_out": "2026-01-13",
        "nights": 3,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "source": "airbnb",
        "cleaning_fee_eur": 50.0,
        "channel_commission_eur": 10.0,
        "payout_price_eur": 300.0,
        "effective_payout_eur": 300.0,
        "airbnb_batch_rate": 25.0,
        "is_payout_adjustment": True,
        "status": "adjustment",
    }
    cnb_rate = {"rate": 25.0, "valid_for": "2026-01-10"}
    prop = {"city_tax_rate": 50, "balicky_per_person": 0, "vat_rate": 0.21, "rentero_commission": 0.15}
    row = calculate_row(reservation, cnb_rate, prop, order=1)

    assert row["uklid_czk"] == 0.0, "adjustment must not include cleaning fee"
    assert row["city_tax_czk"] == 0.0, "adjustment must not include city tax"
    assert row["balicky_czk"] == 0.0, "adjustment must not include balíčky"
    assert row["dph_uklid_balicky_czk"] == 0.0
    assert row["payout_czk"] > 0, "payout must be present"
    assert row.get("is_payout_adjustment") is True
```

- [ ] **Step 2.2: Run — expect FAIL**

```bash
python -m pytest tests/test_calculator.py::test_payout_adjustment_has_zero_cleaning_citytax_balicky -v
```

Expected: FAIL — either `AssertionError` (uklid_czk != 0) or KeyError.

- [ ] **Step 2.3: Add `is_payout_adjustment` handling in `calculator.py`**

In `report/calculator.py`, find line ~120:

```python
    is_cancelled = bool(reservation.get("is_cancelled"))
```

Add directly after:

```python
    is_payout_adjustment = bool(reservation.get("is_payout_adjustment"))
```

Then find lines ~123–130 (the core calculation block):

```python
    city_tax = 0.0 if is_cancelled else (city_tax_rate * nights * city_tax_paying_guests)
    ...
    uklid_czk = 0.0 if is_cancelled else (cleaning_eur * kurz)
    balicky = 0.0 if is_cancelled else (balicky_per_person * (occupancy_adults + occupancy_children_infants))
```

Replace those three lines with:

```python
    _no_fees = is_cancelled or is_payout_adjustment
    city_tax = 0.0 if _no_fees else (city_tax_rate * nights * city_tax_paying_guests)
    provize_czk = commission_eur * kurz
    dph_provize = provize_czk * vat_rate
    payout_czk = float(czk_booked) if ("booking" in source and czk_booked) else (payout_eur * kurz)
    uklid_czk = 0.0 if _no_fees else (cleaning_eur * kurz)
    balicky = 0.0 if _no_fees else (balicky_per_person * (occupancy_adults + occupancy_children_infants))
```

Also in the `return` dict at line ~133, add after `"confirmation_code"`:

```python
        "is_payout_adjustment": is_payout_adjustment,
        "is_excluded": bool(reservation.get("is_excluded")),
        "adjustment_original_year": reservation.get("adjustment_original_year"),
        "adjustment_original_month": reservation.get("adjustment_original_month"),
```

Do the same in `_null_row()` — find the return dict and add:

```python
        "is_payout_adjustment": bool(reservation.get("is_payout_adjustment")),
        "is_excluded": bool(reservation.get("is_excluded")),
        "adjustment_original_year": reservation.get("adjustment_original_year"),
        "adjustment_original_month": reservation.get("adjustment_original_month"),
```

- [ ] **Step 2.4: Run tests — expect PASS**

```bash
python -m pytest tests/test_calculator.py -v
```

Expected: all PASS including the new adjustment test.

- [ ] **Step 2.5: Commit**

```bash
git add report/calculator.py tests/test_calculator.py
git commit -m "feat: calculator handles is_payout_adjustment — zero cleaning/city tax/balíčky"
```

---

## Task 3: Summary and breakdown skip excluded rows

**Files:**
- Modify: `report/summary.py`
- Modify: `report/web_support.py`

- [ ] **Step 3.1: Write failing test**

Add to `tests/test_controls.py`:

```python
def test_summary_skips_excluded_rows():
    from report.summary import build_report_summary
    rows = [
        {"payout_czk": 5000.0, "cena_ubytovani_czk": 4000.0, "priprava_pokoje_czk": 500.0,
         "dph_uklid_balicky_czk": 100.0, "bank_status": "DORAZILO", "is_excluded": False},
        {"payout_czk": 3000.0, "cena_ubytovani_czk": 2500.0, "priprava_pokoje_czk": 300.0,
         "dph_uklid_balicky_czk": 60.0, "bank_status": "DORAZILO", "is_excluded": True},
    ]
    prop = {"rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["gross_payout_czk"] == 5000.0, "excluded row must not be counted"
    assert summary["accommodation_income_czk"] == 4000.0


def test_breakdown_skips_excluded_rows():
    from report.web_support import _compute_row_breakdown
    rows = [
        {"source": "airbnb", "payout_czk": 5000.0, "cena_ubytovani_czk": 4000.0,
         "provize_czk": 0, "dph_provize_czk": 0, "city_tax_czk": 0, "uklid_czk": 0,
         "balicky_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"source": "airbnb", "payout_czk": 3000.0, "cena_ubytovani_czk": 2500.0,
         "provize_czk": 0, "dph_provize_czk": 0, "city_tax_czk": 0, "uklid_czk": 0,
         "balicky_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": True},
    ]
    breakdown = _compute_row_breakdown(rows)
    assert breakdown["airbnb"]["payout_czk"] == 5000.0
    assert breakdown["airbnb"]["count"] == 1
```

- [ ] **Step 3.2: Run — expect FAIL**

```bash
python -m pytest tests/test_controls.py::test_summary_skips_excluded_rows tests/test_controls.py::test_breakdown_skips_excluded_rows -v
```

Expected: FAIL — excluded rows are currently included in sums.

- [ ] **Step 3.3: Fix `report/summary.py`**

In `build_report_summary`, add one line right after `expenses = expenses or []`:

```python
    rows = [r for r in rows if not r.get("is_excluded")]
```

- [ ] **Step 3.4: Fix `report/web_support.py` `_compute_row_breakdown`**

In `_compute_row_breakdown`, change the opening loop:

```python
def _compute_row_breakdown(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {"airbnb": [], "booking": [], "other": []}
    for row in rows:
        if row.get("is_excluded"):    # ← add this guard
            continue
        src = (row.get("source") or "").lower()
```

Also in the total sums at line `"total": _sums(rows)`, change to:

```python
        "total": _sums([r for r in rows if not r.get("is_excluded")]),
```

- [ ] **Step 3.5: Run tests — expect PASS**

```bash
python -m pytest tests/test_controls.py -v
```

Expected: all PASS.

- [ ] **Step 3.6: Commit**

```bash
git add report/summary.py report/web_support.py tests/test_controls.py
git commit -m "feat: summary and breakdown skip is_excluded rows"
```

---

## Task 4: Pipeline — apply moves and exclusions in `main.py`

**Files:**
- Modify: `report/main.py`

- [ ] **Step 4.1: Add imports to `main.py`**

In `report/main.py`, find the existing `from report.db import (` block and add to it:

```python
    get_reservation_month_assignments,
    get_codes_assigned_to_month,
    get_active_exclusions,
```

- [ ] **Step 4.2: Apply month assignments after `filter_for_property_month`**

In `main.py`, find step 4a (after `reservations = filter_for_property_month(...)` and before the `log.info("  %d reservations found"` line). Insert:

```python
            # ── Apply manual month assignments ─────────────────────────────
            if persist_db:
                assignments = get_reservation_month_assignments(db_conn, slug)
                if assignments:
                    # Remove reservations moved AWAY from this month
                    reservations = [
                        r for r in reservations
                        if r["confirmation_code"] not in assignments
                        or (
                            assignments[r["confirmation_code"]]["target_year"] == year
                            and assignments[r["confirmation_code"]]["target_month"] == month
                        )
                    ]
                    # Pull in reservations moved INTO this month from other months
                    codes_moved_in = get_codes_assigned_to_month(db_conn, slug, year, month)
                    current_codes = {r["confirmation_code"] for r in reservations}
                    for code in codes_moved_in - current_codes:
                        # Find the raw reservation in all_raw and re-normalize for this month
                        matched = [
                            r for r in all_raw
                            if str(r.get("channel_reservation_id") or r.get("confirmation_code") or "") == code
                        ]
                        if matched:
                            from report.loader import _normalize_reservation
                            normalized = _normalize_reservation(matched[0])
                            if normalized:
                                # Override assigned month to target month
                                normalized["assigned_year"] = year
                                normalized["assigned_month"] = month
                                reservations.append(normalized)
                                log.info("  Pulled in moved reservation %s from month assignment", code)
```

- [ ] **Step 4.3: Apply exclusions before verification**

In `main.py`, after the assignments block above (and still before 4b verification), insert:

```python
            # ── Apply exclusions ───────────────────────────────────────────
            if persist_db:
                excluded_codes = get_active_exclusions(db_conn, slug)
                if excluded_codes:
                    for r in reservations:
                        if r["confirmation_code"] in excluded_codes:
                            r["is_excluded"] = True
                    log.info(
                        "  %d reservation(s) marked as excluded",
                        sum(1 for r in reservations if r.get("is_excluded")),
                    )
```

- [ ] **Step 4.4: Verify pipeline still runs**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python -m report.main --year 2026 --month 3 --dry-run 2>&1 | head -30
```

Expected: runs without error, shows existing reservations as before.

- [ ] **Step 4.5: Commit**

```bash
git add report/main.py
git commit -m "feat: pipeline applies month assignment overrides and exclusions"
```

---

## Task 5: Pipeline — payout adjustment detection

**Files:**
- Modify: `report/main.py`
- Modify: `report/db.py` (ensure `get_report_row_by_code` is available — done in floating-panel plan Task 1)

- [ ] **Step 5.1: Add `_build_adjustment_reservation` helper near top of `main.py`**

After the last import but before `def _auto_find_csvs`, add:

```python
def _build_adjustment_reservation(past_row: dict, batch_info: dict) -> dict:
    """
    Build a synthetic HostifyReservation-compatible dict for a payout adjustment.
    Represents money arriving this month for a reservation from a past month.
    Cleaning, city tax, and balíčky are zeroed out to avoid double-counting.
    """
    source = past_row.get("source", "")
    payout_eur = float(
        batch_info.get("payout_eur")
        or batch_info.get("payout_czk", 0) / max(float(batch_info.get("airbnb_rate") or 25.0), 1.0)
        or 0.0
    )
    return {
        "confirmation_code": past_row.get("confirmation_code", ""),
        "guest_name": past_row.get("guest_name", ""),
        "check_in": past_row.get("check_in", ""),
        "check_out": past_row.get("check_out", ""),
        "nights": past_row.get("nights") or 0,
        "adults": past_row.get("adults") or 0,
        "children": 0,
        "infants": 0,
        "source": source,
        "status": "adjustment",
        "is_cancelled": False,
        "is_payout_adjustment": True,
        "adjustment_original_year": past_row.get("year"),
        "adjustment_original_month": past_row.get("month"),
        "listing_nickname": past_row.get("listing_nickname", ""),
        "listing_id": past_row.get("listing_id"),
        "confirmed_at": past_row.get("check_in", ""),
        # Zeroed — no double-counting:
        "cleaning_fee_eur": 0.0,
        "city_tax_eur": 0.0,
        "channel_commission_eur": float(batch_info.get("commission_eur") or 0.0),
        "payout_price_eur": payout_eur,
        "effective_payout_eur": payout_eur,
        # Batch info for rate calculation:
        "airbnb_batch_rate": float(batch_info.get("airbnb_rate") or 0.0),
        "airbnb_payout_date": batch_info.get("payout_date", ""),
        "batch_ref": batch_info.get("gref") or batch_info.get("batch_ref", ""),
        "batch_payout_date": batch_info.get("payout_date", ""),
        "batch_amount_czk": batch_info.get("payout_czk"),
        "czk_booked": batch_info.get("payout_czk") if "booking" in source.lower() else None,
    }
```

- [ ] **Step 5.2: Add `get_report_row_by_code` to `main.py` imports**

In `report/main.py`, find the `from report.db import (` block and add:

```python
    get_report_row_by_code,
```

- [ ] **Step 5.3: Add adjustment detection in the per-property pipeline loop**

In `main.py`, after the exclusions block (Task 4 step 4.3) and before `# 4b. Verify against CSV`, insert:

```python
            # ── Detect payout adjustments for cross-month codes ────────────
            if persist_db:
                current_codes = {r["confirmation_code"] for r in reservations}
                # Codes present in payout data for this property but NOT in current month
                payout_codes_this_prop: set[str] = set()
                # Airbnb: gref_map has all codes from the CSV (all properties combined)
                # Filter to this property by listing_nickname
                for code, pinfo in gref_map.items():
                    if code not in current_codes:
                        payout_codes_this_prop.add(code)
                booking_pid = booking_cfg.get("property_id", "")
                for code, pinfo in booking_batch_map.items():
                    if code not in current_codes and pinfo.get("property_id", "") == booking_pid:
                        payout_codes_this_prop.add(code)

                for code in payout_codes_this_prop:
                    past_row = get_report_row_by_code(db_conn, code)
                    if past_row is None:
                        continue  # Unknown code — let verifier handle as CHYBÍ_V_HOSTIFY
                    if past_row.get("slug") != slug:
                        continue  # Code belongs to a different property
                    if past_row.get("year") == year and past_row.get("month") == month:
                        continue  # Same month — not an adjustment
                    batch_info = gref_map.get(code) or booking_batch_map.get(code) or {}
                    adjustment = _build_adjustment_reservation(past_row, batch_info)
                    reservations.append(adjustment)
                    log.info(
                        "  Payout adjustment detected: %s (originally %d-%02d)",
                        code,
                        past_row.get("year", 0),
                        past_row.get("month", 0),
                    )
```

- [ ] **Step 5.4: Verify dry-run still works**

```bash
python -m report.main --year 2026 --month 3 --dry-run 2>&1 | head -40
```

Expected: runs without error. If any adjustment is detected, it shows `Payout adjustment detected: ...` in logs.

- [ ] **Step 5.5: Commit**

```bash
git add report/main.py
git commit -m "feat: pipeline detects cross-month payout adjustments and creates Doplatek rows"
```

---

## Task 6: HTTP routes — move, move-revert, exclude, reinstate

**Files:**
- Modify: `report/routes/property_routes.py`
- Modify: `report/web_support.py` (add new functions to state)

- [ ] **Step 6.1: Add new functions to state in `report/web_support.py`**

In `web_support.py`, find where `get_report_rows` and other DB functions are added to the state dict (search for `"get_report_rows"` in the file). Add alongside them:

```python
from report.db_controls import (
    create_reservation_month_assignment,
    revert_reservation_month_assignment,
    get_assignment_for_code,
    create_reservation_exclusion,
    reinstate_reservation,
    get_exclusion_for_code,
)
```

Then in the state dict, add:

```python
"create_reservation_month_assignment": create_reservation_month_assignment,
"revert_reservation_month_assignment": revert_reservation_month_assignment,
"get_assignment_for_code": get_assignment_for_code,
"create_reservation_exclusion": create_reservation_exclusion,
"reinstate_reservation": reinstate_reservation,
"get_exclusion_for_code": get_exclusion_for_code,
```

- [ ] **Step 6.2: Add 4 routes to `report/routes/property_routes.py`**

At the end of the `register()` function, before the final `state.update({...})`, add these four routes:

```python
    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/move")
    async def reservation_move(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        target_year: int = Form(...),
        target_month: int = Form(...),
        reason: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        if not (1 <= target_month <= 12):
            raise HTTPException(400, "Neplatný cílový měsíc")
        if target_year == year and target_month == month:
            raise HTTPException(400, "Cílový měsíc je stejný jako zdrojový")
        # Verify reservation exists in source month
        rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
        row = next((r for r in rows if r.get("confirmation_code") == code), None)
        if row is None:
            raise HTTPException(404, "Rezervace nenalezena")
        state["create_reservation_month_assignment"](conn, {
            "slug": slug,
            "confirmation_code": code,
            "target_year": target_year,
            "target_month": target_month,
            "original_year": year,
            "original_month": month,
            "reason": reason.strip(),
            "actor": state["_get_actor_username"](request),
        })
        state["mark_report_month_stale"](conn, slug, year, month)
        state["mark_report_month_stale"](conn, slug, target_year, target_month)
        state["_set_flash"](request, "success",
            f"Rezervace přesunuta do {target_month:02d}/{target_year}.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)


    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/move-revert")
    async def reservation_move_revert(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        assignment = state["get_assignment_for_code"](conn, slug, code)
        state["revert_reservation_month_assignment"](
            conn, slug, code, actor=state["_get_actor_username"](request)
        )
        state["mark_report_month_stale"](conn, slug, year, month)
        if assignment:
            state["mark_report_month_stale"](
                conn, slug, assignment["target_year"], assignment["target_month"]
            )
        state["_set_flash"](request, "success", "Přesun byl vrácen zpět.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)


    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/exclude")
    async def reservation_exclude(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        reason: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["create_reservation_exclusion"](conn, {
            "slug": slug,
            "confirmation_code": code,
            "reason": reason.strip(),
            "actor": state["_get_actor_username"](request),
        })
        state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Rezervace byla vyřazena z výpočtu.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)


    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/reinstate")
    async def reservation_reinstate(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["reinstate_reservation"](
            conn, slug, code, actor=state["_get_actor_username"](request)
        )
        state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Rezervace byla vrácena do výpočtu.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)
```

Register them at the bottom:

```python
        "reservation_move": reservation_move,
        "reservation_move_revert": reservation_move_revert,
        "reservation_exclude": reservation_exclude,
        "reservation_reinstate": reservation_reinstate,
```

- [ ] **Step 6.3: Run existing tests**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 6.4: Commit**

```bash
git add report/routes/property_routes.py report/web_support.py
git commit -m "feat: add move/exclude/reinstate HTTP routes for reservation controls"
```

---

## Task 7: Table row UI — excluded rows and adjustment Doplatek label

**Files:**
- Modify: `templates/partials/property_reservations.html`
- Modify: `templates/partials/base_styles.html`

- [ ] **Step 7.1: Add CSS for excluded rows and adjustment sub-label**

In `templates/partials/base_styles.html`, find the end of existing CSS and append before closing `</style>`:

```css
/* ─── Excluded rows ──────────────────────────────────────────────── */
tr.row-excluded td {
  opacity: 0.42;
}
tr.row-excluded .num {
  text-decoration: line-through;
  text-decoration-color: var(--text-muted);
}

/* ─── Payout adjustment sub-label ───────────────────────────────── */
.adjustment-sublabel {
  display: block;
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 400;
  margin-top: 2px;
  letter-spacing: 0;
}
```

- [ ] **Step 7.2: Update `property_reservations.html` — excluded rows**

In `templates/partials/property_reservations.html`, find the main `<tr>` element (line ~36):

```html
        <tr id="res-row-{{ idx }}"
```

Add `{% if r.is_excluded %}row-excluded{% endif %}` to the class:

```html
        <tr id="res-row-{{ idx }}"
            class="{% if r.is_excluded %}row-excluded{% endif %}"
```

Also update the badge in the Host column (currently shows `upraveno`). Find:

```html
              {% if has_override %}<span class="override-badge">upraveno</span>{% endif %}
```

Add after:

```html
              {% if r.is_excluded %}<span class="override-badge" style="background:rgba(239,68,68,0.12);color:var(--red);border-color:rgba(239,68,68,0.2);">Vyřazeno</span>{% endif %}
```

- [ ] **Step 7.3: Update `property_reservations.html` — adjustment Doplatek label**

Find the Kanál column td block (lines ~52–59):

```html
          <td data-col="kanal" data-val="{{ channel_label }}">
            {% if "airbnb" in src %}
            <span class="badge channel-airbnb" style="font-size:10.5px;">Airbnb</span>
            {% elif "booking" in src %}
            <span class="badge channel-booking" style="font-size:10.5px;">Booking</span>
            {% else %}
            <span style="font-size:12px;color:var(--text-muted);">{{ r.source or "—" }}</span>
            {% endif %}
          </td>
```

Replace with:

```html
          <td data-col="kanal" data-val="{{ channel_label }}">
            {% if "airbnb" in src %}
            <span class="badge channel-airbnb" style="font-size:10.5px;">Airbnb</span>
            {% elif "booking" in src %}
            <span class="badge channel-booking" style="font-size:10.5px;">Booking</span>
            {% else %}
            <span style="font-size:12px;color:var(--text-muted);">{{ r.source or "—" }}</span>
            {% endif %}
            {% if r.is_payout_adjustment %}
            <span class="adjustment-sublabel">
              Doplatek ↗ {{ "%02d/%d"|format(r.adjustment_original_month, r.adjustment_original_year) if r.adjustment_original_month else "" }}
            </span>
            {% endif %}
          </td>
```

- [ ] **Step 7.4: Update header count to exclude excluded rows**

In `property_reservations.html`, find:

```html
    <span style="font-size:12px;color:var(--text-muted);">{{ rows|length }} záznamů</span>
```

Replace with:

```html
    <span style="font-size:12px;color:var(--text-muted);">{{ rows|selectattr('is_excluded', 'undefined')|list|length + rows|selectattr('is_excluded', 'equalto', false)|list|length }} záznamů</span>
```

Actually, Jinja2 makes this cleaner with a namespace:

```html
    {%- set active_count = rows | rejectattr('is_excluded') | list | length -%}
    <span style="font-size:12px;color:var(--text-muted);">{{ active_count }} záznamů</span>
```

Note: `rejectattr('is_excluded')` keeps rows where `is_excluded` is falsy. Works for both `False` and missing key.

- [ ] **Step 7.5: Commit**

```bash
git add templates/partials/property_reservations.html templates/partials/base_styles.html
git commit -m "feat: table UI — excluded row style, Doplatek adjustment sub-label"
```

---

## Task 8: Panel Akce section in `reservation_detail.html`

**Files:**
- Modify: `templates/partials/reservation_detail.html`

**Prerequisite:** Floating Panel plan must be complete (panel renders `reservation_detail.html` as tab content, and passes `month_state`, `slug`, `year`, `month` in template context via the `/reservation/{code}/panel` endpoint).

- [ ] **Step 8.1: Add Akce section at end of `reservation_detail.html`**

At the very end of `templates/partials/reservation_detail.html` (after all existing sections), add:

```html
{# ── Akce ─────────────────────────────────────────────────────────────────── #}
{% if month_state and month_state.status != 'LOCKED' %}
<div style="padding:20px 24px;border-top:1px solid var(--border);">
  <div style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-muted);margin-bottom:14px;">Akce</div>

  {# ── Move reservation ──────────────────────────────────────── #}
  {% set prev_m = (month - 1) if month > 1 else 12 %}
  {% set prev_y = year if month > 1 else (year - 1) %}
  {% set next_m = (month + 1) if month < 12 else 1 %}
  {% set next_y = year if month < 12 else (year + 1) %}

  <div style="margin-bottom:12px;">
    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;font-weight:500;">Přesunout do jiného měsíce</div>

    {% if r.get('_assignment') %}
    {# Already moved — show banner + revert #}
    <div style="padding:10px 12px;background:rgba(245,161,74,0.08);border:1px solid rgba(245,161,74,0.2);border-radius:8px;font-size:12px;color:var(--amber);margin-bottom:8px;">
      ⚠ Přesunuto do {{ "%02d/%d"|format(r._assignment.target_month, r._assignment.target_year) }}
      {% if r._assignment.reason %} — {{ r._assignment.reason }}{% endif %}
    </div>
    <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/move-revert">
      {{ csrf_input(request) }}
      <button type="submit" class="btn btn-ghost btn-sm">Vrátit přesun zpět</button>
    </form>
    {% else %}
    <div style="display:flex;gap:8px;">
      <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/move" style="flex:1;">
        {{ csrf_input(request) }}
        <input type="hidden" name="target_year" value="{{ prev_y }}">
        <input type="hidden" name="target_month" value="{{ prev_m }}">
        <input type="hidden" name="reason" value="">
        <button type="submit" class="btn btn-ghost btn-sm" style="width:100%;">← {{ "%02d/%d"|format(prev_m, prev_y) }}</button>
      </form>
      <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/move" style="flex:1;">
        {{ csrf_input(request) }}
        <input type="hidden" name="target_year" value="{{ next_y }}">
        <input type="hidden" name="target_month" value="{{ next_m }}">
        <input type="hidden" name="reason" value="">
        <button type="submit" class="btn btn-ghost btn-sm" style="width:100%;">{{ "%02d/%d"|format(next_m, next_y) }} →</button>
      </form>
    </div>
    {% endif %}
  </div>

  {# ── Exclude / reinstate ────────────────────────────────────── #}
  <div>
    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;font-weight:500;">Výpočet</div>
    {% if r.is_excluded %}
    {% set excl = r.get('_exclusion') %}
    <div style="padding:10px 12px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.15);border-radius:8px;font-size:12px;color:var(--red);margin-bottom:8px;">
      Vyřazeno z výpočtu{% if excl and excl.reason %} — {{ excl.reason }}{% endif %}
    </div>
    <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/reinstate">
      {{ csrf_input(request) }}
      <button type="submit" class="btn btn-sm" style="background:rgba(34,197,94,0.1);color:var(--green);border-color:rgba(34,197,94,0.2);">Vrátit do výpočtu</button>
    </form>
    {% else %}
    <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/exclude">
      {{ csrf_input(request) }}
      <div style="display:flex;gap:8px;align-items:flex-end;">
        <div style="flex:1;">
          <input type="text" name="reason" placeholder="Důvod vyřazení (volitelné)" class="form-input" style="font-size:12px;">
        </div>
        <button type="submit" class="btn btn-ghost btn-sm" style="color:var(--red);border-color:rgba(239,68,68,0.2);white-space:nowrap;">Vyřadit z výpočtu</button>
      </div>
    </form>
    {% endif %}
  </div>

  {# ── Payout adjustment info ─────────────────────────────────── #}
  {% if r.is_payout_adjustment %}
  <div style="margin-top:12px;padding:10px 12px;background:rgba(124,109,255,0.07);border:1px solid rgba(124,109,255,0.2);border-radius:8px;font-size:12px;color:var(--color-primary);">
    Toto je platební korekce (Doplatek) k rezervaci z {{ "%02d/%d"|format(r.adjustment_original_month, r.adjustment_original_year) if r.adjustment_original_month else "minulého měsíce" }}.
    Úklid, city tax a balíčky nejsou zahrnuty (již zaúčtováno v původním měsíci).
  </div>
  {% endif %}

</div>
{% endif %}
```

- [ ] **Step 8.2: Update `/reservation/{code}/panel` endpoint to pass assignment + exclusion data**

In `report/routes/dashboard.py`, in the `reservation_panel_partial` route added in the floating-panel plan (Task 2), add after `row = rows_with_overrides[0] if rows_with_overrides else row`:

```python
    # Attach assignment + exclusion info for Akce section
    assignment = state.get("get_assignment_for_code") and state["get_assignment_for_code"](conn, slug, code)
    exclusion = state.get("get_exclusion_for_code") and state["get_exclusion_for_code"](conn, slug, code)
    row = dict(row)
    if assignment:
        row["_assignment"] = assignment
    if exclusion:
        row["_exclusion"] = exclusion
```

- [ ] **Step 8.3: Also update the existing `/property/{slug}/{year}/{month}/reservation/{code}/detail` route**

In `report/routes/dashboard.py`, in the existing `reservation_detail_partial` route (line ~44), add the same assignment/exclusion enrichment after `row = next(...)`:

```python
        if row:
            assignment = state.get("get_assignment_for_code") and state["get_assignment_for_code"](conn, slug, code)
            exclusion = state.get("get_exclusion_for_code") and state["get_exclusion_for_code"](conn, slug, code)
            row = dict(row)
            if assignment:
                row["_assignment"] = assignment
            if exclusion:
                row["_exclusion"] = exclusion
```

- [ ] **Step 8.4: Test Akce section renders**

1. Open a property page with an OPEN month
2. Click a reservation → panel opens
3. Scroll to bottom of panel tab content
4. Should see "Akce" section with move buttons and exclude form
5. Click "← 03/2026" → page reloads with flash "Rezervace přesunuta do 03/2026"
6. Regenerate → reservation should appear in 03/2026 instead of 04/2026

- [ ] **Step 8.5: Commit**

```bash
git add templates/partials/reservation_detail.html report/routes/dashboard.py
git commit -m "feat: Akce section in panel — move to prev/next month, exclude from calculation"
```

---

## Task 9: Wire `_prepare_rows_for_display` to carry through control flags

**Files:**
- Modify: `report/web_support.py`

The `_prepare_rows_for_display` function in `web_support.py` copies rows and adds display fields. It must pass through `is_excluded`, `is_payout_adjustment`, `adjustment_original_year`, `adjustment_original_month` so the template can use them.

- [ ] **Step 9.1: Update `_prepare_rows_for_display`**

In `report/web_support.py`, find `_prepare_rows_for_display`:

```python
def _prepare_rows_for_display(rows: list[dict]) -> list[dict]:
    prepared = []
    for row in rows:
        display_status, display_note = _display_verification_status(row)
        prepared.append(
            {
                **row,
                "display_verification_status": display_status,
                "display_verification_note": display_note,
            }
        )
    return prepared
```

The `{**row, ...}` already passes through all existing keys. The template flags `is_excluded`, `is_payout_adjustment`, etc. are in the raw row and will be included via `**row` automatically.

Verify by adding an explicit check: the `is_excluded` key exists in the `row` dict before calling this function. Since it's set in `main.py` during generation and saved in `report_rows.data` JSON, it will be present. No code change needed — just confirm.

- [ ] **Step 9.2: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 9.3: Final integration test**

1. Generate a report for a month that has both Airbnb and Booking reservations
2. Exclude one reservation via the Akce panel → re-generate → verify it doesn't appear in Finanční přehled totals
3. Move a reservation to next month → re-generate both months → verify it appears only in target month
4. If payout CSV contains a code from a previous locked month → verify a "Doplatek" row appears in current month

- [ ] **Step 9.4: Final commit**

```bash
git add -A
git commit -m "feat: reservation controls complete — move, exclude, payout adjustment"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in task |
|---|---|
| `reservation_month_assignments` table | Task 1 |
| `reservation_exclusions` table | Task 1 |
| `is_excluded`, `is_payout_adjustment` flags in `report_rows` | Task 2 (calculator) |
| Move reservation — pipeline applies | Task 4 |
| Exclude reservation — pipeline marks flag | Task 4 |
| Payout adjustment auto-detection | Task 5 |
| `is_cancelled`-like logic for adjustments | Task 2 |
| `_build_adjustment_reservation` | Task 5 |
| Summary skips excluded rows | Task 3 |
| Breakdown skips excluded rows | Task 3 |
| 4 HTTP routes (move, move-revert, exclude, reinstate) | Task 6 |
| Excluded row style in table | Task 7 |
| "Doplatek ↗ MM/YYYY" sub-label in Kanál column | Task 7 |
| `Vyřazeno` badge on excluded row | Task 7 |
| Akce section in panel (move buttons + exclude form) | Task 8 |
| Assignment/exclusion info attached to panel row | Task 8 |

**No placeholders found.** Every step has concrete code.

**Type consistency:**
- `create_reservation_month_assignment(conn, data)` — defined Task 1, called Task 6 ✓
- `get_assignment_for_code(conn, slug, code)` — defined Task 1, called Tasks 6 and 8 ✓
- `get_active_exclusions(conn, slug)` → `set[str]` — defined Task 1, used Task 4 ✓
- `_build_adjustment_reservation(past_row, batch_info)` — defined Task 5, called Task 5 ✓
- `r.is_payout_adjustment`, `r.adjustment_original_year`, `r.adjustment_original_month` — set Task 2, used Task 7 template ✓
- `r.is_excluded` — set Task 2 + Task 4, used Tasks 3, 7, 8 ✓
