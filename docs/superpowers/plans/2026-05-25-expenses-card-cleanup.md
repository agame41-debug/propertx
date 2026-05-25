# Výdaje Card Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the add-expense form's broken recurring-section layout and remove the duplicated "pravidelný" presentation from the expenses table (replace with a compact `↻` marker that manages the series).

**Architecture:** Pure template + CSS changes. The recurring section of the add form stops reusing the `.ae-row` 3-column grid and gets its own flex layout (which also fixes a visibility bug where inline `display:flex` overrode the `hidden` attribute). The expenses table drops its separate `PRAVIDELNÉ VÝDAJE` block; each recurring row renders a `↻` control whose `title` shows the period (resolved inline from the already-passed `expense_templates` list via `selectattr`) and whose click posts to `/expense-templates/{id}/delete`. No Python view-model change is required.

**Tech Stack:** Jinja2 templates, FastAPI test client / direct partial rendering, pytest.

---

## Refinement vs. spec

The design doc proposed adding a `templates_by_id` context key in `web_support.py`. This plan instead resolves the template inline in the Jinja template with `expense_templates | selectattr('id', 'equalto', e.template_id) | first`. Same behavior (the row shows the series period), one fewer file touched, no Python change. Deactivated templates (filtered out by `active_only=True`) simply fall back to a generic tooltip — exactly the accepted trade-off in the spec.

## File Structure

- `templates/partials/property_expense_form.html` — replace the recurring `<div class="ae-row" data-recurring-wrap …>` block (lines 81–96) with a `.ae-recurring` flex block.
- `templates/partials/property_expenses.html` — remove the `exp-templates` block (lines 34–58); replace the per-row `pravidelný` badge (lines 93–96) with the `↻` series control.
- `templates/partials/property_styles_property.html` — add `.ae-recurring*` rules (near the `.ae-row` rules, ~line 979) and `.exp-recur*` rules (in the EXPENSES section, ~line 902).
- `tests/test_web_generation.py` — append render tests for both partials.

No JavaScript change: `property_scripts.html` keeps toggling `[data-recurring-toggle]` / `[data-recurring-period].hidden` / `[data-recurring-wrap]`; all three hooks are preserved.

---

## Task 1: Fix the add-expense form recurring-section layout

**Files:**
- Modify: `templates/partials/property_expense_form.html:81-96`
- Modify: `templates/partials/property_styles_property.html` (add CSS after line 979)
- Test: `tests/test_web_generation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_generation.py`:

```python
# ───────────────────── Výdaje card cleanup ─────────────────────

def test_expense_form_recurring_uses_flex_not_grid_row():
    # The recurring section must use the dedicated .ae-recurring flex layout,
    # NOT the .ae-row 3-column grid (140/180/1fr) that squashed the month inputs.
    tmpl = web_module.templates.get_template("partials/property_expense_form.html")
    html = tmpl.render(
        request=_admin_request(),
        slug="x", year=2026, month=5,
        categories=[{"id": 1, "name": "Energie"}],
    )
    assert 'class="ae-recurring" data-recurring-wrap' in html
    assert 'class="ae-row" data-recurring-wrap' not in html
    # Period block keeps both month inputs and starts hidden...
    assert 'name="start_ym"' in html and 'name="end_ym"' in html
    assert re.search(r'data-recurring-period[^>]*\bhidden\b', html)
    # ...and no inline display:flex (the bug that kept it visible while unchecked)
    assert 'display:flex' not in html


def test_styles_define_ae_recurring():
    css = web_module.templates.get_template(
        "partials/property_styles_property.html"
    ).render()
    assert ".ae-recurring" in css
    assert ".ae-recurring-period:not([hidden])" in css
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_generation.py::test_expense_form_recurring_uses_flex_not_grid_row tests/test_web_generation.py::test_styles_define_ae_recurring -v`
Expected: FAIL — `'class="ae-recurring" data-recurring-wrap' in html` is False (form still uses `ae-row`); `.ae-recurring` not in CSS.

- [ ] **Step 3: Add the CSS rules**

In `templates/partials/property_styles_property.html`, immediately after the `.ae-field { … }` rule (currently line 981), add:

