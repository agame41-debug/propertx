# DPH settlement for all client objects (incl. provize) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For klient/z_klient objects, fold the Airbnb/Booking commission VAT (and recharged-expense VAT) into the output VAT, and show the "Vyúčtování DPH" settlement (card + KPI/expenses DPH lines) for **all** objects — keeping the "Plátce DPH" badge tied to the client's real VAT status.

**Architecture:** All math lives in `build_report_summary` ([report/summary.py](../../../report/summary.py)). Output VAT for klient/z_klient becomes `prefakturace + commission VAT + recharged-expense VAT`; the recharged expenses also sit in `vat_input`, so they net out in `vat_balance`. Display gating splits: a new always-true `_show_dph` flag drives the settlement display, while `_is_dph_applicable` keeps driving only the "Plátce DPH" badge.

**Tech Stack:** Python 3, Jinja2, pytest. Test interpreter: `.venv-win/Scripts/python.exe`. Spec: [docs/superpowers/specs/2026-05-23-dph-for-all-client-objects-design.md](../specs/2026-05-23-dph-for-all-client-objects-design.md).

---

## File Structure

- `report/summary.py` — add `platform_commission_vat_czk` aggregate; move the `vat_input` block before the output-VAT branch; klient/z_klient output VAT = prefakturace + commission VAT + recharged-expense VAT.
- `templates/property.html` — add `_show_dph = True`; gate the card include on it.
- `templates/partials/property_kpi.html` — two DPH gates `_is_dph_applicable` → `_show_dph`.
- `templates/partials/property_expenses.html` — one DPH gate `_is_dph_applicable` → `_show_dph`.
- `templates/partials/property_dph_summary.html` — klient breakdown gains "Provize (21 %)" and "Přefakturované výdaje" lines.
- `tests/test_summary.py`, `tests/test_summary_new_fields.py` — computation tests.
- `tests/test_web_generation.py` — render tests (KPI DPH for klient; "Provize" line).

---

### Task 1: commission VAT in `build_report_summary`

**Files:**
- Modify: `report/summary.py`
- Test: `tests/test_summary.py`, `tests/test_summary_new_fields.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_summary.py`:

```python
# ── Commission VAT in klient/z_klient output VAT ────────────────────────
# Rentero recharges the Airbnb/Booking commission to the client with 21%
# output VAT (the reverse charge on the platform invoice nets to zero), so the
# object's output VAT = prefakturace (fee + room prep) + commission VAT +
# recharged-expense VAT. Recharged expenses also sit in vat_input → net zero.

def _klient_provize_rows():
    return [{"payout_czk": 12000.0, "cena_ubytovani_czk": 10000.0,
             "provize_czk": 5000.0, "dph_provize_czk": 1050.0,
             "city_tax_czk": 0, "priprava_pokoje_czk": 0,
             "dph_uklid_balicky_czk": 100.0}]


def test_klient_output_vat_includes_commission():
    prop = {"client_type": "klient", "rentero_commission": 0.20, "vat_rate": 0.21}
    s = build_report_summary(_klient_provize_rows(), prop)
    assert s["platform_commission_vat_czk"] == 1050.0
    assert s["vat_rentero_fee_czk"] == 420.0            # 10000 × 0.20 × 0.21
    assert s["vat_room_prep_czk"] == 100.0
    assert s["dph_prefakturace_klient_czk"] == 520.0     # 420 + 100
    assert s["vat_output_czk"] == 1570.0                 # 520 + 1050 + 0 input
    assert s["vat_balance_czk"] == 1570.0


def test_klient_recharged_expense_nets_out_in_balance():
    prop = {"client_type": "klient", "rentero_commission": 0.20, "vat_rate": 0.21}
    expenses = [{"amount_czk": 363.0, "amount_dph_czk": 63.0,
                 "amount_net_czk": 300.0, "vat_rate": 0.21}]
    s = build_report_summary(_klient_provize_rows(), prop, expenses=expenses)
    assert s["vat_input_czk"] == 63.0
    assert s["vat_output_czk"] == 1633.0                 # 520 + 1050 + 63 recharged
    assert s["vat_balance_czk"] == 1570.0                # expense nets out
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q -k "klient_output_vat or recharged_expense"`
Expected: FAIL — `KeyError: 'platform_commission_vat_czk'`.

