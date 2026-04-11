# Architecture Changelog

This document records the architectural changes already implemented during the roadmap work.

It is not a plan.

It describes the delivered architecture after the completed work in Phase 1, Phase 2, Phase 3, and the hardening pass completed today.

## 2026-04-10
Recent operational/documented changes

### Marriott / HVMB source support

- Hostify child listing aliases are now first-class runtime identity, so one report object can absorb reservations from multiple Hostify child listings, including Marriott.
- In the current Hostify dataset, Marriott arrives as source `HVMB`.
- UI now maps `HVMB` to the human label `Marriott`, while internal raw source values remain unchanged.
- Marriott reservations are intentionally allowed to flow through the report pipeline even without a dedicated Marriott CSV importer; until that importer exists, they typically remain `CHYBÍ_V_CSV`.

### Import audit trail expansion

- Audit trail is no longer limited to manual user edits.
- `import_runs` are now exposed as audit events with source type, source document, affected property-months, and orchestration outcomes.
- This makes post-import regeneration and locked-month side effects reviewable after the fact.

### Calculation contract corrections

- Airbnb payout and cleaning continue to use the Airbnb batch-implied rate.
- Airbnb commission now uses CNB rate on reservation date.
- Booking verification now compares CSV against Hostify payout after subtracting raw Hostify `city_tax_eur`.
- `Balíčky` now use the city-tax/checkin guest count rather than raw occupancy.
- `Cena ubytování` is now clamped at zero.
- Manual `payout_czk` overrides now also recalculate dependent `cena_ubytovani_czk`.

### Bulk-generation runner hardening

- The Windows launcher now starts the web server detached by default instead of tying server lifetime to the launcher console window.
- Stale sequential bulk-generation runs are still auto-expired, but stale UI residue is reduced:
  finished/failed runs no longer keep showing an old `current_slug` as if that object caused the failure.

## Phase 1
Business parity and month lifecycle

### Delivered architectural change

Phase 1 moved the system from “CLI does the real work, web shows a simplified view” into a single month-lifecycle model with canonical financial meaning.

The key decision was to stop letting the web invent its own financial summary and instead make one summary service the canonical business layer.

### Core code references

- Canonical summary builder:
  [report/summary.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/summary.py)
- Web now consuming summary instead of local arithmetic:
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Excel summary consuming the same summary layer:
  [report/excel.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/excel.py)
- CLI generation path writing persisted month state after generation:
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- Persistent month-state schema and helpers:
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

### Concrete runtime contracts introduced

- `report_month_state` became the persistent month lifecycle table in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- The month-state API was formalized through:
  `get_report_month_state()`,
  `get_report_month_states()`,
  `set_report_month_locked()`,
  `touch_report_month_generation()`,
  `mark_report_month_stale()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Web month access now enforces lock semantics through `_ensure_month_open()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Dashboard month-state projection is centralized in `_build_dashboard_maps()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)

### What this changed architecturally

- Financial meaning is no longer duplicated between web and Excel.
- Month state is no longer implicit in runtime flow.
- Recalculation/read-only behavior became a persisted domain rule instead of “whatever the current code path allows”.

### UI surfaces changed in this phase

- Month-state and stale visibility on the dashboard:
  [templates/dashboard.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/dashboard.html)
- Locked/open state and summary parity on property page:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

## Phase 2
DB-first configuration and listing identity

### Delivered architectural change

Phase 2 moved operational configuration out of mutable JSON and into durable DB-backed runtime config, while preserving JSON as bootstrap/fallback.

The most important change was not storage by itself.

It was making object identity and alias resolution historical and month-aware.

### Core code references

- DB-first runtime config loader:
  `load_runtime_config()` in [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py)
- JSON-to-DB sync path:
  `sync_property_to_db()` and `sync_json_config_to_db()` in [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py)
- Alias-aware property assembly:
  `_build_property_from_db()` in [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py)
- Month-context alias resolution:
  `_get_alias_rows()`,
  `_resolve_multi_alias_values()`,
  `_resolve_scalar_alias_value()`,
  `get_hostify_listing_names()`,
  `get_airbnb_listing_names()`,
  `get_booking_config()`
  in [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py)

### Persistent data model added

- `report_objects`
- `report_object_channel_config`
- `report_object_aliases`

All three live in the schema of
[report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

### Web editing path changed

- Client/config save path moved into DB-backed persistence in
  `client_save()` inside [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Client/config editing UI now reads/writes DB state through
  [templates/client.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/client.html)
- One-time migration entry point added in
  [report/config_migration.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config_migration.py)

### What this changed architecturally

- Runtime config is no longer a mutable file contract.
- Alias history is now part of the application model, not a manual convention.
- Historical month behavior can survive naming changes across Airbnb, Booking, and Hostify.
- Alias cutover semantics are now explicit at the day boundary instead of ambiguous within the same month.
  Code anchors:
  `_alias_valid_to_before()` and `set_report_object_aliases()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

