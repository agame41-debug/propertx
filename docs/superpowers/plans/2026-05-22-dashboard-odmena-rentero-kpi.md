# Odměna Rentero KPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the 4th dashboard KPI card from net profit ("Zisk Rentero") to total Rentero fee ("Odměna Rentero"), with `client_type='rentero'` objects contributing 0.

**Architecture:** Zero the fee at the SQL aggregation level (`rentero_fee_sum_czk` in `_build_dashboard_maps`) so it is the single source of truth. The dashboard route then sums it directly, the template binds the new total, and the client-side filter JS sums the per-row `data-rentero-fee` attribute (which is already 0 for rentero objects after the SQL change).

**Tech Stack:** Python 3.14, FastAPI, SQLite (`json_extract`), Jinja2, vanilla JS, pytest.

**Spec:** [docs/superpowers/specs/2026-05-22-dashboard-odmena-rentero-kpi-design.md](../specs/2026-05-22-dashboard-odmena-rentero-kpi-design.md)

---

## File Structure

- `report/web_support.py` — modify the `rentero_fee_sum_czk` `CASE` in `_build_dashboard_maps` (Task 1).
- `report/routes/dashboard.py` — replace the `net_profit` loop with a `total_rentero_fee_czk` sum (Task 2).
- `templates/dashboard.html` — KPI card #4 label/value/sub (Task 2) + `_applyFilters` JS (Task 3).
- `tests/test_dashboard_rentero_fee.py` — **new** unit test for the SQL fee zeroing (Task 1).
- `tests/test_web_generation.py` — **add** a render assertion for the new card label (Task 2).

**Task ordering note:** `report/routes/dashboard.py` and `templates/dashboard.html` are
coupled through the summary key (`total_net_profit_czk` → `total_rentero_fee_czk`). They
MUST change together (Task 2, one step) so the dashboard never renders against a missing
key. Task 1 (SQL) and Task 3 (filter JS) each leave the app fully working at their
boundaries.

---

## Task 1: Zero the Rentero fee at SQL level

**Files:**
- Test: `tests/test_dashboard_rentero_fee.py` (create)
- Modify: `report/web_support.py` (the `rentero_fee_sum_czk` `CASE`, around line 464-470)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_rentero_fee.py`:

```python
import pytest

from report.db import get_connection, save_report_rows, log_report_generated
from report.db_admin import upsert_report_object
from report.web_support import _build_dashboard_maps


def _setup_object(conn, slug, client_type):
    upsert_report_object(conn, {
        "slug": slug,
        "display_name": slug,
        "listing_nickname": slug,
        "client_type": client_type,
        "rentero_commission": 0.15,
        "vat_rate": 0.21,
        "active": True,
    })


def _setup_month(conn, slug, year, month):
    rows = [{
        "confirmation_code": f"{slug}-1",
        "payout_czk": 10000.0,
        "cena_ubytovani_czk": 8000.0,
        "verification_status": "MATCHED",
    }]
    save_report_rows(conn, slug, year, month, rows)
    log_report_generated(conn, slug, year, month, f"{slug}.xlsx", rows)


def test_build_dashboard_maps_zeros_fee_only_for_rentero_client_type():
    conn = get_connection(":memory:")
    try:
        _setup_object(conn, "Own", "rentero")
        _setup_object(conn, "Klient", "klient")
        _setup_object(conn, "ZKlient", "z_klient")
        for slug in ("Own", "Klient", "ZKlient"):
            _setup_month(conn, slug, 2026, 4)

        properties = [
            {"slug": "Own", "listing_nickname": "Own", "display_name": "Own"},
            {"slug": "Klient", "listing_nickname": "Klient", "display_name": "Klient"},
            {"slug": "ZKlient", "listing_nickname": "ZKlient", "display_name": "ZKlient"},
        ]
        history_map, _state, _data, _notif = _build_dashboard_maps(
            conn, properties, [(2026, 4)]
        )

        own = history_map["Own"][(2026, 4)]["rentero_fee_sum_czk"]
        klient = history_map["Klient"][(2026, 4)]["rentero_fee_sum_czk"]
        zklient = history_map["ZKlient"][(2026, 4)]["rentero_fee_sum_czk"]

        # rentero-owned object: no client → no fee
        assert own == 0
        # klient: cena_ubytovani * commission * (1 + vat) = 8000 * 0.15 * 1.21
        assert klient == pytest.approx(1452.0)
        # z_klient: 3 % of payout = 10000 * 0.03
        assert zklient == pytest.approx(300.0)
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_rentero_fee.py -v`
Expected: FAIL — `own` is `1452.0` (the notional fee), not `0`, so `assert own == 0` fails.

- [ ] **Step 3: Add the rentero→0 branch to the SQL CASE**

In `report/web_support.py`, find this block inside `_build_dashboard_maps`:

```python
                ROUND(COALESCE(SUM(CASE
                    WHEN COALESCE(o.client_type, 'rentero') = 'z_klient' THEN
                        CAST(json_extract(r.data, '$.payout_czk') AS REAL) * 0.03
                    ELSE
                        CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)
                        * COALESCE(o.rentero_commission, 0.15) * (1.0 + COALESCE(o.vat_rate, 0.21))
                END), 0), 2) as rentero_fee_sum_czk,
