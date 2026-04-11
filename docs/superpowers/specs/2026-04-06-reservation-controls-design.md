# Reservation Controls + Floating Panel — Design Spec

**Date:** 2026-04-06  
**Scope:** Four new capabilities for reservation management in Rentero

---

## 1. Overview

Four features are added to the reservation management system:

1. **Floating panel** — draggable, resizable, tabbed window that replaces the current right-side drawer and persists across page navigation
2. **Move reservation** — manually reassign a reservation to a different month
3. **Exclude reservation** — soft-remove a reservation from calculations without deleting it
4. **Payout adjustment** — automatically detect and generate adjustment rows when a payout item references a reservation from a locked/past month

Features 2–4 must survive report re-generation and must be auditable (who did what, why).

---

## 2. Feature: Floating Panel

### Overview

Replace the current right-side drawer with a floating, draggable, resizable panel that behaves like a mini-browser window. Up to 5 reservation tabs + 1 permanent Summary tab. Persists across full page reloads via `localStorage`.

### Visual structure

```
┌──────────────────────────────────────────────────┬──┬──┐
│ ⠿  [×] Jan Novák  [×] Marie K.  [×] +  │ Σ  │ _ │ □ │
├──────────────────────────────────────────────────┴──┴──┤
│                                                        │
│  (tab content — reservation detail or summary)         │
│                                                        │
└──────────────────────────────────────▐▌────────────────┘
                                        ↑ resize handle
```

- **Tab bar**: each open reservation = one tab (guest name, short). `+` appears when < 5 tabs. `Σ` = Summary tab (always last, cannot be closed).
- **`_`** = minimize to tab bar only (content collapses, tabs remain visible).
- **`□`** = toggle between compact and expanded sizes (two preset sizes).
- **`⠿`** = drag handle (left side of header).
- **Resize** via bottom-right corner handle (native CSS `resize: both` or custom drag).

### Persistence (`localStorage` key: `rentero_panel`)

State saved on every change (tab open/close, position, size, active tab, minimized):

```json
{
  "tabs": [
    {"code": "HMA...", "slug": "obj1", "year": 2026, "month": 3, "title": "Jan Novák"}
  ],
  "activeTab": "HMA...",
  "position": {"x": 120, "y": 80},
  "size": {"w": 560, "h": 420},
  "minimized": false
}
```

On `DOMContentLoaded`: read state, recreate panel, fetch each tab's content from `/reservation/{code}/panel`.

### New backend endpoint

```
GET /reservation/{code}/panel
    → looks up confirmation_code in report_rows (most recent entry)
    → returns reservation_detail HTML partial (same template as current drawer)
    → 404 if not found
```

This endpoint is slug/month agnostic — it finds the row by code across all months. The panel stores slug/year/month in tab metadata for actions (move, exclude) that need them.

### Tab lifecycle

- **Open**: clicking a row in the reservations table calls `openPanel(code, slug, year, month, guestName)`. If code already open → switch to that tab.
- **Close**: X button on tab removes it from state, if it was active → switch to next tab.
- **Max tabs**: when 5 are open, clicking a new row replaces the oldest tab (LRU).
- **Cross-page**: clicking "open in new tab" on another property's reservation page opens a new tab in the same panel.

### Summary tab (`Σ`)

Shows aggregated totals of all currently open reservation tabs:

| Field | Value |
|---|---|
| Rezervací | count |
| Nocí | sum |
| Výplata | sum CZK |
| Ubytování | sum CZK |
| Příprava pokoje | sum CZK |
| City tax | sum CZK |
| Provize | sum CZK |

Plus a mini breakdown table: Airbnb vs Booking rows.

Summary is computed client-side from `data-val` attributes already present in the open tab HTML — no additional fetch needed.

### Move/Exclude actions in panel

The **Akce** section (described in sections 3 and 4) lives inside the panel content, not a separate drawer. Same forms, same POST endpoints.

### CSS/JS implementation

- New file: `templates/partials/base_panel.html` — the panel HTML skeleton (empty, hidden by default), included in `base.html`
- New file: `static/js/floating_panel.js` (or inline in `base_scripts.html`) — ~300 lines vanilla JS
- CSS in `base_styles.html`: `.fp-panel`, `.fp-tabbar`, `.fp-tab`, `.fp-content`, `.fp-minimize`, `.fp-resize-handle`
- Panel `z-index: 1000` (above everything including dropdowns)
- Smooth `box-shadow` and subtle backdrop on drag

### Migration from current drawer

The current `openDrawerFromRow()` function is replaced by `openPanel()`. The drawer HTML (`property_drawer.html`) content is reused as the panel tab content template. Existing `property_scripts.html` drawer JS is replaced.

---

## 3. Data Model

### 3.1 `reservation_month_assignments` (new table)

Stores manual month reassignments. Overrides the automatic `assign_report_month()` logic.

```sql
CREATE TABLE IF NOT EXISTS reservation_month_assignments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    target_year       INTEGER NOT NULL,
    target_month      INTEGER NOT NULL,
    original_year     INTEGER NOT NULL,
    original_month    INTEGER NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    actor             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    reverted_at       TEXT,
    reverted_by       TEXT,
    UNIQUE(slug, confirmation_code)   -- one active assignment per reservation
);
```

