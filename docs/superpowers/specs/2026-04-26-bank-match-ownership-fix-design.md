# Bank-match ownership fix — design

**Date:** 2026-04-26
**Author:** Nikita (with Claude)
**Status:** Approved — ready for implementation plan

## Problem

`payout_batch_bank_matches` has a multi-month / multi-property race that silently
flips `report_rows.bank_status` from `DORAZILO` to `CHYBÍ` on any LOCKED month
whose ownership was overwritten by a later regen. The bug has been on production
since 2026-04-13.

Symptom from 2026-04-26: reservation `HMRA54MA5N` (Francouzska_50/2026-03,
LOCKED) showed `CHYBÍ` even though the bank tx exists. Three sibling reservations
in the same Airbnb batch (different slug/month) showed `DORAZILO`. After the bulk
regen of 2026-04, ownership of the batch was overwritten to
`U_Parniho_Mlyna_6 / 2026-04` even though that month had no reservation in the
batch — likely because some other April code shared the batch via
`all_batches_map`.

## Background — current flow

Three pieces of code create the race:

1. `save_payout_batch_bank_matches` ([report/db.py:2386-2412](report/db.py:2386))
   uses
   `ON CONFLICT(channel, batch_ref, tx_key) DO UPDATE SET slug=excluded.slug, year=excluded.year, month=excluded.month`.
   Last writer wins — every regen overwrites prior ownership.

2. `enrich_rows_with_bank` second loop ([report/bank.py:322-347](report/bank.py:322))
   iterates `all_batches_map[code]` for every `gref` the code is part of — no
   slug/month filter. Any regen of any month can claim ownership for any batch
   its codes touch.

3. `get_bank_match_owner` ([report/db.py:2429-2447](report/db.py:2429))
   keys on `(year, month)` only, slug intentionally ignored
   (commit `e6e80e2`). When a row's match is owned by a different month,
   the row is forced to `bank_status = CHYBÍ` ([report/bank.py:373-392](report/bank.py:373),
   [report/bank.py:629-630](report/bank.py:629)) silently — no breadcrumb.

LOCKED months freeze the wrong status forever. The same downgrade exists on the
booking path.

### Why the downgrade was added (commit `ed3408d`)

To prevent "double-counting" of bank arrivals across months. Audit of the actual
aggregations shows the protection is unnecessary:

- `bank_amount_czk` (full tx amount) is not summed anywhere across
  `enriched_rows`. The single sum at [report/summary.py:76](report/summary.py:76)
  is over `transferred_rows` (resolved pending payments — a separate flow).
- `bank_confirmed_czk` at [report/summary.py:65-67](report/summary.py:65) sums
  `payout_czk` (per-reservation expected share), so even if every reservation in
  a cross-month batch shows `DORAZILO`, the sum equals the real bank tx amount.
- Web views ([report/web_support.py:825, 1078](report/web_support.py:825))
  read the table only via `(channel, batch_ref, tx_key)`. The `slug/year/month`
  columns are dead weight, kept solely to feed the broken downgrade.

The downgrade did, by accident, mask one real bug: a single reservation appearing
in two different `(slug, year, month)` snapshots ("duplicates"). The user has
since fixed the root cause but wants explicit defense-in-depth so future
duplicates are **visible**, not masked.

## Goals

1. Remove the ownership/downgrade machinery completely — the table holds one
   row per `(channel, batch_ref, tx_key)`, no per-month attribution.
2. Add three explicit integrity layers so duplicate reservations surface as
   warnings instead of silent miscounts.
3. Heal existing LOCKED report rows that were silently downgraded to `CHYBÍ`.
4. Cover the new behavior with regression tests.

## Non-goals

- Redesigning the broader bank-arrivals reporting model.
- Building a generic data-integrity / migration framework — we add only what
  this fix needs.
- Splitting the engine further — the prior single-engine consolidation
  (commits `61d802f`, `00cc90c`, `f019b39`) already collapsed CLI and web onto
  `generate_report_in_process`. Both paths are covered by this fix.

## Approach

Two parts run together:

- **Part A — remove ownership.** Drop the columns, drop the helper functions,
  drop the downgrade blocks, simplify the upsert.
