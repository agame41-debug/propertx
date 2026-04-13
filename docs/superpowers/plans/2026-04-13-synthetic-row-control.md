# Synthetic Row Control & Bank Match Integrity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent synthetic row duplication across months and ensure bank matches are never double-counted.

**Architecture:** Idempotent engine with strict payout-date window placement for all synthetic types. Bank match ownership tracked via extended `payout_batch_bank_matches` table. No new tables or modules.

**Tech Stack:** Python, SQLite, FastAPI

---

### Task 1: DB Migration — extend payout_batch_bank_matches

**Files:**
- Modify: `report/db.py:749` (inside `_run_migrations`)
- Modify: `report/db.py:2153-2183` (`save_payout_batch_bank_matches`)

- [ ] **Step 1: Add migration for new columns**

In `report/db.py`, inside `_run_migrations()`, before the `_seed_admin_user(conn)` call (line 750), add:

```python
_ensure_column(conn, "payout_batch_bank_matches", "slug", "slug TEXT DEFAULT ''")
_ensure_column(conn, "payout_batch_bank_matches", "year", "year INTEGER DEFAULT 0")
_ensure_column(conn, "payout_batch_bank_matches", "month", "month INTEGER DEFAULT 0")
```

- [ ] **Step 2: Add cleanup helper function**

In `report/db.py`, after `save_payout_batch_bank_matches` (after line 2183), add:

```python
def clear_bank_matches_for_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> None:
    """Remove bank match records for a specific slug/month before regeneration."""
    conn.execute(
        "DELETE FROM payout_batch_bank_matches WHERE slug = ? AND year = ? AND month = ?",
        (slug, year, month),
    )


def get_bank_match_owner(
    conn: sqlite3.Connection,
    channel: str,
    batch_ref: str,
    tx_key: str,
) -> dict | None:
    """Return the (slug, year, month) that owns this bank match, or None."""
    row = conn.execute(
        """SELECT slug, year, month FROM payout_batch_bank_matches
           WHERE channel = ? AND batch_ref = ? AND tx_key = ?
           AND slug != '' AND year > 0 AND month > 0""",
        (channel, batch_ref, tx_key),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 3: Update save_payout_batch_bank_matches to accept slug/year/month**

Replace the existing `save_payout_batch_bank_matches` function (lines 2153-2183):

```python
def save_payout_batch_bank_matches(
    conn: sqlite3.Connection,
    channel: str,
    matches: list[dict],
    *,
    slug: str = "",
    year: int = 0,
    month: int = 0,
) -> None:
    """Persist batch - bank transaction links with month ownership."""
    if not matches:
        return
    now = _now()
    conn.executemany(
        """INSERT INTO payout_batch_bank_matches
           (channel, batch_ref, tx_key, match_method, matched_amount_czk, matched_at,
            slug, year, month)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel, batch_ref, tx_key) DO UPDATE SET
             match_method=excluded.match_method,
             matched_amount_czk=excluded.matched_amount_czk,
             matched_at=excluded.matched_at,
             slug=excluded.slug,
             year=excluded.year,
             month=excluded.month""",
        [
            (
                channel,
                m.get("batch_ref", ""),
                m.get("tx_key", ""),
                m.get("match_method", ""),
                m.get("matched_amount_czk"),
                now,
                slug,
                year,
                month,
            )
            for m in matches
            if m.get("batch_ref") and m.get("tx_key")
        ],
    )
    conn.commit()
```

- [ ] **Step 4: Commit**

```bash
git add report/db.py
git commit -m "feat: add slug/year/month ownership to payout_batch_bank_matches"
```

---

### Task 2: Bank match integrity in bank.py

**Files:**
- Modify: `report/bank.py:255-397` (`enrich_rows_with_bank`)
- Modify: `report/bank.py:515-620` (`enrich_booking_rows_with_bank`)

- [ ] **Step 1: Add conn and ownership params to enrich_rows_with_bank**

Change the function signature at line 255 to accept `conn`, `slug`, `year`, `month`:

```python
def enrich_rows_with_bank(
    calc_rows: list[dict],
    gref_map: dict[str, dict],
    index_by_gref: dict,
    no_ref_rows: list[dict],
    all_batches_map: dict[str, list[dict]] | None = None,
    bank_index_full: dict | None = None,
    bank_no_ref_full: list | None = None,
    *,
    conn=None,
    slug: str = "",
    year: int = 0,
    month: int = 0,
) -> tuple[list[dict], list[dict]]:
```

- [ ] **Step 2: Add ownership check before DORAZILO (Airbnb)**

In the enrichment loop (around line 368), before setting `bank_status: "DORAZILO"`, add ownership check:

```python
        if bank_row:
            # Check if this match is already owned by another month
            already_owned = False
            if conn and slug:
                from report.db import get_bank_match_owner
                owner = get_bank_match_owner(conn, "airbnb", batch_key, bank_row.get("tx_key", ""))
                if owner and (owner["slug"] != slug or owner["year"] != year or owner["month"] != month):
                    already_owned = True
            if already_owned:
                datum = bank_row.get("datum")
                enriched.append({
                    **row,
                    "batch_ref": gref,
                    "batch_payout_date": payout_date_str,
                    "batch_amount_czk_expected": payout_czk,
                    "payout_gref":     gref,
                    "bank_tx_key":     "",
                    "bank_datum":      "",
                    "bank_amount_czk": None,
                    "bank_status":     "CHYBÍ",
                })
            else:
                datum = bank_row.get("datum")
                enriched.append({
                    **row,
                    "batch_ref": gref,
                    "batch_payout_date": payout_date_str,
                    "batch_amount_czk_expected": payout_czk,
                    "payout_gref":     gref,
                    "bank_tx_key":     bank_row.get("tx_key", ""),
                    "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                    "bank_amount_czk": bank_row["amount_czk"],
                    "bank_status":     "DORAZILO",
                })
