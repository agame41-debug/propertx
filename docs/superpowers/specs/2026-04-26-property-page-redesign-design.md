# Property page redesign — design spec

Date: 2026-04-26
Author: Nikita Shlykov + Claude (brainstorming session)
Status: Awaiting user review before plan-writing

## 1. Goal

Replace the current Czech property-month page (`/property/{slug}/{year}/{month}`) with a redesigned UI based on the reference mock at `/Users/nikitashlykov/Downloads/315n/property/`. Preserve every existing feature (lock/unlock, manual override, generation status, flash, expense CRUD, FloatingPanel) — no functional regression. Implement in three phases; ship visual redesign first.

## 2. Constraints (non-negotiable)

- **Aurora background** — animated gradient `::before` layer in `templates/partials/base_styles_layout.html:215-281` stays. Cosmos particle canvas (`#cosmos`) stays.
- **No new runtime** — Jinja templates + vanilla JS + HTMX. No React, no Babel, no Alpine.
- **Existing endpoints unchanged** in Phase 1 — lock, unlock, override-create, override-revert, expense add/edit/delete, generation use the same routes.
- **Theme switcher** — uses the existing one in sidebar; the mock's top-right `.theme-switch` is dropped.
- **Excel export** — out of scope. Not used in production.
- **Mobile** — full responsive support down to 640px (single phone breakpoint plus tablet at 1100px).

## 3. Three phases

### Phase 1 — Visual redesign (single PR, ~3-4 days)

Full visual port of the reference mock with all current functionality wired through existing endpoints. Reservation action buttons `← MM/YYYY`, `MM/YYYY →`, `Vyloučit` rendered as `disabled` with tooltip `Připravujeme`.

### Phase 2 — Vyloučit (single PR, ~0.5-1 day)

Activates the `Vyloučit` button. Backend creates an `override_event` with `field='is_excluded'`, `new_value='1'`, `reason='Vyloučeno přes UI'`. Reuses the existing override-application machinery in `apply_overrides_to_rows`.

### Phase 3 — Přesunout prev/next (single PR, ~0.5 day)

Activates the two `← MM/YYYY` and `MM/YYYY →` buttons. Single endpoint `/property/{slug}/{year}/{month}/reservation/{code}/move?direction=prev|next`. Auto-generated reason: `Přesun do MM/YYYY přes UI (direction)`. Persists `display_year` + `display_month` overrides.

`Split` is **not in scope**.

## 4. Architecture / file layout

### 4.1 Page composition (in `templates/property.html`)

```jinja
{% extends "base.html" %}
{% block content %}
{% include "partials/property_styles_property.html" %}{# inline <style> with property-only CSS #}
<div class="page">
  {% include "partials/property_intro.html" %}
  {% include "partials/property_notify_stack.html" %}
  {% include "partials/property_generation_progress.html" %}
  {% include "partials/property_kpi.html" %}
  {% include "partials/property_reservations.html" %}
  {% include "partials/property_breakdown.html" %}
  {% if client and client.platce_dph %}
    {% include "partials/property_dph_summary.html" %}
  {% endif %}
  {% include "partials/property_expenses.html" %}
  {% if override_events %}
    {% include "partials/property_override_history.html" %}
  {% endif %}
</div>
{% include "partials/property_scripts.html" %}
{% endblock %}
```

### 4.2 Partials catalogue