- [ ] **Step 3: Add the `platform_commission_vat_czk` aggregate** — in `report/summary.py`, immediately after the `platform_commission_czk = ...` line:

```python
    platform_commission_czk = _r(sum(float(r.get("provize_czk") or 0) for r in rows))
    platform_commission_vat_czk = _r(sum(float(r.get("dph_provize_czk") or 0) for r in rows))
    room_prep_czk = _r(sum(float(r.get("priprava_pokoje_czk") or 0) for r in rows))
```

- [ ] **Step 4: Expose it in the result dict** — add the line right after `"platform_commission_czk": platform_commission_czk,`:

```python
        "platform_commission_czk": platform_commission_czk,
        "platform_commission_vat_czk": platform_commission_vat_czk,
        "room_prep_czk": room_prep_czk,
```

- [ ] **Step 5: Reorder vat_input before output VAT, and add the klient formula** — replace this whole block:

```python
    # ── Output VAT (DPH na výstupu) ──────────────────────────────────────
    #   klient / z_klient → Rentero's prefakturace VAT (fee + room prep);
    #                       alias of dph_prefakturace_klient_czk.
    #   rentero           → Rentero is the accommodation supplier and owes the
    #                       12% reduced-rate accommodation VAT on the full guest
    #                       consideration (payout + platform commission − city
    #                       tax), extracted from the VAT-inclusive gross. This
    #                       replaces the prefakturace breakdown (the gross
    #                       already contains úklid/balíčky, so the separate 21%
    #                       room-prep line would double-tax them).
    if client_type == "rentero":
        accommodation_gross_czk = _r(
            gross_payout_czk + platform_commission_czk - city_tax_czk
        )
        accommodation_vat_czk = _r(
            accommodation_gross_czk
            * ACCOMMODATION_VAT_RATE
            / (1 + ACCOMMODATION_VAT_RATE)
        )
        result["accommodation_gross_czk"] = accommodation_gross_czk
        result["accommodation_vat_czk"] = accommodation_vat_czk
        result["vat_output_czk"] = accommodation_vat_czk
    else:
        result["vat_output_czk"] = result["dph_prefakturace_klient_czk"]

    # vat_input: sum of DPH from expenses that have a VAT rate set.
    # Legacy expenses with NULL vat_rate are excluded from the aggregate
    # so we don't lie about the deduction.
    rated_expenses = [
        e for e in expenses
        if (e.get("vat_rate") is not None) and (float(e.get("vat_rate") or 0) > 0)
    ]
    result["vat_input_czk"] = _r(sum(float(e.get("amount_dph_czk") or 0) for e in rated_expenses))
    result["vat_input_count"] = len(rated_expenses)
    result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])
```

with:

```python
    # vat_input: sum of DPH from expenses that have a VAT rate set.
    # Legacy expenses with NULL vat_rate are excluded from the aggregate so we
    # don't lie about the deduction. Computed before output VAT so the
    # klient/z_klient branch can fold recharged-expense VAT into the output.
    rated_expenses = [
        e for e in expenses
        if (e.get("vat_rate") is not None) and (float(e.get("vat_rate") or 0) > 0)
    ]
    result["vat_input_czk"] = _r(sum(float(e.get("amount_dph_czk") or 0) for e in rated_expenses))
    result["vat_input_count"] = len(rated_expenses)

    # ── Output VAT (DPH na výstupu) ──────────────────────────────────────
    #   rentero           → 12% reduced-rate accommodation VAT on the full
    #                       guest consideration (payout + commission − city
    #                       tax). The commission is already inside this base.
    #   klient / z_klient → Rentero recharges costs to the client with output
    #                       VAT and charges its odměna with VAT. Output VAT =
    #                       prefakturace (fee + room prep) + commission VAT (net
    #                       of the Airbnb/Booking reverse charge) +
    #                       recharged-expense VAT. The recharged expenses also
    #                       sit in vat_input, so they net out in the balance.
    if client_type == "rentero":
        accommodation_gross_czk = _r(
            gross_payout_czk + platform_commission_czk - city_tax_czk
        )
        accommodation_vat_czk = _r(
            accommodation_gross_czk
            * ACCOMMODATION_VAT_RATE
            / (1 + ACCOMMODATION_VAT_RATE)
        )
        result["accommodation_gross_czk"] = accommodation_gross_czk
        result["accommodation_vat_czk"] = accommodation_vat_czk
        result["vat_output_czk"] = accommodation_vat_czk
    else:
        result["vat_output_czk"] = _r(
            result["dph_prefakturace_klient_czk"]
            + platform_commission_vat_czk
            + result["vat_input_czk"]
        )

    result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])
```