```

Replace the existing `if bank_row:` block (lines 368-391) with this logic.

- [ ] **Step 3: Same ownership check for Booking enrichment**

Add `conn`, `slug`, `year`, `month` params to `enrich_booking_rows_with_bank` signature (line 525):

```python
def enrich_booking_rows_with_bank(
    calc_rows: list[dict],
    booking_bank_idx: dict[str, list[dict]],
    property_config: dict,
    *,
    year: int | None = None,
    month: int | None = None,
    booking_bank_idx_all: dict[str, list[dict]] | None = None,
    conn=None,
    slug: str = "",
) -> tuple[list[dict], list[dict]]:
```

In the Booking enrichment loop (around line 610), add the same ownership check before DORAZILO:

```python
        if matched_bank:
            already_owned = False
            if conn and slug and year and month:
                from report.db import get_bank_match_owner
                owner = get_bank_match_owner(conn, "booking", batch_ref, matched_bank.get("tx_key", ""))
                if owner and (owner["slug"] != slug or owner["year"] != year or owner["month"] != month):
                    already_owned = True
            if already_owned:
                enriched.append({
                    **row,
                    "payout_gref": batch_ref,
                    "bank_tx_key": "",
                    "bank_datum": "",
                    "bank_amount_czk": None,
                    "bank_status": "CHYBÍ",
                })
            else:
                datum = matched_bank.get("datum")
                enriched.append({
                    **row,
                    "payout_gref": batch_ref,
                    "bank_tx_key": matched_bank.get("tx_key", ""),
                    "bank_datum":  datum.strftime("%d.%m.%Y") if datum else "",
                    "bank_amount_czk": matched_bank["amount_czk"],
                    "bank_status": "DORAZILO",
                })
```

- [ ] **Step 4: Commit**

```bash
git add report/bank.py
git commit -m "feat: bank match ownership check prevents double-counting across months"
```

---

### Task 3: Engine — unified synthetic handling + bank cleanup

**Files:**
- Modify: `report/engine.py:260-270` (start of generate_report_in_process)
- Modify: `report/engine.py:375-397` (hidden_confirmation_codes)
- Modify: `report/engine.py:565-603` (AirCover section)
- Modify: `report/engine.py:474-563` (adjustment section)
- Modify: `report/engine.py:757-768` (bank enrichment calls)

- [ ] **Step 1: Add bank match cleanup at generation start**

After the `DELETE FROM report_rows` call (line 276-279), add:

```python
    # ── Clear bank match ownership for this slug/month ─────────────────────
    from report.db import clear_bank_matches_for_month
    clear_bank_matches_for_month(conn, slug, year, month)
```

- [ ] **Step 2: Unify hidden_confirmation_codes for all synthetic types**

Replace the `hidden_confirmation_codes` logic (lines 375-397) to handle `__AC` suffix alongside `__ADJ`/`__SP`:

```python
    # ── Month assignments (month-scoped) ──────────────────────────────────────
    all_assignments = get_reservation_month_assignments(conn, slug)
    hidden_confirmation_codes: set[str] = set()

    codes_main_out: set[str] = set()
    adj_grefs_out: set[tuple[str, str]] = set()
    codes_synthetic_out: set[str] = set()

    for asgn in all_assignments:
        if asgn["original_year"] == year and asgn["original_month"] == month:
            raw_code = asgn["confirmation_code"]
            if asgn.get("is_adjustment") or re.search(r"__(ADJ|SP|AC)\d*$", raw_code):
                base_code = re.sub(r"__(ADJ|SP|AC)\d*$", "", raw_code)
                adj_grefs_out.add((base_code, asgn.get("batch_ref", "")))
                codes_synthetic_out.add(raw_code)
            else:
                codes_main_out.add(raw_code)

    hidden_confirmation_codes = codes_main_out | codes_synthetic_out
