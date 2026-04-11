# Auto-Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual "Přegenerovat" button with fully automatic in-process report generation triggered by every data change.

**Architecture:** Extract the property calculation loop from `report/main.py` into `report/engine.py` as a callable function. Manual mutation routes call it synchronously before redirect; CSV imports call it via FastAPI `BackgroundTasks`; a daily asyncio loop keeps Hostify snapshots fresh. No subprocess, no Excel on web path.

**Tech Stack:** FastAPI BackgroundTasks, asyncio, SQLite, existing `report/preview_service.py` patterns (already does in-process DB-backed calculation — engine.py follows the same approach plus save-to-DB).

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `report/engine.py` | **Create** | `generate_report_in_process(conn, slug, year, month, config)` — full calc + save, no Excel |
| `report/hostify_sync.py` | **Create** | `HostifySyncLoop` — daily asyncio task, fetches 5 months, triggers regen |
| `report/web.py` | **Modify** | lifespan starts sync loop; `_apply_import_impacts` simplified |
| `report/routes/property_routes.py` | **Modify** | all mutation routes call engine sync; remove `/generate` route |
| `report/routes/sources.py` | **Modify** | CSV import uses `BackgroundTasks` + engine |
| `templates/partials/property_intro.html` | **Modify** | remove generate button; add "last updated" label |
| `tests/test_engine.py` | **Create** | unit tests for `generate_report_in_process` |
| `tests/test_hostify_sync.py` | **Create** | unit tests for sync loop |
| `tests/test_web_generation.py` | **Modify** | update/remove obsolete subprocess-based tests |

---

## Task 1: `report/engine.py` — in-process generation function

**Files:**
- Create: `report/engine.py`
- Create: `tests/test_engine.py`

### Context for the engineer

`report/preview_service.py` already does in-process DB-backed calculation (read-only). `engine.py` is the write version: same pipeline plus month assignments, exclusions, payout adjustments, and DB persistence. The existing `report/main.py` is the reference for the full pipeline (steps 4a–4i).

Key DB functions already available:
- `get_hostify_reservations_for_listing_month(conn, *, listing_nicknames, year, month)` → list of raw Hostify dicts (payload_json decoded)
- `get_active_source_files(conn, source_type)` → active CSV source records for Airbnb/Booking/Bank
- `get_reservation_month_assignments(conn, slug)` → {code: {target_year, target_month}}
- `get_codes_assigned_to_month(conn, slug, year, month)` → set of confirmation codes
- `get_active_exclusions(conn, slug)` → set of excluded codes
- `get_report_row_by_code(conn, code)` → past row dict (for payout adjustments)
- `save_report_rows(conn, slug, year, month, rows)` → upsert calc rows
- `log_report_generated(conn, slug, year, month, output_path, rows)` → insert into report_history
- `touch_report_month_generation(conn, slug, year, month)` → update last_generated_at
- `mark_report_month_has_data(conn, slug, year, month)`
- `get_report_month_state(conn, slug, year, month)` → {status: 'OPEN'|'LOCKED', ...}
- `MONTH_STATUS_LOCKED` constant

- [ ] **Step 1: Write failing tests**

Create `tests/test_engine.py`:

```python
from __future__ import annotations

import json
from datetime import date

import pytest

from report.db import get_connection, MONTH_STATUS_LOCKED
from report.engine import generate_report_in_process


def _make_config(slug: str = "test_prop") -> dict:
    return {
        "properties": {
            slug: {
                "listing_id": 1,
                "listing_nickname": "Test Listing",
                "display_name": "Test Property",
                "active": True,
                "balicky_per_person": 0,
                "city_tax_rate": 45.0,
                "vat_rate": 21.0,
                "channels": {
                    "airbnb": {"listing_names": ["Test Listing"]},
                    "booking": {"listing_nickname": "", "property_id": ""},
                },
            }
        }
    }


def test_generate_report_in_process_returns_dict_with_rows_count():
    conn = get_connection(":memory:")
    try:
        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, _make_config()
        )
        assert isinstance(result, dict)
        assert "rows_count" in result
        assert result["rows_count"] == 0  # no hostify data in empty DB
    finally:
        conn.close()


def test_generate_report_in_process_skips_locked_month():
    from report.db import get_report_month_state
    from report.config import sync_json_config_to_db
    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)
        # Lock the month
        conn.execute(
            """INSERT OR REPLACE INTO report_month_state
               (slug, year, month, status, data_state)
               VALUES (?, ?, ?, 'LOCKED', 'READY')""",
            ("test_prop", 2026, 3),
        )
        conn.commit()

        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, config
        )
        assert result["skipped"] is True
        assert result["reason"] == "locked"
    finally:
        conn.close()


def test_generate_report_in_process_saves_rows_to_db():
    from report.db import get_report_rows, save_hostify_reservations
    from report.config import sync_json_config_to_db
    conn = get_connection(":memory:")
    try:
        config = _make_config()
        sync_json_config_to_db(conn, config)
        # Seed one Hostify reservation
        raw = {
            "channel_reservation_id": "HM123456789",
            "guest_name": "Test Guest",
            "listing_nickname": "Test Listing",
            "checkIn": "2026-03-10",
            "checkOut": "2026-03-12",
            "status": "confirmed",
            "numberOfAdults": 2,
            "numberOfChildren": 0,
            "numberOfInfants": 0,
            "totalPrice": 100.0,
            "cleaningFee": 0.0,
            "cityTax": 0.0,
            "channelCommission": 15.0,
            "payout": 85.0,
            "confirmedAt": "2026-01-15T10:00:00",
        }
        from report.loader import normalize_reservations
        save_hostify_reservations(conn, normalize_reservations([raw]))

        result = generate_report_in_process(
            conn, "test_prop", 2026, 3, config
        )
        rows = get_report_rows(conn, "test_prop", 2026, 3)
        assert result["rows_count"] >= 1
        assert any(r.get("confirmation_code") == "HM123456789" for r in rows)
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
pytest tests/test_engine.py -v
```
Expected: `ModuleNotFoundError: No module named 'report.engine'`

- [ ] **Step 3: Create `report/engine.py`**

