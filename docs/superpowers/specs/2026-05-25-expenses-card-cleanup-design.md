# Expenses card cleanup — design

**Date:** 2026-05-25
**Status:** approved (design)
**Scope:** property page — `Výdaje` card (table + add/edit form)

## Problem

The `Výdaje` card on the property page has two issues:

1. **Add-expense form breaks layout.** When the form opens, the recurring
   ("Pravidelný") section misaligns: the checkbox label wraps to two lines and
   clips, the two month inputs are cut off ("dube…", "-------- ---"), the
   "(prázdné = bez konce)" hint wraps and breaks vertical alignment, and a wide
   empty gap is left on the right.

2. **Recurring expenses are shown twice.** The same recurring item (e.g.
   Internet) appears in the top `PRAVIDELNÉ VÝDAJE` block (the template
   definition + period + base amount) AND as a normal table row carrying a
   second `pravidelný` badge (the materialized expense for the month). The word
   "pravidelný" appears three times (section header + two badges).

## Root cause

### Form
`templates/partials/property_expense_form.html` reuses the `.ae-row` class for
the recurring section:

```html
<div class="ae-row" data-recurring-wrap style="align-items:center;gap:16px;">
```

`.ae-row` is `display:grid; grid-template-columns:140px 180px 1fr` — a layout
designed for the Datum/Kategorie/Popis row. The inline style overrides only
`align-items` and `gap`, not `display`/`grid-template-columns`. So the recurring
section's two children are crammed into the 140px and 180px grid columns:
- checkbox label → 140px → 2-line wrap, clipped by `.ae-k { height:14px }`;
- period block (two month fields + 12px gap) → 180px → inputs squeezed to ~84px,
  the "Do měsíce (prázdné = bez konce)" label wraps;
- the 1fr column is left empty.

### Table
`templates/partials/property_expenses.html` renders a dedicated
`exp-templates` block (lines 34–58) listing each active template, while the
materialized expenses already appear as table rows with a `pravidelný` badge
(line 95). The template definition and the materialized row are different
records (template base amount vs. actual month amount), but visually duplicative.

## Design

### Part 1 — Form layout fix

Files: `property_expense_form.html`, `property_styles_property.html`.

Replace the `.ae-row` grid on the recurring section with a dedicated flex layout:

- New class `.ae-recurring`: `display:flex; flex-wrap:wrap; align-items:flex-end;
  gap:14px 28px; margin-bottom:18px`. Checkbox and period fields are no longer
  bound to the 140/180px columns.
- Checkbox label: single line — `white-space:nowrap`, height matched to the
  input (34px) so `align-items:flex-end` aligns it with the field inputs.
- Month fields: fixed width ~160px each so "2026-04" + the calendar control are
  fully visible.
- "Do měsíce" label shortened; the "prázdné = bez konce" hint moves to the
  input's `title` attribute (a `type=month` input cannot show a placeholder),
  removing the two-line label that broke alignment.
- Narrow screens: the period block wraps under the checkbox via `flex-wrap`;
  no separate grid media rule needed for this section.

No JS change: `_setRecurring()` keeps toggling `data-recurring-period.hidden` and
swapping the POST action between `/expenses/add` and `/expense-templates/add`.

### Part 2 — Table: single source of truth + `↻` marker

Files: `property_expenses.html`, `property_styles_property.html`, `web_support.py`.

- **Remove** the entire `exp-templates` block (lines 34–58). The table is the
  single source of truth for what is charged this month.
- On a recurring row, **replace the `pravidelný` badge with a compact `↻`
  control** (small muted SVG) placed before the description.
- The `↻` control carries all series management:
  - **tooltip** (`title`) = `Pravidelný · od {start_ym} → {end_ym or "…"}`;
  - **click** = delete the whole series via `POST /expense-templates/{id}/delete`,
    guarded by a confirm: «Smazat celou pravidelnou serii «{desc}»? (tento i
    příští měsíce se přestanou generovat)».
- Existing per-row actions are preserved and now have distinct meanings:
  - ✏️ edit — edit this month's amounts (`/expenses/{id}/...`);
  - 🗑 delete — skip only this month (`/expenses/{id}/delete`, which already adds
    a `expense_template_skip`).
- View-model: add `templates_by_id = {t["id"]: t for t in expense_templates}` to
  the property template context so the row can render the period in the `↻`
  tooltip. If a row's `template_id` is not in the map (template deactivated), the
  `↻` shows a generic `Pravidelný` tooltip without a period and still posts the
  series-delete (the route is a no-op / harmless if already gone).

### Accepted trade-off

If a recurring expense is skipped for the current month, or its template starts
in a future month, no row exists this month — so the series is managed from a
month where it materializes. Previously the top block surfaced such templates;
now it does not. Acceptable for v1.

## Testing

Extend `tests/test_web_generation.py` (property page render):

1. The `Pravidelné výdaje` block / `exp-templates` markup is **absent** from the
   rendered HTML.
2. A row whose expense has a `template_id` renders a `↻` control whose form
   action is `/expense-templates/{id}/delete`.
3. A non-recurring expense row renders **no** `↻` control and no `pravidelný`
   text.
4. The add-expense form's recurring wrapper uses `ae-recurring`, not `ae-row`.

## Out of scope

- Recurring-template editing UI (period/amount edit) — only create (via the
  form's recurring toggle) and delete-series remain.
- Any change to materialization logic in `db_expense_templates.py` or the engine.
- DPH / totals calculation.