- **Part B — three-layer integrity defense.**
  - **L1 per-snapshot** in `engine.generate_report_in_process` before
    `save_report_rows`: detect duplicate `confirmation_code` within the snapshot,
    annotate violators via `verification_comment`, deduplicate when summing.
  - **L2 cross-report** in `enrich_rows_with_bank`: after a successful bank
    match, query `report_rows` for the same `confirmation_code` in other
    `(slug, year, month)` snapshots; annotate.
  - **L3 global audit**: new `integrity_audit` table; non-blocking SELECT at
    app boot writes findings; admin page renders them.

A one-shot data-migration helper restores `bank_status = DORAZILO` on LOCKED
rows that were silently downgraded.

## Detailed design

### Part A — Remove ownership

#### A1. Schema migration

New helper in `report/db.py`, idempotent, called from `ensure_schema()`:

```python
def _drop_ownership_columns_from_payout_batch_bank_matches(conn) -> None:
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(payout_batch_bank_matches)"
    )}
    if not {"slug", "year", "month"} & cols:
        return  # already migrated
    conn.executescript("""
        CREATE TABLE payout_batch_bank_matches__new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel         TEXT NOT NULL,
            batch_ref       TEXT NOT NULL,
            tx_key          TEXT NOT NULL,
            match_method    TEXT,
            matched_amount_czk REAL,
            matched_at      TEXT NOT NULL,
            UNIQUE(channel, batch_ref, tx_key)
        );
        INSERT INTO payout_batch_bank_matches__new
            (id, channel, batch_ref, tx_key, match_method,
             matched_amount_czk, matched_at)
        SELECT id, channel, batch_ref, tx_key, match_method,
               matched_amount_czk, matched_at
        FROM payout_batch_bank_matches;
        DROP TABLE payout_batch_bank_matches;
        ALTER TABLE payout_batch_bank_matches__new
              RENAME TO payout_batch_bank_matches;
    """)
    conn.commit()
```

Remove the corresponding `_ensure_column` calls at
[report/db.py:764-766](report/db.py:764).

#### A2. `save_payout_batch_bank_matches`

Strip `slug`, `year`, `month` parameters. UPSERT becomes:

```sql
INSERT INTO payout_batch_bank_matches
       (channel, batch_ref, tx_key, match_method, matched_amount_czk, matched_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(channel, batch_ref, tx_key) DO UPDATE SET
    match_method=excluded.match_method,
    matched_amount_czk=excluded.matched_amount_czk,
    matched_at=excluded.matched_at;
```

Update both call sites [report/engine.py:930-931](report/engine.py:930).

#### A3. Delete dead helpers

- `clear_bank_matches_for_month` [report/db.py:2416-2426](report/db.py:2416).
- `get_bank_match_owner` [report/db.py:2429-2447](report/db.py:2429).
- The corresponding call site `clear_bank_matches_for_month`
  [report/engine.py:390-391](report/engine.py:390) — drop, no replacement.

#### A4. Delete downgrade blocks

- Airbnb path: [report/bank.py:373-392](report/bank.py:373) — collapse to the
  unconditional `DORAZILO` branch.
- Booking path: [report/bank.py:629-630](report/bank.py:629) and the
  surrounding `already_owned` branch.
- Remove the `from report.db import get_bank_match_owner` imports inside both.

The downstream `MATCHED → KE_KONTROLE` cascade
[report/engine.py:933-946](report/engine.py:933) stays; it now only triggers
when the bank truly hasn't paid yet.

### Part B — Three-layer integrity defense

#### B1. Layer 1 — per-snapshot integrity (engine)

In `report/engine.py` `generate_report_in_process`, immediately before
`save_report_rows` ([engine.py:974](report/engine.py:974)):

```python
def _flag_duplicate_codes_within_snapshot(rows: list[dict]) -> int:
    """Annotate rows whose confirmation_code repeats inside this snapshot.

    Returns count of annotated rows. Empty codes are ignored (legacy synthetic
    rows can share an empty string by design).
    """
    seen: dict[str, int] = {}
    for r in rows:
        code = r.get("confirmation_code") or ""
        if not code:
            continue
        seen[code] = seen.get(code, 0) + 1
    dupes = {c for c, n in seen.items() if n > 1}
    if not dupes:
        return 0
    for r in rows:
        if r.get("confirmation_code") in dupes:
            existing = r.get("verification_comment") or ""
            r["verification_comment"] = (
                f"INTEGRITY: duplicate confirmation_code in snapshot. {existing}".strip()
            )
    return sum(1 for r in rows if r.get("confirmation_code") in dupes)
```

