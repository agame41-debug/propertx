# 12% Accommodation VAT on Rentero Objects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For Rentero-owned objects (`client_type == 'rentero'`), compute the 12% reduced-rate accommodation VAT (`(Σpayout + Σprovize − Σcity_tax) × 0.12/1.12`) as the object's output VAT, replacing the prefakturace breakdown, and surface it in the "Vyúčtování DPH" card.

**Architecture:** All math lives in `build_report_summary` ([report/summary.py](../../../report/summary.py)) — the single source of truth read by the property page, the "Zisk" KPI, and the dashboard aggregates. The output-VAT alias branches on `client_type`; `vat_balance_czk` and `zisk_czk` already derive from `vat_output_czk` so they update automatically. The DPH summary partial branches on the presence of `accommodation_vat_czk`.

**Tech Stack:** Python 3, Jinja2, pytest. Test interpreter: `.venv-win/Scripts/python.exe`. Spec: [docs/superpowers/specs/2026-05-23-rentero-accommodation-vat-12pct-design.md](../specs/2026-05-23-rentero-accommodation-vat-12pct-design.md).

---

## File Structure

- `report/summary.py` — add `ACCOMMODATION_VAT_RATE` constant, `platform_commission_czk` aggregate, and the rentero-only `accommodation_gross_czk` / `accommodation_vat_czk` fields; branch the `vat_output_czk` alias.
- `templates/partials/property_dph_summary.html` — branch the "DPH na výstupu" breakdown: one "Ubytovací služby (12 %)" line for rentero, the existing fee/příprava lines otherwise.
- `tests/test_summary.py` — new tests for the accommodation VAT.
- `tests/test_summary_new_fields.py` — update the existing `test_vat_output_alias_for_rentero` (the alias no longer holds for rentero).
- `tests/test_web_generation.py` — render-tests for the DPH summary partial.

---

### Task 1: accommodation VAT in `build_report_summary`

**Files:**
- Modify: `report/summary.py`
- Test: `tests/test_summary.py`, `tests/test_summary_new_fields.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_summary.py`:

```python
# ── 12% accommodation VAT on Rentero-owned objects ──────────────────────
# Rentero is the accommodation supplier on its own objects, so it owes the
# Czech 12% reduced-rate VAT on the full guest consideration
# (payout + platform commission − city tax), extracted from the VAT-inclusive
# gross. Replaces the prefakturace output-VAT for rentero objects only.

def _accommodation_rows():
    return [{"payout_czk": 10000.0, "provize_czk": 2000.0, "city_tax_czk": 200.0,
             "cena_ubytovani_czk": 8000.0, "priprava_pokoje_czk": 0,
             "dph_uklid_balicky_czk": 0}]


def test_rentero_accommodation_vat_is_output_vat():
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_accommodation_rows(), prop)
    assert s["platform_commission_czk"] == 2000.0
    assert s["accommodation_gross_czk"] == 11800.0          # 10000 + 2000 − 200
    assert s["accommodation_vat_czk"] == 1264.29            # 11800 × 0.12/1.12
    assert s["vat_output_czk"] == 1264.29                   # replaces prefakturace
    assert s["vat_balance_czk"] == 1264.29                  # output − 0 input
    # zisk reflects the new (larger) vat_balance
    assert s["zisk_czk"] == round(10000.0 - 0.0 - 1264.29, 2)  # 8735.71


def test_klient_and_zklient_have_no_accommodation_vat():
    for ct in ("klient", "z_klient"):
        prop = {"client_type": ct, "rentero_commission": 0.15, "vat_rate": 0.21}
        s = build_report_summary(_accommodation_rows(), prop)
        assert "accommodation_vat_czk" not in s
        assert "accommodation_gross_czk" not in s
        assert s["vat_output_czk"] == s["dph_prefakturace_klient_czk"]
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q -k "accommodation"`
Expected: FAIL — `KeyError: 'platform_commission_czk'` (and `accommodation_*`).

- [ ] **Step 3: Add the module constant** — in `report/summary.py`, between `from __future__ import annotations` and `def _r`:

```python
from __future__ import annotations


# Czech reduced VAT rate for accommodation services (ubytovací služby).
# Rentero-owned objects are the supplier and owe this on the full guest
# consideration. Fixed by law; not a per-object config value.
ACCOMMODATION_VAT_RATE = 0.12


def _r(value) -> float:
    return round(float(value or 0), 2)
```

- [ ] **Step 4: Add the `platform_commission_czk` aggregate** — in `report/summary.py`, immediately after the `city_tax_czk = ...` line:

```python
    city_tax_czk = _r(sum(float(r.get("city_tax_czk") or 0) for r in rows))
    platform_commission_czk = _r(sum(float(r.get("provize_czk") or 0) for r in rows))
    room_prep_czk = _r(sum(float(r.get("priprava_pokoje_czk") or 0) for r in rows))
```

