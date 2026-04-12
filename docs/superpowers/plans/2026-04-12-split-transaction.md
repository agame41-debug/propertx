# Split Transaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to manually split individual bank transactions (by batch_ref) from a parent reservation into separate rows that can be independently moved between months, excluded, and merged back.

**Architecture:** New `split_transactions` DB table stores persistent split records (slug, code, batch_ref). During report generation, engine reads splits and creates `__SP`/`__SP2` suffixed rows (like `__ADJ`/`__AC`). Split rows carry their own batch_ref for bank matching and reduce the parent's effective_payout_eur. UI shows "Oddělit" button on bank transaction cards when 2+ transactions exist, and "Vrátit do hlavní rezervace" on split rows.

**Tech Stack:** Python/FastAPI, SQLite, Jinja2 templates, HTMX

---

### Task 1: DB Schema — `split_transactions` table

**Files:**
- Modify: `report/db.py` (schema + migration + CRUD functions)

- [ ] **Step 1: Add table to _SCHEMA**

In `report/db.py`, add after the `reservation_exclusions` table (before `CREATE TABLE IF NOT EXISTS users`):

```sql
CREATE TABLE IF NOT EXISTS split_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    batch_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    UNIQUE(slug, confirmation_code, batch_ref)
);
```

- [ ] **Step 2: Add migration fallback in `_run_migrations`**

Add at the end of `_run_migrations()`:

```python
# split_transactions table (added 2026-04)
conn.execute("""
    CREATE TABLE IF NOT EXISTS split_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        confirmation_code TEXT NOT NULL,
        batch_ref TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        UNIQUE(slug, confirmation_code, batch_ref)
    )
""")
```

- [ ] **Step 3: Add CRUD functions**

Add these functions to `report/db.py` (after the existing exclusion imports/functions, near the bottom):

```python
def get_split_transactions(conn, slug):
    """Return all active split transaction records for a property."""
    rows = conn.execute(
        "SELECT * FROM split_transactions WHERE slug = ?", (slug,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_split_transactions_for_code(conn, slug, confirmation_code):
    """Return split records for a specific reservation code."""
    rows = conn.execute(
        "SELECT * FROM split_transactions WHERE slug = ? AND confirmation_code = ?",
        (slug, confirmation_code),
    ).fetchall()
    return [dict(r) for r in rows]


def create_split_transaction(conn, slug, confirmation_code, batch_ref, actor=""):
    """Create a split transaction record. Idempotent via UNIQUE constraint."""
    conn.execute(
        """INSERT OR IGNORE INTO split_transactions
               (slug, confirmation_code, batch_ref, created_at, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        (slug, confirmation_code, batch_ref, _now(), actor),
    )
    conn.commit()


def delete_split_transaction(conn, slug, confirmation_code, batch_ref):
    """Delete a split transaction record (merge back)."""
    conn.execute(
        "DELETE FROM split_transactions WHERE slug = ? AND confirmation_code = ? AND batch_ref = ?",
        (slug, confirmation_code, batch_ref),
    )
    conn.commit()
```

- [ ] **Step 4: Add exports**

Add `get_split_transactions`, `get_split_transactions_for_code`, `create_split_transaction`, `delete_split_transaction` to the public imports used by engine/routes.

- [ ] **Step 5: Commit**

```bash
git add report/db.py
git commit -m "feat: add split_transactions DB table and CRUD functions"
```

---

### Task 2: Calculator — Add split transaction fields

**Files:**
- Modify: `report/calculator.py:144-211` (`_compute_row` return dict) and `report/calculator.py:214-282` (`_null_row` return dict)

- [ ] **Step 1: Add fields to `calculate_row` return dict**

In the `calculate_row` function's return dict (after `adjustment_parent_code` line ~210), add:

```python
"is_split_transaction": bool(reservation.get("is_split_transaction")),
"split_parent_code": reservation.get("split_parent_code", ""),
```