- [ ] **Step 6: Run the new tests, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q -k "klient_output_vat or recharged_expense"`
Expected: PASS (2 tests).

- [ ] **Step 7: Fix the now-broken alias test** — in `tests/test_summary_new_fields.py`, replace:

```python
def test_vat_output_alias_for_klient():
    # klient/z_klient: output VAT is still the prefakturace alias.
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    assert s["vat_output_czk"] == s["dph_prefakturace_klient_czk"]
```

with:

```python
def test_vat_output_for_klient_is_prefakturace_plus_commission_plus_input():
    # klient/z_klient: output VAT = prefakturace + commission VAT + recharged
    # expense VAT (no longer a plain alias of dph_prefakturace_klient_czk).
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    expected = round(
        s["dph_prefakturace_klient_czk"]
        + s["platform_commission_vat_czk"]
        + s["vat_input_czk"],
        2,
    )
    assert s["vat_output_czk"] == expected
```

- [ ] **Step 8: Run both summary files, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py tests/test_summary_new_fields.py -q`
Expected: PASS (all). `test_vat_balance_positive_means_owed`, `test_zisk_present_for_rentero`, and `test_vat_output_is_accommodation_vat_for_rentero` stay green (rentero/relational).

- [ ] **Step 9: Commit**

```bash
git add report/summary.py tests/test_summary.py tests/test_summary_new_fields.py
git commit -m "feat(summary): include commission VAT in klient/z_klient output VAT"
```

---

### Task 2: show DPH for all objects + provize line

**Files:**
- Modify: `templates/property.html`, `templates/partials/property_kpi.html`, `templates/partials/property_expenses.html`, `templates/partials/property_dph_summary.html`
- Test: `tests/test_web_generation.py`

- [ ] **Step 1: Write the failing tests** — in `tests/test_web_generation.py`, (a) update the `_render_property_kpi` helper to also pass `_show_dph`, and (b) append two tests.

Update the helper render call:

```python
def _render_property_kpi(*, is_rentero_owned, summary, prop, expenses=None, is_dph=True):
    tmpl = web_module.templates.get_template("partials/property_kpi.html")
    return tmpl.render(
        _is_rentero_owned=is_rentero_owned,
        _is_dph_applicable=is_dph,
        _show_dph=is_dph,
        summary=summary,
        prop=prop,
        expenses=expenses or [],
    )
```

Append:

```python
_KLIENT_PROVIZE_DPH_SUMMARY = {
    "vat_output_czk": 1570.0,
    "vat_input_czk": 0.0,
    "vat_input_count": 0,
    "vat_balance_czk": 1570.0,
    "vat_rentero_fee_czk": 420.0,
    "vat_room_prep_czk": 100.0,
    "platform_commission_vat_czk": 1050.0,
}


def test_property_dph_summary_klient_shows_provize_line():
    html = _render_property_dph_summary(summary=_KLIENT_PROVIZE_DPH_SUMMARY)
    assert "Provize" in html
    assert "Ubytovací služby" not in html   # not a Rentero-owned object


def test_property_kpi_klient_shows_dph_row_when_show_dph():
    summary = dict(_RENTERO_OWNED_SUMMARY, vat_balance_czk=1570.0)
    html = _render_property_kpi(
        is_rentero_owned=False, summary=summary,
        prop={"client_type": "klient"}, is_dph=True,
    )
    assert "DPH&nbsp;=" in html
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -q -k "klient_shows_provize or klient_shows_dph_row"`
Expected: `test_property_dph_summary_klient_shows_provize_line` FAILS (no "Provize"). (`klient_shows_dph_row` passes already — line 52 currently uses `_is_dph_applicable` which the helper still passes; it will keep passing after the swap.)