- [ ] **Step 5: Expose `platform_commission_czk` in the result dict** — in the `result = { ... }` literal, add the line right after `"accommodation_income_czk": accommodation_income_czk,`:

```python
        "accommodation_income_czk": accommodation_income_czk,
        "platform_commission_czk": platform_commission_czk,
```

- [ ] **Step 6: Branch the output-VAT alias** — replace this block:

```python
    # ── New fields (property-page redesign Phase 1) ──────────────────────
    # Alias: dph_prefakturace_klient_czk == vat_output_czk semantically.
    # Kept under both names for template clarity without renaming the field
    # used elsewhere (Excel, other consumers).
    result["vat_output_czk"] = result["dph_prefakturace_klient_czk"]
```

with:

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
```

(The existing `result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])` line, which comes after this block, picks up the new value. The `zisk_czk` block, further down, derives from `vat_balance_czk` — no edit needed.)

- [ ] **Step 7: Run the new tests, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q -k "accommodation"`
Expected: PASS (2 tests).

- [ ] **Step 8: Fix the now-broken alias test** — in `tests/test_summary_new_fields.py`, replace:

```python
def test_vat_output_alias_for_rentero():
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["vat_output_czk"] == s["dph_prefakturace_klient_czk"]
```

with:

```python
def test_vat_output_alias_for_klient():
    # klient/z_klient: output VAT is still the prefakturace alias.
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    assert s["vat_output_czk"] == s["dph_prefakturace_klient_czk"]


def test_vat_output_is_accommodation_vat_for_rentero():
    # rentero: output VAT is the 12% accommodation VAT, no longer the
    # prefakturace alias. _row() has no provize_czk, so gross = 10000 − 200.
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["accommodation_vat_czk"] == 1050.0          # 9800 × 0.12/1.12
    assert s["vat_output_czk"] == s["accommodation_vat_czk"]
    assert s["vat_output_czk"] != s["dph_prefakturace_klient_czk"]
```

- [ ] **Step 9: Run the updated file, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary_new_fields.py tests/test_summary.py -q`
Expected: PASS (all). `test_vat_balance_positive_means_owed` and `test_zisk_present_for_rentero` stay green (they assert relationships, not hardcoded values).

- [ ] **Step 10: Commit**

```bash
git add report/summary.py tests/test_summary.py tests/test_summary_new_fields.py
git commit -m "feat(summary): 12% accommodation VAT as output VAT for Rentero objects"
```

---

### Task 2: render the accommodation-VAT line in the DPH card

**Files:**
- Modify: `templates/partials/property_dph_summary.html` (the "Výstup column" breakdown, currently lines ~32–41)
- Test: `tests/test_web_generation.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_web_generation.py` (after the `_render_property_kpi` tests):

```python
def _render_property_dph_summary(*, summary):
    tmpl = web_module.templates.get_template("partials/property_dph_summary.html")
    return tmpl.render(summary=summary)


_RENTERO_DPH_SUMMARY = {
    "vat_output_czk": 5141.36,
    "accommodation_vat_czk": 5141.36,
    "vat_input_czk": 0.0,
    "vat_input_count": 0,
    "vat_balance_czk": 5141.36,
    "vat_rentero_fee_czk": 0.0,
    "vat_room_prep_czk": 1315.75,
}

# klient summary: no accommodation_vat_czk key → legacy prefakturace breakdown.
_KLIENT_DPH_SUMMARY = {
    "vat_output_czk": 1452.0,
    "vat_input_czk": 0.0,
    "vat_input_count": 0,
    "vat_balance_czk": 1452.0,
    "vat_rentero_fee_czk": 1200.0,
    "vat_room_prep_czk": 252.0,
}


def test_property_dph_summary_rentero_shows_accommodation_vat():
    html = _render_property_dph_summary(summary=_RENTERO_DPH_SUMMARY)
    assert "Ubytovací služby" in html
    assert "Příprava pokoje" not in html
    assert "Rentero fee" not in html


def test_property_dph_summary_klient_shows_prefakturace_breakdown():
    html = _render_property_dph_summary(summary=_KLIENT_DPH_SUMMARY)
    assert "Ubytovací služby" not in html
    assert "Příprava pokoje" in html
    assert "Rentero fee" in html
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -q -k "dph_summary"`
Expected: FAIL — `test_property_dph_summary_rentero_shows_accommodation_vat` fails (no "Ubytovací služby"; "Příprava pokoje" still present).

- [ ] **Step 3: Implement the branch** — in `templates/partials/property_dph_summary.html`, replace the breakdown block inside the Výstup column:

```jinja
      <div class="dph-breakdown">
        <div class="dph-row">
          <span class="dph-row-k">Rentero fee</span>
          <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}</span>
        </div>
        <div class="dph-row">
          <span class="dph-row-k">Příprava pokoje</span>
          <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}</span>
        </div>
      </div>