- [ ] **Step 2: Add fields to `_null_row` return dict**

In the `_null_row` function's return dict (after `adjustment_parent_code` line ~281), add:

```python
"is_split_transaction": bool(reservation.get("is_split_transaction")),
"split_parent_code": reservation.get("split_parent_code", ""),
```

- [ ] **Step 3: Add `is_split_transaction` to `_no_fees` condition**

In `calculate_row` (~line 130), change:

```python
_no_fees = is_cancelled or is_payout_adjustment
```

to:

```python
is_split = bool(reservation.get("is_split_transaction"))
_no_fees = is_cancelled or is_payout_adjustment or is_split
```

This ensures split rows don't get cleaning, city tax, or balíčky (already counted in parent).

- [ ] **Step 4: Commit**

```bash
git add report/calculator.py
git commit -m "feat: add is_split_transaction and split_parent_code to calculator output"
```

---

### Task 3: Engine — Create split rows during generation

**Files:**
- Modify: `report/engine.py:200-611` (inside `generate_report_in_process`)

- [ ] **Step 1: Import the new DB function**

At the top of `report/engine.py`, add `get_split_transactions` to the imports from `report.db`:

```python
from report.db import (
    ...existing imports...
    get_split_transactions,
)
```

- [ ] **Step 2: Load split records after exclusions section**

After the exclusions block (~line 396, after `r["is_excluded"] = True`), add:

```python
# ── Split transactions ─────────────────────────────────────────────────
split_records = get_split_transactions(conn, slug)
# Group by confirmation_code for quick lookup
splits_by_code: dict[str, list[dict]] = {}
for sr in split_records:
    splits_by_code.setdefault(sr["confirmation_code"], []).append(sr)
```

- [ ] **Step 3: Build split rows after AirCover section**

After the AirCover items block (~line 521, after the AirCover log.info), add a new section:

```python
# ── Split transaction rows ─────────────────────────────────────────────
# For each split record, find the batch in all_batches_map and create
# a separate row with __SP suffix. The parent's effective_payout_eur
# will be reduced later (after batch rate attachment).
split_batch_refs_by_code: dict[str, set[str]] = {}  # code -> set of split batch_refs
for code, splits in splits_by_code.items():
    parent_res = next(
        (r for r in reservations if r.get("confirmation_code") == code),
        None,
    )
    if parent_res is None:
        # Parent not in this month — skip splits
        continue
    all_batches = airbnb_all_batches.get(code, [])
    if not all_batches:
        continue
    sp_count = 0
    for sr in splits:
        batch_ref = sr["batch_ref"]
        # Find the batch info for this batch_ref
        batch_info = next(
            (b for b in all_batches if b.get("gref", "") == batch_ref),
            None,
        )
        if batch_info is None:
            continue
        sp_count += 1
        suffix = "__SP" if sp_count == 1 else f"__SP{sp_count}"
        split_res = _build_split_reservation(parent_res, batch_info, suffix=suffix)
        reservations.append(split_res)
        split_batch_refs_by_code.setdefault(code, set()).add(batch_ref)
        log.info(
            "Split transaction for %s: batch %s, %.2f EUR",
            code, batch_ref, batch_info.get("payout_eur", 0),
        )
```

- [ ] **Step 4: Add `_build_split_reservation` function**

Add this function after `_build_aircover_reservation` (~line 166):