| Partial | Status | Renders |
|---|---|---|
| `property_intro.html` | rewrite | Page-header: breadcrumb + title + 3 badges (`Otevřeno/Uzamčeno`, `RENTERO/KLIENT/Z_KLIENT`, `Plátce DPH`) + 3 action buttons (`Změny`, `Checkin report`, `Uzamknout/Odemknout`) |
| `property_notify_stack.html` | new | Stack of notify-strips: locked-warning, new-data-warning, generation-FAILED-banner, change-log lines, flash-message |
| `property_generation_progress.html` | new | Spinner + auto-reload JS for PENDING/RUNNING generation status (extracted from current intro) |
| `property_kpi.html` | new (extracted from intro) | 4-card KPI row, content varies by `client_type` (see §5.1) |
| `property_reservations.html` | rewrite | Card with header, filter-pills, 7-column reservation table, click-to-expand row, inline override form |
| `property_breakdown.html` | rewrite + relocate | Standalone card with new column semantics (was nested inside intro) |
| `property_dph_summary.html` | new | "Vyúčtování DPH" card; rendered only if `client.platce_dph == 1` |
| `property_expenses.html` | rewrite | Card with header, calculator-strip add/edit form, table grouped by category |
| `property_expense_form.html` | new | Reusable add/edit form (one DOM, two modes) |
| `property_reservation_detail.html` | new | Expanded-row body: 3 groups (Finance / Identifikátor / Stav) + action row + inline override form |
| `property_override_history.html` | restyle | Existing card, new tokens, no structural changes |
| `property_scripts.html` | rewrite | Vanilla JS for filter-pills, expand-row, calculator-form, inline override-form, delete confirms, generation auto-reload |
| `property_styles_property.html` | new | Property-page-isolated CSS (everything from `styles.css` of the mock except tokens) |

### 4.3 Token additions to `base_styles.html`

```css
:root {
  --brand:        oklch(0.66 0.18 285);
  --brand-soft:   oklch(0.66 0.18 285 / 0.12);
  --brand-line:   oklch(0.66 0.18 285 / 0.35);
  --brand-text:   oklch(0.80 0.14 285);
  --dph:          oklch(0.72 0.12 185);
  --dph-soft:     oklch(0.72 0.12 185 / 0.10);
  --dph-line:     oklch(0.72 0.12 185 / 0.30);
  --dph-text:     oklch(0.78 0.10 185);
  --glass-hi:     inset 0 1px 0 oklch(1 0 0 / 0.04);
}
[data-theme="light"] {
  --brand:        oklch(0.55 0.18 285);
  --brand-text:   oklch(0.45 0.18 285);
  --dph:          oklch(0.55 0.12 185);
  --dph-text:    oklch(0.42 0.13 185);
  --glass-hi:    inset 0 1px 0 oklch(1 0 0 / 0.6);
}
```

Existing tokens (`--bg-*`, `--text-*`, `--color-*`, `--space-*`, `--radius-*`, `--shadow-*`, `--font-sans`, `--font-mono`) are reused unchanged.

### 4.4 Class scoping

All property-specific classes are prefixed (`kpi-`, `rs-`, `bd-`, `dph-`, `exp-`, `ae-`, `rex-`, `ph-`) and isolated to `property_styles_property.html`. The single class that conflicts with global styles is `.badge` (used differently on dashboard); resolved by scoping new badge styles to `.page .badge { ... }` since `.page` only exists on property pages.

## 5. Components

### 5.1 KPI row (`property_kpi.html`)

4 cards, identical visual layout, content varies by `client_type`.

| Card | client_type=`rentero` | client_type=`klient` / `z_klient` |
|---|---|---|
| 1. Zisk (`kpi-ok kpi-zisk`) | Value: `summary.zisk_czk = gross_payout − expenses_total − vat_balance`. Sub-row: `Hrubý zisk: {gross−expenses}` · `DPH = ±{vat_balance}` (DPH part only if `platce_dph`). | Value: `summary.client_payout_after_expenses_czk`. Sub-row: `Před výdaji: {client_payout_before}` · `{bank_account}` if any. |
| 2. Vyplaceno platformami (`kpi-blue`) | `summary.gross_payout_czk`. Sub: `Cena ubytování: {accommodation}` | (same) |
| 3. Rentero fee (`kpi-err`) | Label: `Rentero fee ({rate*100}%)` (or `Odměna Rentero (3 %)` for `z_klient`). Value: `−(rentero_fee + vat_rentero_fee)`. Sub: `Net: −{rentero_fee}` · `DPH: −{vat_rentero_fee}` (DPH only if > 0) | (same) |
| 4. Výdaje (`kpi-warn` if non-zero, `kpi-mute` if zero) | Value: `−{expenses_total}` or `—`. Sub: `{N} položek` · `DPH: +{vat_input}` (DPH part only if `client.platce_dph`) | (same) |

