# Dashboard KPI Carousel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the dashboard KPI row into a paginated carousel (4 cards/page, ‹ › arrows + dots) and promote "Bilance DPH" into its own always-visible card that recomputes per filter.

**Architecture:** A CSS-`transform` track of `.kpi-page` blocks inside an `overflow:hidden` viewport; a small vanilla-JS pager moves the track. The DPH card joins the existing `_applyFilters()` data-attribute aggregation (per-property vat values are stashed on each `.prop-row`, summed over visible rows). Dashboard-only — the shared `.kpi-grid`/`.kpi-card` classes are untouched.

**Tech Stack:** FastAPI + Jinja2 templates, vanilla JS, CSS custom properties, pytest (+ Starlette `TestClient`).

---

## Reference: spec

`docs/superpowers/specs/2026-05-23-dashboard-kpi-carousel-design.md` — read it before starting.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `report/routes/dashboard.py` | Dashboard route; per-property DPH already computed in a sweep | Stash per-property `vat_output/input/balance` onto the current-month cell |
| `templates/dashboard.html` | Dashboard markup + filter/KPI JS | Restructure KPI block into carousel; standalone amber DPH card; `data-vat-*` on prop rows; JS pager + DPH recompute; remove swap |
| `templates/partials/base_styles_components.html` | Shared component CSS | Add scoped `.kpi-carousel*` styles |
| `templates/partials/base_styles_responsive.html` | Mobile breakpoints | Add `.kpi-page` 2×2 rule inside the existing `@media (max-width:639px)` block |
| `tests/test_web_generation.py` | Web render/route tests | Add backend cell-attach test + carousel smoke test |

---

## Task 1: Backend — stash per-property DPH on the current-month cell

**Files:**
- Modify: `report/routes/dashboard.py` (the per-property sweep + cell-attach loop, currently ~lines 371–414)
- Test: `tests/test_web_generation.py`

The route already runs `build_report_summary(...)` for every property (variable `s`) and accumulates `rentero_vat_output/input/balance`. We only need to also keep the per-property values and attach them to the current-month cell — exactly mirroring how `zisk_czk` and `expenses_sum_czk` are already attached so the template can emit them as data attributes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_generation.py` (the `asyncio`, `SimpleNamespace`, `get_connection`, `web_module`, `_admin_request` imports/helpers already exist at the top of the file):

```python
def test_dashboard_attaches_per_property_dph_to_current_cell(monkeypatch):
    # The dashboard sweep computes build_report_summary per property; this
    # asserts the per-property DPH (output/input/balance) is stashed onto the
    # current-month cell so the template can emit data-vat-* attributes for
    # the per-filter recompute (mirrors the existing zisk_czk attach).
    from report.db import save_report_rows, log_report_generated
    from report.db_admin import upsert_report_object

    conn = get_connection(":memory:")
    try:
        upsert_report_object(conn, {
            "slug": "Own", "display_name": "Own", "listing_nickname": "Own",
            "client_type": "rentero", "rentero_commission": 0.15,
            "vat_rate": 0.21, "active": True,
        })
        rows = [{
            "confirmation_code": "Own-1",
            "payout_czk": 10000.0,
            "cena_ubytovani_czk": 8000.0,
            "verification_status": "MATCHED",
        }]
        save_report_rows(conn, "Own", 2026, 4, rows)
        log_report_generated(conn, "Own", 2026, 4, "Own.xlsx", rows)

        seeded_prop = {
            "slug": "Own", "display_name": "Own", "listing_nickname": "Own",
            "client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21,
        }
        monkeypatch.setattr(web_module, "_get_active_properties", lambda config: [seeded_prop])

        captured = {}
        monkeypatch.setattr(
            web_module.templates, "TemplateResponse",
            lambda request, template, context: captured.update({"context": context})
            or SimpleNamespace(status_code=200),
        )

        asyncio.run(web_module.dashboard(
            request=_admin_request(), year=2026, month=4, conn=conn,
            config={"properties": {}},
        ))

        rows_vm = captured["context"]["dashboard_rows"]
        own = next(r for r in rows_vm if r["slug"] == "Own")
        cur = next(c for c in own["cells"]
                   if c.get("year") == 2026 and c.get("month") == 4)
        assert "vat_output_czk" in cur
        assert "vat_input_czk" in cur
        assert "vat_balance_czk" in cur
        assert isinstance(cur["vat_balance_czk"], float)
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-win\Scripts\python.exe -m pytest tests/test_web_generation.py::test_dashboard_attaches_per_property_dph_to_current_cell -v`
Expected: FAIL with `KeyError`/`assert "vat_output_czk" in cur` (the cell has no vat fields yet).

- [ ] **Step 3: Add the per-property vat dict in the sweep**

In `report/routes/dashboard.py`, find the sweep initialization:

```python
        slug_to_prop = {p["slug"]: p for p in properties}
        slug_to_zisk: dict[str, float] = {}
        rentero_vat_output = 0.0
        rentero_vat_input = 0.0
        rentero_vat_balance = 0.0