```python
def _build_split_reservation(parent_row: dict, batch_info: dict, suffix: str = "__SP") -> dict:
    """
    Build a synthetic reservation for a manually split transaction.
    These represent individual payout batches separated from the parent
    for independent month management. Cleaning, city tax, and balíčky
    are zeroed out to avoid double-counting (same as adjustments).
    """
    parent_code = parent_row.get("confirmation_code", "")
    source = parent_row.get("source", "")
    payout_eur = float(batch_info.get("payout_eur") or 0.0)
    return {
        "confirmation_code": f"{parent_code}{suffix}",
        "split_parent_code": parent_code,
        "guest_name": parent_row.get("guest_name", ""),
        "check_in": parent_row.get("check_in", ""),
        "check_out": parent_row.get("check_out", ""),
        "nights": parent_row.get("nights") or 0,
        "adults": parent_row.get("adults") or 0,
        "children": 0,
        "infants": 0,
        "source": source,
        "status": "split",
        "is_cancelled": False,
        "is_split_transaction": True,
        "listing_nickname": parent_row.get("listing_nickname", ""),
        "listing_id": parent_row.get("listing_id"),
        "confirmed_at": parent_row.get("check_in", ""),
        "cleaning_fee_eur": 0.0,
        "city_tax_eur": 0.0,
        "channel_commission_eur": float(batch_info.get("commission_eur") or 0.0),
        "payout_price_eur": payout_eur,
        "effective_payout_eur": payout_eur,
        "airbnb_batch_rate": float(batch_info.get("airbnb_rate") or 0.0),
        "airbnb_payout_date": batch_info.get("payout_date", ""),
        "batch_ref": batch_info.get("gref") or batch_info.get("batch_ref", ""),
        "batch_payout_date": batch_info.get("payout_date", ""),
        "batch_amount_czk": batch_info.get("payout_czk"),
    }
```

- [ ] **Step 5: Skip split rows in "Attach batch rate" section**

In the "Attach batch rate" loop (~line 555-580), after the `is_aircover` skip, add:

```python
if row.get("is_split_transaction"):
    continue
```

- [ ] **Step 6: Reduce parent's effective_payout_eur**

In the "Split payout: limit effective_payout_eur" section (~line 581-610), add logic to subtract split amounts. After the existing `window_eur` logic, before the CNB rates section, add:

```python
    # Reduce parent effective_payout_eur by split transaction amounts
    for row in all_verified:
        code = row.get("confirmation_code", "")
        if code not in split_batch_refs_by_code:
            continue
        if row.get("is_split_transaction") or row.get("is_payout_adjustment") or row.get("is_aircover"):
            continue
        split_refs = split_batch_refs_by_code[code]
        batches = airbnb_all_batches.get(code, [])
        split_eur = sum(
            b.get("payout_eur", 0.0) for b in batches
            if b.get("gref", "") in split_refs
        )
        if split_eur > 0:
            current = float(row.get("effective_payout_eur") or 0.0)
            row["effective_payout_eur"] = max(current - split_eur, 0.0)
            log.info(
                "Split deduction for %s: -%.2f EUR (new effective: %.2f EUR)",
                code, split_eur, row["effective_payout_eur"],
            )
```

- [ ] **Step 7: Add split codes to hidden_confirmation_codes for moved splits**

In the month assignments section (~line 332-354), extend the suffix stripping to handle `__SP` suffixes too. Change:

```python
base_code = re.sub(r"__ADJ\d*$", "", raw_code)
```

to:

```python
base_code = re.sub(r"__(ADJ|SP)\d*$", "", raw_code)
```

Do this in BOTH places (adj_grefs_out and adj_codes_in/adj_grefs_in).

- [ ] **Step 8: Commit**

```bash
git add report/engine.py
git commit -m "feat: engine creates split transaction rows during generation"
```

---

### Task 4: Bank matching — Add split awareness

**Files:**
- Modify: `report/routes/dashboard.py:46-84` (`_filter_bank_txns_for_row`)
- Modify: `report/routes/dashboard.py:35-43` (`_bank_lookup_code`)

- [ ] **Step 1: Update `_bank_lookup_code` for split rows**

In `_bank_lookup_code`, add split handling:

```python
def _bank_lookup_code(row, code):
    """Return the confirmation_code to use for bank transaction lookup."""
    if row.get("is_aircover") and row.get("aircover_parent_code"):
        return row["aircover_parent_code"]
    if row.get("is_payout_adjustment") and row.get("adjustment_parent_code"):
        return row["adjustment_parent_code"]
    if row.get("is_split_transaction") and row.get("split_parent_code"):
        return row["split_parent_code"]
    return code
```

