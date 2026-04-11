# Rentero Implementation Plan

## 0. Purpose

This document translates [implementation-spec.md](../specs/implementation-spec.md) into an execution plan.

It is written for a developer who needs to:
- understand what to implement first,
- avoid breaking critical reporting behavior,
- know which modules to touch,
- know which data migrations are needed,
- and know how to verify each stage.

This plan is ordered by risk and dependency, not by convenience.

## 1. Delivery Strategy

The system already exists and is already used. That means the plan must optimize for:
- preserving current working behavior where correct,
- closing correctness gaps first,
- avoiding broad rewrites before contracts are stabilized,
- and moving toward a DB-first, web-first architecture incrementally.

### 1.1 Primary sequencing rule

Implement in this order:
1. Stabilize core business logic parity and month lifecycle
2. Introduce DB-backed operational state and migration-safe structures
3. Move key actions into the web UI
4. Add auditability and override safety
5. Expand usability and role support

### 1.2 Do not do first

Do not start with:
- visual redesign,
- deployment packaging,
- auth hardening,
- advanced role model,
- major framework refactor,
- bulk template cleanup.

Those are secondary until business correctness is locked down.

## 2. Project End State

At the end of this roadmap, the system should support:
- web-first report generation,
- web-first source imports,
- preview and Excel using the same logic,
- listing-based reporting with alias history,
- DB as durable operational memory,
- month lock/unlock lifecycle,
- full override audit trail,
- import delta feedback,
- and safe handling of new information after month lock.

## 3. Phase Overview

### Phase 1
Business parity and report lifecycle

### Phase 2
DB-first configuration and month state

### Phase 3
Web generation, import, and source management

### Phase 4
Overrides, restoration, and audit trail

### Phase 5
Role model, operational polish, and deployment-readiness

## 4. Phase 1: Business Parity and Month Lifecycle

### 4.1 Goal

Before adding more UI features, guarantee that:
- web and Excel mean the same thing financially,
- report generation logic is canonical,
- month closing semantics exist,
- and recalculation behavior is explicit.

### 4.2 Deliverables

- canonical financial summary service shared by CLI, Excel, and web
- month lock model
- stale/open month recalculation policy
- visibility of “data exists but report not generated”
- parity tests for preview/final/report summary

### 4.3 Main code areas