```

Replace it with (adds the first `WHEN` so `rentero` / NULL client_type → 0):

```python
                ROUND(COALESCE(SUM(CASE
                    WHEN COALESCE(o.client_type, 'rentero') = 'rentero' THEN 0
                    WHEN COALESCE(o.client_type, 'rentero') = 'z_klient' THEN
                        CAST(json_extract(r.data, '$.payout_czk') AS REAL) * 0.03
                    ELSE
                        CAST(json_extract(r.data, '$.cena_ubytovani_czk') AS REAL)
                        * COALESCE(o.rentero_commission, 0.15) * (1.0 + COALESCE(o.vat_rate, 0.21))
                END), 0), 2) as rentero_fee_sum_czk,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_rentero_fee.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_dashboard_rentero_fee.py report/web_support.py
git commit -m "feat(dashboard): zero rentero_fee_sum_czk for client_type='rentero'"
```

---

## Task 2: Rename the summary key in the route + template (coupled)

**Files:**
- Modify: `report/routes/dashboard.py` (the KPI 4 aggregate loop, around line 416-435)
- Modify: `templates/dashboard.html` (KPI card #4, around line 107-114)
- Test: `tests/test_web_generation.py` (add a render assertion)

The route and the template share the summary key, so they change together in one step
(Step 3) to keep the page renderable. Per-object fee correctness is already covered by
Task 1; this task verifies the label/value swap end-to-end via a render test.

- [ ] **Step 1: Write the failing render test**

Add this function to `tests/test_web_generation.py` (mirrors the existing
`test_bank_page_renders_successfully` login pattern, but GETs `/`):

```python
def test_dashboard_renders_odmena_rentero_kpi():
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
    assert "Odměna Rentero" in response.text
    assert "Zisk Rentero" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_generation.py::test_dashboard_renders_odmena_rentero_kpi -v`
Expected: FAIL — the route still emits `total_net_profit_czk` and the template still shows
"Zisk Rentero" (status 200, but the `Odměna Rentero` / `Zisk Rentero` assertions fail).

- [ ] **Step 3: Apply both edits together**

**3a — `report/routes/dashboard.py`**, find this block:

```python
        # ── KPI 4 net profit + KPI 2 client payout aggregates ──────────────
        # Rentero-owned (heuristic, includes Rentero-as-z_klient): the row
        # contributes its zisk_czk to "Zisk Rentero".
        # External klient/z_klient: contributes provize + výdaje (clients
        # reimburse expenses through Rentero, so cash-flow to Rentero on
        # those objects is fee + expenses).
        client_payout_total = 0.0
        net_profit = 0.0
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    if row["is_rentero"]:
                        net_profit += cell.get("zisk_czk", 0) or 0
                    else:
                        client_payout_total += cell.get("client_payout_sum_czk", 0) or 0
                        net_profit += (cell.get("rentero_fee_sum_czk", 0) or 0) \
                                    + (cell.get("expenses_sum_czk", 0) or 0)
                    break
        dashboard_summary["total_client_payout_czk"] = client_payout_total
        dashboard_summary["total_net_profit_czk"] = round(net_profit, 2)
```

Replace it with:

```python
        # ── KPI 4 Rentero fee + KPI 2 client payout aggregates ─────────────
        # KPI 4 "Odměna Rentero" = total fee Rentero earns this month, summed
        # across all objects. rentero_fee_sum_czk already encodes the fee per
        # client_type (klient → commission, z_klient → 3 %, rentero → 0), so
        # we just sum it. client_payout_total stays as the rentero → klienti
        # cash-flow that KPI 2 "Výplata klientům" shows (excludes Rentero).
        client_payout_total = 0.0
        rentero_fee_total = 0.0
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    rentero_fee_total += cell.get("rentero_fee_sum_czk", 0) or 0
                    if not row["is_rentero"]:
                        client_payout_total += cell.get("client_payout_sum_czk", 0) or 0
                    break
        dashboard_summary["total_client_payout_czk"] = client_payout_total
        dashboard_summary["total_rentero_fee_czk"] = round(rentero_fee_total, 2)