- [ ] **Step 2: Update `_filter_bank_txns_for_row` for split rows**

Add `is_split_transaction` to the `is_secondary` check:

```python
is_secondary = row.get("is_payout_adjustment") or row.get("is_aircover") or row.get("is_split_transaction")
```

This means split rows will filter to only show the bank transaction matching their own `batch_ref`, and the parent row will exclude batch_refs claimed by split siblings.

- [ ] **Step 3: Update sibling SQL query to include split rows**

In the SQL query inside `_filter_bank_txns_for_row`, the existing conditions check `is_payout_adjustment` and `is_aircover`. Add `is_split_transaction`:

```sql
AND (json_extract(data, '$.is_payout_adjustment') = 1
     OR json_extract(data, '$.is_aircover') = 1
     OR json_extract(data, '$.is_split_transaction') = 1)
```

- [ ] **Step 4: Commit**

```bash
git add report/routes/dashboard.py
git commit -m "feat: bank matching filters handle split transaction rows"
```

---

### Task 5: Checkin — Skip split rows in verification

**Files:**
- Modify: `report/checkin.py` (the section where `is_aircover` rows are skipped)

- [ ] **Step 1: Find and update the skip condition**

At line ~444, where `is_aircover` rows are skipped, add `is_split_transaction`:

```python
if row.get("is_aircover") or row.get("is_split_transaction"):
```

- [ ] **Step 2: Commit**

```bash
git add report/checkin.py
git commit -m "feat: skip split transaction rows in checkin verification"
```

---

### Task 6: API Routes — Split and merge endpoints

**Files:**
- Modify: `report/routes/property_routes.py` (add two new POST endpoints after `reservation_reinstate`)

- [ ] **Step 1: Add split endpoint**

After `reservation_reinstate` (~line 670), add:

```python
@app.post("/property/{slug}/{year}/{month}/reservation/{code}/split-transaction")
async def reservation_split_transaction(
    request: Request,
    slug: str,
    year: int,
    month: int,
    code: str,
    batch_ref: str = Form(...),
    _csrf=Depends(require_csrf),
    _=Depends(require_auth),
    _w=Depends(require_write_access),
    conn=Depends(get_db),
    config=Depends(get_config),
):
    state["_ensure_month_open"](conn, slug, year, month)
    # Validate: code must exist in this month
    rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
    row = next((r for r in rows if r.get("confirmation_code") == code), None)
    if row is None:
        raise HTTPException(404, "Rezervace nenalezena")
    if not batch_ref.strip():
        raise HTTPException(400, "Chybí batch_ref")
    # Strip __SP suffix if user somehow sends split code
    import re as _re
    base_code = _re.sub(r"__(SP|ADJ|AC)\d*$", "", code)
    state["create_split_transaction"](
        conn, slug, base_code, batch_ref.strip(),
        actor=state["_get_actor_username"](request),
    )
    try:
        state["generate_report_in_process"](conn, slug, year, month, config)
    except Exception:
        state["mark_report_month_stale"](conn, slug, year, month)
    state["_set_flash"](request, "success", "Transakce byla oddělena.")
    return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)
```

- [ ] **Step 2: Add merge endpoint**

```python
@app.post("/property/{slug}/{year}/{month}/reservation/{code}/merge-transaction")
async def reservation_merge_transaction(
    request: Request,
    slug: str,
    year: int,
    month: int,
    code: str,
    batch_ref: str = Form(...),
    _csrf=Depends(require_csrf),
    _=Depends(require_auth),
    _w=Depends(require_write_access),
    conn=Depends(get_db),
    config=Depends(get_config),
):
    state["_ensure_month_open"](conn, slug, year, month)
    import re as _re
    base_code = _re.sub(r"__(SP|ADJ|AC)\d*$", "", code)
    state["delete_split_transaction"](conn, slug, base_code, batch_ref.strip())
    try:
        state["generate_report_in_process"](conn, slug, year, month, config)
    except Exception:
        state["mark_report_month_stale"](conn, slug, year, month)
    state["_set_flash"](request, "success", "Transakce byla vrácena do hlavní rezervace.")
    return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)
```

