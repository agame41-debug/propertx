# Rentero Implementation Specification

## 0. Document Purpose

This document is an implementation-level product and engineering specification for the Rentero reporting system.

It is written for a developer who:
- has not worked on this project before,
- needs enough business and technical context to extend or finish the product,
- must understand not only what the system does, but why specific rules exist.

This document intentionally goes beyond a short technical brief. It defines:
- business context,
- target product behavior,
- domain terms,
- source-of-truth rules,
- data lifecycle,
- UI behavior,
- report calculation behavior,
- import behavior,
- bank matching rules,
- locking rules,
- override rules,
- roles,
- acceptance criteria.

If a future implementation conflicts with this document, this document should be treated as the product contract unless a newer written decision replaces it.

## 1. Product Context

Rentero is a monthly financial reporting tool for rental properties.

The goal is to generate a **draft monthly report** for each report object using:
- Hostify reservations,
- Airbnb payout exports,
- Booking.com payout exports,
- bank statement exports,
- internal configuration,
- and user-entered operational data such as expenses and client details.

The generated result is used to:
- understand what reservations belong to a month,
- understand what payouts are expected,
- see whether money arrived in bank,
- calculate the client's monthly result,
- inspect mismatches and unresolved cases,
- and finally export a draft Excel report.

This is not a final accounting ledger. It is an operational reporting and review system.

## 2. Current and Target Operating Model

### 2.1 Current state

The system already contains:
- a CLI reporting pipeline,
- a FastAPI + Jinja web interface,
- SQLite operational storage,
- importable source files,
- Excel export,
- reporting history,
- client and expense management,
- bank matching logic,
- pending payment handling.

### 2.2 Target direction

The **web application becomes the main interface**.

The CLI remains useful as:
- a technical entry point,
- a fallback tool,
- a developer/admin tool,
- and a reusable backend execution path.

### 2.3 Deployment expectations

For now:
- the tool is used locally,
- simple authentication is acceptable,
- SQLite is acceptable.

In the future:
- the system should be deployable to a server,
- all important operational data must live in the database,
- multiple users and roles must be supported.

## 3. Core Product Principles

The system must follow these principles:

1. Do not silently produce financially misleading output.
2. Prefer explicit statuses over optimistic auto-resolution.
3. `web preview`, `dry-run`, CLI generation, and Excel must use the same business logic.
4. Closed months must remain immutable until explicitly unlocked by an admin.
5. Historical meaning must not be destroyed when names or listing mappings change.
6. The database must become the operational system of record.
7. Any manual change must be traceable and reversible.

## 4. Glossary

### 4.1 Report object

The primary reporting unit is a **listing**, not an abstract apartment group.

Important decision:
- each Hostify listing is treated as a separate report object,
- even if in business language a platform may visually present several rooms/units as one apartment concept.

This means:
- reporting identity is listing-level,
- not property-group-level,
- unless a future feature explicitly introduces grouping above listing level.

### 4.2 Reservation

A reservation is a stay-level record that comes primarily from Hostify and may be enriched or corrected by channel CSV data.

### 4.3 Channel

A source platform such as:
- Airbnb,
- Booking.com,
- or another booking source if added later.

### 4.4 Source file

A raw imported file stored in the archive database:
- Airbnb CSV,
- Booking CSV,
- bank export,
- accounting export,
- etc.

### 4.5 Batch

A payout batch from a platform:
- Airbnb batch identified by `G-ref`,
- Booking batch identified by `Deskriptor výpisu` reference.

### 4.6 Report month

The accounting/reporting month the system is generating.

This is not always equal to:
- reservation check-in month,
- payout month,
- or bank arrival month.

It is determined by explicit business rules.

### 4.7 Open month

A month that may still be recalculated automatically when new source data arrives.

### 4.8 Locked month

A month that is frozen.

Locked month behavior:
- view only,
- no recalculation,
- no source-driven mutation,
- no expenses/overrides/config changes affecting it,
- unless explicitly unlocked by an admin.