## Phase 3
Web generation, source management, preview, and import impact orchestration

### Delivered architectural change

Phase 3 transformed the web app from a passive viewer into the primary operational control surface.

This happened in several steps:

1. generation became a persisted background workflow
2. source files became a DB-backed archive
3. imports gained duplicate and delta semantics
4. preview became an explicit execution path
5. imports started causing runtime month effects under lifecycle rules

### 3.1 Background generation

#### Core code references

- Background job schema:
  `report_generation_jobs` in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Background job API:
  `create_report_generation_job()`,
  `get_active_report_generation_job()`,
  `get_latest_report_generation_job()`,
  `set_report_generation_job_running()`,
  `finish_report_generation_job()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Detached runner process:
  [report/generation_job_runner.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/generation_job_runner.py)
- Web orchestration helpers:
  `_start_report_generation_runner()` and `_enqueue_report_generation()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Web route starting generation:
  `property_generate_month()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)

#### Architectural effect

- Generation is no longer tied to HTTP request latency.
- Job state is durable and inspectable.
- The web app now coordinates generation instead of blocking on it.

### 3.2 Source archive and import runs

#### Core code references

- Archive table:
  `source_files` in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Import run table:
  `import_runs` in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Archive CRUD and listing:
  `import_source_file_with_result()`,
  `get_source_file()`,
  `list_source_files()`,
  `set_source_file_active()`,
  `log_import_run()`,
  `list_import_runs()`,
  `update_import_run_summary()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Source registry service:
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Legacy CLI source tooling still supported in:
  [report/sources.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/sources.py)

#### Web entry points

