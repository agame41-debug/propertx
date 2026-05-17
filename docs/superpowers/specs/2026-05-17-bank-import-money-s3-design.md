# Bank Import: Money S3 Format Support — Design

Date: 2026-05-17
Status: Approved (pending written-spec review)

## Problem

`report/bank.py` parses bank statement CSVs in exactly one format: UTF-16
encoded, comma-separated, single header row with columns `Datum zaúčtování`,
`Název protiúčtu`, `Částka`, `Typ transakce`, `Zpráva pro příjemce`,
`Reference platby`, `ID transakce`. The bank now exports a different format —
Money S3 (cp1250 encoded, `;`-separated, multi-section `Header` / `Detail 1` /
`Footer` structure). The current parser cannot read it.

Constraints:

- The legacy file `source/bank/transakce CS 2024-2026.csv` is still an **active**
  source. It is re-parsed on every startup by
  `_backfill_payout_batches_from_active_sources` and historical months are
  reconciled against it. The legacy parser must keep working unchanged.
- The Money S3 detail export has **no transaction-direction / sign column**
  (`Typ transakce` is absent; all amounts are positive). Incoming payouts are
  isolated purely by counterparty name, as today.

## Decisions (from brainstorming)

1. **Coexistence**: support both formats with automatic per-file detection.
   The legacy archived file and new-format imports work simultaneously, no
   manual action.
2. **Channel scope**: Airbnb (`CITIBANK EUROPE PLC`) + Booking
   (`BOOKING.COM B.V.`) fully reconciled as today. Additionally **collect**
   Marriott / `Global Hospitality Licensing S.a` rows into `bank_transactions`
   (new `marriott` channel) so the data is persisted and visible.
3. **Marriott matching**: Marriott has no payout CSV and no per-reservation
   reference in the bank line (only a `/ROC/<token>/` and sometimes a period
   tail like `M17 31.03.2026`). Precise reconciliation is **deferred**. Scope
   now is parse + persist only; approximate date/amount matching explicitly
   out of scope for this change.

## Chosen Approach: Normalizing Record Iterator (Approach A)

Introduce a single internal generator that owns all format knowledge and
yields a unified record shape. Consumer functions keep their channel filters
and public signatures. The strict reference-based matching for Airbnb/Booking
is untouched.

### Component 1 — Format detection & normalizing iterator

`_detect_bank_format(raw: bytes) -> "legacy" | "money_s3"`

- `raw[:2] in (b"\xff\xfe", b"\xfe\xff")` → `legacy` (UTF-16, comma).
- Else decode as cp1250; if it starts with `Header;` or contains `Detail 1;`
  → `money_s3`.
- Neither → `logger.error`, file skipped (matches current `except Exception`
  behavior).

`_source_bytes(source)` — new helper returning the raw bytes for a path or an
archived source dict (mirrors existing `_source_text` / `_source_name`).

`_iter_bank_records(source) -> Iterator[dict]` — reads bytes once, dispatches
on detected format, yields the unified record:

```python
{
  "datum": date | None,        # already parsed via _parse_date
  "amount_czk": float,
  "counterparty": str,         # Název protiúčtu
  "message": str,              # legacy: Zpráva pro příjemce; money_s3: Doplňující údaj
  "ref_secondary": str,        # legacy: Reference platby; money_s3: ""
  "tx_id": str,                # legacy: ID transakce; money_s3: Identifikátor položky
  "transaction_type": str,     # legacy: Typ transakce; money_s3: ""
}
```

- **legacy branch**: `csv.DictReader` (comma, utf-16) — current logic moved
  here verbatim, no semantic change.
- **money_s3 branch**: `csv.reader` (`;`, cp1250, `errors="replace"`). Build a
  `name → index` map from the `Detail 1;0;…` definition row using real Czech
  column names (post-cp1250-decode): `Částka`, `Datum zaúčtování`,
  `Doplňující údaj`, `Identifikátor položky`, `Název protiúčtu`. Then yield
  only rows where col0 == `Detail 1` and col1 == `1`; `Header` / `Footer` /
  definition rows are skipped. If the definition row is missing, fall back to
  fixed indices (amount=3, datum=9, message=10, tx_id=11, counterparty=14).

`_parse_date`, `_safe_float`, `_extract_gref`, `_normalize_booking_ref` are
reused unchanged.

### Component 2 — Consumer functions

