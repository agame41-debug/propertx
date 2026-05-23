# Dashboard KPI carousel — Design Spec

**Date:** 2026-05-23
**Scope:** Turn the dashboard (`Přehled portfolia`) KPI row into a paginated carousel — 4 cards per page, ‹ › arrows + dots below — so more KPI cards can be added over time. As the first new content, promote "Bilance DPH" out of the card-2 swap into its own always-visible card on page 2, recomputing per filter.

---

## 1. Overview

The portfolio dashboard shows a `.kpi-grid` of **4 cards**:

1. **Celková výplata** (green)
2. **Výplata klientům** — which JS *swaps* to **Bilance DPH** when the *Rentero* filter tab is active
3. **Celkem rezervací** (sparkline)
4. **Odměna Rentero** (red)

Two problems this addresses:

- DPH is only reachable through the *Rentero* filter (it hides inside card 2). It should be a first-class, always-visible card.
- There's no room to grow. The user plans to add more KPI cards.

Solution: wrap the cards in a horizontal **carousel** that shows 4 per page with ‹ › arrows and page dots centered below the grid. Card 2 reverts to always showing **Výplata klientům** (swap removed). **Bilance DPH** becomes a standalone card on page 2, and — like every other card — **recomputes when a filter tab is clicked**.

This change is **dashboard-only**. The shared `.kpi-grid` / `.kpi-card` classes (also used by bank, inventory, logs, reconciliation, guest_evidence pages) are **not modified**.

---

## 2. Layout

### Card placement

| Page | Cards |
|---|---|
| 1 | Celková výplata · **Výplata klientům** (always) · Celkem rezervací · Odměna Rentero |
| 2 | **Bilance DPH** (standalone, always visible) · 3 empty slots for future KPIs |

### DOM structure (replaces the current `.kpi-grid` block in `dashboard.html`)

```
<div class="kpi-carousel" id="kpi-carousel">
  <div class="kpi-carousel-viewport">
    <div class="kpi-carousel-track" id="kpi-carousel-track">
      <div class="kpi-page">  <!-- page 1 -->
        … card 1 … card 2 … card 3 … card 4 …
      </div>
      <div class="kpi-page">  <!-- page 2 -->
        … Bilance DPH card …
      </div>
    </div>
  </div>
  <div class="kpi-carousel-nav" id="kpi-carousel-nav">
    <button class="kpi-nav-btn" id="kpi-nav-prev" aria-label="Předchozí" disabled>‹</button>
    <div class="kpi-dots" id="kpi-dots">
      <button class="kpi-dot is-active" aria-label="Stránka 1"></button>
      <button class="kpi-dot" aria-label="Stránka 2"></button>
    </div>
    <button class="kpi-nav-btn" id="kpi-nav-next" aria-label="Další">›</button>
  </div>
</div>
```

### CSS (new, scoped — added to `base_styles_components.html`)

- `.kpi-carousel-viewport { overflow: hidden; }`
- `.kpi-carousel-track { display: flex; transition: transform 0.3s ease; }`
- `.kpi-page { flex: 0 0 100%; display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-md); }`
  (mirrors the desktop look of the old `auto-fit` grid, but **fixed 4 columns** so a partial page keeps consistent card widths and aligns left instead of stretching one card full width)
