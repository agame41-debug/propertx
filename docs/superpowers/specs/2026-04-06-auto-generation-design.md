# Auto-Generation Design

## Goal

Replace manual "Přegenerovat" button with fully automatic report generation. Every data change triggers an immediate recalculation. Generation runs in-process (no subprocess), skips Excel, reads all inputs from SQLite. Target: <500ms per property.

## Architecture

### In-process calculation engine

Extract the property calculation loop from `report/main.py` into a standalone callable:

```python
def generate_report_in_process(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    config: dict,
    *,
    cutoff_day: int = 7,
) -> dict  # returns {"rows_count": int, "status_counts": dict}
```

This function contains the same logic as `main.py` steps 4a–4h (filter reservations, apply assignments/exclusions, verify against CSV, calculate rows, save to DB) but:
- Called directly — no subprocess, no Python cold start
- Reads Hostify data from `hostify_reservations` SQLite table (not API)
- Reads CSV payout data from `payout_batch_items` / `payout_batches` SQLite tables (not raw CSV files). Bootstrap fallback: if `payout_batch_items` is empty for the relevant channel, falls back to loading from active `source_files` records (same as current `main.py` behaviour).
- Reads CNB rates from SQLite cache
- Does **not** write any Excel file
- Does **not** call `log_report_generated` with a file path (passes `file_path=""`)

Location: `report/engine.py` (new file). `report/main.py` refactored to call `engine.py` for CLI use (keeps Excel writing there).

### Trigger system — two paths

**Synchronous path** (user makes a single manual change):
- Route handler saves the change to DB
- Immediately calls `generate_report_in_process(conn, slug, year, month, config)`
- Redirects only after calculation completes (~300–500ms)
- User opens page and sees fresh data — no spinner needed

Applies to:
- Override saved or reverted (`/property/{slug}/{year}/{month}/override/...`)
- Month assignment saved or removed (`/property/{slug}/{year}/{month}/assign/...`)
- Exclusion saved or removed (`/property/{slug}/{year}/{month}/exclude/...`)
- Expense added, edited, or deleted (`/property/{slug}/{year}/{month}/expense/...`)
- Month unlocked (`/property/{slug}/{year}/{month}/unlock`)

**Asynchronous path** (bulk or background changes):
- Change is saved to DB, HTTP response returned immediately
- Background task calls `generate_report_in_process` for each affected (slug, year, month)
- UI shows existing "obnovляется…" spinner + 5s auto-refresh until done

Applies to:
- CSV import (Airbnb, Booking, Bank, Checkin) — existing `_apply_import_impacts`, refactored to call engine instead of subprocess
- Daily Hostify sync detecting changes

### Background Hostify sync

An `asyncio` periodic task started on app startup (in `report/web.py` lifespan handler):

- Runs every **24 hours**
- Fetches reservations from Hostify API for **5 months**: current month −1, current month, current month +1, +2, +3 (e.g. in April 2026: March, April, May, June, July)
- Upserts into `hostify_reservations` table (already exists)
- Compares fetched codes against stored snapshot; if any reservation added, removed, or changed → marks affected open months as STALE and queues async `generate_report_in_process` for each
- Locked months: marked as STALE with notification, not regenerated
- Runs silently — no user-visible notification unless there is a generation error

### Deduplication

A lightweight in-memory set `_generation_running: set[tuple[str, int, int]]` (slug, year, month) prevents concurrent generation of the same month. If a generation is already running for a month and another trigger arrives, the second trigger is debounced: it re-queues itself to run after the first completes.

The existing `report_generation_jobs` table is kept for audit history but is no longer used to gate execution (in-process jobs don't need a separate process record).

### Excel

- Web-triggered generation (`engine.py`) never writes Excel
- CLI (`python -m report.main`) still writes Excel as before — for manual exports if ever needed
- "Stáhnout Excel" download button removed from property page UI

### UI changes

- Remove "Přegenerovat" / "Vygenerovat" button from `property_intro.html`
- Remove "Generuje se…" disabled button state
- Keep the RUNNING/PENDING spinner banner (still needed for async CSV-import path)
- Keep the FAILED error banner
- Add "Aktualizováno: X minut zpět" label next to the month heading, showing `report_month_state.last_generated_at`
- Remove `/property/{slug}/{year}/{month}/generate` POST route (or keep as no-op for backwards compat, redirecting with info flash)

## Data flow after a manual override save

```
POST /property/jicinska/2026/3/override/save
  → save override to override_events
  → generate_report_in_process(conn, "jicinska", 2026, 3, config)
      → load hostify_reservations from DB
      → load payout data from payout_batch_items
      → load CNB rates from DB cache
      → apply assignments / exclusions / overrides
      → calculate rows
      → save_report_rows(conn, ...)
      → touch_report_month_generation(conn, ...)
  → redirect 303 → /property/jicinska/2026/3
  (page shows fresh data)
```

## Error handling

- If `generate_report_in_process` raises: catch exception, set flash error, redirect — same UX as today's failed generation banner
- If Hostify sync fails: log warning, skip regeneration for that cycle, retry next day
- If a month is LOCKED when a trigger fires: skip generation, create notification (same as current behavior)

## Tech stack

- `report/engine.py` — new module, callable generation function
- `report/web.py` — lifespan handler gets asyncio background task for Hostify sync
- `report/routes/property_routes.py` — all mutation routes call engine synchronously
- `report/routes/sources.py` — CSV import calls engine asynchronously via BackgroundTasks
- No new dependencies

## Out of scope

- Audit log / change diff history (separate spec)
- Real-time websocket push (polling every 5s is sufficient)
- Per-reservation incremental recalculation (full month recalc is fast enough at <500ms)
