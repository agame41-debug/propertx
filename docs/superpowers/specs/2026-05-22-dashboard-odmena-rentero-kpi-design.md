# Dashboard KPI — "Zisk Rentero" → "Odměna Rentero" — Design Spec

**Date:** 2026-05-22
**Scope:** Repurpose the 4th dashboard KPI card from net profit (zisk) to Rentero's earned fee (odměna).

---

## 1. Overview

The 4th KPI card on the portfolio dashboard currently shows **"Zisk Rentero"** — a
mixed net-profit number (`cena_ubytování`-derived zisk for Rentero-owned objects, plus
`rentero_fee + výdaje` for external clients).

It is being replaced with a single, simpler metric: **the total Rentero fee (odměna)**
Rentero earns from managing properties, summed across all objects for the current month.

### Definition of "Rentero fee"

Per object, by `report_objects.client_type`:

| `client_type` | Fee contribution |
|---|---|
| `klient`   | `cena_ubytování × rentero_commission × (1 + vat_rate)` (unchanged formula) |
| `z_klient` | `payout_czk × 0.03` (unchanged formula) |
| `rentero`  | **0** — Rentero-owned objects have no client to charge, so no fee |

Key decision: the zero applies **only** to `client_type='rentero'`. Objects owned by a
Rentero entity but tagged `z_klient` (e.g. `Opletalova_10`, `Ostrovní_4`) still contribute
their real 3 % fee. The owner-name `is_rentero` heuristic is **not** used to decide the
zero — only the raw `client_type` column.

---

## 2. Current state

The fee per object is already computed in the dashboard SQL aggregation
([`report/web_support.py` `_build_dashboard_maps`](../../../report/web_support.py)), column
`rentero_fee_sum_czk`. Today its `CASE` computes a **notional** fee even for
`client_type='rentero'` (`cena_ubytování × commission × (1+vat)`).

`rentero_fee_sum_czk` is used **only** by this dashboard KPI:

- `report/web_support.py` — SQL column, history_map, cell field
- `report/routes/dashboard.py` — `net_profit` accumulation (external rows)
- `templates/dashboard.html` — `data-rentero-fee` attribute + JS `_applyFilters`

It is **not** consumed by `summary.py`, the property page, or anywhere else. Changing it is
therefore fully contained to the dashboard.

---

## 3. Design

### Approach: zero the fee at the SQL level (single source of truth)

In the `rentero_fee_sum_czk` `CASE` in `_build_dashboard_maps`, add a branch so
`client_type='rentero'` evaluates to `0`. Result: `rentero_fee_sum_czk` (and the
`data-rentero-fee` attribute derived from it) is correct everywhere with no per-consumer
special-casing.

Rejected alternative: keep the SQL as-is and filter `client_type != 'rentero'` in both the
backend and the JS. Rejected because it duplicates the condition in two places and leaves a
misleading notional value in `data-rentero-fee`.

### Backend — `report/routes/dashboard.py`

Replace the mixed `net_profit` loop (zisk for rentero + fee+výdaje for external) with a
simple sum of the current month's `rentero_fee_sum_czk` across all properties:

```python
dashboard_summary["total_rentero_fee_czk"] = round(
    sum(cell rentero_fee_sum_czk for the current month), 2
)
```

- `total_client_payout_czk` accumulation stays unchanged.
- The old `total_net_profit_czk` key is removed (its only consumer is this card).
- `zisk_czk` per-row attachment for the row-level "rentero …" indicator stays unchanged.

### Template — `templates/dashboard.html`

KPI card #4 (`#kpi-profit-card`):

- Label: `Zisk Rentero` → **`Odměna Rentero`**
- Value: bound to `dashboard_summary.total_rentero_fee_czk`
- Sub-label: `ubytování vl. obj. + odměna` → **`provize ze správy objektů · MM/YYYY`**
- Card colour stays red (`kpi-card-red`) — no colour change in scope.

The element `id`s (`kpi-profit-card`, `kpi-profit`, `kpi-profit-sub`) are kept to avoid
churn in the JS selectors.

### JS — `_applyFilters` in `templates/dashboard.html`

The client-side recompute that drives KPI #4 on filter-tab switches:

```js
var totalFee = 0;
// over visible rows:
totalFee += parseFloat(row.dataset.renteroFee) || 0;
// (rentero-owned rows already carry 0, so no client_type check needed)
kpiProfit.innerHTML = _formatNumber(totalFee) + ' Kč';
```

The `zisk` / `expenses` terms drop out of this computation.

### Filter behaviour

The card recalculates on tab switch, consistent with the other three KPIs — it sums the
fee over **visible** rows. On the **Rentero** tab the value will be the 3 % fee from the
Rentero-owned `z_klient` objects only (Opletalova/Ostrovní); all true `rentero` objects are
0. This follows directly from the fee definition above.

---

## 4. Out of scope

- The per-row "rentero {zisk}" indicator in the property list column (`zisk_czk`).
- The property detail page and `summary.py` fee/zisk math.
- KPI cards #1–#3 (payout, client payout / DPH, reservations).
- Card colour / icon changes.

---

## 5. Testing

- Manual: load `/`, confirm card reads "Odměna Rentero" and the value equals the sum of
  per-object fees with `rentero` objects contributing 0.
- Manual: switch filter tabs (Vše / Rentero / Klienti / Z Klienti) and confirm the card
  recomputes over visible rows; Rentero tab shows only the z_klient-owned 3 % fees.
- Cross-check one `klient` and one `z_klient` object's per-row `data-rentero-fee` against
  the formulas in §1.