- `load_bank_csv(paths)`: iterate `_iter_bank_records`; keep rows where
  `"CITIBANK EUROPE" in counterparty.upper()` **and**
  (`transaction_type == "Příchozí úhrada"` **or** `transaction_type == ""`)
  **and** `amount_czk > 0`. Result fields
  (`datum, amount_czk, gref, tx_id, zprava, source_name, tx_key`) unchanged.
  `gref = _extract_gref(message) or _extract_gref(ref_secondary)`.
- `load_booking_bank_transactions(paths)`: same iteration; filter
  `"BOOKING.COM" in counterparty.upper()`; `property_id` from `/(\d+)$` on
  `message`; `booking_ref = _normalize_booking_ref(message)`. Returns
  `{property_id: [...]}`, structure unchanged.
- **New** `load_marriott_bank_transactions(paths) -> list[dict]`: filter
  `"GLOBAL HOSPITALITY" in counterparty.upper()`; `ref` = ROC token (regex
  `/ROC/([A-Z0-9]+)`), period tail (`M\d+ \d{2}\.\d{2}\.\d{4}`) preserved in
  `zprava`. Returns `{datum, amount_czk, gref:"", property_id:"", zprava,
  tx_id, tx_key, source_name}`. **No matching performed.**

`match_bank_transaction`, `match_booking_by_ref`, and the amount/date fallback
are NOT modified.

### Component 3 — Marriott persistence (source_registry.py)

In the `source_type == "bank"` import branch (~`source_registry.py:616-622`),
add a third channel alongside `airbnb` / `booking`:

```python
marriott_rows = load_marriott_bank_transactions([source])
save_bank_transactions(conn, "marriott", marriott_rows, commit=False)
```

`_bank_delta_summary` (`source_registry.py:324`) includes Marriott rows in the
"+N new bank transactions" count (by `tx_key` vs existing rows in
`bank_transactions`). The `bank_transactions.channel` column already exists.
Report/month reconciliation for Marriott is NOT implemented (deferred).

### Component 4 — Error handling & edge cases

- Unrecognized format → `logger.error`, file skipped, loop continues.
- money_s3 missing `Detail 1;0;…` definition row → fixed-index fallback; rows
  with too few columns skipped with `logger.debug`.
- cp1250 decode uses `errors="replace"` so malformed bytes don't crash parsing.
- Dedup: `tx_key` built by the same `_bank_tx_key`; Money S3
  `Identifikátor položky` → `tx_id` → stable key; `ON CONFLICT(tx_key)`
  upsert prevents re-import duplicates within one format.
- **Known limitation**: legacy `tx_id` ("ID transakce") and Money S3 `tx_id`
  ("Identifikátor položky") are different identifiers for the same underlying
  transaction. If legacy and new files overlapped in time, the same
  transaction could be double-counted. Not expected in practice: legacy
  covers through March 2026, new format starts April 2026.
- Marriott row without ROC token → `ref=""`, still persisted (amount/date/
  counterparty retained).
- Empty `datum` → record dropped for Airbnb/Booking (as today); kept for
  Marriott (best-effort).

### Component 5 — Testing

Extend `tests/test_bank.py` with cp1250 byte-literal fixtures (no real files):

- `test_detect_format_legacy_utf16` / `test_detect_format_money_s3`
- `test_money_s3_airbnb_rows` — Header/Detail-def/Detail-data/Footer fixture →
  `load_bank_csv` returns G-ref rows with correct amount/date/tx_id
- `test_money_s3_booking_rows` — `load_booking_bank_transactions` extracts
  `property_id` and `booking_ref`
- `test_money_s3_marriott_collected` — `load_marriott_bank_transactions`
  collects Global Hospitality rows with ROC token
- `test_legacy_format_unchanged` — regression: existing UTF-16 fixture parses
  exactly as before
- `test_money_s3_skips_header_footer` — `Header` / `Footer` / owner-payout
  rows excluded

Run: `pytest tests/test_bank.py -q`. Note: `test_verifier.py` has 4
pre-existing unrelated failures (city_tax + month_assignment) — ignore.

## Out of Scope

- Marriott precise reconciliation / approximate date+amount matching.
- UI surfacing of the `marriott` channel beyond data being present in
  `bank_transactions`.
- Any change to Airbnb/Booking matching logic or the amount/date fallback.
- Migrating historical data out of the legacy format.