### 3.2 `reservation_exclusions` (new table)

Stores soft-exclusions. Excluded reservations are fetched and displayed but not counted in totals.

```sql
CREATE TABLE IF NOT EXISTS reservation_exclusions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    actor             TEXT NOT NULL DEFAULT '',
    excluded_at       TEXT NOT NULL,
    reinstated_at     TEXT,
    reinstated_by     TEXT,
    UNIQUE(slug, confirmation_code)
);
```

### 3.3 `report_rows` — new fields in JSON blob

Add these fields to the `data` JSON in `report_rows`:

- `is_excluded` (bool) — row is deactivated, not counted in sums
- `is_payout_adjustment` (bool) — row is an auto-generated adjustment entry
- `adjustment_original_year` (int | None) — original month year for adjustments
- `adjustment_original_month` (int | None) — original month number for adjustments

---

## 4. Feature: Move Reservation

### Pipeline integration (report/main.py)

After `filter_for_property_month()` returns reservations for a property-month, apply month assignment overrides:

```python
# Load active assignments for this property
assignments = get_reservation_month_assignments(db_conn, slug)
# {confirmation_code: {target_year, target_month, original_year, original_month}}

# Remove reservations that are being moved AWAY from this month
reservations = [
    r for r in reservations
    if not (
        r["confirmation_code"] in assignments
        and (assignments[r["confirmation_code"]]["target_year"] != year
             or assignments[r["confirmation_code"]]["target_month"] != month)
    )
]

# Add reservations that are being moved INTO this month
# (they were originally in a different month, but reassigned here)
moved_in = get_reservations_assigned_to_month(db_conn, slug, year, month, all_raw)
reservations.extend(moved_in)
```

**Important**: `moved_in` reservations are re-fetched from `all_raw` (the full Hostify fetch for the window) and re-normalized. All financials (city tax, cleaning, payout) are recalculated for the target month context.

### Constraint: month must be OPEN

Moving a reservation is blocked if either the source or target month is LOCKED. The route checks both.

### HTTP routes

```
POST /property/{slug}/{year}/{month}/reservation/{code}/move
     Form: target_year, target_month, reason
     → creates/updates reservation_month_assignments record
     → marks both source and target months as STALE
     → redirect back

POST /property/{slug}/{year}/{month}/reservation/{code}/move-revert
     → sets reverted_at on the assignment record
     → marks both months as STALE
```

### UI — panel Akce section

In the floating panel, add an **Akce** section visible when month is OPEN:

```
┌─────────────────────────────────────────┐
│ Akce                                    │
│                                         │
│ [← 03/2026] Přesunout do jiného měsíce [04/2026 →] │
│ [Vyřadit z výpočtu]                     │
└─────────────────────────────────────────┘
```

- Left button: move to previous month
- Right button: move to next month
- Clicking either opens a small confirmation form (inline in drawer) with a required "Důvod" field
- After submit: drawer closes, page reloads with flash message

If reservation has an active assignment, show a banner:
```
⚠ Přesunuto z 01/2026 (reason) — [Vrátit zpět]
```

---

## 5. Feature: Exclude Reservation

### Pipeline integration

After reservations are collected and month assignments applied, apply exclusions:

```python
exclusions = get_active_exclusions(db_conn, slug)
# {confirmation_code}

for r in reservations:
    if r["confirmation_code"] in exclusions:
        r["is_excluded"] = True
```