- [ ] **Step 3: Register new DB functions in state**

In the app registration (wherever `state.update(...)` is called in property_routes or app.py), add:

```python
"create_split_transaction": create_split_transaction,
"delete_split_transaction": delete_split_transaction,
"get_split_transactions_for_code": get_split_transactions_for_code,
```

- [ ] **Step 4: Commit**

```bash
git add report/routes/property_routes.py
git commit -m "feat: add split-transaction and merge-transaction API endpoints"
```

---

### Task 7: Template — "Oddělit" button on bank transaction cards

**Files:**
- Modify: `templates/partials/reservation_detail.html:184-232` (bank transactions section)

- [ ] **Step 1: Add "Oddělit" button to each bank transaction card**

Inside the `{% for t in bank_txns %}` loop (after the closing `</a>` of each card, ~line 223), add the split button. It should only appear when:
1. There are 2+ bank transactions
2. The current row is NOT already a split/adjustment/aircover (i.e., it's the main reservation)
3. The month is not locked

```jinja2
{% if bank_txns|length >= 2 and not r.is_split_transaction and not r.is_payout_adjustment and not r.is_aircover and month_state and month_state.status != 'LOCKED' and _user and _user.role != 'client' %}
<form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/split-transaction"
      hx-boost="false" style="margin-top:4px;"
      onsubmit="event.preventDefault();if(typeof FloatingPanel!=='undefined')FloatingPanel.submitAction(this,'{{ r.confirmation_code }}','Transakce oddělena')">
  {{ csrf_input(request) }}
  <input type="hidden" name="batch_ref" value="{{ t.batch_ref or t.gref or '' }}">
  <button type="submit" class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 8px;color:var(--accent);border-color:oklch(0.65 0.18 280 / 0.2);">Oddělit</button>
</form>
{% endif %}
```

Note: The `{% set _user = get_current_user(request) %}` is already defined in the Akce section below. Move it above the bank section OR use `request.state.user` if available. Simplest: move the `_user` set to right after the header section.

- [ ] **Step 2: Commit**

```bash
git add templates/partials/reservation_detail.html
git commit -m "feat: add Oddělit button on bank transaction cards"
```

---

### Task 8: Template — Split row badge and "Vrátit" button

**Files:**
- Modify: `templates/partials/reservation_detail.html` (header badges + Akce section)
- Modify: `templates/partials/property_reservations.html` (table row sublabel)

- [ ] **Step 1: Add "Oddělená platba" badge in detail header**

In the header badges area (~line 47, after the AirCover badge), add:

```jinja2
{% if r.is_split_transaction %}
<span class="badge" style="font-size:9.5px;font-weight:700;letter-spacing:0.04em;background:oklch(0.65 0.18 280 / 0.1);color:var(--accent);border:1px solid oklch(0.65 0.18 280 / 0.2);">Oddělená platba</span>
{% endif %}
```

- [ ] **Step 2: Add "Vrátit do hlavní rezervace" button in Akce section**

In the Akce section (~line 353, after the payout adjustment info), add:

```jinja2
{% if r.is_split_transaction %}
<div style="margin-top:12px;">
  <div style="padding:10px 12px;background:oklch(0.65 0.18 280 / 0.06);border:1px solid oklch(0.65 0.18 280 / 0.15);border-radius:8px;font-size:12px;color:var(--accent);margin-bottom:8px;">
    Oddělená platba z rezervace {{ r.split_parent_code or r.confirmation_code.split('__')[0] }}.
    Úklid, city tax a balíčky nejsou zahrnuty (již zaúčtováno v hlavní rezervaci).
  </div>
  <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/merge-transaction"
        hx-boost="false"
        onsubmit="event.preventDefault();if(typeof FloatingPanel!=='undefined')FloatingPanel.submitAction(this,'{{ r.confirmation_code }}','Vráceno do hlavní rezervace')">
    {{ csrf_input(request) }}
    <input type="hidden" name="batch_ref" value="{{ r.batch_ref }}">
    <button type="submit" class="btn btn-sm" style="background:oklch(0.65 0.18 280 / 0.1);color:var(--accent);border-color:oklch(0.65 0.18 280 / 0.2);">Vrátit do hlavní rezervace</button>
  </form>
</div>
{% endif %}
```

- [ ] **Step 3: Add sublabel in property_reservations.html**

After the AirCover sublabel (~line 78), add:

```jinja2
{% elif r.is_split_transaction %}
<span class="adjustment-sublabel" style="color:var(--accent);">Oddělená platba</span>
```

- [ ] **Step 4: Update display_code logic for split rows**

At ~line 226, update the display_code expression to handle split rows:

```jinja2
{% set display_code = r.aircover_parent_code if r.is_aircover and r.aircover_parent_code else r.adjustment_parent_code if r.is_payout_adjustment and r.adjustment_parent_code else r.split_parent_code if r.is_split_transaction and r.split_parent_code else r.confirmation_code %}
```

- [ ] **Step 5: Commit**

```bash
git add templates/partials/reservation_detail.html templates/partials/property_reservations.html
git commit -m "feat: add split transaction badge, sublabel, and merge button in templates"
```

---

### Task 9: Wire up state — Register new functions in app setup

**Files:**
- Modify: the file where `state` dict is built (likely `report/app.py` or wherever DB functions are registered)

- [ ] **Step 1: Find where state is populated with DB functions**

Search for where `create_reservation_exclusion` or similar is added to `state`. Add alongside:

```python
"create_split_transaction": create_split_transaction,
"delete_split_transaction": delete_split_transaction,
"get_split_transactions": get_split_transactions,
"get_split_transactions_for_code": get_split_transactions_for_code,
```

- [ ] **Step 2: Import the new functions**

Add to the import block in the app setup file:

```python
from report.db import (
    ...existing...,
    create_split_transaction,
    delete_split_transaction,
    get_split_transactions,
    get_split_transactions_for_code,
)
```

- [ ] **Step 3: Commit**

```bash
git add <app-setup-file>
git commit -m "feat: register split transaction functions in app state"
```

---

### Task 10: End-to-end test — Verify split flow works

- [ ] **Step 1: Start dev server and navigate to a reservation with 2+ bank transactions**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python -m report.app
```

- [ ] **Step 2: Verify "Oddělit" buttons appear on bank transaction cards**

Open a reservation detail panel for a reservation with multiple bank transactions (e.g., HM8Z34Q9HF). Confirm each transaction card shows an "Oddělit" button.

- [ ] **Step 3: Click "Oddělit" and verify split row appears**

After splitting, confirm:
- New row appears in the table with "Oddělená platba" sublabel
- Parent row's vyplata decreased by the split amount
- Split row shows the correct amount from the split batch
- Bank transaction card on split row shows only its own transaction
- Parent row's bank transactions section no longer shows the split transaction

- [ ] **Step 4: Verify "Vrátit do hlavní rezervace" merges back**

On the split row, open detail and click "Vrátit do hlavní rezervace". Confirm:
- Split row disappears
- Parent row's vyplata is restored
- Bank transactions are back on parent

- [ ] **Step 5: Verify move controls work on split row**

Split a transaction, then move the split row to a different month. Confirm it appears in the target month with correct amount.

- [ ] **Step 6: Commit final state**

```bash
git add -A
git commit -m "feat: split transaction feature — complete implementation"
```