```

Add one line so it reads:

```python
        slug_to_prop = {p["slug"]: p for p in properties}
        slug_to_zisk: dict[str, float] = {}
        slug_to_vat: dict[str, dict] = {}
        rentero_vat_output = 0.0
        rentero_vat_input = 0.0
        rentero_vat_balance = 0.0
```

- [ ] **Step 4: Populate it inside the loop**

In the same loop, find:

```python
            rentero_vat_output  += s.get("vat_output_czk", 0)  or 0
            rentero_vat_input   += s.get("vat_input_czk", 0)   or 0
            rentero_vat_balance += s.get("vat_balance_czk", 0) or 0
```

Add immediately after those three lines:

```python
            slug_to_vat[slug] = {
                "output":  round(float(s.get("vat_output_czk")  or 0), 2),
                "input":   round(float(s.get("vat_input_czk")   or 0), 2),
                "balance": round(float(s.get("vat_balance_czk") or 0), 2),
            }
```

- [ ] **Step 5: Attach to the current-month cell**

Find the existing cell-attach loop:

```python
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    cell["zisk_czk"] = slug_to_zisk.get(row["slug"], 0.0)
                    break
```

Replace it with (add three lines before `break`):

```python
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    cell["zisk_czk"] = slug_to_zisk.get(row["slug"], 0.0)
                    _vat = slug_to_vat.get(row["slug"], {})
                    cell["vat_output_czk"]  = _vat.get("output", 0.0)
                    cell["vat_input_czk"]   = _vat.get("input", 0.0)
                    cell["vat_balance_czk"] = _vat.get("balance", 0.0)
                    break
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv-win\Scripts\python.exe -m pytest tests/test_web_generation.py::test_dashboard_attaches_per_property_dph_to_current_cell -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add report/routes/dashboard.py tests/test_web_generation.py
git commit -m "feat(dashboard): stash per-property DPH on current-month cell"
```

---

## Task 2: Template + CSS — carousel structure, standalone DPH card, data attributes

**Files:**
- Modify: `templates/dashboard.html` (KPI block ~lines 26–116; prop-row data attrs ~lines 175–193)
- Modify: `templates/partials/base_styles_components.html` (after the `.kpi-card-*` definitions, ~line 57)
- Modify: `templates/partials/base_styles_responsive.html` (inside `@media (max-width: 639px)`, after the `.kpi-grid` rule ~line 33)
- Test: `tests/test_web_generation.py`

- [ ] **Step 1: Write the failing smoke test**

Add to `tests/test_web_generation.py`:

```python
def test_dashboard_renders_kpi_carousel():
    import os

    old_allow = os.environ.get("RENTERO_ALLOW_INSECURE_DEFAULTS")
    os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = "1"
    try:
        with TestClient(web_module.app) as client:
            login_page = client.get("/login")
            csrf_token = _extract_csrf_token(login_page.text)
            login = client.post(
                "/login",
                data={"username": "admin", "password": "admin", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            assert login.status_code == 302
            response = client.get("/")
    finally:
        if old_allow is None:
            os.environ.pop("RENTERO_ALLOW_INSECURE_DEFAULTS", None)
        else:
            os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = old_allow

    assert response.status_code == 200
    html = response.text
    assert "kpi-carousel-track" in html          # carousel exists
    assert html.count('class="kpi-page"') == 2   # two pages
    assert "kpi-dots" in html                     # dot nav
    assert "Bilance DPH" in html                  # DPH is now a standalone card
    assert "kpi-card-2-dph" not in html           # old swap markup removed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-win\Scripts\python.exe -m pytest tests/test_web_generation.py::test_dashboard_renders_kpi_carousel -v`
Expected: FAIL (`assert "kpi-carousel-track" in html` — current dashboard uses `.kpi-grid`, no carousel).

- [ ] **Step 3: Replace the KPI block in `templates/dashboard.html`**

Replace the entire current block — from the `{# ── KPI Summary Bar ── #}` comment and its `<div class="kpi-grid">` through its closing `</div>` (the block that contains all four cards, currently ~lines 26–116) — with:

```html
{# ── KPI Summary Bar (carousel) ── #}
<div class="kpi-carousel" id="kpi-carousel">
  <div class="kpi-carousel-viewport">
    <div class="kpi-carousel-track" id="kpi-carousel-track">

      {# ─────────────── Page 1 ─────────────── #}
      <div class="kpi-page">

        {# Total payout #}
        <div class="kpi-card kpi-card-green">
          <div class="kpi-label">Celková výplata</div>
          <div class="kpi-value" style="color:var(--color-green);font-size:24px;" id="kpi-payout"
               data-copy="{{ "%.2f"|format(dashboard_summary.total_payout_czk) }}">
            {{ "{:,.2f}".format(dashboard_summary.total_payout_czk).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
          </div>
          <div class="kpi-sub" id="kpi-payout-sub">platformy → rentero · {{ dashboard_summary.current_month_label }}</div>
        </div>

        {# Client payout — always "Výplata klientům" (DPH moved to its own card) #}
        <div class="kpi-card kpi-card-blue">
          <div class="kpi-label">Výplata klientům</div>
          <div class="kpi-value" style="color:var(--color-blue);font-size:24px;" id="kpi-client-payout"
               data-copy="{{ '%.2f'|format(dashboard_summary.total_client_payout_czk) }}">
            {{ "{:,.2f}".format(dashboard_summary.total_client_payout_czk).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
          </div>
          <div class="kpi-sub">rentero → klienti · {{ dashboard_summary.current_month_label }}</div>
        </div>

        {# Total reservations with sparkline #}
        <div class="kpi-card kpi-card-primary">
          <div class="kpi-label">Celkem rezervací</div>
          <div class="kpi-value" style="font-size:24px;" id="kpi-reservations"
               data-copy="{{ dashboard_summary.total_reservations }}">
            {{ dashboard_summary.total_reservations }}
            {% set d = dashboard_summary.reservations_delta %}
            <span id="kpi-delta" class="kpi-delta {% if d > 0 %}kpi-delta-pos{% elif d < 0 %}kpi-delta-neg{% endif %}" {% if d == 0 %}style="display:none;"{% endif %}>{{ '+' if d > 0 else '' }}{{ d }}</span>
          </div>
          <div class="kpi-sub">
            {% set pts = dashboard_summary.sparkline_points %}
            {% set max_pt = (pts | max) if pts and (pts | max) > 0 else 1 %}
            <svg width="80" height="24" viewBox="0 0 80 24" style="display:inline-block;vertical-align:middle;margin-right:6px;">
              {% for v in pts %}
              {% set bh = [((v / max_pt) * 20) | round | int, 2] | max %}
              {% set bx = loop.index0 * 14 %}
              <rect x="{{ bx }}" y="{{ 24 - bh }}" width="10" height="{{ bh }}"
                    rx="2"
                    fill="{% if loop.last %}var(--color-blue){% else %}oklch(0.65 0.15 250 / 0.3){% endif %}"/>
              {% endfor %}
            </svg>
            vs minulý měsíc
          </div>
        </div>

        {# Rentero fee (odměna) #}
        <div class="kpi-card kpi-card-red" id="kpi-profit-card">
          <div class="kpi-label">Odměna Rentero</div>
          <div class="kpi-value" style="color:var(--color-red);font-size:24px;" id="kpi-profit"
               data-copy="{{ "%.2f"|format(dashboard_summary.total_rentero_fee_czk) }}">
            {{ "{:,.2f}".format(dashboard_summary.total_rentero_fee_czk).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
          </div>
          <div class="kpi-sub" id="kpi-profit-sub">provize ze správy objektů · {{ dashboard_summary.current_month_label }}</div>
        </div>

      </div>

      {# ─────────────── Page 2 ─────────────── #}
      <div class="kpi-page">

        {# Bilance DPH — standalone, always visible, recomputed per filter.
           Aggregate across visible objects = Rentero's DPH position
           (DPH na výstupu z provizí + přípravy minus odpočet na vstupu). #}
        {% set _vat_balance = dashboard_summary.rentero_vat_balance_czk or 0 %}
        {% set _vat_is_refund = _vat_balance < 0 %}
        <div class="kpi-card kpi-card-amber" id="kpi-card-dph"
             title="Souhrn DPH ze všech objektů — DPH, která se týká Rentero">
          <div class="kpi-label">Bilance DPH</div>
          <div class="kpi-value" id="kpi-vat-balance"
               style="font-size:24px;color:{% if _vat_is_refund %}var(--color-green){% else %}var(--color-red){% endif %};"
               data-copy="{{ '%.2f'|format(_vat_balance|abs) }}">
            {% if _vat_is_refund %}+{% else %}−{% endif %}&nbsp;{{ "{:,.2f}".format(_vat_balance|abs).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
          </div>
          <div class="kpi-sub" id="kpi-vat-sub" style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;">
            <span style="font-family:var(--font-mono);font-size:11px;" title="DPH na výstupu — z provizí a přípravy pokoje">
              <span style="color:var(--text-300);">Výstup</span>
              <span style="color:var(--color-green);" id="kpi-vat-output">+{{ "{:,.2f}".format(dashboard_summary.rentero_vat_output_czk or 0).replace(",", "X").replace(".", ",").replace("X", " ") }}</span>
            </span>
            <span style="color:var(--text-300);opacity:.4">·</span>
            <span style="font-family:var(--font-mono);font-size:11px;" title="DPH na vstupu (odpočet) — z výdajů s DPH">
              <span style="color:var(--text-300);">Vstup</span>
              <span style="color:var(--color-red);" id="kpi-vat-input">−{{ "{:,.2f}".format(dashboard_summary.rentero_vat_input_czk or 0).replace(",", "X").replace(".", ",").replace("X", " ") }}</span>
            </span>
          </div>
        </div>

      </div>

    </div>
  </div>

  {# Arrows + dots, centered below the grid #}
  <div class="kpi-carousel-nav" id="kpi-carousel-nav">
    <button type="button" class="kpi-nav-btn" id="kpi-nav-prev" aria-label="Předchozí karty" disabled>‹</button>
    <div class="kpi-dots" id="kpi-dots">
      <button type="button" class="kpi-dot is-active" aria-label="Stránka 1"></button>
      <button type="button" class="kpi-dot" aria-label="Stránka 2"></button>
    </div>
    <button type="button" class="kpi-nav-btn" id="kpi-nav-next" aria-label="Další karty">›</button>
  </div>
</div>
```

- [ ] **Step 4: Add `data-vat-*` attributes to the prop row**

In `templates/dashboard.html`, find the prop-row anchor attribute (currently ~line 189):

```html
     data-zisk="{{ cur_cell.zisk_czk|default(0) if cur_cell else 0 }}"
```

Insert immediately after it:

```html
     data-vat-output="{{ cur_cell.vat_output_czk|default(0) if cur_cell else 0 }}"
     data-vat-input="{{ cur_cell.vat_input_czk|default(0) if cur_cell else 0 }}"
     data-vat-balance="{{ cur_cell.vat_balance_czk|default(0) if cur_cell else 0 }}"
```

- [ ] **Step 5: Add carousel CSS**

In `templates/partials/base_styles_components.html`, find the line:

```css
  .kpi-card-neutral { }
```

Insert immediately after it:

```css

  /* ── Dashboard KPI carousel (dashboard.html only) ─────────────────────
     The shared .kpi-grid stays untouched; the carousel uses its own scoped
     classes. Pages are fixed 4-column grids so a partial page keeps the
     same card width and aligns left instead of stretching one card wide. */
  .kpi-carousel { margin-bottom: var(--space-xl); }
  .kpi-carousel-viewport { overflow: hidden; }
  .kpi-carousel-track {
    display: flex;
    transition: transform 0.3s ease;
  }
  .kpi-page {
    flex: 0 0 100%;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: var(--space-md);
  }
  .kpi-carousel-nav {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-sm);
    margin-top: var(--space-md);
  }
  /* Single page → nothing to page through → hide the controls. */
  .kpi-carousel[data-pages="1"] .kpi-carousel-nav { display: none; }
  .kpi-nav-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 999px;
    background: var(--bg-3);
    border: 1px solid var(--border-base);
    color: var(--text-100);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s, opacity 0.15s;
  }
  .kpi-nav-btn:hover:not(:disabled) {
    background: var(--bg-4, var(--bg-3));
    border-color: oklch(1 0 0 / 0.18);
  }
  .kpi-nav-btn:disabled { opacity: 0.35; cursor: default; }
  .kpi-dots { display: flex; align-items: center; gap: 6px; }
  .kpi-dot {
    width: 7px;
    height: 7px;
    padding: 0;
    border: none;
    border-radius: 999px;
    background: oklch(var(--fg) / 0.18);
    cursor: pointer;
    transition: background 0.15s, transform 0.15s;
  }
  .kpi-dot.is-active { background: var(--brand); transform: scale(1.15); }
```

- [ ] **Step 6: Add the mobile rule**

In `templates/partials/base_styles_responsive.html`, inside the `@media (max-width: 639px)` block, find:

```css
    .kpi-grid {
      grid-template-columns: 1fr 1fr !important;
      gap: var(--space-xs) !important;
    }
```

Insert immediately after that rule (still inside the media query):

```css
    .kpi-page {
      grid-template-columns: 1fr 1fr !important;
      gap: var(--space-xs) !important;
    }
```

- [ ] **Step 7: Run smoke test to verify it passes**

Run: `.venv-win\Scripts\python.exe -m pytest tests/test_web_generation.py::test_dashboard_renders_kpi_carousel -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add templates/dashboard.html templates/partials/base_styles_components.html templates/partials/base_styles_responsive.html tests/test_web_generation.py
git commit -m "feat(dashboard): KPI carousel layout + standalone DPH card"
```

---

## Task 3: JS — pager, per-filter DPH recompute, remove swap

**Files:**
- Modify: `templates/dashboard.html` (`<script>` block: `_applyFilters` ~lines 287–362; append pager)

No automated JS test exists in the repo (the existing filter logic is likewise untested); verify by running the full suite for no regressions, then by the manual checklist below.

- [ ] **Step 1: Add DPH accumulators to `_applyFilters`**

In the `<script>` of `templates/dashboard.html`, find the accumulator initialization at the top of `_applyFilters`:

```javascript
  var totalPayout = 0, totalCenaUbyt = 0, totalProvize = 0, totalClientPayout = 0;
  var totalRez = 0, totalRezPrev = 0, withData = 0, propCount = 0, issues = 0;
  var hasRentero = false, hasClient = false;
```

Add one line after them:

```javascript
  var totalVatOutput = 0, totalVatInput = 0, totalVatBalance = 0;
```

- [ ] **Step 2: Sum vat over visible rows**

In the same function, find the `if (show) {` accumulation block and the line:

```javascript
      if (!isRent) totalClientPayout += parseFloat(row.dataset.clientPayout) || 0;
```

Add immediately after it:

```javascript
      totalVatOutput  += parseFloat(row.dataset.vatOutput)  || 0;
      totalVatInput   += parseFloat(row.dataset.vatInput)   || 0;
      totalVatBalance += parseFloat(row.dataset.vatBalance) || 0;
```

- [ ] **Step 3: Replace the card-2 swap block with the DPH recompute**

Find the swap block (it begins with the `// Swap KPI 2 between ...` comment):

```javascript
  // Swap KPI 2 between "Výplata klientům" (default) and "Bilance DPH" (rentero filter).
  // The DPH numbers are static for current month — we just show/hide the prebuilt block.
  var kpiCard2Default = document.getElementById('kpi-card-2-default');
  var kpiCard2Dph     = document.getElementById('kpi-card-2-dph');
  if (kpiCard2Default && kpiCard2Dph) {
    if (_activeTab === 'rentero') {
      kpiCard2Default.setAttribute('hidden', '');
      kpiCard2Dph.removeAttribute('hidden');
    } else {
      kpiCard2Dph.setAttribute('hidden', '');
      kpiCard2Default.removeAttribute('hidden');
    }
  }
```

Replace it with:

```javascript
  // Bilance DPH — standalone card, recomputed over the visible rows.
  // refund (balance < 0) → green "+"; owed (balance >= 0) → red "−".
  var kpiVatBalance = document.getElementById('kpi-vat-balance');
  var kpiVatOutput  = document.getElementById('kpi-vat-output');
  var kpiVatInput   = document.getElementById('kpi-vat-input');
  if (kpiVatBalance) {
    var vatRefund = totalVatBalance < 0;
    kpiVatBalance.innerHTML = (vatRefund ? '+' : '−') + ' '
      + _formatNumber(Math.abs(totalVatBalance)) + ' Kč';
    kpiVatBalance.style.color = vatRefund ? 'var(--color-green)' : 'var(--color-red)';
  }
  if (kpiVatOutput) kpiVatOutput.textContent = '+' + _formatNumber(totalVatOutput);
  if (kpiVatInput)  kpiVatInput.textContent  = '−' + _formatNumber(totalVatInput);
```

- [ ] **Step 4: Append the pager**

At the very end of the `<script>` block (after the `searchCards` function definition, before `</script>`), add:

```javascript
// ── KPI carousel pager ──
(function () {
  var carousel = document.getElementById('kpi-carousel');
  if (!carousel) return;
  var track = document.getElementById('kpi-carousel-track');
  var prev  = document.getElementById('kpi-nav-prev');
  var next  = document.getElementById('kpi-nav-next');
  var dots  = Array.prototype.slice.call(document.querySelectorAll('#kpi-dots .kpi-dot'));
  var pages = track ? track.children.length : 0;
  carousel.dataset.pages = pages;
  var current = 0;
  function go(n) {
    current = Math.max(0, Math.min(n, pages - 1));
    if (track) track.style.transform = 'translateX(' + (-100 * current) + '%)';
    dots.forEach(function (d, i) { d.classList.toggle('is-active', i === current); });
    if (prev) prev.disabled = current === 0;
    if (next) next.disabled = current === pages - 1;
  }
  if (prev) prev.addEventListener('click', function () { go(current - 1); });
  if (next) next.addEventListener('click', function () { go(current + 1); });
  dots.forEach(function (d, i) { d.addEventListener('click', function () { go(i); }); });
  go(0);
})();
```

- [ ] **Step 5: Run the full suite for regressions**

Run: `.venv-win\Scripts\python.exe -m pytest -q`
Expected: no new failures versus the known baseline of 16 pre-existing failures (4 verifier + 8 checkin + 2 controls + 2 source_imports). The two new tests from Tasks 1–2 pass. If total failures > 16, investigate.

- [ ] **Step 6: Manual verification checklist**

Start the app locally (or verify on prod after deploy). Confirm:
- Dashboard shows page 1 (4 cards); `›` advances to page 2 (Bilance DPH); `‹` returns. Dots reflect the active page; `‹` disabled on page 1, `›` disabled on page 2.
- Clicking filter tabs (Vše/Rentero/Klienti/Z Klienti) updates the Bilance DPH value, the Výstup/Vstup sub-line, and the value color (green refund / red owed) consistently with the other cards.
- Card 2 always reads "Výplata klientům" (never swaps).
- Mobile width (<640px): each page is 2×2; arrows/dots centered below.

- [ ] **Step 7: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): carousel pager + per-filter DPH recompute"
```

---

## Self-Review

**Spec coverage:**
- §2 layout (carousel DOM, scoped CSS, fixed 4-col pages, nav hidden when 1 page) → Task 2 steps 3, 5.
- §2 mobile 2×2 → Task 2 step 6.
- §3 standalone amber DPH card with label/value/sub + initial server values → Task 2 step 3 (page 2).
- §4 backend per-cell stash → Task 1; template `data-vat-*` → Task 2 step 4; JS recompute + swap removal → Task 3 steps 1–3.
- §5 pager JS → Task 3 step 4.
- §6 smoke test → Task 2 step 1; backend test → Task 1 step 1; manual JS verification → Task 3 step 6.
- §7 out-of-scope (no swipe, no `.kpi-grid` change, no DPH math change) → respected; only per-cell stash added to backend.

**Placeholder scan:** none — every code step has complete code and exact anchors.

**Type/name consistency:** ids match across tasks — `kpi-vat-balance`, `kpi-vat-output`, `kpi-vat-input` (template step 3 ↔ JS step 3); `kpi-carousel`, `kpi-carousel-track`, `kpi-nav-prev/next`, `kpi-dots`, `kpi-dot` (template step 3 ↔ CSS step 5 ↔ JS step 4). Data attributes `data-vat-output/input/balance` (template step 4) map to `dataset.vatOutput/vatInput/vatBalance` (JS step 2). Cell keys `vat_output_czk/vat_input_czk/vat_balance_czk` consistent across Task 1 (backend), Task 2 step 4 (template), Task 1 test.

**Note on intermediate state:** After Task 2 and before Task 3, the old swap JS still references `kpi-card-2-default`/`kpi-card-2-dph` (now absent); the existing `if (kpiCard2Default && kpiCard2Dph)` guard short-circuits, so no error — the app stays functional between commits. Task 3 removes that block.