### 4.9 Pending payment

A reservation or payout that belongs to the report but whose bank arrival was not confirmed within the month's cutoff rules.

### 4.10 Manual override

A user-initiated change of a calculated or imported value that must store:
- old value,
- new value,
- reason,
- actor,
- timestamp.

## 5. Users and Roles

Target roles:
- `admin`
- `accountant`
- `administrative`

### 5.1 Admin

Admin can:
- unlock months,
- edit all configuration,
- edit clients,
- edit expenses,
- import files,
- trigger generation/recalculation,
- create or edit overrides,
- manage users and roles later.

### 5.2 Accountant

Accountant will likely need:
- read access to reports,
- read access to bank status,
- maybe export access,
- maybe override access later,
- but not unlock access unless explicitly granted in future.

For now, unlock is admin-only.

### 5.3 Administrative

Administrative users may later work with:
- client info,
- expenses,
- imports,
- review pages,
- but should not be able to unlock closed months by default.

## 6. Source-of-Truth Hierarchy

### 6.1 Reservation structure

Primary source:
- Hostify

Used for:
- reservation identity,
- listing identity,
- guest,
- dates,
- nights,
- source,
- raw financial fields,
- base metadata.

### 6.2 Payout amount

Primary source:
- channel CSV

Rule:
- if channel CSV exists for the reservation, CSV payout amount is authoritative,
- if Hostify differs, CSV wins,
- the difference must remain visible in status/comment.

### 6.3 Money arrival confirmation

Primary source:
- bank statement

Bank answers:
- did money arrive,
- when,
- to which payout batch it belongs,
- whether it should remain pending.

### 6.4 Configuration

For now configuration may live in:
- JSON file,
- database,
- or both during transition.

Final product direction:
- all operationally editable configuration should live in DB,
- with web editing support,
- while preserving compatibility/migration from JSON.

### 6.5 Financial summary in UI

The financial model shown in the web UI must match the Excel summary model.

This is an explicit requirement.

The UI must not use a simplified net calculation that differs from the Excel logic.

## 7. High-Level Functional Scope

The product must support:

1. Import source files into the database.
2. Detect duplicates by file hash.
3. Report how much new information the import added.
4. Load and normalize Hostify data.
5. Detect incomplete Hostify fetch and stop safely.
6. Match Hostify with Airbnb and Booking CSV data.
7. Include CSV-only rows when Hostify data is missing.
8. Apply centralized month-assignment rules.
9. Compute report rows.
10. Match bank payments by strict batch reference only.
11. Persist generated report state.
12. Provide web preview and Excel export.
13. Manage clients and expenses.
14. Support manual overrides and restore-original behavior.
15. Lock and unlock months.
16. Preserve historical meaning when listing names or aliases change.

## 8. Domain Model and Identity Rules

### 8.1 Listing-based reporting

Each Hostify listing is a separate report object.

This is a key business decision.

Do not merge different Hostify listings into one report object implicitly.

### 8.2 Aliases

A listing may have multiple aliases per channel.

Examples:
- Hostify nickname alias
- Airbnb listing names
- Booking listing names
- Booking property IDs

### 8.3 Historical mapping

If a listing changes name or channel linkage:
- do not overwrite old meaning,
- do not destroy historical compatibility,
- keep old mapping,
- add new mapping as an additional listing identity or alias entry.

In practice this means:
- changing names should append history,
- not mutate old historical semantics.

### 8.4 Missing Hostify listing scenario

There has already been a real case where:
- a listing existed on Airbnb,
- but no corresponding listing existed in Hostify,
- causing reporting issues.

Therefore the system must be designed to handle:
- channel data without perfect Hostify symmetry,
- explicit review of unmatched listing mappings,
- and configurable alias/linkage management.

## 9. Configuration Model

### 9.1 Current transitional requirement

Configuration is currently partly in JSON and partly expected to move toward DB.