- [ ] **Step 3: Add the `_show_dph` flag** — in `templates/property.html`, after the `_is_dph_applicable` line:

```jinja
{% set _is_dph_applicable = _is_rentero_owned or (client and client.platce_dph) %}
{# Rentero is itself a VAT payer and settles DPH on every managed object
   (its odměna, reverse-charged platform commission, expense input VAT), so the
   DPH settlement is shown for all objects. The "Plátce DPH" badge stays tied
   to _is_dph_applicable (the client's real VAT status). #}
{% set _show_dph = True %}
```

- [ ] **Step 4: Gate the card include on `_show_dph`** — in `templates/property.html`, change:

```jinja
  {% if _is_dph_applicable %}
    {% include "partials/property_dph_summary.html" %}
  {% endif %}
```

to:

```jinja
  {% if _show_dph %}
    {% include "partials/property_dph_summary.html" %}
  {% endif %}
```

- [ ] **Step 5: Swap the KPI DPH gates** — in `templates/partials/property_kpi.html`, change the KPI-1 klient DPH gate `{% if _is_dph_applicable %}` (the one directly above `<span>DPH&nbsp;=</span>` in the non-rentero branch) to `{% if _show_dph %}`, and change the KPI-4 Výdaje gate:

```jinja
        {% if _is_dph_applicable and (summary.vat_input_czk or 0) > 0 %}
```

to:

```jinja
        {% if _show_dph and (summary.vat_input_czk or 0) > 0 %}
```

- [ ] **Step 6: Swap the expenses DPH gate** — in `templates/partials/property_expenses.html`, change:

```jinja
        {% if _is_dph_applicable and (summary.vat_input_czk or 0) > 0 %}
```

to:

```jinja
        {% if _show_dph and (summary.vat_input_czk or 0) > 0 %}
```

- [ ] **Step 7: Add the Provize / recharged-expense lines** — in `templates/partials/property_dph_summary.html`, replace the klient `{% else %}` breakdown:

```jinja
        {% else %}
          <div class="dph-row">
            <span class="dph-row-k">Rentero fee</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}</span>
          </div>
          <div class="dph-row">
            <span class="dph-row-k">Příprava pokoje</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}</span>
          </div>
        {% endif %}
```

with:

```jinja
        {% else %}
          <div class="dph-row">
            <span class="dph-row-k">Rentero fee</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}</span>
          </div>
          <div class="dph-row">
            <span class="dph-row-k">Příprava pokoje</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}</span>
          </div>
          {% if (summary.platform_commission_vat_czk or 0) > 0 %}
          <div class="dph-row">
            <span class="dph-row-k">Provize (21&nbsp;%)</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.platform_commission_vat_czk or 0) }}</span>
          </div>
          {% endif %}
          {% if (summary.vat_input_czk or 0) > 0 %}
          <div class="dph-row">
            <span class="dph-row-k">Přefakturované výdaje</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_input_czk or 0) }}</span>
          </div>
          {% endif %}
        {% endif %}
```