```

- [ ] **Step 3: Add deduplication helper**

Before the AirCover section (around line 564), add a helper function:

```python
    def _synthetic_already_exists(code: str) -> bool:
        """Check if this synthetic code already exists in another month."""
        existing = conn.execute(
            "SELECT year, month FROM report_rows WHERE slug = ? AND confirmation_code = ? LIMIT 1",
            (slug, code),
        ).fetchone()
        if existing and (existing["year"] != year or existing["month"] != month):
            log.info("Skipping %s: already exists in %d/%d", code, existing["month"], existing["year"])
            return True
        return False
```

- [ ] **Step 4: Apply dedup check to AirCover section**

In the AirCover loop, after the `hidden_confirmation_codes` check, add dedup:

```python
            ac_code = f"{code}{suffix}"
            if ac_code in hidden_confirmation_codes:
                continue
            if _synthetic_already_exists(ac_code):
                continue
            reservations.append(_build_aircover_reservation(parent_row, ac_item, suffix=suffix))
```

- [ ] **Step 5: Apply dedup check to adjustment section**

Find where `_build_adjustment_reservation` is called (around line 563) and add before append:

```python
        adj_code = f"{code}{_next_adj_suffix(code)}"
```

Actually, the suffix is generated by `_next_adj_suffix` before the build call. Add the dedup check after suffix generation but before append. Find the line:
```python
        reservations.append(_build_adjustment_reservation(past_row, batch_info, suffix=_next_adj_suffix(code)))
```

Replace with:
```python
        adj_suffix = _next_adj_suffix(code)
        adj_code = f"{code}{adj_suffix}"
        if adj_code in hidden_confirmation_codes:
            continue
        if _synthetic_already_exists(adj_code):
            continue
        reservations.append(_build_adjustment_reservation(past_row, batch_info, suffix=adj_suffix))
```

- [ ] **Step 6: Pass slug/year/month to bank enrichment calls**

Replace bank enrichment calls (lines 757-768):

```python
    calc_rows, airbnb_matches = enrich_rows_with_bank(
        calc_rows, gref_map, bank_index, bank_no_ref,
        all_batches_map=airbnb_all_batches,
        bank_index_full=bank_index_full,
        bank_no_ref_full=bank_no_ref_full,
        conn=conn, slug=slug, year=year, month=month,
    )
    calc_rows, booking_matches = enrich_booking_rows_with_bank(
        calc_rows, booking_bank_idx, prop, year=year, month=month,
        booking_bank_idx_all=booking_bank_idx_all,
        conn=conn, slug=slug,
    )
    save_payout_batch_bank_matches(conn, "airbnb", airbnb_matches, slug=slug, year=year, month=month)
    save_payout_batch_bank_matches(conn, "booking", booking_matches, slug=slug, year=year, month=month)
```

- [ ] **Step 7: Commit**

```bash
git add report/engine.py
git commit -m "feat: unified synthetic dedup + bank match ownership in engine"
```

---

### Task 4: Verify move routes regenerate both months

**Files:**
- Verify: `report/routes/property_routes.py:589-636` (reservation_move)
- Verify: `report/routes/property_routes.py:638-671` (reservation_move_revert)

- [ ] **Step 1: Verify reservation_move**

The move handler at line 589 already regenerates both months in chronological order:
```python
months_to_regen = sorted({(year, month), (target_year, target_month)})
for _y, _m in months_to_regen:
    state["generate_report_in_process"](conn, slug, _y, _m, config)
```

No changes needed. Verify this is intact.

- [ ] **Step 2: Verify reservation_move_revert**

The revert handler at line 638 already regenerates all affected months:
```python
months_to_regen = {(year, month)}
if assignment:
    months_to_regen.add((assignment["target_year"], assignment["target_month"]))
    months_to_regen.add((assignment["original_year"], assignment["original_month"]))
for _y, _m in sorted(months_to_regen):
    state["generate_report_in_process"](conn, slug, _y, _m, config)
```

No changes needed. Verify this is intact.

- [ ] **Step 3: Commit (if any fixes needed)**

```bash
git add report/routes/property_routes.py
git commit -m "verify: move routes already regenerate both months correctly"
```

---

### Task 5: Integration test

**Files:**
- Verify on production server

- [ ] **Step 1: Deploy to server**

```bash
git push origin main
ssh rentero@204.168.216.181 "cd ~/rentero && git pull origin main && source venv/bin/activate && sudo systemctl restart rentero"
```

- [ ] **Step 2: Regenerate Moskevska_58 Feb and March**

```python
for m in [2, 3]:
    generate_report_in_process(conn, "Moskevska_58", 2026, m, config=config)
```

Verify:
- `HM4SRJ53P8__AC` exists in exactly ONE month
- Bank status DORAZILO only in that one month
- No duplicates anywhere

- [ ] **Step 3: Test move operation**

Move `HM4SRJ53P8__AC` from its current month to the other. Verify:
- Row disappears from source month
- Row appears in target month
- Bank status correct in target, absent in source
- No duplication

- [ ] **Step 4: Test revert**

Revert the move. Verify row returns to original month only.

- [ ] **Step 5: Commit any fixes**

```bash
git commit -m "fix: integration test corrections"
```