User decision:
- editable configuration should eventually live in the database,
- because on a server all users must see the same data.

### 9.2 Short-term storage rule

During transition it is acceptable for configuration to exist in both:
- JSON
- DB

But the system must define a clear source of truth.

Current decision:
- commission may exist in both,
- but the system should move toward DB as the operational source of truth.

### 9.3 Web-editable fields

The user wants to be able to edit **all configuration** through the web UI.

This includes, eventually:
- display name
- commission
- city tax
- VAT rate
- balicky per person
- channel aliases
- Booking property IDs
- cutoff rules
- listing settings

### 9.4 Change impact

If object-level configuration changes:
- affected reservations should become recalculated,
- those recalculated rows must be identifiable as changed,
- and the user must be able to restore previous calculations.

## 10. Month Assignment Logic

### 10.1 Why it exists

Month assignment is needed because:
- payment timing does not align cleanly with stay dates,
- Airbnb and Booking behave differently,
- long stays and late payments create ambiguity,
- report month is a business reporting construct, not just a calendar field.

### 10.2 Centralization requirement

Month assignment logic must exist in one canonical implementation.

No duplicate rule copies in multiple modules.

### 10.3 Airbnb

Airbnb month assignment must use the same logic for:
- Hostify rows,
- Airbnb CSV-only rows,
- preview,
- CLI,
- Excel,
- web UI.

### 10.4 Booking

Booking rules remain channel-specific and may evolve.

The system must preserve late-payment behavior and keep month logic configurable.

Current delivered rule:
- Booking verification compares Booking CSV `net_eur` against a normalized Hostify payout.
- City tax is removed from Hostify payout using the same reporting rule as calculation:
  `property city_tax_rate × nights × paying guests`, including Checkin guest-count overrides when available.
- Raw Hostify `city_tax_eur` is only a fallback when the application cannot infer city tax from its own business inputs.
- If the remaining verification difference is within `±1.00 EUR`, the row is treated as `MATCHED`.
- For matched Booking rows, the calculation path uses the CSV net payout as the effective payout source.

### 10.5 Future requirement

Because these rules may still change, they should be represented as:
- centralized functions,
- documented business rules,
- ideally configurable policies later.

## 11. Incomplete Hostify Fetch Handling

### 11.1 Criticality

Incomplete Hostify fetch is a hard-stop error.

If Hostify says more rows exist than were actually loaded:
- report generation must stop,
- preview must stop,
- no Excel must be produced,
- no partial Hostify data may be persisted.

### 11.2 No persistence of partial critical data

When Hostify fetch is incomplete, do not persist:
- hostify cache,
- normalized hostify snapshots,
- report rows based on that run,
- report history based on that run.

### 11.3 Error visibility

The UI and CLI must clearly explain:
- which month was affected,
- what fetch window was used,
- expected row count,
- fetched row count,
- and that generation was stopped for data safety.

## 12. Import System

### 12.1 General behavior

Imports must be available via web UI, not just CLI.

The web must support:
- selecting file type,
- uploading source file,
- storing it in DB archive,
- showing import results,
- showing whether it was duplicate,
- showing what new data was discovered,
- activating/deactivating source files if needed.

### 12.2 Duplicate imports

If the file is byte-identical:
- detect by SHA256,
- do not create a duplicate archive artifact,
- show an explicit message/error that the file already exists.

### 12.3 Incremental information

The user explicitly wants imports to report delta information.

Examples:
- bank import: how many new transactions were added
- Airbnb import: how many newly paid reservations were discovered
- Booking import: how many new payouts/reservations were discovered

### 12.4 Delta comparison model

Import delta should be computed against normalized data already present in DB.

This means import feedback should answer:
- what is genuinely new in the system,
- not only whether the file bytes differ.

### 12.5 Desired import semantics

When a new file is imported:
- it should not blindly overwrite old knowledge,
- it should enrich the database,
- compare against existing normalized state,
- and fill missing information.

This is especially important for rolling exports where a newer file contains:
- all old rows,
- plus a few new rows.