- [ ] **Step 8: Run the render tests, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -q -k "dph_summary or klient_shows_dph_row"`
Expected: PASS (all dph_summary tests + the KPI klient DPH test).

- [ ] **Step 9: Commit**

```bash
git add templates/property.html templates/partials/property_kpi.html templates/partials/property_expenses.html templates/partials/property_dph_summary.html tests/test_web_generation.py
git commit -m "feat(property): show DPH settlement for all objects + provize line"
```

---

### Task 3: full suite, push, deploy

- [ ] **Step 1: Full suite** — run `.venv-win/Scripts/python.exe -m pytest -q`. Expected: 16 baseline failures and **no new failures**; the new tests + rewritten alias test pass. If any non-baseline test fails, stop and investigate.

- [ ] **Step 2: Push** — `git push origin main`.

- [ ] **Step 3: Deploy (CONFIRM WITH USER FIRST)** — in-place patch on prod of the 5 changed files, with a timestamped backup, then restart. Server git is stale/dirty — copy committed contents over SSH, do NOT `git pull`.

```bash
TS=$(date +%Y%m%d_%H%M%S)
ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "mkdir -p ~/backups/predeploy_$TS"
for f in report/summary.py templates/property.html templates/partials/property_kpi.html templates/partials/property_expenses.html templates/partials/property_dph_summary.html; do
  ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cp ~/rentero/$f ~/backups/predeploy_$TS/$(basename $f)"
  git show HEAD:$f | ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cat > ~/rentero/$f"
done
```

- [ ] **Step 4: Verify sha256** — for each of the 5 files, `git show HEAD:<f> | sha256sum` must equal the prod `sha256sum ~/rentero/<f>`.

- [ ] **Step 5: Restart + health check** — `sudo systemctl restart rentero`, then poll `https://propertx.eu/` until it returns 200/302 (startup ~20–40s).

- [ ] **Step 6: Verify the computation on prod** — `build_report_summary` is stdlib-only:

```bash
ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cd ~/rentero && python3 -c \"
import sqlite3, json
from report.summary import build_report_summary
c = sqlite3.connect('cache/rentero.db')
rows = [json.loads(r[0]) for r in c.execute(\\\"SELECT data FROM report_rows WHERE slug='Svornosti_1497_1' AND year=2026 AND month=4\\\")]
s = build_report_summary(rows, {'client_type':'klient','rentero_commission':0.20,'vat_rate':0.21})
print('platform_commission_vat_czk', s['platform_commission_vat_czk'])
print('vat_output_czk             ', s['vat_output_czk'])
print('vat_balance_czk            ', s['vat_balance_czk'])
\""
```
Expected: `platform_commission_vat_czk 2542.7`, `vat_output_czk ≈ 6693.89`, `vat_balance_czk ≈ 6693.89`.

- [ ] **Step 7: Verify the rendered card on prod** — render `partials/property_dph_summary.html` with the prod Svornosti summary via the venv python (`/home/rentero/rentero/venv/bin/python`, FileSystemLoader('templates'), `fmt_czk` stub) and assert "Provize" present and the saldo ≈ 6 694. Then open `https://propertx.eu/property/Svornosti_1497_1/2026/4` and confirm the "Vyúčtování DPH" card is visible (it was hidden before) with a "Provize (21 %)" line, and that the "Plátce DPH" badge is **not** shown (client is `platce_dph=0`).

---

## Self-Review

**Spec coverage:** commission VAT in output (Task 1 Step 5; tests Step 1) ✓; recharged-expense net-zero (Task 1 `test_klient_recharged_expense_nets_out_in_balance`) ✓; show for all via `_show_dph` (Task 2 Steps 3–6) ✓; badge stays on `_is_dph_applicable` (untouched — property_intro not modified) ✓; Provize/Přefakturované breakdown lines (Task 2 Step 7) ✓; rentero unchanged (rentero branch untouched; `test_vat_output_is_accommodation_vat_for_rentero` stays green) ✓; blast radius validated by full suite + prod check (Task 3) ✓.

**Placeholder scan:** none — every code step shows full code; deploy uses a concrete `$(date)` loop.

**Type/name consistency:** `platform_commission_vat_czk` (summary field), `_show_dph` (template flag), `dph_prefakturace_klient_czk`, `vat_input_czk`, `vat_output_czk`, `vat_balance_czk` used identically across summary edits, both test files, and the template. The breakdown keys (`platform_commission_vat_czk`, `vat_input_czk`) match the fields added/used in Task 1.
