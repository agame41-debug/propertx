# Synthetic Row Control & Bank Match Integrity

## Problem

Synthetic report rows (AirCover `__AC`, adjustments `__ADJ`, splits `__SP`) are recreated from CSV source data on every generation. This conflicts with manual operations (move, split, exclude):

1. **Duplication**: AirCover appears in both parent's month AND payout-date month when they differ
2. **Double bank match**: one bank payment shows DORAZILO in two months simultaneously
3. **Move ignored**: moving a synthetic row to another month fails because regeneration recreates it from CSV
4. **No bank integrity**: nothing prevents the same `tx_key` from being matched to rows in multiple months

## Design Decisions

- **Idempotent engine (no new registry)**: engine uses strict deterministic rules instead of a separate ownership table. Less state to sync, engine remains source-of-truth.
- **Payout-date window is the sole placement rule**: no bypasses for `parent_in_current` or other heuristics. One item = one month, always.
- **Bank control via existing `payout_batch_bank_matches`**: add `slug`/`year`/`month` to track ownership. No new tables.
- **Per-object regeneration on move**: regenerate only the affected slug for both source and target months.

## Specification

### 1. Idempotent Engine Rules

**Placement**: all synthetic types use `_payout_date_in_window()` as the sole criterion. Window = `(cutoff_day+1 of current month)` to `(cutoff_day of next month)`. No exceptions.

**Move override**: `reservation_month_assignments` overrides the window. Engine hides the row in the original month via `hidden_confirmation_codes` and creates it in the target month via existing `moved_in` logic. Works identically for `__AC`, `__ADJ`, `__SP`.

**Deduplication safety check**: before appending any synthetic row, engine checks `report_rows` — if the same `confirmation_code` already exists for another month of the same slug, skip creation. This catches edge cases where window boundaries overlap.

### 2. Bank Match Integrity

**Schema change**: add `slug TEXT`, `year INTEGER`, `month INTEGER` columns to `payout_batch_bank_matches` via migration.

**Write on generation**: when engine matches a bank transaction, record `(slug, year, month)` alongside the existing `(channel, batch_ref, tx_key)`.

**Check before DORAZILO**: before assigning DORAZILO to a row, engine queries: does this `(channel, batch_ref, tx_key)` combination already have a match record for a different `(slug, year, month)`? If yes — row gets `CHYBÍ` with comment "Platba přiřazena k [MM/YYYY]".

**Cleanup on regeneration**: before generating a month, delete matches `WHERE slug=? AND year=? AND month=?` to ensure fresh state. Then write new matches during generation.

**Priority**: sequential generation processes months chronologically. Earlier month records its matches first, later month sees them as taken. This is deterministic.

### 3. Unified Synthetic Row Handling

All three synthetic types (`__AC`, `__ADJ`, `__SP`) share the same post-creation flow:

1. Check `hidden_confirmation_codes` — skip if moved out of this month
2. Check `_payout_date_in_window` — skip if payout date outside window (unless moved in)
3. Check deduplication — skip if code exists in `report_rows` for another month

Bank fallback on `bank_index_full` applies uniformly to all synthetic types (condition: `is_payout_adjustment or is_split_transaction or is_aircover`).

Move assignments work identically: the `is_adjustment` field in `reservation_month_assignments` is treated as `is_synthetic` — any code with `__` suffix follows the same move logic.

### 4. Dual-Month Regeneration on Move

When a row is moved from month A to month B:

1. Route handler calls `generate_report_in_process(conn, slug, year_A, month_A, config)` first (earlier month)
2. Then calls `generate_report_in_process(conn, slug, year_B, month_B, config)` (later month)

Chronological order ensures `get_report_row_by_code()` in the later month sees up-to-date data from the earlier month.

Same logic for revert: regenerate both months in chronological order.

Split/merge and exclude/reinstate: regenerate only the current month (no cross-month effect).

## Files to Change

| File | Changes |
|---|---|
| `report/db.py` | Migration: add `slug`, `year`, `month` columns to `payout_batch_bank_matches`. Add dedup query helper. |
| `report/bank.py` | Write slug/year/month on match. Check "already matched elsewhere" before DORAZILO. Accept slug/year/month params. Cleanup matches before generation. |
| `report/engine.py` | Unify synthetic flow: strict `_payout_date_in_window` for all types, `hidden_confirmation_codes` check for all types, deduplication check. Remove `parent_in_current` bypass. Call bank cleanup before generation. |
| `report/routes/property_routes.py` | Move/revert handlers: regenerate both months of the same slug in chronological order. |