Call it on `calc_rows` after bank enrichment, before `save_report_rows`.

In `report/summary.py` `build_report_summary`, deduplicate `rows` by
`confirmation_code` (keeping first occurrence, ignoring empty codes) before
the existing aggregations at [summary.py:32-77](report/summary.py:32). Add
returned field `integrity_warnings: list[str]` listing duplicated codes
(empty list when none). The web template renders a banner when non-empty.

**Why before aggregations:** existing fields `gross_payout_czk`,
`accommodation_income_czk`, `rentero_fee_czk`, `bank_confirmed_czk` all sum
over `rows`. Without dedup, a duplicate row inflates every sum by its share.
The warning is the visible signal; the dedup keeps numbers correct even when
the warning is missed.

**Suffix discipline.** `__ADJ`, `__AC`, `__SP[N]` suffixes
([engine.py:200, 245, 776](report/engine.py:200)) are stored as the actual
`confirmation_code` values, so they don't collide with their parents and
won't trigger false positives. No special-case needed.

#### B2. Layer 2 — cross-report detector (bank.py)

In `report/bank.py` `enrich_rows_with_bank`, when a successful bank match is
applied (the `DORAZILO` branch), and `conn` and `slug` are available, run:

```python
def _find_code_in_other_snapshots(
    conn, code: str, slug: str, year: int, month: int, limit: int = 5
) -> list[tuple[str, int, int]]:
    if not code:
        return []
    rows = conn.execute(
        """SELECT slug, year, month FROM report_rows
           WHERE confirmation_code = ?
             AND NOT (slug = ? AND year = ? AND month = ?)
           ORDER BY year DESC, month DESC
           LIMIT ?""",
        (code, slug, year, month, limit),
    ).fetchall()
    return [(r["slug"], r["year"], r["month"]) for r in rows]
```

If the result is non-empty, append to `verification_comment`:

    INTEGRITY: also in <slug>/<year>-<month>[, ...]

Apply the same in the booking branch ([report/bank.py:629-655](report/bank.py:629)).

The new index `idx_report_rows_code_lookup`
([report/db.py:231-232](report/db.py:231)) covers the lookup; cost is
~30 indexed selects per generation — negligible inside the 2–5 minute
generation window.

#### B3. Layer 3 — global integrity audit

New table in `_SCHEMA` ([report/db.py](report/db.py)):

```sql
CREATE TABLE IF NOT EXISTS integrity_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT NOT NULL,
    occurrences     TEXT NOT NULL,    -- e.g. "Francouzska_50/2026-03,U_Parniho_Mlyna_6/2026-04"
    detected_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_integrity_audit_detected_at
    ON integrity_audit(detected_at DESC);
```

New helper `run_integrity_audit(conn) -> list[dict]`:

```sql
SELECT confirmation_code,
       GROUP_CONCAT(slug || '/' || year || '-' || printf('%02d', month), ',') AS occurrences,
       COUNT(*) AS occ_count
FROM report_rows
WHERE confirmation_code <> ''
GROUP BY confirmation_code
HAVING occ_count > 1
ORDER BY occ_count DESC, confirmation_code;
```

For each finding:

- Insert a row into `integrity_audit`.
- Emit `logger.warning("integrity violation: %s seen in %s", code, where)`.

Boot wiring: hook the audit into `_app_lifespan`
([web.py:204-223](web.py:204)) as a single non-blocking call after schema
ensure but before yielding control. Audit completes in well under a second
on a multi-month DB; if it ever grows, fall back to a `BackgroundTasks` call.

Admin endpoint `/admin/integrity` renders the contents of `integrity_audit`
ordered by `detected_at DESC`. Add a new section to the existing
`templates/audit.html` (which is already linked from
[templates/base.html:106](templates/base.html:106)) — no new top-level
template, no new nav entry. Section header "Integrity violations" with the
audit table beneath it.