```python
"""
report/engine.py — In-process report generation engine.

Replaces the subprocess-based generation for web-triggered flows.
Reads all inputs from SQLite (Hostify snapshots, payout CSV data, CNB rates).
Writes results to report_rows, report_history, report_month_state.
Does NOT write Excel files.

Public API:
    generate_report_in_process(conn, slug, year, month, config, *, cutoff_day=7) -> dict
    run_generation_background(db_path, slug, year, month, config) -> None  (for BackgroundTasks)
"""
from __future__ import annotations

import json
import logging
import os
from calendar import monthrange
from collections import Counter
from datetime import date as date_cls, datetime as datetime_cls

from report.bank import (
    build_bank_index,
    enrich_booking_rows_with_bank,
    enrich_rows_with_bank,
    filter_bank_by_cutoff,
    load_bank_csv,
    load_booking_bank_transactions,
    resolve_pending_against_bank,
)
from report.calculator import calculate_all_rows, calculate_totals_with_config
from report.checkin import apply_checkin_city_tax_overrides, hydrate_checkin_groups_from_db
from report.cnb import CnbRateError, get_rate_for_reservation, preload_rates_for_month
from report.config import get_booking_config, get_hostify_listing_names
from report.db import (
    MONTH_STATUS_LOCKED,
    apply_overrides_to_rows,
    get_active_source_files,
    get_active_exclusions,
    get_codes_assigned_to_month,
    get_expenses,
    get_hostify_reservations_by_codes,
    get_hostify_reservations_for_listing_month,
    get_pending_payments,
    get_report_month_state,
    get_report_row_by_code,
    get_reservation_month_assignments,
    list_checkin_reservations,
    log_report_generated,
    mark_report_month_has_data,
    replace_checkin_match_audit,
    resolve_pending_payment,
    save_hostify_reservations,
    save_pending_payments,
    save_payout_batch_bank_matches,
    save_report_rows,
    touch_report_month_generation,
    mark_report_month_stale,
)
from report.loader import (
    _normalize_reservation,
    filter_for_property_month,
    normalize_reservations,
)
from report.verifier import (
    STATUS_CHYBI_HOSTIFY,
    STATUS_KE_KONTROLE,
    build_airbnb_payout_data,
    build_booking_payout_data,
    build_verification_index,
    load_airbnb_csv,
    load_booking_csv,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger(__name__)


def _resolve_sources(conn, source_type: str) -> list:
    """Return active DB-backed source file records for the given type."""
    return get_active_source_files(conn, source_type)


def generate_report_in_process(
    conn,
    slug: str,
    year: int,
    month: int,
    config: dict,
    *,
    cutoff_day: int = 7,
) -> dict:
    """
    Run a full report generation in-process (no subprocess, no Excel).

    Returns:
        {"rows_count": int, "status_counts": dict}           on success
        {"skipped": True, "reason": "locked"}                if month is locked
    """
    from report.config import get_all_properties

    props = {p["slug"]: p for p in get_all_properties(config)}
    if slug not in props:
        raise ValueError(f"Unknown property slug: {slug}")
    prop = props[slug]

    # ── Check month lock ────────────────────────────────────────────────────
    month_state = get_report_month_state(conn, slug, year, month)
    if month_state.get("status") == MONTH_STATUS_LOCKED:
        mark_report_month_stale(conn, slug, year, month)
        log.info("Skipping locked month %s %d/%d", slug, month, year)
        return {"skipped": True, "reason": "locked"}

    # ── Cutoff date ─────────────────────────────────────────────────────────
    if month == 12:
        cutoff_year, cutoff_month_num = year + 1, 1
    else:
        cutoff_year, cutoff_month_num = year, month + 1
    _, last_day_cutoff = monthrange(cutoff_year, cutoff_month_num)
    cutoff_date = date_cls(
        cutoff_year, cutoff_month_num, min(cutoff_day, last_day_cutoff)
    )

    # ── Load CSV payout data from DB-backed source files ────────────────────
    airbnb_csvs = _resolve_sources(conn, "airbnb")
    booking_csvs = _resolve_sources(conn, "booking")
    bank_csvs = _resolve_sources(conn, "bank")

    airbnb_index = load_airbnb_csv(airbnb_csvs) if airbnb_csvs else {}
    booking_index = load_booking_csv(booking_csvs) if booking_csvs else {}
    airbnb_payout_data = build_airbnb_payout_data(airbnb_csvs) if airbnb_csvs else {
        "reservation_map": {}, "batches": [], "items": []
    }
    booking_payout_data = build_booking_payout_data(booking_csvs) if booking_csvs else {
        "reservation_map": {}, "batches": [], "items": []
    }
    gref_map = airbnb_payout_data["reservation_map"]
    booking_batch_map = booking_payout_data["reservation_map"]

    # ── Bank data ───────────────────────────────────────────────────────────
    if bank_csvs:
        bank_rows_all = load_bank_csv(bank_csvs)
        bank_rows = filter_bank_by_cutoff(bank_rows_all, cutoff_date)
        bank_index, bank_no_ref = build_bank_index(bank_rows)
        booking_bank_idx_all = load_booking_bank_transactions(bank_csvs)
        booking_bank_idx = {
            pid: [r for r in rows if r.get("datum") and r["datum"] <= cutoff_date]
            for pid, rows in booking_bank_idx_all.items()
        }
    else:
        bank_rows_all, bank_index, bank_no_ref = [], {}, []
        booking_bank_idx, booking_bank_idx_all = {}, {}

    # ── Checkin groups ──────────────────────────────────────────────────────
    checkin_groups = hydrate_checkin_groups_from_db(
        list_checkin_reservations(
            conn=conn,
            active_only=True,
            overlap_year=year,
            overlap_month=month,
            latest_only=True,
        )
    )

    # ── CNB rates ───────────────────────────────────────────────────────────
    preload_rates_for_month(year, month, persist=True)

    # ── Hostify data from DB snapshot ───────────────────────────────────────
    booking_cfg = get_booking_config(prop, year=year, month=month)
    booking_nickname = booking_cfg.get("listing_nickname") or None
    hostify_nicknames = get_hostify_listing_names(prop, year=year, month=month)
    primary_nickname = hostify_nicknames[0] if hostify_nicknames else prop["listing_nickname"]
    extra_hostify_nicknames = [n for n in hostify_nicknames[1:] if n]

    listing_nicknames_all = [
        primary_nickname,
        *extra_hostify_nicknames,
        *([booking_nickname] if booking_nickname else []),
    ]
    all_raw = get_hostify_reservations_for_listing_month(
        conn,
        listing_nicknames=[n for n in listing_nicknames_all if n],
        year=year,
        month=month,
    )
    if all_raw:
        mark_report_month_has_data(conn, slug, year, month)

    reservations = filter_for_property_month(
        all_raw, primary_nickname, year, month,
        extra_nicknames=(
            extra_hostify_nicknames + ([booking_nickname] if booking_nickname else [])
        ) or None,
    )

    # ── Month assignments ───────────────────────────────────────────────────
    assignments = get_reservation_month_assignments(conn, slug)
    if assignments:
        reservations = [
            r for r in reservations
            if r["confirmation_code"] not in assignments
            or (
                assignments[r["confirmation_code"]]["target_year"] == year
                and assignments[r["confirmation_code"]]["target_month"] == month
            )
        ]
        codes_moved_in = get_codes_assigned_to_month(conn, slug, year, month)
        current_codes = {r["confirmation_code"] for r in reservations}
        for code in codes_moved_in - current_codes:
            row = conn.execute(
                "SELECT payload_json FROM hostify_reservations WHERE confirmation_code = ?",
                (code,),
            ).fetchone()
            if row:
                raw = json.loads(row["payload_json"])
                normalized = _normalize_reservation(raw)
                if normalized:
                    normalized["assigned_year"] = year
                    normalized["assigned_month"] = month
                    reservations.append(normalized)
                    log.info("Pulled in moved reservation %s for %s %d/%d", code, slug, month, year)

    # ── Exclusions ──────────────────────────────────────────────────────────
    excluded_codes = get_active_exclusions(conn, slug)
    for r in reservations:
        if r["confirmation_code"] in excluded_codes:
            r["is_excluded"] = True

    # ── Payout adjustments (cross-month codes) ──────────────────────────────
    month_start = date_cls(year, month, 1)
    current_codes = {r["confirmation_code"] for r in reservations}
    booking_pid = booking_cfg.get("property_id", "")
    payout_codes: set = set()
    for code in gref_map:
        if code not in current_codes:
            payout_codes.add(code)
    for code, pinfo in booking_batch_map.items():
        if code not in current_codes and pinfo.get("property_id", "") == booking_pid:
            payout_codes.add(code)
    for code in payout_codes:
        past_row = get_report_row_by_code(conn, code)
        if past_row is None or past_row.get("slug") != slug:
            continue
        if past_row.get("year") == year and past_row.get("month") == month:
            continue
        batch_info = gref_map.get(code) or booking_batch_map.get(code) or {}
        payout_date_str = str(batch_info.get("payout_date") or "").strip()
        if payout_date_str:
            payout_dt = None
            for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
                try:
                    payout_dt = datetime_cls.strptime(payout_date_str, fmt).date()
                    break
                except ValueError:
                    pass
            if payout_dt is not None and not (month_start <= payout_dt <= cutoff_date):
                continue
        from report.main import _build_adjustment_reservation
        reservations.append(_build_adjustment_reservation(past_row, batch_info))

    # ── Verify against CSV ──────────────────────────────────────────────────
    booking_codes = [
        code for code, row in booking_index.items()
        if row.get("property_id", "").strip() == booking_pid
    ]
    late_hostify_lookup = get_hostify_reservations_by_codes(
        conn,
        booking_codes,
        listing_nicknames=[n for n in listing_nicknames_all if n],
        year=year,
        month=month,
    )
    verified, csv_only = build_verification_index(
        reservations, airbnb_index, booking_index, prop,
        hostify_lookup=late_hostify_lookup,
        year=year, month=month,
    )
    all_verified = verified + csv_only

    # ── Checkin city-tax overrides ──────────────────────────────────────────
    checkin_stats: dict = {}
    if checkin_groups:
        all_verified, checkin_stats = apply_checkin_city_tax_overrides(
            all_verified, checkin_groups, prop, year=year, month=month
        )
    replace_checkin_match_audit(
        conn, slug, year, month,
        checkin_stats.get("audit_records", []) if checkin_groups else [],
    )

    # ── Attach Airbnb/Booking batch rate ────────────────────────────────────
    for row in all_verified:
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()
        if "airbnb" in source:
            pinfo = gref_map.get(code, {})
            if pinfo.get("airbnb_rate"):
                row["airbnb_batch_rate"] = pinfo["airbnb_rate"]
            row["airbnb_payout_date"] = pinfo.get("payout_date", "")
            row["batch_ref"] = pinfo.get("gref", "")
            row["batch_payout_date"] = pinfo.get("payout_date", "")
            row["batch_amount_czk"] = pinfo.get("payout_czk")
            row["batch_rate"] = pinfo.get("airbnb_rate")
        elif "booking" in source:
            pinfo = booking_batch_map.get(code, {})
            row["batch_ref"] = pinfo.get("batch_ref", "")
            row["batch_payout_date"] = (
                pinfo.get("payout_date", "") or row.get("booking_payout_date", "")
            )
            row["batch_amount_czk"] = pinfo.get("payout_czk")
            row["batch_rate"] = pinfo.get("booking_batch_rate")

    # ── CNB rates per reservation ───────────────────────────────────────────
    rate_cache: dict = {}
    for res in all_verified:
        confirmed_at = res.get("confirmed_at") or res.get("check_in") or ""
        rate_date = confirmed_at[:10]
        if rate_date and rate_date not in rate_cache:
            try:
                rate_cache[rate_date] = get_rate_for_reservation(confirmed_at, persist=True)
            except CnbRateError:
                rate_cache[rate_date] = {"rate": 0, "valid_for": rate_date}

    # ── Calculate ───────────────────────────────────────────────────────────
    calc_rows = calculate_all_rows(all_verified, rate_cache, prop)
    calc_rows = apply_overrides_to_rows(conn, calc_rows, slug, year, month)

    # ── Bank enrichment ─────────────────────────────────────────────────────
    calc_rows, airbnb_matches = enrich_rows_with_bank(calc_rows, gref_map, bank_index, bank_no_ref)
    calc_rows, booking_matches = enrich_booking_rows_with_bank(
        calc_rows, booking_bank_idx, prop, year=year, month=month
    )
    save_payout_batch_bank_matches(conn, "airbnb", airbnb_matches)
    save_payout_batch_bank_matches(conn, "booking", booking_matches)

    # ── Booking CHYBÍ_V_HOSTIFY → KE_KONTROLE ──────────────────────────────
    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if (
            "booking" in source
            and row.get("verification_status") == STATUS_CHYBI_HOSTIFY
            and row.get("bank_status") == "CHYBÍ"
        ):
            row["verification_status"] = STATUS_KE_KONTROLE

    # ── Pending payments ────────────────────────────────────────────────────
    prev_pending = [
        p for p in get_pending_payments(conn, slug, status="PENDING")
        if p["original_year"] < year
        or (p["original_year"] == year and p["original_month"] < month)
    ]
    if prev_pending:
        bank_index_full, bank_no_ref_full = build_bank_index(bank_rows_all)
        resolved, _ = resolve_pending_against_bank(
            prev_pending, bank_index_full, bank_no_ref_full,
            booking_bank_idx_all, cutoff_date, booking_pid,
        )
        for r in resolved:
            resolve_pending_payment(
                conn, slug, r["confirmation_code"],
                year, month, r["bank_datum"], r["bank_amount_czk"],
            )
            if r.get("original_year") != year or r.get("original_month") != month:
                mark_report_month_stale(
                    conn, slug, int(r["original_year"]), int(r["original_month"])
                )

    chybi_rows = [r for r in calc_rows if r.get("bank_status") == "CHYBÍ"]
    save_pending_payments(conn, slug, year, month, chybi_rows)

    # ── Persist ─────────────────────────────────────────────────────────────
    save_report_rows(conn, slug, year, month, calc_rows)
    log_report_generated(conn, slug, year, month, "", calc_rows)  # no Excel path
    touch_report_month_generation(conn, slug, year, month)

    status_counts = dict(Counter(r.get("verification_status", "") for r in calc_rows))
    log.info(
        "Generated %s %d/%d: %d rows %s",
        slug, month, year, len(calc_rows), status_counts,
    )
    return {"rows_count": len(calc_rows), "status_counts": status_counts}


def run_generation_background(
    db_path: str,
    slug: str,
    year: int,
    month: int,
    config: dict,
) -> None:
    """
    Wrapper for use with FastAPI BackgroundTasks.
    Opens its own DB connection (background tasks run after the request connection closes).
    """
    from report.db import get_connection
    conn = get_connection(db_path)
    try:
        generate_report_in_process(conn, slug, year, month, config)
    except Exception as exc:
        log.error(
            "Background generation failed for %s %d/%d: %s", slug, month, year, exc
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_engine.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: 241 passed (all existing tests still pass)

- [ ] **Step 6: Commit**

```bash
git add report/engine.py tests/test_engine.py
git commit -m "feat: add in-process report generation engine (no subprocess, no Excel)"
```

---

## Task 2: `report/hostify_sync.py` — daily background sync

**Files:**
- Create: `report/hostify_sync.py`
- Create: `tests/test_hostify_sync.py`

### Context

The Hostify API is wrapped in `fetch_raw_reservations_for_period(year, month, db_conn=conn)` which already handles SQLite caching. After fetching, `save_hostify_reservations(conn, normalize_reservations(all_raw))` stores the snapshots used by `engine.py`.

`_shift_month` helper is in `report/loader.py` — use the date arithmetic from the plan to compute 5 target months.

- [ ] **Step 1: Write failing tests**

Create `tests/test_hostify_sync.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

