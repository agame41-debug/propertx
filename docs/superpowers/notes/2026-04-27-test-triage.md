# Triage 2026-04-27

## AI-tells / design-quality follow-ups

Found by `impeccable` BAN 1 audit (`grep border-(left|right): [2-9]px`):

- **[templates/audit.html:18](templates/audit.html:18)** — `<div class="card" style="margin-bottom:24px;border-left:3px solid var(--color-red);">` — classic "left-stripe alert card" AI-tell. Replace with full border + soft tinted background, or with leading icon + bolder typography. Not blocking — fix in a separate UI-polish session.

---

# Test triage 2026-04-27

Snapshot of pytest baseline after hygiene round (engine-unification + bank-match-ownership fix shipped).

**Baseline:** 408 passed / **16 failed**.

The 16 remaining failures are pre-existing — they predate the current refactor cycle (the engine-unification plan recorded a baseline of "295 passed / 26 failed"). They are documented here so the next session can pick them up cold.

## Group A — Checkin parser regression (8 tests in `tests/test_checkin.py`)

Failing tests:
- `test_load_checkin_groups_parses_semicolon_export_and_applies_rules`
- `test_apply_checkin_city_tax_overrides_matches_by_property_dates_and_guest_name`
- `test_analyze_import_delta_for_checkin_counts_grouped_reservations`
- `test_import_uploaded_source_persists_checkin_groups_in_sqlite`
- `test_apply_checkin_city_tax_overrides_returns_audit_records`
- `test_cross_month_checkin_group_is_available_via_overlap_lookup`
- `test_latest_only_checkin_rows_prefer_newest_active_source`
- `test_missing_age_keeps_row_under_review`

Symptom: `assert [] == ['chk-002', 'chk-100']` — the checkin parser returns an empty list for fixtures that previously produced rows.

Root-cause hypothesis: `report/checkin.py:95` now warns `Checkin file ... has unexpected header: [...]` for the test CSV header `['Property Name', 'Full Name', 'Nationality', 'ID Type', ...]` and rejects all rows. Either the expected-header set was tightened, or test fixtures were never updated to include a column the parser now requires. **Decide:** is this a real production-data regression or only a test-fixture drift?

## Group B — `report/db_controls.py` API shape change (2 tests in `tests/test_controls.py`)

Failing tests:
- `test_create_and_get_month_assignment`
- `test_get_codes_assigned_to_month`

Symptom: `assert 'HMA001' in [{'actor': 'admin', 'confirmation_code': 'HMA001', ...}]` — test compares a string to a list of dicts.

The function under test now returns `list[dict]` with metadata; the test was written against `list[str]`. Cheap fix: change asserts to `assert any(r['confirmation_code'] == 'HMA001' for r in result)`.

## Group C — Booking verifier semantics (4 tests in `tests/test_verifier.py`)

Failing tests:
- `TestVerifyReservation::test_booking_does_not_use_inferred_city_tax_for_matching`
- `TestVerifyReservation::test_booking_uses_hostify_city_tax_even_when_inferred_differs`
- `TestVerifyReservation::test_booking_matching_uses_hostify_city_tax_not_checkin_override`
- `TestMonthAssignment::test_airbnb_long_stay_checkout_in_next_month`

Symptom: `assert 'MATCHED' == 'ROZDÍL'` (or vice versa). Tests encode the rule "Booking matching MUST use Hostify city_tax, not the checkin override / not the inferred 2 EUR/person/night". The verifier currently disagrees with the test.

Decide which side is canonical. The README rule (line 246) says "Booking porovnání s CSV: ... systém odečítá paušálně 2 EUR za osobu za noc před porovnáním." This is the inference path the tests forbid in three of four cases — looks like the policy moved and the tests didn't, OR tests are correct and verifier regressed.

## Group D — Import pipeline (2 tests in `tests/test_source_imports.py`)

Failing tests:
- `test_checkin_backfill_materializes_legacy_blob_only_source`
- `test_import_local_file_uses_full_import_pipeline_for_checkin`

Same root cause as Group A — checkin parser rejecting test CSV header.

---

## What was fixed in the hygiene round (already merged context — not for next session)

- Removed dead tests in `tests/test_web_generation.py` for `property_preview_month` / `property_download_month` / `_enqueue_report_generation` (engine-unification deleted these symbols).
- Adapted 5 `tests/test_web_generation.py` mocks to current contracts: `_admin_request()` helper providing `request.state.user`, `request.headers`, plus the `hostify_listing_names` Form arg.
- Added `tests/test_source_imports.py::test_apply_import_impacts_autostarts_open_months_and_notifies_locked` patch to monkeypatch `threading.Thread` + `_db_path_for_connection` (the function moved from `_enqueue_report_generation` to inline daemon-thread regen).
- Updated `_start_bulk_generation_runner` test to expect `python -u` flag.