- Source archive screen:
  `sources_page()` in [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Source upload route:
  `sources_import()` in [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Source activation/deactivation:
  `source_activate()` and `source_deactivate()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Source download:
  `source_download()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- UI screen:
  [templates/sources.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/sources.html)

#### Architectural effect

- Raw source files are now part of durable operational memory.
- The app can work from DB-backed source artifacts instead of assuming local files remain in place forever.
- Import history is now explicit and reviewable.

### 3.3 Duplicate and delta semantics

#### Core code references

- Duplicate detection and archive persistence:
  `import_source_file_with_result()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Source archive uniqueness index:
  `idx_source_files_type_sha256_unique` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Existing archive cleanup during migration:
  `_dedupe_source_files_by_type_sha256()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Per-source delta builders:
  `_airbnb_delta_summary()`,
  `_booking_delta_summary()`,
  `_bank_delta_summary()`,
  `_accounting_delta_summary()`,
  `analyze_import_delta()`
  in [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Import service that combines archive persistence and delta result:
  `import_uploaded_source()` in
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)

#### Architectural effect

- Import feedback is no longer byte-only or file-only.
- Duplicate semantics are now scoped to `(source_type, sha256)` instead of raw blob identity across all source classes.
- Archive writes are now concurrency-safe at the DB boundary instead of relying on application-level check-then-insert ordering.
- The system now distinguishes archive duplication from business delta.
- Import runs now carry operational meaning beyond “a file was stored”.

### 3.4 Preview and download evolution

#### Core code references

- Preview pipeline:
  `build_property_preview()` in
  [report/preview_service.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/preview_service.py)
- Preview route:
  `property_preview_month()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Property page controls:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

#### Architectural effect

- Preview existed as a first-class application path during Phase 3 implementation.
- After today’s hardening pass, preview is intentionally removed from the normal operational workflow and redirected back to the persisted month page.
- The primary operational contract is now:
  generate first, then inspect/download persisted results.
- This avoids synchronous live fetch behavior and removes a misleading quasi-read-only surface from the main UI.

### 3.5 Download path

#### Core code references

- Persisted report history:
  `report_history` and `get_report_history()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Excel writer:
  `write_property_report()` in
  [report/excel.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/excel.py)
- Latest report lookup and download route:
  `_latest_report_for_month()` and `property_download_month()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)

#### Architectural effect

- Web download now consumes persisted report artifacts instead of rebuilding ad hoc files.
- Generation and download have become two separate concerns.

### 3.6 Import impact orchestration

#### Core code references

- Slug-aware impact mapping after import:
  `_match_airbnb_slug()`,
  `_match_booking_slug()`,
  `_estimate_report_month_from_bank_date()`
  in [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Affected month-key emission in delta summaries:
  `_airbnb_delta_summary()`,
  `_booking_delta_summary()`,
  `_bank_delta_summary()`
  in [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Post-import orchestration:
  `_apply_import_impacts()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Locked-month notification storage:
  `report_month_notifications`,
  `create_report_month_notification()`,
  `list_report_month_notifications()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Property-page visibility of month notifications:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)
- Source-page visibility of impact results in import runs:
  [templates/sources.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/sources.html)
- Source-file activation/deactivation reusing the same impact semantics:
  `_apply_source_file_state_change_impacts()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Import-run summary persistence after orchestration:
  `update_import_run_summary()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

#### Behavioral rule now implemented

- If an import affects an open month with an existing report, background regeneration is started automatically.
- If an import affects an open month without a generated report, the month is recorded as impacted but not auto-generated blindly.
- If an import affects a locked month, the month is not rewritten and a persisted notification is created.
- If source activation/deactivation changes the effective input set, the same month-impact rules are applied instead of silently flipping a bit.
- If post-import orchestration fails after the file is already archived, the import remains committed and the failure is recorded in `import_runs.summary_json` instead of pretending the whole request rolled back.

#### Architectural effect

- Import side effects are now mediated by lifecycle state instead of file arrival alone.
- Locked-month immutability is enforced at orchestration level, not just at manual generation time.
- Import run history now captures what the system actually did after ingesting the file.

### 3.7 Summary parity hardening

#### Core code references

- Canonical summary builder:
  `build_report_summary()` in
  [report/summary.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/summary.py)
- Resolved transferred rows:
  `get_resolved_pending_payments_for_month()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Property page now passing transferred rows into summary:
  `property_detail()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)

#### Architectural effect

- The persisted property page no longer diverges from Excel/canonical summary on months with transferred pending payments.
- Summary parity is now maintained not only across web and Excel, but also across normal month pages and lifecycle-generated carry-over rows.

## Hardening Pass
Review-driven fixes completed today

### Delivered architectural change

Today’s work was not a new product phase.

It was a hardening pass that turned several “works in practice” behaviors into explicit contracts at the DB, lifecycle, and orchestration boundaries.

### 4.1 Explicit month data-state model

#### Core code references

- `data_state` persisted in `report_month_state`:
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- State constants:
  `MONTH_DATA_STATE_EMPTY`,
  `MONTH_DATA_STATE_READY`,
  `MONTH_DATA_STATE_GENERATED`,
  `MONTH_DATA_STATE_STALE`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- State transitions:
  `mark_report_month_has_data()`,
  `touch_report_month_generation()`,
  `mark_report_month_stale()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- CLI path marking a month as having data before generation:
  `mark_report_month_has_data()` call in
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- Dashboard/property data-presence projection consuming persisted state:
  `_build_dashboard_maps()` and `_month_has_data()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)

#### Architectural effect

- The system no longer has to infer “data exists but no report was generated yet” only from live Hostify counts.
- Month lifecycle now has a first-class data-readiness state in durable storage.
- Web visibility of actionable months remains stable even if live snapshot heuristics are incomplete.

### 4.2 DB-boundary lock enforcement

#### Core code references

- DB-level lock exception:
  `LockedReportMonthError` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Shared guard:
  `_assert_report_month_mutable()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Month-scoped write helpers now guarded in DB:
  `save_report_rows()`,
  `log_report_generated()`,
  `add_expense()`,
  `update_expense()`,
  `delete_expense()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

#### Architectural effect

- Lock semantics no longer rely only on web and CLI callers doing the right thing.
- Month immutability is now defended at the persistence helper layer for core month-scoped writes.
- This reduces the risk of future regressions from new routes, scripts, or internal call paths bypassing UI checks.

### 4.3 Generation workflow hardening

#### Core code references

- Automatic expiry of stale pending jobs:
  `expire_stale_report_generation_jobs()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Active-job lookup now performing recovery:
  `get_active_report_generation_job()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

#### Architectural effect

- A dead child process can no longer leave a month blocked forever in `PENDING`.
- Generation orchestration now has bounded recovery instead of manual operator cleanup.

### 4.4 Review-driven UX simplification

#### Core code references

- Preview route now redirecting back to the persisted month page:
  `property_preview_month()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Preview button removed from property page:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

#### Architectural effect

- The UI no longer advertises a path that encourages synchronous, non-persisted execution for a workflow that is operationally generation-first.
- The product surface now matches the actual business flow: fetch Hostify during generation, then work from persisted results.

### 4.5 Persisted report snapshot correctness

#### Core code references

- Month report-row persistence:
  `save_report_rows()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Month report-row reads:
  `get_report_rows()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Month regeneration path writing the persisted snapshot:
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)

#### Architectural effect

- Regeneration now replaces the persisted month snapshot instead of incrementally accumulating stale rows from older runs.
- `report_rows` is now treated as a full snapshot of a property-month, not as an append-friendly cache.
- This removes a class of bugs where old CSV-only reservations from unrelated months could remain visible after later regenerations.

### 4.6 Success-banner lifecycle

#### Core code references

- Short-lived success-banner visibility helper:
  `_show_recent_generation_success()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Property page success-banner rendering and dismiss behavior:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

#### Architectural effect

- Successful generation feedback is now treated as an ephemeral UI event instead of a persistent month-state banner.
- The banner is bounded both server-side and client-side:
  it renders only for a short window after `finished_at`, can be dismissed manually, and auto-hides after one minute.
- This keeps operator feedback visible without letting “success” become sticky stale state on later page loads.

### 4.7 Evidence hostů persistence and audit layer

#### Core code references

- Parsed guest-row and grouped-reservation persistence tables:
  `checkin_guest_rows`,
  `checkin_reservations`,
  `checkin_match_audit`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Import-time persistence helpers:
  `save_checkin_source_snapshot()`,
  `list_checkin_reservations()`,
  `get_active_checkin_reservation_ids()`,
  `replace_checkin_match_audit()`,
  `list_checkin_match_audit()`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Parser and matching engine extended for reusable storage/audit shape:
  `load_checkin_guest_rows()`,
  `prepare_checkin_groups_for_storage()`,
  `hydrate_checkin_groups_from_db()`,
  `apply_checkin_city_tax_overrides()`
  in [report/checkin.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/checkin.py)
- Import path now materializing Evidence hostů groups into SQLite:
  `import_uploaded_source()` and `_checkin_delta_summary()` in
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Generation path now consuming persisted Evidence hostů groups and replacing month audit snapshot:
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- Read-only review screen for matched / unmatched evidence:
  `property_guest_evidence()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
  and [templates/guest_evidence.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/guest_evidence.html)
- Property page entrypoint to the evidence review surface:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

#### Architectural effect

- Evidence hostů is no longer a transient blob-only overlay parsed ad hoc during generation.
- The system now has a durable relational layer for imported guest evidence, a month-scoped audit layer for overwritten city-tax counts, and an operator-facing screen for reviewing matched and unmatched evidence.
- City-tax verification is now explainable after the fact: not only whether a reservation was verified, but exactly which counts were overwritten and which evidence group drove the overwrite.

### 4.8 Checkin report correctness pass

#### Core code references

- Shared effective-status semantics used by month page and persisted history:
  `effective_verification_status()` and `count_effective_verification_statuses()` in
  [report/status.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/status.py)
- Web month-page display now consuming the shared helper:
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Persisted report-history counters now using the same effective status:
  `log_report_generated()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Dashboard month cards now recomputing visible counters from persisted `report_rows`:
  `_build_dashboard_maps()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Checkin report matcher now trusting persisted `property_slug` first and treating missing age as non-verified/non-taxable:
  `match_checkin_group_to_property()`,
  `_matched_row_payload()`,
  `load_checkin_groups()`
  in [report/checkin.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/checkin.py)
- Active Checkin report groups now loaded by month overlap and deduped to the newest active source per reservation id:
  `list_checkin_reservations()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
  and the generation use site in
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- Checkin report import delta now detecting changed groups and impacting all overlapped months:
  `_checkin_delta_summary()` in
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- Import path now transactional across `source_files`, parsed Checkin report state, and `import_runs`:
  `import_uploaded_source()` in
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
  plus `commit=False` support in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Legacy blob-only Checkin report archives now backfilled into parsed tables on DB open:
  `_backfill_checkin_source_snapshots()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Review screen now explicitly warns when the audit is stale relative to current active Checkin report files:
  `property_guest_evidence()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
  and [templates/guest_evidence.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/guest_evidence.html)

#### Architectural effect

- `MATCHED` now has one shared meaning across month rows, report history, and dashboard projections.
- Checkin report is no longer vulnerable to the two most dangerous drift modes:
  overlapping active files and cross-month reservations disappearing from generation.
- Legacy imported Checkin report files remain operational after the relational migration instead of requiring manual re-import.
- The import/archive layer for Checkin report now behaves as one transaction boundary instead of three loosely coupled commits.
- The operator-facing review screen now makes it explicit when the stored audit belongs to an older generation than the currently active Checkin report inputs.

### 4.9 Verification references added by tests

- Month-state and lock-boundary tests:
  [tests/test_month_state.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_month_state.py)
- Web generation/property-detail tests:
  [tests/test_web_generation.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_web_generation.py)
- Source import and source-toggle orchestration tests:
  [tests/test_source_imports.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_source_imports.py)
- Config alias cutover tests:
  [tests/test_config_db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_config_db.py)
- Evidence hostů parser, persistence, and audit tests:
  [tests/test_checkin.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_checkin.py)
  and [tests/test_source_imports.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_source_imports.py)

### 4.10 Bank transaction drill-down

#### Core code references

- Bank page now loading transaction drill-down from persisted batch/match tables:
  `_load_bank_rows_with_drilldown()` and `_filter_bank_rows()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Existing persistence layer reused instead of introducing a parallel bank UI model:
  `payout_batches`,
  `payout_batch_items`,
  `payout_batch_bank_matches`
  in [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Bank screen template now supports expandable transaction rows with reservation-level breakdown:
  [templates/bank.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/bank.html)

#### Architectural effect

- The bank page is no longer just a flat list of incoming payments.
- Operators can now inspect a matched bank transaction and see which payout batch it belongs to and which reservations/items are contained in that batch.
- The bank page now also acts as a filtered operational ledger:
  platform column, month-total amount, filtered subtotal, and filters by platform / match state / free-text search.
- This makes the bank screen a real drill-down surface built on the same persisted payout artifacts that generation already writes, instead of a separate ad hoc reconciliation view.
- Bank source imports now materialize transactions into `bank_transactions` immediately instead of waiting for the next report generation cycle, so the operational ledger stays aligned with the active archived bank source.

### 4.11 Booking transaction identity surfaced in bank UI

#### Core code references

- Booking bank rows now derive visible guest identity from persisted payout items first and fall back to Hostify reservation snapshots when item guest names are missing:
  `_load_bank_rows_with_drilldown()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Descriptor-reference fallback for Booking batches reuses the same normalization contract as reconciliation:
  `_normalize_booking_ref()` in
  [report/bank.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/bank.py)
  and `_load_bank_rows_with_drilldown()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Bank table now renders guest summaries inline for transaction-level scanning:
  [templates/bank.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/bank.html)
- Regression coverage for both explicit match and descriptor fallback:
  [tests/test_bank.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_bank.py)

#### Architectural effect

- Booking transactions in the bank ledger are no longer anonymous when the bank message itself does not contain a human-readable guest name.
- The UI now surfaces reservation identity from the persisted payout/Hostify graph instead of forcing the operator to expand every Booking transaction.
- Booking batch visibility remains aligned with the existing reconciliation model because the UI fallback uses the same descriptor-reference normalization rules as the matcher.
- Missing Booking guest names are now materialized into `payout_batch_items` from DB-backed reservation identity, so the bank ledger can recover names from persisted state instead of depending only on per-request enrichment.

### 4.12 Booking payout item identity materialization

#### Core code references

- Missing persisted Booking guest names are now backfilled into payout items from known reservation identity:
  `fill_missing_payout_item_guest_names()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Legacy databases now run a booking payout-item guest-name backfill on open:
  `_backfill_booking_payout_item_guest_names()` and `_run_migrations()` in
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- Main generation now materializes Booking guest identity immediately after payout-item persistence and again after Hostify snapshot persistence:
  [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- Regression coverage for DB-backed backfill and bank visibility:
  [tests/test_bank.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/tests/test_bank.py)

#### Architectural effect

- Booking guest identity is now a durable part of the payout-item layer, not only a presentation-time reconstruction.
- Older databases with blank Booking payout-item names recover that identity from `hostify_reservations` without manual repair.
- The bank screen can now rely on persisted payout-item state as the primary source of guest visibility.

### 4.13 Month UI consistency pass

#### Core code references

- Shared `Kč` formatting for web templates:
  `_fmt_czk()` in
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- Month page labels, bank-status wording, and simplified financial breakdown:
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)
- Bank ledger aligned to the same money formatting:
  [templates/bank.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/bank.html)
- Shortened tax-verification wording used by the month page:
  [report/status.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/status.py)

#### Architectural effect

- The month screen no longer mixes whole-number and decimal `Kč` formats.
- Financial breakdown now presents the main operator view in a simpler “bez DPH” style instead of splitting out extra DPH noise in the summary table.
- Bank-related wording on reservation rows and detail panels is now more explicit and consistent: `Stav banky` instead of mixed `Banka / Stav / Platba od platformy`.
- Checkin/tax verification text is shorter and less disruptive to row rhythm while preserving the review signal.

### 4.14 Verification normalization and detail-panel cleanup

#### Core code references

- Booking verification now uses inferred city tax before falling back to raw Hostify `city_tax_eur`:
  `verify_reservation()`,
  `_infer_booking_city_tax_eur()`,
  `_effective_booking_city_tax_eur()`
  in [report/verifier.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/verifier.py)
- Shared verification tolerance raised to one euro:
  `TOLERANCE_EUR` in [report/verifier.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/verifier.py)
- Cancelled reservations no longer accrue stay-derived costs:
  `calculate_row()` in [report/calculator.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/calculator.py)
- Cancelled reservations are now excluded from Checkin tax-verification semantics:
  `apply_checkin_city_tax_overrides()` in [report/checkin.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/checkin.py)
- Reservation detail drawer now distinguishes “included in payout” from real deductions:
  [templates/partials/reservation_detail.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/partials/reservation_detail.html)

#### Architectural effect

- Booking `ROZDÍL` is no longer dominated by raw Hostify city-tax noise when the application can infer a better tax amount from its own reporting rules.
- Verification status now intentionally ignores micro-drifts up to `1.00 EUR`, reducing low-signal operator noise without hiding material mismatches.
- Cancelled rows no longer look like stayed reservations in either calculation or Checkin verification.
- Reservation detail panels now present payout composition more honestly:
  included platform commission is informational, while only true downstream deductions are shown as calculation steps.

## Cross-phase structural outcome

After the implemented work, the system has these durable layers:

- configuration layer:
  DB-first, alias-aware, month-aware
  Code anchors:
  [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py),
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- month lifecycle layer:
  open/locked month state plus explicit data readiness and stale semantics
  Code anchors:
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py),
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- operational archive layer:
  imported raw files, import runs, source activation state
  Code anchors:
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py),
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- generation execution layer:
  background jobs with persisted status, detached runner, and stale-pending recovery
  Code anchors:
  [report/generation_job_runner.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/generation_job_runner.py),
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py),
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- persisted report inspection layer:
  generation-first month pages and downloadable Excel artifacts
  Code anchors:
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py),
  [report/excel.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/excel.py),
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- notification/event layer:
  month-scoped notices for locked-month import impacts
  Code anchors:
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py),
  [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)
- evidence verification layer:
  imported guest evidence, materialized evidence groups, and month-scoped overwrite audit
  Code anchors:
  [report/checkin.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/checkin.py),
  [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py),
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py),
  [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- DB mutation safety layer:
  lock-aware persistence helpers for month-scoped writes
  Code anchors:
  [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

## Remaining architectural work after these phases

The remaining foundational gaps are much smaller now.

What is left is mostly higher-level operational hardening:

- override auditability and revert semantics
- richer event trails beyond month notifications
- explicit role/actor separation
- stronger auth and deployment hardening
- deeper status-matrix formalization for review workflows