### 5.2 Reservations (`property_reservations.html`)

**Card header** — title `Rezervace` + counts (`active`, `nights`, `adjustments`) + filter-pills `VŠE / PROBLÉMY / PŘESUNY / VYLOUČENÉ` (with counts; disabled when count is 0).

**Table — 7 columns:**

| # | Class width | Content |
|---|---|---|
| 1 | `.rs-idx` 40px | `01`, `02`, … (loop.index, padded) |
| 2 | `.c` 48px | Channel SVG mark (Airbnb / Booking / Other) |
| 3 | — | Guest name + `· N hosté` hint |
| 4 | `.r` 148px | Date stack: `02.04 → 05.04` line + `3 noci` sub |
| 5 | `.r` 130px | Payout stack: `9 480 Kč` line + `ubyt. 8 400 Kč` sub |
| 6 | `.c` 150px | Status badge + `Banka` mini-indicator stack |
| 7 | `.rs-caret` 24px | Expand caret (hidden on `is_payout_adjustment`) |

**Row classes:**
- `.rs-row` — base
- `.rs-row-muted` if `is_excluded`
- `.rs-row-child` if `is_payout_adjustment` (italic guest, no caret, no expand)
- `.rs-row-open` when expanded (only one open at a time)

**Data attributes** for filter-JS: `data-status`, `data-channel`, `data-code`, `data-row-idx`.

**Footer (`<tfoot>`):** `Celkem · N aktivních · M nocí` left, `payout` + `ubyt` right. JS recomputes `data-totals` cells on filter change.

#### Expanded row — `property_reservation_detail.html`

Three-column groups grid:

- **Group 1 — Finance** (3 sub-cols): Provize platformy, City tax, Úklid + balíček
- **Group 2 — Identifikátor** (`cols-2`): Kód rezervace, Kurz EUR/CZK
- **Group 3 — Stav** (`cols-2`, conditional): Dorazilo na účet (✓ + date) OR Banka: Nedorazilo, plus Rozdíl/CSV-payout/Původní měsíc/Poznámka depending on status

**Action row** — 5 button slots:

```
[← MM/YYYY] [MM/YYYY →]   [Vyloučit]   [Úprava]               [Otevřít panel →]
```

- `← MM/YYYY` — Phase 1 disabled, Phase 3 wired to `move?direction=prev`
- `MM/YYYY →` — same, Phase 3
- `Vyloučit` — Phase 1 disabled, Phase 2 wired
- `Úprava` — Phase 1, toggles inline override-form below the action row (variant (i)). Auth-gated: hidden if `month_state.status == 'LOCKED'` or session role is `client`.
- `Otevřít panel →` — Phase 1, calls existing `FloatingPanel.open(code, slug, year, month, guest, channel)`

**Inline override-form** (auth-gated, hidden by default):

Form fields: `field` (select, populated from `override_field_labels`), `original` (read-only display, set by JS from row data-attrs), `new_value` (input, `verification_status` uses datalist from `verification_status_options`), `reason` (input, optional, max 200 chars). POST to existing `/property/{slug}/{year}/{month}/reservation/{code}/override`.

#### Status mapping table

`web_support.attach_mock_status(rows)` adds `_mock_status`, `_mock_status_class`, `_mock_status_label` to each row in-place.