### 12.6 Auto-recalculation after import

If a month is **not locked**, and newly imported data affects it:
- the system should automatically recalculate impacted months.

If a month **is locked**:
- do not mutate it,
- propagate the relevant effect into the next open month,
- and notify the user that new information was detected for a locked month.

## 13. Generation and Preview

### 13.1 Web as main control surface

The web interface must be able to:
- trigger generation,
- show preview,
- allow Excel download,
- show month state,
- show whether data exists but report is not generated yet.

### 13.2 Dashboard behavior

If a month has data but no generated report yet:
- show a `Vygenerovat` button.

Do not leave it as silent empty state.

### 13.3 Preview parity

Web preview, CLI dry-run, and final generation must produce the same logical data:
- same rows,
- same statuses,
- same totals,
- same bank matching,
- same month assignment.

Differences should only be:
- persistence side effects,
- exported file creation,
- explicit preview decorations.

## 14. Excel and Web Parity

The web summary must match the Excel summary.

This is an explicit decision.

If Excel uses a financial model based on:
- `cena_ubytovani_czk`,
- fees,
- VAT,
- and client payout logic,

then web must show the same business meaning.

It is not acceptable for the web to show a simplified alternative result unless it is clearly labeled as preliminary.

### 14.1 Preliminary future-month data

The web may show preliminary future-month data when:
- Hostify reservations exist in advance,
- but exchange rates are still uncertain,
- so final payout interpretation is not fully stable.

This should be shown explicitly as preview/preliminary mode, not mixed invisibly with final month output.

## 15. Clients, Expenses, and Full Financial Result

### 15.1 Clients

Client records belong to report objects and must be fully manageable through web UI.

### 15.2 Expenses

Expenses must participate in the full financial result, not only in an auxiliary web-only net calculation.

This is an explicit requirement.

Therefore:
- expenses must be part of canonical financial output,
- not just a decorative operational layer.

### 15.3 Future implication

If expenses affect the official report result, then:
- Excel,
- preview,
- report persistence,
- and lock semantics

must all agree on expense impact.

## 16. Bank Matching Rules

### 16.1 General principle

Bank matching must be strict and reference-based.

Weak auto-match using only:
- amount,
- amount + date,
- property_id + amount,

must not confirm payment automatically.

### 16.2 Airbnb

Airbnb batch match must use:
- `G-ref` only.

If no matching `G-ref` exists in bank:
- payment is treated as not found.

### 16.3 Booking

Booking batch match must use:
- payout report reference from `Deskriptor výpisu`,
- normalized against bank `Zpráva pro příjemce`.

### 16.4 Review visibility

The UI should make bank matching inspectable.

The user wants to be able to see more detail when needed.

This means future bank screens should expose:
- raw bank message,
- normalized reference,
- property_id if relevant,
- matched batch reference,
- and possibly the reason for mismatch.

## 17. Status System

The system must support at minimum:
- `MATCHED`
- `ROZDÍL`
- `CHYBÍ_V_CSV`
- `CHYBÍ_V_HOSTIFY`
- `ZRUŠENO`
- `KE KONTROLE`

### 17.1 `KE KONTROLE`

Current decision:
- use it broadly for cases where the system is not confident.

This implies a wide rule:
- if business meaning is uncertain,
- if the system included the row but confidence is incomplete,
- or if unusual reconciliation conditions occurred,

then `KE KONTROLE` is allowed.

### 17.1a `MATCHED` tolerance rule

Current shared tolerance contract:
- `MATCHED` includes small verification drifts up to `1.00 EUR` in absolute value.
- The purpose is to suppress FX rounding and similar low-signal payout noise.
- Larger differences must remain `ROZDÍL`.

### 17.2 Future task

Because the status is broad, a formal status matrix should be created to define:
- exact assignment rules,
- precedence,
- display colors,
- and whether bank or verification uncertainty dominates.

## 18. Pending Payments