from report.hostify_sync import compute_sync_months, HostifySyncTask


def test_compute_sync_months_returns_five_months():
    months = compute_sync_months(reference_date=date(2026, 4, 6))
    assert len(months) == 5


def test_compute_sync_months_correct_range():
    months = compute_sync_months(reference_date=date(2026, 4, 6))
    # prev=March, current=April, +1=May, +2=June, +3=July
    assert months[0] == (2026, 3)
    assert months[1] == (2026, 4)
    assert months[2] == (2026, 5)
    assert months[3] == (2026, 6)
    assert months[4] == (2026, 7)


def test_compute_sync_months_handles_january():
    months = compute_sync_months(reference_date=date(2026, 1, 15))
    assert months[0] == (2025, 12)   # previous
    assert months[1] == (2026, 1)    # current


def test_hostify_sync_task_calls_fetch_for_each_month(monkeypatch):
    fetched = []

    def fake_fetch(year, month, *, db_conn=None):
        fetched.append((year, month))
        return []

    monkeypatch.setattr("report.hostify_sync.fetch_raw_reservations_for_period", fake_fetch)
    monkeypatch.setattr("report.hostify_sync.save_hostify_reservations", lambda *a: None)
    monkeypatch.setattr("report.hostify_sync.normalize_reservations", lambda x: x)
    monkeypatch.setattr("report.hostify_sync.generate_report_in_process", lambda *a, **kw: {"rows_count": 0})
    monkeypatch.setattr("report.hostify_sync.get_report_month_state", lambda *a: {"status": "OPEN", "last_generated_at": "2026-04-01"})

    from report.db import get_connection
    conn = get_connection(":memory:")
    try:
        task = HostifySyncTask(db_path=":memory:", config={}, config_path=None)
        task._sync_once(conn=conn, reference_date=date(2026, 4, 6))
    finally:
        conn.close()

    assert len(fetched) == 5
    assert (2026, 3) in fetched
    assert (2026, 7) in fetched