Excluded rows are **included** in `report_rows` (so they're visible in UI) but skipped in all financial aggregations: `build_report_summary`, `calculate_totals_with_config`, `row_breakdown`.

### HTTP routes

```
POST /property/{slug}/{year}/{month}/reservation/{code}/exclude
     Form: reason
     → creates reservation_exclusions record
     → marks month as STALE

POST /property/{slug}/{year}/{month}/reservation/{code}/reinstate
     → sets reinstated_at on the record
     → marks month as STALE
```

### UI — table row

Excluded rows shown in the reservations table with:
- `opacity: 0.45` on the row
- Badge `Vyřazeno` next to guest name (same position as `upraveno`)
- Amounts show strikethrough style
- Row is NOT included in the header count ("X záznamů" excludes them)
- Row IS visible, just clearly inactive

### UI — panel Akce section

- If not excluded: button `Vyřadit z výpočtu` (red/muted style)
- If excluded: button `Vrátit do výpočtu` (green) + reason shown

---

## 6. Feature: Payout Adjustment (Automatic)

### Detection logic (report/main.py, per-property loop)

After loading airbnb/booking payout data, before building `verification_index`:

```python
# All confirmation_codes seen in the payout CSV for this property
payout_codes_this_month = set(gref_map.keys())  # airbnb
payout_codes_this_month |= set(booking_batch_map.keys())  # booking

# Codes assigned to THIS month's reservations
current_month_codes = {r["confirmation_code"] for r in reservations}

# Codes in payout CSV that are NOT in current month — check if they belong to a past locked month
cross_month_codes = payout_codes_this_month - current_month_codes

for code in cross_month_codes:
    past_row = get_report_row_by_code(db_conn, slug, code)
    if past_row is None:
        continue  # unknown code, let verifier handle as CHYBÍ_V_HOSTIFY
    past_year = past_row["year"]
    past_month = past_row["month"]
    if past_year == year and past_month == month:
        continue  # same month, not cross-month
    # This is a payout adjustment for a past reservation
    # Create a synthetic adjustment reservation
    adjustment = _build_adjustment_reservation(past_row, gref_map.get(code) or booking_batch_map.get(code))
    reservations.append(adjustment)
```

### `_build_adjustment_reservation(past_row, batch_item)`

Produces a dict compatible with `HostifyReservation` format:
- `confirmation_code`: original code (same)
- `guest_name`: from past_row
- `check_in` / `check_out` / `nights` / `stay_label`: from past_row
- `source`: from past_row (Airbnb or Booking)
- `is_payout_adjustment = True`
- `adjustment_original_year` / `adjustment_original_month`: from past_row
- `payout_price_eur`: from batch_item amount
- `channel_commission_eur`: from batch_item if present, else 0
- `cleaning_fee_eur = 0`, `city_tax_eur = 0` (no duplication)
- `balicky = 0`
- `status = "adjustment"` (not cancelled, not normal)

### Calculator changes (`calculator.py`)

When `is_payout_adjustment` is True:
- `uklid_czk = 0`
- `city_tax = 0`
- `balicky = 0`
- `dph_uklid_balicky = 0`
- `cena_ubytovani` is derived normally from adjusted payout
- `provize_czk` is calculated if `channel_commission_eur > 0`

Same pattern as cancelled reservations, but `is_payout_adjustment` flag instead of `is_cancelled`.

### UI — table row

Channel column shows:
```
[Airbnb]
Doplatek ↗ 01/2026
```

- Channel badge stays (Airbnb / Booking) — full colour
- Below it: small text `Doplatek ↗ {original_month:02d}/{original_year}` in `var(--text-muted)`
- No special row background (doesn't need to stand out like overrides)
- Cleaning, city tax, balíčky columns show `—`
- Drawer shows "Toto je platební korekce k rezervaci z {original_month:02d}/{original_year}"

---

## 7. Summary of New DB Functions

In `report/db.py` (or a new `report/db_controls.py`):

| Function | Description |
|---|---|
| `create_reservation_month_assignment(conn, data)` | Insert/replace assignment |
| `revert_reservation_month_assignment(conn, slug, code, actor)` | Set reverted_at |
| `get_reservation_month_assignments(conn, slug)` | Dict of active assignments |
| `get_reservations_assigned_to_month(conn, slug, year, month)` | Codes moved INTO this month |
| `create_reservation_exclusion(conn, data)` | Insert exclusion |
| `reinstate_reservation(conn, slug, code, actor)` | Set reinstated_at |
| `get_active_exclusions(conn, slug)` | Set of excluded confirmation_codes |
| `get_report_row_by_code(conn, slug, code)` | Fetch past row for adjustment detection |

---

## 8. Constraints and Edge Cases

- **Locked month**: Move and Exclude are blocked if month is LOCKED. Adjustment rows can be added to an OPEN month even if the original was locked.
- **Re-generation**: All three features are re-applied on every generation. Moving/excluding is stored in DB, not in `report_rows` — so re-generating always picks up current controls.
- **Adjustment dedup**: If the same `confirmation_code` appears in payout CSV for multiple months, create one adjustment row per unique batch_ref. In practice this means at most one Airbnb adjustment and one Booking adjustment per code — both appear as separate rows in the same month report.
- **Excel export**: Excluded rows are omitted from Excel. Adjustment rows appear in Excel with "Doplatek" label in the Kanál column.
- **Finanční přehled (breakdown)**: Excluded rows not counted. Adjustment rows counted in the channel's payout total but not in cleaning/city tax/balíčky totals.

---

## 9. UI Component Map

| Component | Change |
|---|---|
| `templates/base.html` | Include `base_panel.html` partial |
| `templates/partials/base_panel.html` | New — floating panel HTML skeleton |
| `templates/partials/base_scripts.html` | Add `floating_panel.js` (or inline panel JS) |
| `templates/partials/base_styles.html` | Add `.fp-*` panel CSS, `.row-excluded`, `.adjustment-sublabel` |
| `templates/partials/property_scripts.html` | Replace `openDrawerFromRow()` with `openPanel()` |
| `templates/partials/property_reservations.html` | Excluded row style, adjustment channel sub-label |
| `templates/partials/reservation_detail.html` | Add Akce section (move + exclude forms) |
| `report/routes/property_routes.py` | Add 4 new POST routes + 1 GET `/reservation/{code}/panel` |
| `report/db_controls.py` | New module — 8 DB functions + 2 new tables |
| `report/db.py` `_SCHEMA` | Add 2 new table CREATE statements |
| `report/main.py` | Apply assignments, exclusions, adjustment detection in pipeline |
| `report/calculator.py` | Handle `is_payout_adjustment` flag |