### 18.1 Purpose

Pending payments are reservations/payouts with no confirmed bank arrival in the relevant month window.

### 18.2 Visibility

The user wants pending-like visibility in the interface.

This can be implemented:
- on the bank tab,
- on the property page,
- or both.

### 18.3 Behavior with locked months

If a month is locked and a relevant payment appears later:
- the locked month must not be rewritten,
- the system must notify the user,
- and the impact should be transferred into the next open month.

## 19. Locking and Unlocking Months

### 19.1 Purpose

Month locking is a core business feature.

The user explicitly wants to be able to close a month "like putting a lock on it".

### 19.2 Locked month behavior

When a month is locked:
- generation/recalculation is disabled,
- imports must not mutate that month,
- expenses cannot be changed for that month,
- client/config changes cannot rewrite that month's result,
- overrides cannot mutate that month's stored report result,
- the month becomes read-only in UI.

### 19.3 Unlock behavior

Unlock is allowed:
- admin only.

### 19.4 New data after lock

If new bank or source information arrives after lock:
- do not rewrite the locked month automatically,
- inform the user,
- and carry impact forward to the next open month where applicable.

## 20. Manual Overrides and Restoration

### 20.1 Desired capability

The user wants to be able to edit values and then restore the old calculations.

### 20.2 Override storage model

Each override must store:
- target entity
- field name
- old value
- new value
- reason
- actor
- timestamp
- month context
- lock compatibility checks

### 20.3 Restore-original behavior

There must be a clear UI action to restore the original calculated value.

This requires:
- original value storage,
- not just current override value storage.

### 20.4 Scope

Overrides may be needed on:
- reservation level,
- object config level,
- maybe month summary level later.

## 21. Historical Immutability and Recalculation Rules

### 21.1 Open months

If month is open:
- new imports can affect it,
- recalculation can happen automatically,
- config changes can trigger recalculation,
- rows changed by recalculation should be identifiable.

### 21.2 Closed months

If month is locked:
- the month is display-only,
- historical result is preserved,
- new relevant signals should not mutate past stored results.

### 21.3 Config changes

If configuration changes and the month is open:
- recalculate automatically.

If configuration changes and the month is locked:
- do not rewrite the month.

## 22. Database as Operational Source of Truth

### 22.1 Long-term expectation

The system should eventually be able to work fully from DB even if raw files are no longer present on disk.

This is an explicit requirement.

### 22.2 Implication

The DB should store enough normalized and archived information to:
- regenerate previews,
- explain status outcomes,
- inspect history,
- reproduce calculations,
- and continue working after source files disappear from local filesystem.

### 22.3 Recommendation

The implementation should move toward:
- normalized source-derived tables,
- import audit tables,
- month state tables,
- override audit tables,
- configuration tables,
- role/user tables.

## 23. UI Scope

The web UI should eventually contain at least:

1. Dashboard
2. Property detail page
3. Bank page
4. Pending visibility
5. Import page
6. Source archive page
7. Client page
8. Expense page
9. Configuration/settings page
10. Month close/unlock controls
11. Excel generation/download controls
12. Preview and recalculation controls

### 23.1 Language

The website must be in Czech.

The language policy priority is:
- UI in Czech,
- internal code can remain English,
- mixed-language UI is not acceptable as final product state.

## 24. Security and Authentication

### 24.1 Current state

For now:
- local-only use is acceptable,
- simple auth is acceptable.

### 24.2 Future requirement

Because server deployment is expected later:
- roles must be supported,
- auth must eventually be improved,
- operational changes should be attributable to a user identity.

### 24.3 Auditability

The user said auditability "can be done".

Therefore the architecture should not block:
- actor tracking,
- change logs,
- month lock/unlock history,
- override history,
- config change history.

## 25. Recommended Target Data Model

This section describes a recommended target structure. Exact SQL names may vary, but the concepts should exist.

### 25.1 Core entities

