# Month-Versioned Object Profiles & Recurring Expenses

## Problem

Two related operational gaps:

1. **Object info is global, not historical.** Owner identity (`clients`) and object
   state (`report_objects`: `client_type`, rates, `active`, středisko) are stored as a
   single row per object and overwritten in place. When an owner changes, the type
   changes, or středisko is reassigned, past months silently inherit the new value.
   Things change often ("nový majitel od …"), so we need each month to keep the value
   that was true *for that month*, while a manual change applies forward.

2. **Expenses are one-off only and partly manual.** Recurring charges (e.g. `internet`)
   must be re-entered every month. The new `Objekty.tsv` carries `internet` and
   `ost_služby` amounts that should become výdaje automatically. There is no concept of a
   recurring expense, nor a path to import object data through the Zdroje pipeline.

This design covers two parts implemented in order: **A — month-versioned object
profiles + `Objekty.tsv` import**, then **C — recurring expense templates + TSV
auto-expenses**. The TSV import bridges both.

> Out of scope: the "edit expense" button (verified working — no bug), day-level
> granularity within a month, and any unrelated refactoring.

## Design Decisions

- **Effective-dated versioning, month granularity.** Object info is stored as
  non-overlapping monthly segments per object. Each month resolves to exactly one
  segment. This mirrors the existing `report_object_aliases` `valid_from`/`valid_to`
  precedent. Granularity is the report month (no intra-month dates).
- **Single unified "object profile".** Owner + type + rates + `active` + středisko are
  versioned together. Changing any field creates a new segment that copies the rest
  forward, so the operator never re-enters everything each month. (Rejected: separate
  version streams for owner vs. params — dual writes, drift, heavier UI for a case the
  unified profile already expresses.)
- **`clients` is retired.** Its data migrates into the profile. All owner reads go
  through one resolver. The `clients` table is kept physically frozen for one release as
  a rollback seatbelt, then dropped in a follow-up. (Matches the project's decisive
  "engine unification" consolidation style while respecting prod-runs-uncommitted.)
- **One month-aware resolver.** `get_property_config(conn, slug, year, month)` is the
  single place month-dependence lives; the rest of the app threads `(year, month)` into
  it. A bulk variant serves the dashboard.
- **Manual changes only; system never rewrites silently.** Changes happen via UI
  (scope selector) or explicit TSV re-import.
- **Recurring expenses: template → per-month rows.** A template on the object
  generates an editable `expenses` row in each month of its period. Editing a month's
  row never touches the template; the "rule" and the "month fact" are cleanly separated.
- **TSV amounts are net; +21 % DPH always.** `internet`/`ost_služby`/`ost_služby2`
  values are bez DPH; the materialized výdaj always gets `vat_rate = 0.21` regardless of
  the object's `platce_dph`.
- **`todo` objects are not imported** (neither as drafts).

## Specification

### Part A — Month-Versioned Object Profiles

#### A.1 Schema: `report_object_profiles`

Non-overlapping monthly segments per object:

```
id              INTEGER PK
slug            TEXT  → report_objects.slug
valid_from_ym   TEXT  "YYYY-MM"   (first month this version applies; NULL = open start)
valid_to_ym     TEXT  "YYYY-MM"|NULL  (last month inclusive; NULL = open-ended)
-- owner (migrated from clients):
owner_name, ico, dic, platce_dph, adresa, bank_account, email, phone, notes
-- type & params (from report_objects):
client_type, city_tax_rate, balicky_per_person, vat_rate, rentero_commission
stredisko, active
-- provenance:
source          TEXT  'tsv' | 'ui'
created_at, updated_at
```

Index: `(slug, valid_from_ym)`. Month keys are `"YYYY-MM"` (zero-padded → lexically
comparable).

`report_objects` remains the slug registry + stable channel identity
(`hostify_listing_id`, `listing_nickname`, `display_name`); its business fields are
superseded by the profile and read through the resolver.

**Invariant:** for any object, segments do not overlap → every month maps to exactly
one segment.

#### A.2 Edit operations (preserve non-overlap)

| Operation | Timeline effect |
|---|---|
| **"From month M onward"** (default) | Trim the prior segment to `M-1`; insert a new segment `[M, B-1]` where `B` is the start of the next existing segment (if any), else `[M, NULL]`. Deliberate future segments are **preserved**, not clobbered. Values carry forward automatically. |
| **"This month only" (M)** | Split the covering segment into `[start, M-1]`, `[M, M]` (the edit), `[M+1, end]` (old values restored). |
| **Edit a historical segment** | Change values within an existing segment's bounds (open months only). |

