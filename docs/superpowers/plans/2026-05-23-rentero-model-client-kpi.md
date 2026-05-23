# Modelová výplata klienta KPI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an illustrative "if this were a client object" model in KPI slot 3 for Rentero-owned objects (`client_type='rentero'`), replacing the empty "—" card.

**Architecture:** `build_report_summary` attaches a `model_client` sub-dict (klient-style calc) only for `client_type='rentero'`; `property_kpi.html` renders it as a muted KPI card, falling back to "—" when there's nothing to model.

**Tech Stack:** Python, Jinja2, pytest. Spec: `docs/superpowers/specs/2026-05-23-rentero-model-client-kpi-design.md`.

---

### Task 1: `model_client` in summary

**Files:**
- Modify: `report/summary.py` (after the `zisk_czk` block, before `return result`)
- Test: `tests/test_summary.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_summary.py`)

```python
def test_rentero_owned_object_has_model_client():
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_fee_rows(), prop)
    assert s["rentero_fee_czk"] == 0.0            # real fee stays zero
    m = s["model_client"]
    assert m["rentero_fee_czk"] == 1200.0          # 8000 * 0.15
    assert m["vat_rentero_fee_czk"] == 252.0        # 1200 * 0.21
    assert m["rentero_odmena_total_czk"] == 1452.0  # 1200 + 252
    assert m["client_payout_before_expenses_czk"] == 6548.0  # 8000 - 1200 - 252
    assert m["rentero_commission_rate"] == 0.15


def test_klient_and_zklient_have_no_model_client():
    for ct in ("klient", "z_klient"):
        prop = {"client_type": ct, "rentero_commission": 0.15, "vat_rate": 0.21}
        s = build_report_summary(_fee_rows(), prop)
        assert "model_client" not in s
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q`
Expected: FAIL — `KeyError: 'model_client'`.

- [ ] **Step 3: Implement** — in `report/summary.py`, after the `if client_type == "rentero": result["zisk_czk"] = ...` / `else:` zisk block and before `return result`:

```python
    # Illustrative "if this were a client object" model for Rentero-owned
    # objects: what a client would be paid and what Rentero would earn.
    # Display-only; does not affect any real figure (fee stays 0).
    if client_type == "rentero":
        model_fee = _r(accommodation_income_czk * rentero_commission_rate)
        model_vat = _r(model_fee * vat_rate)
        result["model_client"] = {
            "rentero_commission_rate": rentero_commission_rate,
            "rentero_fee_czk": model_fee,
            "vat_rentero_fee_czk": model_vat,
            "rentero_odmena_total_czk": _r(model_fee + model_vat),
            "client_payout_before_expenses_czk": _r(
                accommodation_income_czk - model_fee - model_vat
            ),
        }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_summary.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add report/summary.py tests/test_summary.py
git commit -m "feat(summary): model_client calc for Rentero-owned objects"
```

---

### Task 2: render model card

**Files:**
- Modify: `templates/partials/property_kpi.html` (KPI slot 3, the `_is_rentero_owned` + zero-fee `else` branch)
- Test: `tests/test_web_generation.py`

- [ ] **Step 1: Write failing tests** (append near the other `_render_property_kpi` tests)

```python
def test_property_kpi_rentero_owned_shows_model_card():
    summary = dict(
        _RENTERO_OWNED_SUMMARY,
        model_client={
            "rentero_commission_rate": 0.15,
            "rentero_fee_czk": 1200.0,
            "vat_rentero_fee_czk": 252.0,
            "rentero_odmena_total_czk": 1452.0,
            "client_payout_before_expenses_czk": 6548.0,
        },
    )
    html = _render_property_kpi(
        is_rentero_owned=True, summary=summary, prop={"client_type": "rentero"},
    )
    assert "Modelová výplata klienta" in html
    assert "Odměna Rentero" in html


def test_property_kpi_rentero_owned_no_data_still_shows_dash():
    summary = dict(
        _RENTERO_OWNED_SUMMARY,
        model_client={
            "rentero_commission_rate": 0.15,
            "rentero_fee_czk": 0.0,
            "vat_rentero_fee_czk": 0.0,
            "rentero_odmena_total_czk": 0.0,
            "client_payout_before_expenses_czk": 0.0,
        },
    )
    html = _render_property_kpi(
        is_rentero_owned=True, summary=summary, prop={"client_type": "rentero"},
    )
    assert "Modelová výplata klienta" not in html
    assert "—" in html
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -k property_kpi -q`
Expected: `test_property_kpi_rentero_owned_shows_model_card` FAILS (no "Modelová výplata klienta").

- [ ] **Step 3: Implement** — replace the muted "—" `else` block in `templates/partials/property_kpi.html` with:

```html
    {% else %}
      {% set _mc = summary.model_client %}
      {% if _mc and (_mc.client_payout_before_expenses_czk or 0) > 0 %}
        {# Rentero-owned object: no real fee, but show an illustrative
           "if this were a client object" model. Muted styling = a model,
           not real cash. #}
        <div class="kpi kpi-mute">
          <div class="kpi-label">Modelová výplata klienta</div>
          <div class="kpi-value" data-copy="{{ '%.2f'|format(_mc.client_payout_before_expenses_czk or 0) }}">
            {{ fmt_czk(_mc.client_payout_before_expenses_czk or 0) }}
          </div>
          <div class="kpi-sub">
            <span class="pair"><span>Odměna Rentero</span><span class="v">−{{ fmt_czk(_mc.rentero_fee_czk or 0) }} ({{ '%.0f'|format((_mc.rentero_commission_rate or 0) * 100) }} %)</span></span>
            {% if (_mc.vat_rentero_fee_czk or 0) > 0 %}
              <span class="sep">·</span>
              <span class="pair" style="color:var(--dph-text)">
                <span>DPH</span>
                <span class="v">−{{ fmt_czk(_mc.vat_rentero_fee_czk) }}</span>
              </span>
            {% endif %}
          </div>
        </div>
      {% else %}
        {# No bookings to model → empty muted card. #}
        <div class="kpi kpi-mute">
          <div class="kpi-label">Rentero fee</div>
          <div class="kpi-value">—</div>
          <div class="kpi-sub">
            <span class="pair"><span>Vlastní objekt</span></span>
          </div>
        </div>
      {% endif %}
    {% endif %}
```

- [ ] **Step 4: Run, verify pass** (plus the existing dash test still passes)

Run: `.venv-win/Scripts/python.exe -m pytest tests/test_web_generation.py -k property_kpi -q`
Expected: PASS (all 4 property_kpi tests).

- [ ] **Step 5: Commit**

```bash
git add templates/partials/property_kpi.html tests/test_web_generation.py
git commit -m "feat(property): show modelová výplata klienta card for Rentero objects"
```

---

### Task 3: full suite + deploy

- [ ] **Step 1: Full suite** — `.venv-win/Scripts/python.exe -m pytest -q`; expect 16 failed (baseline) + the rest passed, no new failures.
- [ ] **Step 2: Commit spec + plan** — `git add docs/superpowers/...2026-05-23-rentero-model-client-kpi*` and commit.
- [ ] **Step 3: Push** — `git push origin main`.
- [ ] **Step 4: Deploy (confirm with user first)** — in-place patch of `report/summary.py` + `templates/partials/property_kpi.html` on prod, with backup of both → `systemctl restart rentero` → verify HTTP 200 + a real rentero object renders the model card.