```

**3b — `templates/dashboard.html`**, find:

```html
  {# Net profit #}
  <div class="kpi-card kpi-card-red" id="kpi-profit-card">
    <div class="kpi-label">Zisk Rentero</div>
    <div class="kpi-value" style="color:var(--color-red);font-size:24px;" id="kpi-profit"
         data-copy="{{ "%.2f"|format(dashboard_summary.total_net_profit_czk) }}">
      {{ "{:,.2f}".format(dashboard_summary.total_net_profit_czk).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
    </div>
    <div class="kpi-sub" id="kpi-profit-sub">ubytování vl. obj. + odměna · {{ dashboard_summary.current_month_label }}</div>
  </div>
```

Replace it with:

```html
  {# Rentero fee (odměna) #}
  <div class="kpi-card kpi-card-red" id="kpi-profit-card">
    <div class="kpi-label">Odměna Rentero</div>
    <div class="kpi-value" style="color:var(--color-red);font-size:24px;" id="kpi-profit"
         data-copy="{{ "%.2f"|format(dashboard_summary.total_rentero_fee_czk) }}">
      {{ "{:,.2f}".format(dashboard_summary.total_rentero_fee_czk).replace(",", "X").replace(".", ",").replace("X", " ") }} Kč
    </div>
    <div class="kpi-sub" id="kpi-profit-sub">provize ze správy objektů · {{ dashboard_summary.current_month_label }}</div>
  </div>
```

- [ ] **Step 4: Run the render test to verify it passes**

Run: `pytest tests/test_web_generation.py::test_dashboard_renders_odmena_rentero_kpi -v`
Expected: PASS

- [ ] **Step 5: Confirm no stray references to the removed key**

Run: `git grep -n "total_net_profit_czk"`
Expected: no matches.

- [ ] **Step 6: Run the existing dashboard route test for regression**

Run: `pytest tests/test_web_generation.py::test_dashboard_uses_requested_month_from_query -v`
Expected: PASS (it monkeypatches the maps/view-model and does not render the template).

- [ ] **Step 7: Commit**

```bash
git add report/routes/dashboard.py templates/dashboard.html tests/test_web_generation.py
git commit -m "feat(dashboard): KPI 4 shows Odměna Rentero (total rentero fee)"
```

---

## Task 3: Update the filter recompute JS

**Files:**
- Modify: `templates/dashboard.html` (`_applyFilters` JS, around line 356-367)

After Task 2 the server-rendered card is correct on load, but the client-side
`_applyFilters` (which fires on filter-tab clicks) still recomputes the old zisk-based
number. This task realigns the JS with the new metric. No JS test infra exists in this
repo, so verification is manual (Step 3).

- [ ] **Step 1: Replace the recompute block**

In `templates/dashboard.html`, find:

```javascript
  // Net profit per row: Rentero-owned = zisk; external = provize + výdaje
  // (clients reimburse property expenses through Rentero). The is-rentero
  // flag uses the heuristic from the backend (owner name starts with
  // "Rentero" OR no client row), not the raw client_type column.
  var totalProfit = 0;
  document.querySelectorAll('#property-grid .prop-row').forEach(function(row) {
    if (row.style.getPropertyValue('display') === 'none') return;
    if (row.dataset.isRentero === '1') {
      totalProfit += parseFloat(row.dataset.zisk) || 0;
    } else {
      totalProfit += (parseFloat(row.dataset.renteroFee) || 0)
                   + (parseFloat(row.dataset.expenses)   || 0);
    }
  });
  var kpiProfit = document.getElementById('kpi-profit');
  if (kpiProfit) kpiProfit.innerHTML = _formatNumber(totalProfit) + ' Kč';
```

Replace it with:

```javascript
  // Rentero fee per visible row. The backend SQL already encodes 0 for
  // client_type='rentero', so data-rentero-fee carries 0 for rentero-owned
  // objects and no client-type check is needed here.
  var totalFee = 0;
  document.querySelectorAll('#property-grid .prop-row').forEach(function(row) {
    if (row.style.getPropertyValue('display') === 'none') return;
    totalFee += parseFloat(row.dataset.renteroFee) || 0;
  });
  var kpiProfit = document.getElementById('kpi-profit');
  if (kpiProfit) kpiProfit.innerHTML = _formatNumber(totalFee) + ' Kč';
```

- [ ] **Step 2: Confirm the per-row attribute is still emitted**

The JS reads `row.dataset.renteroFee`, emitted by `data-rentero-fee` in the property-row
markup (around `templates/dashboard.html:187`). Confirm that line is unchanged and present.

Run: `git grep -n 'data-rentero-fee' templates/dashboard.html`
Expected: one match in the `.prop-row` anchor.

- [ ] **Step 3: Manual verification in the browser**

Start the app (`python run_web.py`), open `http://localhost:8000/`, log in, and confirm:
- Card #4 reads **"Odměna Rentero"** with sub "provize ze správy objektů · MM/YYYY".
- The value equals the sum of per-object fees (rentero-owned objects contribute 0).
- Switching filter tabs (Vše / Rentero / Klienti / Z Klienti) recomputes the card and the
  number stays consistent with the active filter; on **Rentero** the value is the 3 % fee
  from Rentero-owned z_klient objects (Opletalova/Ostrovní) only.

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): filter recompute sums rentero fee over visible rows"
```

---

## Final verification

- [ ] Run the focused suite for touched areas:

Run: `pytest tests/test_dashboard_rentero_fee.py tests/test_web_generation.py -v`
Expected: all PASS.

- [ ] Confirm no stray references to the old metric:

Run: `git grep -n "total_net_profit_czk"`
Expected: no matches.