Empty side-segments (e.g. `[start, M-1]` when `M = start`) are omitted, not stored as
zero-width rows.

#### A.3 Migration

Collapse current `report_objects` + `clients` into one open segment per object
(`valid_from_ym = NULL`, `valid_to_ym = NULL`) holding present values. Subsequent TSV
import / UI edits create later segments. Legacy months with no segment fall back to the
open segment / `report_objects` defaults.

#### A.4 Resolver & integration

`get_property_config(conn, slug, year, month)` merges stable identity + channel/listing
aliases (already month-aware) + the profile segment covering `M`. A bulk resolver
returns all slugs for one month for the dashboard (single query, matching the existing
dashboard bulk-query optimization).

Integration points:

| Place | Change |
|---|---|
| `engine.generate_report_in_process(…, slug, year, month, …)` | resolve the profile for `(slug, year, month)` — that month's `client_type`/rates |
| `summary.build_report_summary(rows, property_config, …)` | unchanged (receives the month's profile) |
| `_build_dashboard_maps` (web_support) | categorize Vše/Rentero/Klienti/Z Klienti and `active` filter **by the selected month's profile** |
| Property page | owner/type/rates of the viewed month |
| `/clients`, `/inventory` (non-month pages) | show the current (open) segment; edits write a version |

#### A.5 Regeneration on profile change

Reuse `_apply_object_config_change_impacts`, made range-aware:

- OPEN months in the changed segment's range → auto-regenerate.
- LOCKED months → not rewritten; create a `report_month_notifications` notice.
- Months outside the changed range → untouched.

#### A.6 `Objekty.tsv` import via Zdroje

New source type **`objekty`** in the existing pipeline (`source_registry.py`,
`/sources` route, `sources.html`), alongside airbnb/booking/bank/checkin.

- **Parsing:** skip `#` lines; tab `DictReader` (per `seed_clients.py`); UTF-8 with
  BOM/cp1250 fallback. Match each row → slug via `canonical_name`/`aliases`/`display_name`
  (seed_clients normalization + `report_object_aliases`). Unmatched rows → reported in
  the delta summary, not applied.
- **Effective month:** chosen at import, default = **current month** (today 05/2026).
  Import = "from month M onward": for objects whose TSV-derived fields differ from the
  segment covering M, create `[M, NULL]`; otherwise no-op (idempotent). SHA256 dedup for
  this type is **informational, not blocking** (same file for a new month is normal).
- **Field mapping:** `category → client_type` (`rentero→rentero`, `standard→klient`,
  `zrežim→z_klient`, `todo→ skip entirely`); `owner_name, ico, dic, platce_dph`, address
  (`ulice/misto/psc`), bank (`ucet/kod_banky`), `středisko`, `active`. Rates
  (`city_tax_rate, balicky_per_person, vat_rate, rentero_commission`) are **absent from
  TSV → copied from the previous version** (never overwritten with defaults).
- **Auto-expenses** (see Part C): `internet > 0` → upsert recurring template; `ost_služby`
  / `ost_služby2` → one-off výdaj in month M.
- **Delta summary:** objects updated / new segments / unmatched / templates upserted /
  one-off výdaje / affected months — with OPEN-month regeneration and LOCKED notices.
- **Object dropped from TSV** → **not** auto-deactivated (explicit only).

### Part C — Recurring Expenses

#### C.1 Schema: `expense_templates`

```
id, property_slug, category_id, description,
amount_czk, amount_net_czk, amount_dph_czk, vat_rate,
start_ym "YYYY-MM", end_ym "YYYY-MM"|NULL  (inclusive; NULL = ongoing),
source 'tsv:internet' | 'ui', active, created_at, updated_at
```

Link from instances: add `template_id INTEGER NULL` to `expenses` (NULL = manual one-off).

Tombstone table `expense_template_skips(template_id, year, month)` records months whose
generated row the operator deleted, so it is not recreated.

#### C.2 Materialization (expand template into month M)

For each active template where `start_ym ≤ M ≤ end_ym` (or open):

- A row `expenses(template_id=T, year=M.y, month=M.m)` already exists → leave it
  (respect manual edits).
- No row and no tombstone → create it from template values.
- Tombstone exists → skip.

**Triggers (write-guarded only):** (1) month generation in `engine.py` (runs on every
mutation and daily sync, before summary); (2) template create/edit; (3) TSV import. **No
writes on GET** — the page reads already-materialized `expenses`. LOCKED months are never
materialized (`_assert_report_month_mutable`, already enforced by `add_expense`).

#### C.3 Edit semantics

| Action | Effect |
|---|---|
| Edit a month's row (existing form) | that month only; template untouched |
| Edit the template (amount/period) | future **not-yet-materialized** months; existing rows stay (edit individually) |
| Delete a month's row | tombstone — not recreated |
| Deactivate/delete the template | stops future materialization; past rows remain |

#### C.4 TSV → expenses (net + 21 % always)

For the import month M:

- `internet > 0` → upsert a template by `(slug, source='tsv:internet')`, description
  "Internet", `start_ym = M`, open-ended.
- `ost_služby(_popis)` / `ost_služby2(_popis)` → one-off `expenses` row (`template_id =
  NULL`) in M, description = popis. Dedup by `(slug, M, source='tsv', popis, amount)`.
  Negative amounts allowed (e.g. Strakonická −5632,23).
- For all TSV-derived výdaje: `amount_net_czk =` TSV value, `vat_rate = 0.21`,
  `amount_dph_czk = net × 0.21`, `amount_czk = net × 1.21` — **regardless of the object's
  `platce_dph`**.

### UI

**Object profile editor** (extends `client.html` + `client_save`):

- Shows current (open-segment) owner + type + rates + `active` + středisko.
- **Scope selector** on save: "Od tohoto měsíce dál" (default) / "Jen tento měsíc". The
  "this month" anchor is the month context the editor was opened from (property page),
  else the current month.
- **Segment history**: compact list ("01–04/2026: Vlastník X, klient" / "05/2026–…:
  Vlastník Y, z_klient") to view and edit a specific segment.

**Expenses card** (`property_expenses.html` + `property_scripts.html`):

- New **"Pravidelné výdaje"** subsection: templates list (description, amount, period
  "od MM/RRRR" + "bez konce"/"do MM/RRRR", edit/delete) + "+ Pravidelný výdaj".
- Add/edit form gains a **"Pravidelný" toggle**; on → period inputs (from-month /
  to-month or "bez konce"). Saving in this mode writes a template (new
  `/expense-templates/...` routes), not a single výdaj.
- Materialized rows show a "pravidelný" badge (linked to template); editing affects only
  that month.

### Edge Cases

- LOCKED months: never auto-modified by import, profile change, or materialization →
  notice instead.
- Non-overlap invariant enforced by the three profile operations + an integrity test.
- Idempotent re-import: no change → no new segments; one-off výdaje deduped; internet
  template upserted.
- Unmatched TSV rows → delta only, not applied.
- Legacy fallback: month with no segment → open segment / `report_objects` defaults.
- Object absent from TSV → not deactivated.

### Testing

pytest, in the style of `tests/`:

- `test_object_profiles.py` — segment operations (from-onward trims/creates; this-month
  splits into 3; restore after override), unique resolution per month, non-overlap
  invariant, legacy fallback.
- extend `test_source_imports.py` — TSV parse, `category→client_type`, `todo` skipped,
  default effective month, rates carried forward, unmatched reported, idempotent
  re-import, auto-expense net+21 %, internet template upsert, ost_služby one-off + dedup.
- `test_get_property_config_month_aware.py` — engine/summary/dashboard use the month's
  profile; dashboard categorization per month.
- `test_expense_templates.py` — CRUD, materialization (creates once, respects edits),
  tombstone on delete, LOCKED guard, period range.
- migration `clients → profiles`; existing months unaffected.
- Baseline: keep ≤ 16 known-failing tests (verified baseline); only > 16 is a regression.

## Implementation Order

1. Schema + migration (`report_object_profiles`, collapse `clients`/`report_objects`).
2. Resolver `get_property_config(conn, slug, year, month)` + bulk; thread `(year, month)`
   into engine, dashboard, property page.
3. Range-aware regeneration on profile change.
4. `Objekty.tsv` import as Zdroje source type (profile segments only).
5. `expense_templates` + `template_id` + tombstones + materialization in engine.
6. TSV auto-expenses (internet template + ost_služby one-off, net+21 %).
7. UI: profile editor scope selector + segment history; recurring-expenses subsection +
   toggle.
8. Retire `clients` writes (table frozen this release).