```

with (uses `is defined` because a missing dict key resolves to Undefined, and `Undefined is not none` would wrongly be true):

```jinja
      <div class="dph-breakdown">
        {% if summary.accommodation_vat_czk is defined %}
          {# Rentero-owned object: Rentero is the accommodation supplier and
             owes the 12% reduced-rate VAT on the full guest consideration.
             This single line replaces the fee/příprava breakdown — the gross
             already contains úklid/balíčky, so a separate 21% room-prep line
             would double-tax them. #}
          <div class="dph-row">
            <span class="dph-row-k">Ubytovací služby (12&nbsp;%)</span>
            <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.accommodation_vat_czk or 0) }}</span>
          </div>
        {% else %}
          <div class="dph-row">
            <span class="dph-row-k">Rentero fee</span>
            <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}</span>
          </div>
          <div class="dph-row">
            <span class="dph-row-k">Příprava pokoje</span>
            <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}</span>
          </div>
        {% endif %}
      </div>
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -q -k "dph_summary"`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add templates/partials/property_dph_summary.html tests/test_web_generation.py
git commit -m "feat(property): show 12% Ubytovací služby line in DPH card for Rentero objects"
```

---

### Task 3: full suite, push, deploy

- [ ] **Step 1: Full suite** — run `.venv-win/Scripts/python.exe -m pytest -q`. Expected: 16 baseline failures (per the known-failing baseline) and **no new failures**; the 4 new tests + 2 rewritten tests pass. If any non-baseline test fails, stop and investigate.

- [ ] **Step 2: Push** — `git push origin main`.

- [ ] **Step 3: Deploy (CONFIRM WITH USER FIRST)** — in-place patch of the two changed files on prod, with a timestamped backup, then restart. The server git is stale/dirty, so do NOT `git pull`; copy the committed file contents over SSH.

```bash
TS=$(date +%Y%m%d_%H%M%S)
ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 \
  "mkdir -p ~/backups/predeploy_$TS && cp ~/rentero/report/summary.py ~/backups/predeploy_$TS/ && cp ~/rentero/templates/partials/property_dph_summary.html ~/backups/predeploy_$TS/"
git show HEAD:report/summary.py | ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cat > ~/rentero/report/summary.py"
git show HEAD:templates/partials/property_dph_summary.html | ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cat > ~/rentero/templates/partials/property_dph_summary.html"
```

- [ ] **Step 4: Restart + health check**

```bash
ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "sudo systemctl restart rentero"
```
Then poll until the service is listening and the public URL returns 200 (startup ~36s).

- [ ] **Step 5: Verify the computation on prod data** — `build_report_summary` is stdlib-only, so plain `python3` can run it:

```bash
ssh -i ~/.ssh/id_ed25519_rentero -o BatchMode=yes rentero@204.168.216.181 "cd ~/rentero && python3 -c \"
import sqlite3, json
from report.summary import build_report_summary
c = sqlite3.connect('cache/rentero.db')
rows = [json.loads(r[0]) for r in c.execute(\\\"SELECT data FROM report_rows WHERE slug='Zitna_208_NOVA' AND year=2026 AND month=4\\\")]
s = build_report_summary(rows, {'client_type':'rentero','rentero_commission':0.15,'vat_rate':0.21})
print('accommodation_gross_czk', s['accommodation_gross_czk'])
print('accommodation_vat_czk  ', s['accommodation_vat_czk'])
print('vat_output_czk         ', s['vat_output_czk'])
\""
```
Expected: `accommodation_gross_czk 47986.05`, `accommodation_vat_czk 5141.36`, `vat_output_czk 5141.36`.

- [ ] **Step 6: Verify the rendered card** — open `https://propertx.eu/property/Zitna_208_NOVA/2026/4` (or the prod equivalent) and confirm the "Vyúčtování DPH" card shows **DPH na výstupu + 5 141 Kč** with the line **"Ubytovací služby (12 %)"**, and that a klient object still shows "Rentero fee / Příprava pokoje".

---

## Self-Review

**Spec coverage:** formula + verification (Task 1 Step 1/Step 8 assert 5141.36 and 1050.0 / 1264.29) ✓; replace-not-add (Step 6 branch; template Step 3 single line) ✓; výstup − vstup (vat_balance line untouched, derives from vat_output) ✓; rentero-only scope (`if client_type == "rentero"`, klient/z_klient tests) ✓; dashboard blast radius (no code change needed — it reads vat_output/vat_balance, validated by full suite) ✓; klient unchanged (Task 2 klient test) ✓.

**Placeholder scan:** none — every code step shows full code; deploy commands are concrete (timestamp via `$(date)`, not a placeholder).

**Type/name consistency:** `platform_commission_czk`, `accommodation_gross_czk`, `accommodation_vat_czk`, `ACCOMMODATION_VAT_RATE`, `vat_output_czk` used identically across summary.py edits, both test files, and the template. The template branch key (`accommodation_vat_czk`) matches the field added in Task 1 Step 6.