```css
/* Recurring toggle row — its own flex layout, NOT the .ae-row grid.
   The grid's 140/180px columns squashed the month inputs; flex lets them
   size naturally. `:not([hidden])` re-asserts hiding so the period block
   actually collapses when the checkbox is off (author display:flex would
   otherwise beat the UA [hidden] rule). */
.ae-recurring {
  display: flex; flex-wrap: wrap; align-items: flex-end;
  gap: 14px 28px; margin-bottom: 18px;
}
.ae-check {
  display: inline-flex; align-items: center; gap: 8px;
  height: 34px; cursor: pointer; white-space: nowrap;
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .1em; color: var(--text-200);
}
.ae-check input[type="checkbox"] {
  width: 14px; height: 14px; accent-color: var(--color-primary);
}
.ae-check-sub { color: var(--text-400); }
.ae-recurring-period:not([hidden]) {
  display: flex; gap: 16px; align-items: flex-end;
}
.ae-field-month { width: 160px; }
```

- [ ] **Step 4: Rewrite the form's recurring block**

In `templates/partials/property_expense_form.html`, replace the block at lines 80–96 (from the `{# Recurring toggle … #}` comment through its closing `</div>`):

```html
  {# Recurring toggle (add-mode only). When on, submit posts to /expense-templates/add. #}
  <div class="ae-recurring" data-recurring-wrap>
    <label class="ae-check">
      <input type="checkbox" data-recurring-toggle>
      <span>Pravidelný <span class="ae-check-sub">(každý měsíc)</span></span>
    </label>
    <div class="ae-recurring-period" data-recurring-period hidden>
      <div class="ae-field ae-field-month">
        <label class="ae-k">Od měsíce</label>
        <input class="ae-in" type="month" name="start_ym" value="{{ year }}-{{ '%02d'|format(month) }}">
      </div>
      <div class="ae-field ae-field-month">
        <label class="ae-k">Do měsíce</label>
        <input class="ae-in" type="month" name="end_ym" value="" title="Prázdné = bez konce">
      </div>
    </div>
  </div>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_web_generation.py::test_expense_form_recurring_uses_flex_not_grid_row tests/test_web_generation.py::test_styles_define_ae_recurring -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add templates/partials/property_expense_form.html templates/partials/property_styles_property.html tests/test_web_generation.py
git commit -m "fix(expenses): recurring form section uses flex layout, not the ae-row grid"
```

---

## Task 2: Drop the recurring block and add the `↻` series marker

**Files:**
- Modify: `templates/partials/property_expenses.html:34-58` (remove block), `:93-96` (replace badge)
- Modify: `templates/partials/property_styles_property.html` (add CSS after line 902)
- Test: `tests/test_web_generation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_generation.py`:

```python
def _expenses_ctx(**over):
    recurring = {
        "id": 101, "date": "2026-05-10", "description": "Internet",
        "category_id": 9, "vat_rate": 0.21,
        "amount_net_czk": 1560.33, "amount_dph_czk": 327.67, "amount_czk": 1888.0,
        "template_id": 7,
    }
    oneoff = {
        "id": 102, "date": "2026-05-12", "description": "Elektřina",
        "category_id": 9, "vat_rate": 0.21,
        "amount_net_czk": 100.0, "amount_dph_czk": 21.0, "amount_czk": 121.0,
        "template_id": None,
    }
    ctx = {
        "request": _admin_request(),
        "slug": "x", "year": 2026, "month": 5,
        "categories": [{"id": 9, "name": "Ostatní"}],
        "expenses": [recurring, oneoff],
        "expenses_by_cat": {"Ostatní": [recurring, oneoff]},
        "expense_templates": [{
            "id": 7, "description": "Internet",
            "start_ym": "2026-04", "end_ym": None, "amount_czk": 1222.5,
        }],
        "cat_dot_class": {},
        "month_state": {"status": "OPEN"},
        "_show_dph": True,
        "summary": {
            "expenses_total_czk": 2009.0, "vat_input_czk": 348.67,
            "expenses_net_total_czk": 1660.33,
        },
    }
    ctx.update(over)
    return ctx


def _render_expenses(**over):
    return web_module.templates.get_template(
        "partials/property_expenses.html"
    ).render(**_expenses_ctx(**over))


def test_expenses_table_drops_recurring_block():
    html = _render_expenses()
    assert "Pravidelné výdaje" not in html      # block heading gone
    assert "exp-templates" not in html          # block markup gone
    assert ">pravidelný<" not in html           # old per-row badge text gone


def test_expenses_recurring_row_has_series_delete_control():
    html = _render_expenses()
    assert 'action="/expense-templates/7/delete"' in html  # series delete
    assert "exp-recur" in html                             # the marker class
    assert "2026-04" in html                               # period in tooltip


def test_expenses_oneoff_row_has_no_series_control():
    html = _render_expenses()
    # Exactly one series-delete form total → the one-off row got no marker.
    assert html.count("/expense-templates/") == 1


def test_expenses_recurring_marker_readonly_when_locked():
    html = _render_expenses(month_state={"status": "LOCKED"})
    assert "/expense-templates/7/delete" not in html  # no delete form when locked
    assert "exp-recur" in html                         # marker still shown
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_generation.py -k "expenses_table_drops or recurring_row_has or oneoff_row or marker_readonly" -v`
Expected: FAIL — block still present (`exp-templates`/`Pravidelné výdaje` found), no `/expense-templates/7/delete` in a row, `exp-recur` missing.