| Production state | `_mock_status` | Badge class | Label | Filter-pill bucket |
|---|---|---|---|---|
| `is_excluded` | EXCLUDED | badge-mute | VYLOUČENO | EXCLUDED |
| `is_payout_adjustment` | ADJUSTMENT | badge-brand | ÚPRAVA | (no pill, child row) |
| `is_split_transaction` | SPLIT | badge-brand | SPLÁTKA | (no pill) |
| `display_year/month` ≠ current | MOVED_OUT | badge-info | PŘESUN DO MM | PŘESUNY |
| `adjustment_original_year/month` set | MOVED_IN | badge-info | PŘESUN Z MM | PŘESUNY |
| `verification_status == "MATCHED"` | MATCHED | badge-ok | MATCHED | (no pill) |
| `verification_status == "ROZDÍL"` | ROZDIL | badge-warn | ROZDÍL | PROBLÉMY |
| `verification_status == "CHYBÍ_V_CSV"` | CHYBI_V_CSV | badge-err | CHYBÍ V CSV | PROBLÉMY |
| `verification_status == "CHYBÍ_V_HOSTIFY"` | CHYBI_V_HOSTIFY | badge-err | CHYBÍ V HOSTIFY | PROBLÉMY |
| `verification_status == "ZRUŠENO"` | ZRUSENO | badge-mute | ZRUŠENO | EXCLUDED |
| `verification_status == "KE KONTROLE"` | KE_KONTROLE | badge-mute | KE KONTROLE | (no pill) |
| (default) | KE_KONTROLE | badge-mute | KE KONTROLE | (no pill) |

`bank_status` checked against `"DORAZILO"` / `"CHYBÍ"` / `"N/A"` per `report/bank.py:278`.

### 5.3 Breakdown (`property_breakdown.html`)

Card with collapsible header, table with 7 columns: `Kanál`, `Rez.`, `Ubytování` (`.income` strong-white), `Provize` (`.deduction` muted with `−`), `Příprava pokoje` (`.neutral`), `City tax` (`.neutral`), `Výplata` (`.bd-payout` highlight bg + bold).

Rows: Airbnb, Booking, Other (only rendered if count > 0). Footer: `Celkem` totals row.

Data: existing `row_breakdown` context var, no backend changes.

### 5.4 DPH summary (`property_dph_summary.html`)

Rendered only if `client.platce_dph == 1`.

Card header: title `Vyúčtování DPH` + state badge (`Nadměrný odpočet` ok / `K odvedení` dph) with absolute balance amount.

Body: 3-column `.dph-box` grid `(col1 | − | col2 | = | col3)`:

- **Col 1 — DPH na výstupu**: `+{vat_output}`. Hint: `Rentero fee · {vat_rentero_fee}` and `Příprava pokoje · {vat_room_prep}`.
- **Col 2 — DPH na vstupu (odpočet)**: `−{vat_input}`. Hint: `Z výdajů (sub-nájem, energie, služby…)` and `{N} položek s DPH` (`vat_input_count`, only items with `vat_rate IS NOT NULL AND vat_rate > 0`).
- **Col 3 — Saldo** (`.dph-col-result`): label is `K odvedení státu` or `Nadměrný odpočet`; value is `±{|vat_balance|}` with `.ok` if refund or `.neg` if owed.

### 5.5 Expenses (`property_expenses.html`)

**Card header** — title `Výdaje` + meta (`{N} položek` · `−{total}` · `DPH +{vat_input}` if `platce_dph`) + add button (`+ Přidat výdaj`, auth-gated).