- `report_objects`
- `report_object_aliases`
- `source_files`
- `import_runs`
- `hostify_reservations`
- `channel_reservations_airbnb`
- `channel_reservations_booking`
- `payout_batches`
- `payout_batch_items`
- `bank_transactions`
- `bank_matches`
- `report_months`
- `report_rows`
- `report_history`
- `pending_payments`
- `clients`
- `expenses`
- `expense_categories`
- `manual_overrides`
- `override_history`
- `users`
- `roles`
- `month_locks`

### 25.2 Important conceptual additions

The current codebase may not yet have all these tables. That is acceptable.

But a developer extending the system should work toward this shape rather than adding more hidden logic in templates or ad hoc JSON writes.

## 26. Required Workflows

### 26.1 Import workflow

1. User uploads file in web UI.
2. System detects file type.
3. System checks SHA256 duplicate.
4. If duplicate:
   - reject or return explicit message.
5. If new:
   - archive file,
   - normalize contents,
   - compare to DB state,
   - calculate delta,
   - show import summary.
6. If new data affects open months:
   - auto-recalculate them.
7. If new data affects locked months:
   - do not rewrite,
   - show notification,
   - move relevant impact forward where appropriate.

### 26.2 Generate workflow

1. User opens month/object.
2. If report does not exist but data exists:
   - show `Vygenerovat`.
3. On generation:
   - run full canonical pipeline.
4. Save report rows/history if successful.
5. Offer Excel download.

### 26.3 Preview workflow

1. User requests preview.
2. Run same logic as final generation.
3. Do not perform final side effects unless the workflow explicitly confirms generation.

### 26.4 Override workflow

1. User edits a reservation value or object config.
2. System stores old and new values.
3. System stores reason and actor.
4. Affected rows become marked as changed.
5. User can restore original calculation.

### 26.5 Lock workflow

1. Admin locks month.
2. Month becomes read-only.
3. UI visibly shows locked state.
4. Future imports cannot rewrite it.
5. Admin may unlock later if necessary.

## 27. Acceptance Criteria

The implementation is acceptable only if all of the following are true.

### 27.1 Business correctness

- web and Excel use the same financial logic
- CSV payout overrides Hostify payout where applicable
- strict bank reference matching is enforced
- incomplete Hostify fetch blocks generation safely

### 27.2 UI behavior

- dashboard shows `Vygenerovat` when data exists but report does not
- import UI exists or is explicitly implemented as the primary import method
- users can download Excel from the web flow
- pending or late payment state is visible

### 27.3 Historical safety

- locked month is read-only
- only admin can unlock
- new data for locked month does not silently rewrite the past

### 27.4 Override traceability

- manual changes store old value, new value, reason, actor
- original calculations can be restored

### 27.5 Import traceability

- duplicate files are rejected or explicitly reported
- import result shows delta counts
- DB can continue operating even if raw disk files are unavailable later

## 28. Implementation Priorities

### Must-have

- web generation flow
- web import flow
- full parity with Excel financial model
- month lock / unlock
- strict source-of-truth handling
- DB-first operational model
- override audit + restore-original
- import delta summaries

### Should-have

- richer bank diagnostics in UI
- pending section
- role-aware permissions
- alias/history management UI
- import history screens

### Later

- server-grade auth
- user management
- advanced grouping above listing level
- richer accounting exports
- more advanced audit tooling

## 29. Non-Goals for the Current Phase

The following are not required immediately:
- replacing SQLite
- building a final enterprise auth system
- building a full accounting ERP
- merging multiple Hostify listings into one business object by default

## 30. Final Engineering Instruction

A developer implementing this system should assume:

1. The product is listing-centric.
2. The web application is the main product.
3. The database should become the durable operational memory.
4. Historical data must be protected.
5. Any ambiguous data situation must be visible to the user.
6. Excel and web must agree financially.
7. Closed months must be immutable until admin unlock.
8. Manual changes must be auditable and reversible.

This is the intended direction even if some current modules still reflect an earlier CLI-first architecture.