- `.kpi-carousel-nav { display: flex; align-items: center; justify-content: center; gap: var(--space-sm); margin-bottom: var(--space-xl); }`
  (the old `.kpi-grid` carried `margin-bottom: var(--space-xl)`; the carousel's bottom margin now lives on the nav so vertical rhythm is unchanged)
- `.kpi-nav-btn` — circular button, design-system colors (`--bg-3`, `--border-base`, hover `--bg-4`), `:disabled { opacity:.35; cursor:default; }`
- `.kpi-dot` — small round button; `.kpi-dot.is-active` filled with `--brand`, inactive `oklch(var(--fg) / 0.18)`
- Hide nav when only one page: `.kpi-carousel[data-pages="1"] .kpi-carousel-nav { display: none; }`

### Responsive (`base_styles_responsive.html`, `@media (max-width: 639px)`)

- `.kpi-page { grid-template-columns: 1fr 1fr; gap: var(--space-xs); }` → 2×2 per page (matches the current mobile layout). Nav stays centered below.

---

## 3. Bilance DPH card (standalone)

Reuse the existing DPH markup currently inside `#kpi-card-2-dph`, moved to its own `.kpi-card kpi-card-amber` on page 2:

- **Label:** `Bilance DPH`
- **Value** (`id="kpi-vat-balance"`): `+/− {abs} Kč`; green (`--color-green`) when refund (balance < 0), red (`--color-red`) when owed (balance ≥ 0)
- **Sub-line** (`id="kpi-vat-sub"`): `Výstup +{output}` · `Vstup −{input}`
- Border color `kpi-card-amber` (static); the value color conveys sign.

Initial server-rendered values come from the existing `dashboard_summary.rentero_vat_output_czk / rentero_vat_input_czk / rentero_vat_balance_czk` (the all-objects aggregate). Default tab is *Vše* (all rows visible), so the initial render equals what JS would compute over all rows — consistent.

---

## 4. Per-filter recompute (data flow)

The other KPI cards already recompute in `_applyFilters()` by summing `data-*` attributes over visible `.prop-row`s (`data-payout`, `data-zisk`, `data-expenses`, `data-rentero-fee`). DPH joins this mechanism.

### Backend (`report/routes/dashboard.py`)

The current-month sweep already calls `build_report_summary(...)` per property (variable `s`) and accumulates `rentero_vat_output/input/balance`. **No new computation** — just stash the per-property values onto the current-month cell, alongside the existing `expenses_sum_czk` / `zisk_czk` attach:

```python
# in the per-property loop, after computing s:
slug_to_vat[slug] = {
    "output":  round(float(s.get("vat_output_czk")  or 0), 2),
    "input":   round(float(s.get("vat_input_czk")   or 0), 2),
    "balance": round(float(s.get("vat_balance_czk") or 0), 2),
}
# in the cell-attach loop (current-month cell):
cell["vat_output_czk"]  = slug_to_vat.get(slug, {}).get("output", 0.0)
cell["vat_input_czk"]   = slug_to_vat.get(slug, {}).get("input", 0.0)
cell["vat_balance_czk"] = slug_to_vat.get(slug, {}).get("balance", 0.0)
```

The existing aggregate assignments (`dashboard_summary["rentero_vat_*"]`) stay unchanged.

### Template (`dashboard.html`)

Emit three new data attributes on each `.prop-row` (next to `data-zisk`, `data-expenses`):

```
data-vat-output="{{ cur_cell.vat_output_czk|default(0) if cur_cell else 0 }}"
data-vat-input="{{ cur_cell.vat_input_czk|default(0) if cur_cell else 0 }}"
data-vat-balance="{{ cur_cell.vat_balance_czk|default(0) if cur_cell else 0 }}"
```

### JS (`dashboard.html` `_applyFilters`)

While iterating visible rows, accumulate `totalVatOutput`, `totalVatInput`, `totalVatBalance`. After the loop, update the DPH card:

- `#kpi-vat-balance` text → `(balance < 0 ? '+' : '−') + _formatNumber(Math.abs(balance)) + ' Kč'`, color green if `balance < 0` else red.
- `#kpi-vat-sub` output/input spans → `+output` / `−input`.

Remove the old swap block (the `kpiCard2Default` / `kpiCard2Dph` show/hide logic), and remove the `#kpi-card-2-default` / `#kpi-card-2-dph` wrappers from card 2 — card 2 renders **Výplata klientům** directly.

---

## 5. Pager JS (`dashboard.html`)

Small vanilla pager appended to the existing `<script>`:

- `pages = track.children.length`; set `carousel.dataset.pages = pages` (CSS hides nav when 1).
- `current = 0`.
- `go(n)`: clamp `n` to `[0, pages-1]`; `track.style.transform = 'translateX(' + (-100 * current) + '%)'`; toggle `.is-active` on the matching dot; `prev.disabled = current===0`; `next.disabled = current===pages-1`.
- `prev` → `go(current-1)`, `next` → `go(current+1)`, each dot → `go(index)`.

No auto-advance, no touch-swipe (out of scope — arrows/dots work on touch; swipe can be added later). No dependencies.

---

## 6. Testing

- **Smoke** (`TestClient GET /`, like `test_dashboard_renders_odmena_rentero_kpi`): assert `kpi-carousel-track` present, two `kpi-page` blocks, `kpi-dots` present, `Bilance DPH` present, and the old `kpi-card-2-dph` id is **absent**.
- **Backend** (call `web_module.dashboard(...)` with seeded rows via `save_report_rows`/`log_report_generated` and capture the template context, like `test_dashboard_uses_requested_month_from_query`): assert the current-month cell carries `vat_output_czk` / `vat_input_czk` / `vat_balance_czk`.
- **JS** filter-recompute and pager: verified manually on prod (no JS test harness in the repo, consistent with the existing filter logic).

---

## 7. Out of scope

- Touch-swipe gestures (arrows + dots only).
- Adding KPI cards beyond promoting DPH (the empty page-2 slots are intentional placeholders for the user's future cards).
- Any change to the shared `.kpi-grid` / `.kpi-card` styles or to other pages using them.
- Backend DPH math (`build_report_summary`) — unchanged; only per-cell stashing is added.