**Form** — `property_expense_form.html` (new partial). Lives in DOM between the card-header and the table (matches mock's `<AddForm/>` placement). Hidden by default via `[hidden]`. Same form node is reused for add and edit; JS swaps the action URL, title text, submit label, and prefilled values when entering edit-mode.

```
[ Datum ] [ Kategorie ] [ Popis           ]
─────────────────────────────────────────
[ Bez DPH | + | DPH (0% 12% 21%) | = | Celkem ]
─────────────────────────────────────────
                              [Zrušit] [Uložit]
```

Three input fields (`amount_net_czk`, `amount_dph_czk`, `amount_czk`), interlinked via vanilla JS (blur on any field recomputes the others using the same algorithm as the mock: `net = gross / (1+rate); dph = gross - net`). Hidden `vat_rate` field syncs with the `0% / 12% / 21%` segment-control.

On edit: pencil click reads `data-expense-*` attrs from the row, populates form, switches `action` to `/expenses/{id}/edit`, changes title to `Upravit výdaj #{id}` and submit-label to `Uložit změny`, scrolls form into view.

**Table** — 7 columns: `Datum`, `Popis`, `Sazba` (VAT pill), `Bez DPH` (right-aligned), `DPH` (right-aligned, dph-text color or `—` if rate is 0/NULL), `Celkem` (right-aligned, strong), actions (pencil + trash icons, auth-gated).

Rows are grouped by category. Each group has a sub-header row (`.exp-cat-row`) with: colored dot + category name + `· N` count + `−{cat_sum}` total.

**Category dot mapping** lives in Jinja:

```jinja
{% set cat_dot_class = {
  'Sub-nájem':  'exp-dot-rent',
  'Energie':    'exp-dot-energy',
  'Služby':     'exp-dot-svc',
  'Opravy':     'exp-dot-fix',
  'Pojištění':  'exp-dot-ins',
} %}
```

Unknown categories → `exp-dot-other`.

**Trash button**: small icon next to pencil, POST form to existing `/expenses/{id}/delete` with `onsubmit="return confirm('Smazat výdaj «{description}»?')"`.

### 5.6 Override history (`property_override_history.html`)

Restyle only. Existing data and endpoints. Default-collapsed card with: reservation code, field, old → new, reason, date, state badge (`Aktivní` / `Obnoveno`), revert button (auth-gated, only on active rows).

### 5.7 Notify stack (`property_notify_stack.html`)

Stack of `notify`-strips above the KPI row (between intro and KPI). Strip variants:

- Lock warning: `Měsíc je uzamčen — {locked_at}` (warn)
- New-data warning: `Nová data dostupná od poslední generace` (warn)
- Generation FAILED: `Generace selhala — {error_detail}` (err)
- Change-log lines: `Změny: {N} položek od {date}` (info)
- Flash: success/error/info/amber, dismissible

Each strip is rendered conditionally based on existing context vars (`month_state`, `month_notifications`, `latest_change_lines`, `flash`, `generation_job`).

### 5.8 Generation progress (`property_generation_progress.html`)

Only rendered when `generation_job.status in ("PENDING", "RUNNING")`. Animated spinner + progress text. Auto-reload JS in `property_scripts.html` polls every N seconds (existing logic from current intro).

### 5.9 Mobile breakpoints

Single `@media (max-width: 1100px)` (tablet) — copied from mock:
- KPI row: 2-column grid
- DPH-box: stacks vertically, separator becomes top border
- Calculator-strip: wraps, operators hidden
- Add-form metadata row: 2 columns

Additional `@media (max-width: 640px)` (phone):
- KPI row: 2×2 grid
- Reservation table: converts to card-list — each row becomes a stacked card with channel-icon + guest + date + payout + status; expand-state is full-width below the card. Header columns hidden.
- Breakdown: horizontal scroll (no transformation)
- Expenses table: hide `Bez DPH` and `DPH` columns; show only `Datum / Popis / Sazba pill / Celkem / pencil+trash`
- Override history: hide `Důvod` column

## 6. Backend changes (Phase 1)

### 6.1 `report/summary.py`

Refactor the trailing `return { ... }` literal to build a `result = { ... }` variable, then append the new fields below before `return result`:

```python
result["vat_output_czk"] = result["dph_prefakturace_klient_czk"]  # alias for new templates
result["vat_input_czk"] = _r(sum((e.get("amount_dph_czk") or 0) for e in expenses if (e.get("vat_rate") is not None) and (e.get("vat_rate") > 0)))
result["vat_input_count"] = sum(1 for e in expenses if (e.get("vat_rate") is not None) and (e.get("vat_rate") > 0))
result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])
result["expenses_net_total_czk"] = _r(sum((e.get("amount_net_czk") if e.get("amount_net_czk") is not None else (e.get("amount_czk") or 0)) for e in expenses))
result["zisk_czk"] = _r(result["gross_payout_czk"] - result["expenses_total_czk"] - result["vat_balance_czk"]) if client_type == "rentero" else None
```

Existing fields (`dph_prefakturace_klient_czk`, `vat_rentero_fee_czk`, `vat_room_prep_czk`, `client_payout_*`, `gross_payout_czk`, `accommodation_income_czk`, `expenses_total_czk`) untouched.

### 6.2 `report/web_support.py` — new helpers

```python
def attach_mock_status(rows: list[dict]) -> None:
    """In-place: adds _mock_status, _mock_status_class, _mock_status_label per row.
    See §5.2 mapping table."""

def compute_status_counts(rows: list[dict]) -> dict:
    """Returns counts dict for filter-pills + card-meta:
    {all_rows, active, nights, adjustments, excluded, moved, problems}"""

def group_expenses_by_category(expenses: list[dict]) -> dict[str, list[dict]]:
    """Preserves insertion order; key = category_name or 'Ostatní'."""

def get_adjacent_month(year: int, month: int, direction: str) -> tuple[int, int]:
    """direction: 'prev' | 'next'. Wraps Dec ↔ Jan with year delta."""
```

### 6.3 `report/expenses_validation.py` — new file

```python
EPSILON_CZK = 0.02
ALLOWED_VAT_RATES = (0.0, 0.12, 0.21)

class ExpenseValidationError(Exception): ...

def validate_and_canonicalize(
    gross: float | None,
    net: float | None,
    dph: float | None,
    vat_rate: float | None,
) -> tuple[float, float, float, float]:
    """Returns (gross, canonical_net, canonical_dph, vat_rate) or raises.
    Canonical = round(gross / (1+rate), 2) for net; gross - net for dph.
    Raises if user-provided net or dph diverges from canonical by > EPSILON."""
```

Persists canonical values in DB regardless of what the client sent.

Tests in `tests/report/test_expenses_validation.py` cover: gross-only, all-fields-consistent, mismatched net, mismatched dph, gross ≤ 0, invalid rate, rate=0 edge case, rounding boundary.

### 6.4 `report/db.py` — schema migration

In `init_db` (idempotent):

```python
def _ensure_expense_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(expenses)")}
    if "amount_net_czk" not in cols:
        conn.execute("ALTER TABLE expenses ADD COLUMN amount_net_czk REAL")
    if "amount_dph_czk" not in cols:
        conn.execute("ALTER TABLE expenses ADD COLUMN amount_dph_czk REAL")
    if "vat_rate" not in cols:
        conn.execute("ALTER TABLE expenses ADD COLUMN vat_rate REAL")  # NULL = legacy/unknown

DEFAULT_EXPENSE_CATEGORIES = ["Sub-nájem", "Energie", "Služby", "Opravy", "Pojištění", "Ostatní"]

def _seed_default_categories_if_empty(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]
    if n == 0:
        for name in DEFAULT_EXPENSE_CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO expense_categories (name) VALUES (?)", (name,))
```

Both called from existing `init_db()` after `CREATE TABLE` statements.

Legacy expenses keep `vat_rate IS NULL`. They render with `—` in `Sazba` and `DPH` columns and are excluded from `vat_input_czk` aggregation. User can edit any record retroactively to set a rate.

### 6.5 `report/routes/property_routes.py` — new context vars

In existing `GET /property/{slug}/{year}/{month}` handler, after `_prepare_rows_for_display(rows)` has run (so that override-applied + display-formatted rows are stable):

```python
attach_mock_status(rows)  # in-place, adds _mock_status / _mock_status_class / _mock_status_label
counts = compute_status_counts(rows)
expenses_by_cat = group_expenses_by_category(expenses)
prev_year, prev_month = get_adjacent_month(year, month, "prev")
next_year, next_month = get_adjacent_month(year, month, "next")
totals = {
    "payout": sum((r.get("payout_czk") or 0) for r in rows if not r.get("is_excluded")),
    "ubyt":   sum((r.get("cena_ubytovani_czk") or 0) for r in rows if not r.get("is_excluded")),
}

context.update({
    "counts": counts,
    "expenses_by_cat": expenses_by_cat,
    "prev_month_target": {"year": prev_year, "month": prev_month},
    "next_month_target": {"year": next_year, "month": next_month},
    "totals": totals,
    "client": client,  # ensure client (with platce_dph) is in context
    "cat_dot_class": {
        "Sub-nájem": "exp-dot-rent",
        "Energie":   "exp-dot-energy",
        "Služby":    "exp-dot-svc",
        "Opravy":    "exp-dot-fix",
        "Pojištění": "exp-dot-ins",
    },
})
```

### 6.6 `report/routes/operations.py` — expense add/edit validation

Existing `add_expense` and `edit_expense` handlers wrap their parsing with `validate_and_canonicalize`. On `ExpenseValidationError` → flash error → redirect-back. On success: persist canonical `(gross, net, dph, rate)`.

### 6.7 New endpoint (Phase 2 only)

`POST /property/{slug}/{year}/{month}/reservation/{code}/exclude` — auth-required, lock-checked. Creates an `override_event` with `field='is_excluded'`, `new_value='1'`, `reason='Vyloučeno přes UI'`. Triggers regen, redirects back.

### 6.8 New endpoint (Phase 3 only)

`POST /property/{slug}/{year}/{month}/reservation/{code}/move` — accepts form param `direction` in `('prev', 'next')`. Computes target year/month via `get_adjacent_month`. Creates two `override_event` records: `field='display_year' new_value='{ty}'` and `field='display_month' new_value='{tm}'`. Auto-reason `Přesun do {tm:02d}/{ty} přes UI ({direction})`. Reads in `_prepare_rows_for_display` honor `display_year/month` overrides — current month renders the row as `MOVED_OUT` and target month renders it as `MOVED_IN`.

## 7. JavaScript architecture (`property_scripts.html`)

Single ES5-compatible script (no module system, runs at end of `<body>` like current code). Modules:

- **Section toggles** — `[data-section-toggle="X"]` toggles `[data-section-body="X"]` `hidden` + caret rotation. Persists state in `sessionStorage` keyed by `rentero_sec_<slug>_<YYYY>_<MM>_<X>` (per-property, fixes the cross-property collision in current code).
- **Filter pills** — `[data-filter-group="rs"] [data-filter]` clicks. Shows/hides `tr.rs-row` whose `[data-status]` doesn't match the bucket. Updates `[data-totals]` cells in `<tfoot>`. State in `sessionStorage` `rentero_rs_filter_<slug>_<YYYY>_<MM>`.
- **Expand row** — click on `.rs-row` (not on `<button>`) toggles `tr.rs-ex[data-for=code]` `hidden` + `.rs-row-open`. Only one open at a time; clicking another row closes the previous.
- **Override-form toggle** — `[data-action="override-toggle"]` toggles `[data-ov-form][hidden]` inside the same expanded row. Field-select change updates `[data-ov-original]` from row data-attrs and re-populates datalist.
- **Open panel** — `[data-action="open-panel"]` calls existing `FloatingPanel.open(code, slug, year, month, guest, channel)`.
- **Expense form** — `[data-action="expense-form-toggle"]` opens form in add-mode (reset state, action = `/expenses/add`, title = `Nový výdaj`). `[data-action="expense-edit"]` opens in edit-mode (reads `data-expense-*` attrs, action = `/expenses/{id}/edit`, populates fields, scrolls form into view).
- **Calculator** — three-way binding for `[data-calc-net]`, `[data-calc-dph]`, `[data-calc-gross]` + `[data-calc-rate-tabs]`. Algorithm matches `expenses.jsx:50-87` of the reference.
- **Generation auto-reload** — preserved from current code; runs only if `[data-generation-status="PENDING|RUNNING"]` is present.
- **Move/Exclude handlers** — Phase 1: no-op (buttons are `disabled`). Phase 2/3: confirm-dialog + native form submit.

## 8. What does not change in Phase 1

- `report/engine.py`, `report/calculator.py`, `report/verifier.py`, `report/bank.py` — unchanged
- `report/db.py` — only the two helpers in §6.4 added; existing schema preserved
- All existing endpoints keep their URLs, form-fields, and redirect behavior
- `templates/base.html`, `templates/partials/base_*` — only the token additions in §4.3
- FloatingPanel, RenteroMonthNav, theme toggle, cosmos animation, aurora layer — untouched
- Excel generation path (`report.main` subprocess) — out of scope, not touched

## 9. Risks / open items

- **Legacy expense rows with NULL `vat_rate`** display "—" in the `Sazba` and `DPH` columns. The `vat_input_czk` aggregate excludes them. If the user has dozens of such rows, the DPH summary will under-report until they're edited. Mitigation: provide a one-time admin script to bulk-set `vat_rate=0.21` for all NULL rows, run manually after Phase 1 deploy.
- **Status values containing the underscore-encoded form** (`CHYBÍ_V_CSV`, `CHYBÍ_V_HOSTIFY`) come from `report/verifier.py`. The mapping in `attach_mock_status` matches the literal string values; if `verifier.py` changes any of these constants, the helper must be updated. Tests in `tests/report/test_attach_mock_status.py` should pin these.
- **Mobile card-list for reservations at <640px** is the largest visual transformation; needs careful CSS-grid restructure to not regress the desktop table.
- **Phase 2 (`Vyloučit`) reuses the override-event mechanism** which is already proven. Phase 3 (`Přesunout`) requires `_prepare_rows_for_display` to honor `display_year/month` overrides — this is a new code path that must coexist with the existing `adjustment_original_year/month` MOVED_IN logic. Plan must check both interactions.

## 10. Testing strategy

- **Unit tests**:
  - `tests/report/test_expenses_validation.py` — happy paths + every error path (§6.3)
  - `tests/report/test_attach_mock_status.py` — every row state mapped to correct `_mock_status` (§5.2 table)
  - `tests/report/test_compute_status_counts.py` — counts arithmetic against fixture rows
  - `tests/report/test_get_adjacent_month.py` — December → January, January → December, mid-year both directions
  - `tests/report/test_summary_new_fields.py` — new vat_output / vat_input / vat_balance / zisk fields for all three client_types
- **Integration test** (Phase 1):
  - Render `/property/test-prop/2026/4` with seeded DB, assert response 200, key strings present (`Vyúčtování DPH` only when `platce_dph=1`, KPI variant matches `client_type`, all 4 filter-pills present)
- **No automated visual tests**; manual smoke after each phase using a known property with mixed status rows + several expense entries.

## 11. Deliverables per phase

### Phase 1
- 11 modified or new partials + 1 new property CSS file + 1 modified base styles file
- 4 new helper functions in `web_support.py`
- 1 new file `expenses_validation.py` with tests
- Schema migration in `db.py` (additive, idempotent)
- Default categories seed
- Updated `summary.py` with 6 new aggregate fields
- Updated `property_routes.py` and `operations.py`
- Manual smoke against staging property; merged to main; deployed

### Phase 2
- 1 new endpoint
- 1 button activation
- 1 confirm dialog
- Tests for the new endpoint

### Phase 3
- 1 new endpoint
- 2 button activations
- Updates to `_prepare_rows_for_display` and possibly `attach_mock_status` for MOVED_OUT
- Tests covering month-wrap (Dec→Jan, Jan→Dec) and round-trip override → revert