#### B4. Data migration — restore silently-downgraded LOCKED rows

One-shot helper, idempotent, called from `_app_lifespan`
([web.py:204-223](web.py:204)) **after** `ensure_schema()` (which runs the
column drop) and **before** `run_integrity_audit`. Not called from
`ensure_schema()` itself — that runs on every test and CLI invocation, where
re-walking all `report_rows` is wasteful and pollutes test isolation.

```python
def _restore_bank_status_after_ownership_fix(conn) -> int:
    """Re-mark report_rows that were silently downgraded to CHYBÍ.

    Targets rows whose batch_ref still has a bank match in
    payout_batch_bank_matches. Safe to run repeatedly: it skips rows already
    marked DORAZILO. Bypasses LOCKED protection because this is a corrective
    rewrite of stored history, not a recalculation.

    Also clears matching pending_payments rows, since a row that arrives at
    DORAZILO no longer belongs in pending.
    """
```

Algorithm:

1. Select `report_rows.id, slug, year, month, data` where the JSON `data`
   has `bank_status = 'CHYBÍ'` and `data.batch_ref` is non-empty and exists
   in `payout_batch_bank_matches.batch_ref`.
2. For each, look up `tx_key, datum, amount_czk` from
   `payout_batch_bank_matches` join `bank_transactions`.
3. Patch the JSON: set `bank_status = 'DORAZILO'`, fill `bank_tx_key`,
   `bank_datum`, `bank_amount_czk`. **Prepend** to `verification_comment`
   (do not overwrite — preserves any existing comment, including prior
   `INTEGRITY:` annotations):
   `verification_comment = "RECOVERED: bank match restored after ownership fix. " + prev_comment`.
4. Bypass `_assert_report_month_mutable` — write the JSON directly via
   `UPDATE report_rows SET data = ? WHERE id = ?`.
5. Delete the matching `pending_payments` row(s) keyed by
   `(slug, confirmation_code)` — see
   [report/db.py:606](report/db.py:606) for the table's UNIQUE shape.
6. Return count of restored rows; log it.

Idempotency comes from the `bank_status = 'CHYBÍ'` filter: once restored, the
row no longer matches the WHERE clause.

This helper runs **once** at the next boot. To make a second run a no-op, no
sentinel column needed — the WHERE clause is the gate.

#### B5. UI integration

- `templates/partials/reservation_detail.html` near the existing
  `verification_diff` block ([line 274-279](templates/partials/reservation_detail.html:274)):
  detect `verification_comment` starting with `INTEGRITY:` or containing
  `INTEGRITY:` after a recovery note; render a red `Integrita`
  badge with the rest of the comment.
- Report-level summary template: when `integrity_warnings` is non-empty,
  render a banner at the top: "Pozor: nalezeny duplicitní rezervace v reportu
  (N): &lt;codes&gt;" with link to `/admin/integrity`.
- New "Integrity violations" section inside `templates/audit.html`: table
  of `integrity_audit` rows, columns "Code", "Snapshots", "Detected at".
  Endpoint `/admin/integrity` renders the same template with the
  integrity table positioned at top.

## Tests

New tests in `tests/test_bank.py`:

- `test_cross_month_batch_no_silent_downgrade` — one batch, two
  reservations in `(Francouzska_50, 2026-03)` and `(Francouzska_50, 2026-04)`,
  one bank tx. Regenerate March then April: both rows show `DORAZILO`
  regardless of order.
- `test_cross_property_batch_no_silent_downgrade` — same shape, two
  different slugs.
- Update existing `tests/test_bank.py:343, 448` callers of
  `save_payout_batch_bank_matches` — drop `slug=`, `year=`, `month=`
  kwargs (the function no longer accepts them). Add an assertion
  verifying the persisted row has only the new column set.

New tests in `tests/test_engine.py`:

- `test_l1_flags_duplicate_within_snapshot` — synthesize `calc_rows` with
  two identical `confirmation_code`, run `_flag_duplicate_codes_within_snapshot`,
  assert both rows have `INTEGRITY:` in `verification_comment` and that
  `build_report_summary` returns non-empty `integrity_warnings`.