- [report/main.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/main.py)
- [report/calculator.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/calculator.py)
- [report/excel.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/excel.py)
- [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- [templates/dashboard.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/dashboard.html)
- [templates/property.html](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/templates/property.html)

### 4.4 Required changes

#### 4.4.1 Extract a canonical report summary service

Problem:
- web currently computes `gross/commission/net` locally,
- Excel uses a different richer summary model.

Action:
- create one canonical summary builder, for example `report/summary.py`
- summary input should be:
  - report rows
  - property config
  - expenses
  - transferred/pending context if needed
- summary output should include:
  - gross payout
  - accommodation income
  - rentero fee
  - VAT values
  - expenses
  - client net
  - bank-confirmed amount
  - pending amount

Consumers:
- Excel summary block
- web property page
- future preview API

#### 4.4.2 Introduce month state model

Need a persistent month state table, e.g.:
- `report_month_state`

Recommended fields:
- `slug`
- `year`
- `month`
- `status` (`OPEN`, `LOCKED`)
- `locked_at`
- `locked_by`
- `unlocked_at`
- `unlocked_by`
- `last_generated_at`
- `last_recalculated_at`
- `has_new_data_since_generation`
- `notes`

Rules:
- open month may recalculate automatically
- locked month is read-only
- only admin may unlock later

#### 4.4.3 Add dashboard generation state

Dashboard should distinguish:
- no data
- data exists but no generated report
- generated report exists
- generated report exists but new data arrived afterward
- month locked

At minimum:
- show `Vygenerovat` button if data exists but report does not
- show lock icon/badge for locked months
- show “new data” badge if open month became stale

### 4.5 Data migration for Phase 1

Add table:
- `report_month_state`

Optionally add derived flags to history/state:
- `is_locked`
- `has_new_data_since_generation`

### 4.6 Acceptance criteria for Phase 1

- web property summary equals Excel summary for the same month
- dashboard can show `Vygenerovat`
- month lock is persisted
- locked month becomes read-only in UI
- open month may still regenerate

## 5. Phase 2: DB-First Configuration and Listing Identity

### 5.1 Goal

Move the system away from mutable JSON as operational truth and toward DB-managed configuration with historical safety.

### 5.2 Deliverables

- DB-backed report object config
- alias management model
- historical alias support
- controlled JSON-to-DB migration

### 5.3 Main code areas

- [report/config.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/config.py)
- [config/properties.json](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/config/properties.json)
- [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)

### 5.4 Required data model

Recommended new tables:
- `report_objects`
- `report_object_aliases`
- `report_object_channel_config`

Suggested fields for `report_objects`:
- `slug`
- `display_name`
- `hostify_listing_id`
- `active`
- `created_at`
- `updated_at`

Suggested fields for `report_object_aliases`:
- `id`
- `report_object_slug`
- `channel`
- `alias_type`
- `alias_value`
- `valid_from`
- `valid_to`
- `is_active`
- `created_at`
- `updated_at`

Alias examples:
- channel=`airbnb`, alias_type=`listing_name`
- channel=`booking`, alias_type=`property_id`
- channel=`booking`, alias_type=`listing_nickname`
- channel=`hostify`, alias_type=`listing_nickname`

### 5.5 Required behavior

- new alias should be added, not overwrite historical one
- old alias should remain available for historical months
- system should resolve aliases valid for a month context

### 5.6 Migration strategy

Step 1:
- read from DB if config exists there
- fallback to JSON otherwise

Step 2:
- create admin migration tool or one-time script to import JSON config into DB

Step 3:
- web edits update DB

Step 4:
- JSON becomes seed/bootstrap, not runtime truth

### 5.7 Acceptance criteria for Phase 2

- UI edits do not require writing JSON
- aliases are stored in DB
- old aliases are not lost when new ones are added
- report object identity remains listing-based

## 6. Phase 3: Web Generation, Import, and Source Archive Management

### 6.1 Goal

Make the web app the real operational tool.

### 6.2 Deliverables

- generation from web
- preview from web
- source upload pages
- source archive page
- delta feedback on imports
- activation/deactivation of source files

### 6.3 Main code areas

- [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- [report/source_registry.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/source_registry.py)
- [report/sources.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/sources.py)
- [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- templates to add:
  - `imports.html`
  - `sources.html`
  - possibly `preview.html`

### 6.4 Web generation flow

Add endpoints like:
- `GET /property/{slug}/{year}/{month}/generate`
- `POST /property/{slug}/{year}/{month}/generate`
- `GET /property/{slug}/{year}/{month}/preview`
- `GET /property/{slug}/{year}/{month}/download`

Expected behavior:
- if month open: generation allowed
- if month locked: generation disabled unless admin unlocks first
- after generation: history and rows are persisted
- download should serve latest generated Excel or generate-export atomically, depending on chosen strategy

### 6.5 Source import flow

Add a dedicated import screen:
- upload by source type
- see archive entries
- see duplicate warning
- see delta summary

Expected delta examples:
- bank import: `+12 new transactions`
- Airbnb import: `+4 newly paid reservations discovered`
- Booking import: `+7 new payout rows, +2 new reservations`

### 6.6 Needed import metadata

Recommended new table:
- `import_runs`

Fields:
- `id`
- `source_type`
- `source_file_id`
- `imported_by`
- `imported_at`
- `duplicate_of_source_file_id`
- `new_rows_count`
- `new_transactions_count`
- `new_reservations_count`
- `summary_json`

### 6.7 Auto-recalculation behavior

If import affects open months:
- auto-recalculate affected months

If import affects locked months:
- do not mutate them
- create notification/event
- roll effect into next open month where business logic says so

### 6.8 Acceptance criteria for Phase 3

- user can import files in web
- duplicate imports show explicit feedback
- import result shows deltas
- user can generate from web
- user can download Excel from web

## 7. Phase 4: Overrides, Restore, and Audit Trail

### 7.1 Goal

Make manual corrections safe and reversible.

### 7.2 Deliverables

- reservation-level overrides
- object-level config overrides
- restore original calculations
- full audit logging

### 7.3 Main code areas

- [report/db.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/db.py)
- [report/web.py](/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315%20new/report/web.py)
- new template partials for edit/restore dialogs

### 7.4 Recommended schema additions

Current `manual_overrides` table is not enough because it does not store full before/after audit semantics.

Add or replace with:
- `override_events`

Suggested fields:
- `id`
- `scope_type` (`reservation`, `property_config`, `month_summary`)
- `scope_id`
- `slug`
- `year`
- `month`
- `field`
- `old_value`
- `new_value`
- `reason`
- `actor`
- `created_at`
- `reverted_at`
- `reverted_by`
- `is_active`

### 7.5 UI behavior

When a user changes a reservation or config:
- show diff
- ask for reason
- mark changed rows in UI
- allow restore to original

### 7.6 Acceptance criteria for Phase 4

- manual edits record old and new value
- actor and reason are stored
- restore original works
- changed rows are visibly marked

## 8. Phase 5: Role Support, Diagnostics, and Deployment Readiness

### 8.1 Goal

Prepare the system for multi-user/server usage without rewriting the product.

### 8.2 Deliverables

- role model
- admin-only unlock
- user tracking
- better bank diagnostics
- deployment contract

### 8.3 Recommended schema additions

- `users`
- `roles`
- `user_roles`
- `month_lock_events`
- `config_change_events`

### 8.4 Bank diagnostics

Improve bank page with:
- raw bank message
- normalized ref
- matched batch ref
- unmatched reason
- pending linkage visibility

### 8.5 Deployment contract

Create:
- environment variable support for auth secrets
- requirements lock file if missing
- startup documentation
- possible Docker path later

Current answer from product owner:
- local-only now
- server later
- deployment strategy not decided yet

So deployment work should not block core product work.

### 8.6 Acceptance criteria for Phase 5

- roles exist conceptually in code/data model
- unlock is admin-only
- user actions can be attributed
- bank diagnostics are visible in UI

## 9. Cross-Cutting Refactors

These refactors are recommended as enabling work, but should be done incrementally.

### 9.1 Create application services layer

Recommended new module group:
- `report/services/generate_report.py`
- `report/services/preview_report.py`
- `report/services/import_source.py`
- `report/services/recalculate_months.py`
- `report/services/lock_month.py`

Reason:
- keep business orchestration out of web routes
- keep CLI and web sharing the same execution path

### 9.2 Create shared DTOs or typed contracts

The codebase currently passes many dicts.

Recommended:
- gradually introduce typed data objects or at least documented schemas
- especially for:
  - report rows
  - summary data
  - import result
  - month state
  - override events

### 9.3 Unify status rendering

Currently status semantics exist in backend, Excel, and templates.

Recommended:
- centralize status definitions and display metadata
- provide one source for:
  - code
  - label
  - color
  - severity
  - review meaning

## 10. Testing Plan

The owner explicitly said the most dangerous failures are:
- calculations
- matching
- imports

Therefore tests should be ordered accordingly.

### 10.1 Test Tier 1: calculation and parity

- Excel summary and web summary produce same key amounts
- preview and final generation use same row set
- expenses affect canonical summary
- open month recalculation updates expected rows

### 10.2 Test Tier 2: bank matching

- Airbnb `G-ref` strict matching
- Booking descriptor strict matching
- no amount/date fallback accepted
- pending resolution honors strict matching
- locked month receives notice/forward behavior

### 10.3 Test Tier 3: imports

- duplicate source upload rejected or explicitly reported
- delta counts computed correctly
- import of newer rolling export enriches DB instead of losing older data
- open month auto-recalculation triggered
- locked month not mutated

### 10.4 Test Tier 4: locking

- locked month becomes read-only
- unlock requires admin role
- edits rejected while locked
- imports do not rewrite locked month

### 10.5 Test Tier 5: web workflows

- generate from web
- preview from web
- import from web
- restore override from web
- `Vygenerovat` visibility on dashboard

## 11. Recommended Milestones

### Milestone 1
Parity and month state

Includes:
- canonical summary service
- dashboard generation state
- month lock schema
- locked/open rules

### Milestone 2
DB-backed config and alias history

Includes:
- report object tables
- alias tables
- JSON fallback bridge

### Milestone 3
Web imports and generation

Includes:
- upload UI
- import delta summaries
- generate/preview/download UI

### Milestone 4
Overrides and restore

Includes:
- override events
- restore action
- changed-row markers

### Milestone 5
Diagnostics and roles

Includes:
- better bank diagnostics
- role model
- admin unlock flow

## 12. Immediate Next Tasks

If starting implementation now, begin with these tasks:

1. Extract canonical summary builder shared by web and Excel
2. Introduce `report_month_state` with `OPEN/LOCKED`
3. Update dashboard to show `Vygenerovat`, lock state, and stale/new-data markers
4. Add backend API/service for web-triggered report generation
5. Add import result object that can return delta counts
6. Build import page in web
7. Design override audit schema
8. Move config edits away from direct JSON writes toward DB-backed storage

## 13. Definition of Done

A major feature in this roadmap is only done when all are true:
- backend logic implemented
- database migration created
- web behavior exposed
- existing tests pass
- new tests added
- behavior documented if user-facing
- no hidden CLI-only behavior remains for a web-first feature

## 14. Final Guidance

When in doubt, the developer should prefer:
- explicit state over hidden inference
- DB persistence over filesystem assumptions
- shared services over route-local logic
- history-preserving changes over destructive rewrites
- stricter bank/report correctness over convenience

The system is now beyond “small script” stage. Future changes should be implemented like product features, not one-off patches.