def test_hostify_sync_task_skips_locked_months(monkeypatch):
    regenerated = []

    monkeypatch.setattr("report.hostify_sync.fetch_raw_reservations_for_period", lambda *a, **kw: [])
    monkeypatch.setattr("report.hostify_sync.save_hostify_reservations", lambda *a: None)
    monkeypatch.setattr("report.hostify_sync.normalize_reservations", lambda x: x)

    def fake_state(conn, slug, year, month):
        return {"status": "LOCKED", "last_generated_at": "2026-04-01"}

    monkeypatch.setattr("report.hostify_sync.get_report_month_state", fake_state)

    def fake_generate(*a, **kw):
        regenerated.append(a)
        return {"rows_count": 0}

    monkeypatch.setattr("report.hostify_sync.generate_report_in_process", fake_generate)

    from report.db import get_connection
    from report.config import sync_json_config_to_db, load_runtime_config
    conn = get_connection(":memory:")
    config = {
        "properties": {
            "test": {
                "listing_id": 1, "listing_nickname": "Test", "display_name": "Test",
                "active": True, "channels": {"airbnb": {"listing_names": []}, "booking": {}},
            }
        }
    }
    sync_json_config_to_db(conn, config)
    loaded_config = load_runtime_config(None, db_conn=conn)

    task = HostifySyncTask(db_path=":memory:", config=loaded_config, config_path=None)
    task._sync_once(conn=conn, reference_date=date(2026, 4, 6))
    conn.close()

    assert len(regenerated) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_hostify_sync.py -v