- `test_l1_ignores_empty_and_suffixed_codes` — rows with `''` and with
  `__ADJ` suffixes do not trigger.
- `test_l2_annotates_cross_report_duplicate` — pre-populate
  `report_rows` for `(slug=A, 2026-03)`, then run `enrich_rows_with_bank`
  on `(slug=A, 2026-04)` for the same `confirmation_code`; row gets
  `INTEGRITY: also in A/2026-03`.

New test file `tests/test_integrity.py`:

- `test_run_integrity_audit_finds_cross_snapshot_dupe` — seed two
  `report_rows` for the same code in different months, call
  `run_integrity_audit`, assert one row in `integrity_audit`.
- `test_run_integrity_audit_ignores_empty_codes` — seed several rows
  with `confirmation_code = ''`, call audit, assert zero rows added.
- `test_run_integrity_audit_idempotent_per_run` — calling twice produces
  two `detected_at` entries (it's an event log, not a state) — verify
  this is the desired semantic.

New test in `tests/test_db.py` (or migration-specific file):

- `test_drop_ownership_columns_migration` — create a table with old shape +
  data; run helper; verify columns gone and data preserved (ids,
  match_method, matched_amount_czk).
- `test_drop_ownership_columns_idempotent` — second call is a no-op.
- `test_restore_bank_status_after_ownership_fix` — seed a LOCKED report_row
  with `bank_status = 'CHYBÍ'` whose `batch_ref` has a match; call
  helper; assert row patched to `DORAZILO`, `verification_comment`
  prefixed with `RECOVERED:`, matching pending_payments row deleted,
  lock untouched.
- `test_restore_bank_status_idempotent` — second call patches zero rows.

## Risks & mitigations

- **Migration data loss.** The recreate-table dance for
  `payout_batch_bank_matches` copies all rows; a transient failure between
  `DROP` and `RENAME` could lose data. Mitigation: wrap in a single
  `executescript` (it's transactional in SQLite when wrapped in
  `BEGIN/COMMIT`, and `executescript` runs in autocommit but each statement
  is atomic). Recommend running `backup-db.sh` (already daily on prod via
  ~/rentero/bin/backup-db.sh) immediately before deploy.

- **Performance regression on L2.** Worst case ~50 indexed selects per
  generation. Index already exists. If a future change moves
  `enrich_rows_with_bank` into a tight loop (e.g. bulk regen of 12
  months × 20 properties), revisit.

- **L3 audit growth.** The audit table is an append-only log. After
  N years it could grow large but each row is tiny (~200 bytes). If
  pruning becomes needed, add a "keep last 30 days" cron-style cleanup.

- **Locked rows mutation in B4.** We bypass `_assert_report_month_mutable`
  inside the migration helper. This is a deliberate one-time correction.
  The helper is gated by a precise WHERE clause and is idempotent. After the
  next deploy, lock semantics are restored — no new code path mutates
  locked snapshots.

- **Suffix collision assumption (L1/L2).** Layer correctness depends on
  `__ADJ`/`__AC`/`__SP[N]` always being part of the stored
  `confirmation_code`. If a future feature adds a new synthetic-row type
  without a suffix, it would create false positives. Mitigation: when adding
  such a feature, follow the existing suffix convention or extend
  `_flag_duplicate_codes_within_snapshot` to read parent-code fields.

## Rollout plan

1. Implement on `claude/hungry-elgamal-bf3a52` worktree branch. Tests pass.
2. Run on a copy of the production DB locally; verify migration is clean
   and `_restore_bank_status_after_ownership_fix` patches the known
   `HMRA54MA5N` symptom.
3. Run `bin/backup-db.sh` on prod (or wait for nightly 03:00 backup).
4. Merge & deploy. On first boot, schema migration + restore + audit run
   in `_app_lifespan`.
5. Verify `/admin/integrity` is empty (or contains expected historic dupes).
6. Verify `HMRA54MA5N` shows `DORAZILO` on Francouzska_50/2026-03 (the
   originally-reported symptom).

## Out of scope (recorded for future)

- Pruning policy for `integrity_audit` history.
- Generic migration framework / version table.
- Restructuring `payout_batch_bank_matches` into a multi-row table for
  audit-trail purposes — current design intentionally keeps one row per
  match.