- [ ] **Step 3: Remove the `exp-templates` block**

In `templates/partials/property_expenses.html`, delete the entire block currently at lines 34–58 (from `{% if expense_templates %}` through its matching `{% endif %}` and the trailing blank line). The card goes straight from the included form to the `<table class="t exp-table">`.

- [ ] **Step 4: Replace the per-row badge with the `↻` series control**

In `templates/partials/property_expenses.html`, replace the POPIS cell (currently lines 93–96):

```html
              <td style="color:var(--text-100)">
                {{ e.description }}
                {% if e.template_id %}<span class="badge" style="background:var(--color-primary-50,#eef);color:var(--color-primary);font-size:10px;">pravidelný</span>{% endif %}
              </td>
```

with:

```html
              <td style="color:var(--text-100)">
                {%- if e.template_id -%}
                  {%- set _tmpl = (expense_templates | selectattr('id', 'equalto', e.template_id) | first) -%}
                  {%- set _period = ('od ' ~ _tmpl.start_ym ~ ' → ' ~ (_tmpl.end_ym or '…')) if _tmpl else '' -%}
                  {%- if month_state.status != 'LOCKED' and _user and _user.role != 'client' -%}
                  <form method="post" action="/expense-templates/{{ e.template_id }}/delete" class="exp-recur-form"
                        onsubmit="return confirm('Smazat celou pravidelnou serii «{{ e.description|e }}»? (tento i příští měsíce se přestanou generovat)');">
                    {{ csrf_input(request) }}
                    <input type="hidden" name="property_slug" value="{{ slug }}">
                    <input type="hidden" name="year" value="{{ year }}">
                    <input type="hidden" name="month" value="{{ month }}">
                    <button type="submit" class="exp-recur" title="Pravidelný{% if _period %} · {{ _period }}{% endif %} · kliknutím smazat celou serii">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
                    </button>
                  </form>
                  {%- else -%}
                  <span class="exp-recur" title="Pravidelný{% if _period %} · {{ _period }}{% endif %}">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
                  </span>
                  {%- endif -%}
                {%- endif -%}
                {{ e.description }}
              </td>
```

- [ ] **Step 5: Add the `↻` marker CSS**

In `templates/partials/property_styles_property.html`, immediately after the `.exp-edit:hover` light-theme rule (currently line 902), add:

```css
/* Recurring marker — replaces the old "pravidelný" badge.
   As a <button> (editable months) it deletes the whole series on click;
   as a <span> (locked / client) it is a read-only marker. Tooltip carries
   the period. */
.exp-recur-form { display: inline; }
.exp-recur {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; padding: 0; margin-right: 6px;
  vertical-align: -3px;
  background: transparent; border: none; cursor: pointer;
  color: var(--text-400); border-radius: var(--radius-sm);
  transition: color var(--t-fast) var(--ease);
}
button.exp-recur:hover { color: var(--text-100); }
span.exp-recur { cursor: default; }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_web_generation.py -k "expenses_table_drops or recurring_row_has or oneoff_row or marker_readonly" -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add templates/partials/property_expenses.html templates/partials/property_styles_property.html tests/test_web_generation.py
git commit -m "feat(expenses): drop recurring block; compact ↻ series marker on rows"
```

---

## Task 3: Full regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the targeted file**

Run: `pytest tests/test_web_generation.py -v`
Expected: PASS (all, including the 6 new tests).

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS, 0 failures. (Baseline is fully green per `reference_test_baseline` — any failure here is a real regression introduced by this change, most likely a template render error.)

- [ ] **Step 3: Manual visual confirmation (optional but recommended)**

Start the web app and open a property month that has a recurring expense (e.g. one with an `Internet` template). Verify:
1. Click "+ Přidat výdaj", toggle "Pravidelný" — the Od/Do month inputs appear inline, fully readable, aligned; toggling off hides them.
2. The table shows no top "Pravidelné výdaje" block; the Internet row shows the `↻` marker before the name; hovering shows the period; clicking asks to delete the whole series.
```