```
Expected: `ModuleNotFoundError: No module named 'report.hostify_sync'`

- [ ] **Step 3: Create `report/hostify_sync.py`**

```python
"""
report/hostify_sync.py — Daily Hostify data sync background task.

Fetches 5 months of Hostify reservations (prev, current, +1, +2, +3),
updates hostify_reservations snapshots, then re-generates all open months
that have existing report data.

Usage (started from web.py lifespan):
    task = HostifySyncTask(db_path=..., config=..., config_path=...)
    asyncio_task = asyncio.create_task(task.run_loop())
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from report.db import (
    get_connection,
    get_report_month_state,
    save_hostify_reservations,
    MONTH_STATUS_LOCKED,
)
from report.engine import generate_report_in_process
from report.loader import fetch_raw_reservations_for_period, normalize_reservations

log = logging.getLogger(__name__)

_SYNC_INTERVAL_SECONDS = 24 * 3600  # 24 hours
_STARTUP_DELAY_SECONDS = 60          # brief delay after app start


def compute_sync_months(*, reference_date: date | None = None) -> list[tuple[int, int]]:
    """Return [(year, month)] for: prev, current, +1, +2, +3 months."""
    today = reference_date or date.today()
    y, m = today.year, today.month
    months = []
    for delta in range(-1, 4):   # -1, 0, 1, 2, 3
        total_month = m - 1 + delta   # zero-based
        my = y + total_month // 12
        mm = total_month % 12 + 1
        months.append((my, mm))
    return months


class HostifySyncTask:
    def __init__(self, *, db_path: str, config: dict, config_path: str | None):
        self._db_path = db_path
        self._config = config
        self._config_path = config_path

    def _sync_once(
        self,
        *,
        conn=None,
        reference_date: date | None = None,
    ) -> None:
        """Synchronous core: fetch Hostify for 5 months, regen open months."""
        from report.config import get_all_properties, load_runtime_config

        own_conn = conn is None
        if own_conn:
            conn = get_connection(self._db_path)
        try:
            config = self._config
            if not config:
                config = load_runtime_config(self._config_path, db_conn=conn)

            target_months = compute_sync_months(reference_date=reference_date)
            log.info("Hostify sync starting for months: %s", target_months)

            # 1. Fetch and persist Hostify snapshots for all target months
            for year, month in target_months:
                try:
                    all_raw = fetch_raw_reservations_for_period(
                        year, month, db_conn=conn
                    )
                    save_hostify_reservations(conn, normalize_reservations(all_raw))
                    log.info(
                        "Hostify sync: fetched %d reservations for %d/%d",
                        len(all_raw), month, year,
                    )
                except Exception as exc:
                    log.warning(
                        "Hostify sync: fetch failed for %d/%d: %s", month, year, exc
                    )

            # 2. Re-generate all open months with existing data
            try:
                properties = get_all_properties(config)
            except Exception:
                properties = []

            active_props = [p for p in properties if p.get("active", False)]
            for prop in active_props:
                slug = prop["slug"]
                for year, month in target_months:
                    try:
                        state = get_report_month_state(conn, slug, year, month)
                        if state.get("status") == MONTH_STATUS_LOCKED:
                            continue
                        if not state.get("last_generated_at"):
                            continue  # never generated → skip (nothing to refresh)
                        generate_report_in_process(conn, slug, year, month, config)
                    except Exception as exc:
                        log.warning(
                            "Hostify sync regen failed %s %d/%d: %s",
                            slug, month, year, exc,
                        )
        finally:
            if own_conn:
                conn.close()

    async def run_loop(self) -> None:
        """Asyncio loop: wait for startup delay, then sync every 24h."""
        await asyncio.sleep(_STARTUP_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(self._sync_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("Hostify sync loop error: %s", exc)
            await asyncio.sleep(_SYNC_INTERVAL_SECONDS)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_hostify_sync.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: 241+ passed

- [ ] **Step 6: Commit**

```bash
git add report/hostify_sync.py tests/test_hostify_sync.py
git commit -m "feat: add daily Hostify background sync task"
```

---

## Task 3: Wire sync loop into `report/web.py` lifespan

**Files:**
- Modify: `report/web.py:146-152`

### Context

Current `_app_lifespan` (line 146–149) only calls `_validate_web_runtime_config()` then yields. We need to start `HostifySyncTask.run_loop()` as an asyncio background task and cancel it on shutdown.

`_DB_PATH` is already defined in `web.py` (search for `_DB_PATH =`). `_CONFIG_PATH` is also already defined.

- [ ] **Step 1: Find exact location of `_DB_PATH` and `_CONFIG_PATH` in `web.py`**

```bash
grep -n "_DB_PATH\|_CONFIG_PATH" report/web.py | head -10
```

- [ ] **Step 2: Modify `_app_lifespan` in `report/web.py`**

Find the current lifespan (lines ~146–152):
```python
@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    _validate_web_runtime_config()
    yield
```

Replace with:
```python
@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    _validate_web_runtime_config()
    from report.hostify_sync import HostifySyncTask
    _sync_config = load_runtime_config(_CONFIG_PATH, db_conn=get_connection(_DB_PATH))
    _sync_task_obj = HostifySyncTask(
        db_path=_DB_PATH,
        config=_sync_config,
        config_path=_CONFIG_PATH,
    )
    _bg_task = asyncio.create_task(_sync_task_obj.run_loop())
    try:
        yield
    finally:
        _bg_task.cancel()
        try:
            await _bg_task
        except asyncio.CancelledError:
            pass
```

Also add `import asyncio` at the top of `web.py` if not already present (check first).

- [ ] **Step 3: Verify app still starts**

```bash
pytest tests/test_web_generation.py::test_login_rejects_missing_csrf -v
```
Expected: PASS (TestClient starts the app with lifespan)

- [ ] **Step 4: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add report/web.py
git commit -m "feat: start Hostify daily sync loop in app lifespan"
```

---

## Task 4: Simplify `_apply_import_impacts` — remove subprocess, accept BackgroundTasks

**Files:**
- Modify: `report/web.py:367-452` (the `_apply_import_impacts` function)

### Context

Current `_apply_import_impacts` calls `_enqueue_report_generation` (which spawns a subprocess). We replace this with `background_tasks.add_task(run_generation_background, ...)`. The function gains a new required parameter `background_tasks` and `config`.

`_db_path_for_connection(conn)` already exists in `web.py` — use it to get the DB path for the background task.

The notification side-effects (creating import impact notifications) stay exactly as-is.

- [ ] **Step 1: Update `_apply_import_impacts` signature and body in `report/web.py`**

Find the current function at line ~367. Change the signature and replace the `_enqueue_report_generation` call:

```python
def _apply_import_impacts(
    conn,
    summary: dict,
    *,
    requested_by: str,
    background_tasks,          # FastAPI BackgroundTasks
    config: dict,
) -> dict:
    if summary.get("is_duplicate"):
        return {
            "auto_started": [],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
        }
    affected = {
        (str(slug), int(year), int(month))
        for slug, year, month in (summary.get("affected_month_keys") or [])
        if slug
    }
    result = {
        "auto_started": [],
        "already_running": [],
        "open_without_report": [],
        "locked_notified": [],
    }
    source_type = str(summary.get("source_type") or "")

    db_path = _db_path_for_connection(conn)

    for slug, year, month in sorted(affected):
        mark_report_month_stale(conn, slug, year, month)
        state = get_report_month_state(conn, slug, year, month)
        if state.get("status") == MONTH_STATUS_LOCKED:
            _create_import_impact_notification(
                conn, slug=slug, year=year, month=month,
                event_type="IMPORT_IMPACT_LOCKED_MONTH",
                source_type=source_type,
                message=(
                    f"Nová importovaná data ({source_type}) ovlivňují uzamčený měsíc "
                    f"{month:02d}/{year}. Měsíc nebyl přepsán automaticky."
                ),
                summary=summary,
            )
            result["locked_notified"].append((slug, year, month))
            continue

        background_tasks.add_task(
            run_generation_background, db_path, slug, year, month, config
        )
        _create_import_impact_notification(
            conn, slug=slug, year=year, month=month,
            event_type="IMPORT_IMPACT_AUTO_REGENERATE_STARTED",
            source_type=source_type,
            message=(
                f"Nová importovaná data ({source_type}) změnila měsíc {month:02d}/{year}. "
                f"Automatická regenerace byla spuštěna."
            ),
            summary=summary,
        )
        result["auto_started"].append((slug, year, month))

    return result
```

Also add this import near the top of `web.py` (with the other imports):
```python
from report.engine import generate_report_in_process, run_generation_background
```

- [ ] **Step 2: Update the existing tests that call `_apply_import_impacts`**

Search for all test calls to `_apply_import_impacts`:
```bash
grep -rn "_apply_import_impacts" tests/
```

For each test that calls it, add `background_tasks=MagicMock()` and `config={}` to the call.

- [ ] **Step 3: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add report/web.py tests/
git commit -m "refactor: _apply_import_impacts uses BackgroundTasks+engine instead of subprocess"
```

---

## Task 5: Update `report/routes/sources.py` — pass BackgroundTasks to `_apply_import_impacts`

**Files:**
- Modify: `report/routes/sources.py`

### Context

The sources import route currently calls `state["_apply_import_impacts"](conn, summary, requested_by=...)`. It needs to also pass `background_tasks` and `config`. FastAPI injects `BackgroundTasks` automatically when declared as a route parameter.

- [ ] **Step 1: Find the import route handler in `sources.py`**

```bash
grep -n "_apply_import_impacts\|background_tasks\|BackgroundTasks" report/routes/sources.py
```

- [ ] **Step 2: Update the route to accept and pass BackgroundTasks**

In `report/routes/sources.py`, find the route handler that calls `_apply_import_impacts`. Add `BackgroundTasks` import and parameter:

```python
from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
```

Add `background_tasks: BackgroundTasks` to the route function signature, and update the call:

```python
# Before:
impact_result = state["_apply_import_impacts"](conn, summary, requested_by=actor)

# After:
impact_result = state["_apply_import_impacts"](
    conn,
    summary,
    requested_by=actor,
    background_tasks=background_tasks,
    config=config,
)
```

Also add `config=Depends(get_config)` to the route signature if not already there.

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_source_imports.py -v
pytest --tb=short -q
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add report/routes/sources.py
git commit -m "feat: CSV import triggers async in-process regeneration via BackgroundTasks"
```

---

## Task 6: Mutation routes — synchronous engine call on every change

**Files:**
- Modify: `report/routes/property_routes.py`

### Context

Every mutation route currently calls `state["mark_report_month_stale"]`. Replace with `state["generate_report_in_process"]`. The route already has `conn` and `config` available via `Depends`.

Routes to update (all are POST, all redirect to property page after saving):

| Route | Months to regenerate |
|-------|---------------------|
| `reservation_override` (line ~511) | `(slug, year, month)` |
| `reservation_override_revert` (line ~528) | `(slug, year, month)` |
| `reservation_move` (lines ~565-566) | `(slug, year, month)` AND `(slug, target_year, target_month)` |
| `reservation_move_revert` (lines ~587-591) | `(slug, year, month)` AND assignment target if exists |
| `reservation_exclude` (line ~614) | `(slug, year, month)` |
| `reservation_reinstate` (line ~633) | `(slug, year, month)` |
| `expense_add` (line ~206) | `(property_slug, year, month)` |
| `expense_edit` (line ~246) | `(property_slug, year, month)` |
| `expense_delete` (line ~263) | `(expense.property_slug, expense.year, expense.month)` |
| `property_unlock_month` (line ~445) | `(slug, year, month)` |

Also: **remove the `property_generate_month` route entirely** (the `/property/{slug}/{year}/{month}/generate` POST endpoint).

The `generate_report_in_process` function needs `config`. All routes that don't currently have `config=Depends(get_config)` in their signature need it added.

The expense routes use `property_slug` as the slug variable name (not `slug`).

- [ ] **Step 1: Add `generate_report_in_process` to state in `register()` at the bottom of `property_routes.py`**

At the bottom of `register()`, find the `state.update({...})` call and add:
```python
"generate_report_in_process": generate_report_in_process,
```

Also add the import at the top of the file:
```python
from report.engine import generate_report_in_process
```

- [ ] **Step 2: Update `reservation_override` route**

Find (line ~511):
```python
state["mark_report_month_stale"](conn, slug, year, month)
state["_set_flash"](request, "success", "Úprava byla uložena.")
return RedirectResponse(...)
```

Replace with:
```python
try:
    state["generate_report_in_process"](conn, slug, year, month, config)
except Exception as _exc:
    state["mark_report_month_stale"](conn, slug, year, month)
state["_set_flash"](request, "success", "Úprava byla uložena.")
return RedirectResponse(...)
```

- [ ] **Step 3: Update `reservation_override_revert` route**

Find (line ~528):
```python
state["mark_report_month_stale"](conn, slug, year, month)
```

Replace with:
```python
try:
    state["generate_report_in_process"](conn, slug, year, month, config)
except Exception:
    state["mark_report_month_stale"](conn, slug, year, month)
```

Add `config=Depends(get_config)` to the route signature if missing.

- [ ] **Step 4: Update `reservation_move` route**

Find (lines ~565-566):
```python
state["mark_report_month_stale"](conn, slug, year, month)
state["mark_report_month_stale"](conn, slug, target_year, target_month)
```

Replace with:
```python
for _y, _m in [(year, month), (target_year, target_month)]:
    try:
        state["generate_report_in_process"](conn, slug, _y, _m, config)
    except Exception:
        state["mark_report_month_stale"](conn, slug, _y, _m)
```

Add `config=Depends(get_config)` to the route signature.

- [ ] **Step 5: Update `reservation_move_revert` route**

Find (lines ~587-591):
```python
state["mark_report_month_stale"](conn, slug, year, month)
if assignment:
    state["mark_report_month_stale"](
        conn, slug, assignment["target_year"], assignment["target_month"]
    )
```

Replace with:
```python
months_to_regen = [(year, month)]
if assignment:
    months_to_regen.append((assignment["target_year"], assignment["target_month"]))
for _y, _m in months_to_regen:
    try:
        state["generate_report_in_process"](conn, slug, _y, _m, config)
    except Exception:
        state["mark_report_month_stale"](conn, slug, _y, _m)
```

Add `config=Depends(get_config)` to the route signature.

- [ ] **Step 6: Update `reservation_exclude` and `reservation_reinstate` routes**

For each, replace `state["mark_report_month_stale"](conn, slug, year, month)` with:
```python
try:
    state["generate_report_in_process"](conn, slug, year, month, config)
except Exception:
    state["mark_report_month_stale"](conn, slug, year, month)
```

Add `config=Depends(get_config)` to each route signature.

- [ ] **Step 7: Update expense routes (`expense_add`, `expense_edit`, `expense_delete`)**

Each currently calls `state["mark_report_month_stale"](conn, property_slug, year, month)`. Replace with the same try/except pattern. Note: `expense_delete` gets `property_slug` from `expense["property_slug"]`, `year` from `expense["year"]`, `month` from `expense["month"]`.

Add `config=Depends(get_config)` to all three expense route signatures.

- [ ] **Step 8: Update `property_unlock_month` route**

After the unlock call, add engine regen:
```python
# existing unlock logic...
try:
    state["generate_report_in_process"](conn, slug, year, month, config)
except Exception:
    pass  # unlock succeeded even if regen fails
```

Add `config=Depends(get_config)` to the route signature.

- [ ] **Step 9: Remove `property_generate_month` route**

Delete the entire `@app.post("/property/{slug}/{year}/{month}/generate")` handler (lines ~267–307). Also remove it from the `state.update({...})` call at the bottom of `register()`.

- [ ] **Step 10: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass (the tests for `property_generate_month` in `test_web_generation.py` will need updating — see Task 8)

- [ ] **Step 11: Commit**

```bash
git add report/routes/property_routes.py
git commit -m "feat: all mutation routes trigger synchronous in-process regeneration"
```

---

## Task 7: UI — remove generate button, add "last updated" label

**Files:**
- Modify: `templates/partials/property_intro.html`

### Context

The generate button is rendered at line ~173–183. The `generation_job` context variable and job status banners (RUNNING/PENDING spinner, FAILED error) should stay — they're still relevant for async CSV-import-triggered generation.

The `month_state.last_generated_at` is an ISO datetime string like `"2026-04-06T14:23:11"`. Format it as e.g. "Aktualizováno: 6. 4. 2026 14:23".

- [ ] **Step 1: Find the generate button section in `property_intro.html`**

```bash
grep -n "generate\|Přegenerovat\|Vygenerovat\|Generuje" templates/partials/property_intro.html
```

- [ ] **Step 2: Remove the generate button block**

Find and delete the entire block that renders the generate button:
```html
{% if month_state.status != 'LOCKED' and data_exists and not (generation_job and generation_job.status in ['PENDING', 'RUNNING']) %}
<form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/generate">
  {{ csrf_input(request) }}
  <button type="submit" class="btn btn-primary btn-sm">
    ...Přegenerovat / Vygenerovat...
  </button>
</form>
{% elif generation_job and generation_job.status in ['PENDING', 'RUNNING'] %}
<span class="btn btn-ghost btn-sm" style="cursor:default;color:var(--blue);">Generuje se…</span>
{% endif %}
```

Keep the `{% if generation_job and generation_job.status in ['PENDING', 'RUNNING'] %}` spinner banner higher up (the full-width notification div with the spinning icon). Only the button/link at the bottom header area is removed.

- [ ] **Step 3: Add "last updated" label near the month heading**

In the header area where the month/year is displayed, add after the existing date/lock info:

```html
{% if month_state.last_generated_at %}
<span style="font-size:11px;color:var(--text-300);margin-left:8px;">
  Aktualizováno:
  {% set _ts = month_state.last_generated_at[:16].replace('T', ' ') %}
  {{ _ts }}
</span>
{% endif %}
```

- [ ] **Step 4: Verify template renders without errors**

```bash
pytest tests/test_web_generation.py -v -k "template or render"
```

- [ ] **Step 5: Commit**

```bash
git add templates/partials/property_intro.html
git commit -m "ui: remove manual generate button; add last-updated timestamp"
```

---

## Task 8: Update tests for new generation path

**Files:**
- Modify: `tests/test_web_generation.py`

### Context

Several tests in `test_web_generation.py` test the old subprocess-based `/generate` route which no longer exists. These tests need to be either deleted (if they test removed functionality) or updated (if they test logic that still exists in a new form).

Tests to **delete** (test a removed route/function):
- `test_run_report_generation_invokes_cli` — tests `_run_report_generation` (old subprocess path)
- `test_run_report_generation_raises_http_exception_on_failure`
- `test_start_report_generation_runner_invokes_background_process`
- `test_property_generate_redirects_with_info_flash_when_job_is_already_running`
- `test_property_generate_redirects_with_success_flash_and_starts_background_job`
- `test_property_generate_marks_job_failed_when_background_start_fails`

Tests to **keep** (still valid):
- All other tests (login, bulk generation, dashboard, property detail, etc.)

New tests to **add** (replace the deleted ones):
```python
def test_engine_is_called_on_override_save(monkeypatch):
    """Saving an override triggers in-process generation synchronously."""
    generated = []

    monkeypatch.setattr(
        "report.routes.property_routes.generate_report_in_process",
        lambda conn, slug, year, month, config, **kw: generated.append((slug, year, month)) or {"rows_count": 0},
    )
    # ... set up minimal monkeypatches for the override route ...
    # This test verifies generate_report_in_process is called, not the implementation


def test_import_csv_queues_background_generation(monkeypatch):
    """CSV import adds background tasks for affected months."""
    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    queued = []
    original_add = bg.add_task
    bg.add_task = lambda fn, *args, **kw: queued.append(args) or original_add(fn, *args, **kw)

    # Call _apply_import_impacts with a summary that has affected_month_keys
    import report.web as web_module
    summary = {
        "source_type": "airbnb",
        "affected_month_keys": [("test_prop", 2026, 3)],
        "import_run_id": 1,
        "is_duplicate": False,
    }
    monkeypatch.setattr(web_module, "get_report_month_state", lambda *a: {"status": "OPEN"})
    monkeypatch.setattr(web_module, "mark_report_month_stale", lambda *a: None)
    monkeypatch.setattr(web_module, "_create_import_impact_notification", lambda **kw: None)
    monkeypatch.setattr(web_module, "_db_path_for_connection", lambda c: "/tmp/test.db")

    from report.db import get_connection
    conn = get_connection(":memory:")
    try:
        result = web_module._apply_import_impacts(
            conn, summary,
            requested_by="test",
            background_tasks=bg,
            config={},
        )
    finally:
        conn.close()

    assert len(queued) == 1
    assert queued[0][1] == "test_prop"  # slug
    assert result["auto_started"] == [("test_prop", 2026, 3)]
```

- [ ] **Step 1: Delete obsolete tests and add new ones**

Edit `tests/test_web_generation.py`:
- Remove the 6 tests listed above
- Add the 2 new tests above

- [ ] **Step 2: Run updated test file**

```bash
pytest tests/test_web_generation.py -v
```
Expected: all remaining tests PASS

- [ ] **Step 3: Run full suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_web_generation.py
git commit -m "test: update web generation tests for in-process engine path"
```

---

## Final verification

- [ ] **Run full test suite one last time**

```bash
pytest --tb=short -q
```
Expected: all tests pass

- [ ] **Manual smoke test** (if app is available)

1. Start app: `uvicorn report.web:app --reload --port 8000`
2. Open a property page — confirm no "Přegenerovat" button
3. Save an override on a reservation — page should redirect back with fresh data (no spinner needed)
4. Import a CSV file — spinner should appear, then data updates after a few seconds
5. Check app logs — confirm "Generated jicinska 3/2026: N rows" message appears

---

## Notes for the engineer

**What is NOT changed in this plan:**
- `/months/generate-all` (inventory page bulk generation) — still uses subprocess. Out of scope.
- `report/main.py` — still fully functional for CLI use (`python -m report.main`). Excel still written there.
- `report/generation_job_runner.py` and `report/bulk_generation_runner.py` — unchanged, still used by bulk generation.
- The RUNNING/FAILED generation job banners in `property_intro.html` — kept for async CSV import path.

**Import circular dependency risk:**
`engine.py` imports `_build_adjustment_reservation` from `report.main`. This creates a dependency on the CLI entry point. If this causes issues (e.g. circular imports), move `_build_adjustment_reservation` to `report/engine.py` itself — it's a pure function with no dependencies beyond basic types.
