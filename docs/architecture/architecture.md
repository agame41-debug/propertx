# Architecture

## Pipeline
```
SQLite source registry / explicit CLI paths
       │
       ├─ Airbnb / Booking / Bank raw files
       ▼
Hostify API  →  loader.py  →  normalized Hostify snapshots in SQLite
                    │
              verifier.py  ←  Airbnb CSV  (MATCHED/ROZDÍL/CHYBÍ + payout_gref + airbnb_rate)
              verifier.py  ←  Booking CSV + late Hostify re-link by exact booking id
                    │
             calculator.py  (pure: Airbnb payout/cleaning = batch-implied, Airbnb commission = CNB, Booking = actual payout rate)
                    │
                bank.py  ←  bank CSV  (G-ref match → DORAZILO/CHYBÍ)
                    │
            summary.py  →  canonical property/month summary
                    │
         ┌──────────┼──────────┐
         │          │          │
         ▼          ▼          ▼
  excel.py     SQLite state    web.py entrypoint
  (.xlsx)      db.py facade    + routes/ + web_support.py
               + db_months.py
               + db_admin.py
```

`excel.py` and the web layer are parallel consumers of the same calculated state. The web UI does not depend on Excel generation.

## Modules

| Module | Role |
|---|---|
| `loader.py` | Fetch Hostify by date range, filter by Hostify listing nicknames / aliases, assign report month. SQLite cache 2h TTL. Also normalizes and persists Hostify reservation snapshots for later reconciliation, including non-Airbnb / non-Booking sources such as `HVMB` (Marriott). |
| `verifier.py` | Load Airbnb/Booking CSVs with encoding and delimiter autodetection, then enforce strict required-column checks. Cross-check payout. Build `payout_ref_map`: code → `{gref, airbnb_rate}`. For Booking, compare CSV `net_eur` against Hostify payout after subtracting raw `Hostify city_tax_eur`, then apply tolerance rules. Small drifts within `±1.00 EUR` remain `MATCHED`. Can re-link Booking CSV-only rows to stored Hostify reservations by exact booking id when Hostify lags behind. |
| `calculator.py` | Pure functions. Airbnb payout/cleaning use `airbnb_batch_rate`, but Airbnb commission uses CNB rate on reservation date. Booking uses actual payout CZK / payout rate from CSV. Other sources fall back to CNB. `Balíčky` use the city-tax/checkin guest count, cancelled reservations do not accrue stay-derived costs, and `cena_ubytovani_czk` is clamped to `>= 0`. Carries batch metadata forward for UI/storage. |
| `bank.py` | Load bank CSV (UTF-16). Match bank transaction per payout batch, not per reservation. |
| `excel.py` | One sheet "Finální sestava". Columns A–R + Payout ref / Banka datum / Banka status. |
| `cnb.py` | CNB EUR/CZK rate. Two-level cache (memory + SQLite). Weekend/holiday fallback up to 5 days back. |
| `db.py` | SQLite connection, schema, migrations, and facade exports for the rest of the app. |
| `db_months.py` | Month lifecycle and background generation jobs (`report_month_state`, `report_generation_jobs`, `bulk_generation_runs`). |
| `db_admin.py` | Report objects, aliases, clients, expenses, and override events. |
| `config.py` | Load `config/properties.json`. Lookup by listing_id or slug. |
| `main.py` | CLI. Prefers DB-backed active source files, applies active overrides before export/persist, persists batch drill-down data, isolates errors per property. |
| `web.py` | Thin FastAPI app entrypoint: auth, session, CSRF, dependency wiring, route registration. |
| `routes/` | Route modules split by domain: auth, dashboard, sources, property, operations. |
| `web_support.py` | Web orchestration helpers, route-neutral view assembly, generation runner commands. |

## Exchange rate logic
```
Airbnb:
  batch_rate = Vyplaceno_CZK / Σ(Částka of ALL non-Payout rows in batch)
  ← includes Rezervace + Vyrovnání + adjustments (same as reconcile.py)
  ← stored in gref_map, attached to reservation before calculator
  commission_rate = CNB EUR/CZK rate on reservation date

Booking:
  payout_rate = Splatná částka / Hodnota transakce
  ← actual Booking payout conversion from payout CSV
```

## Airbnb CSV structure
Parser accepts UTF-8 BOM, CP1250, and common delimiter variants (`,`, `;`, tab, `|`) before schema validation.

Typical payout export:
```
Payout   | Referenční kód=G-XXXXX | Vyplaceno=43219.63 | Datum=02/09/2026
Vyrovnání z řešení | code | Částka=-80.00   ← included in batch EUR sum
Rezervace          | code | Částka=236.60   ← gets assigned gref + rate
Rezervace          | code | Částka=388.70
...
Payout   | Referenční kód=G-YYYYY | ...     ← next batch starts here
```

## Source import path
Web uploads and `python -m report.sources import` use the same import pipeline:
archive raw file → dedupe by SHA256 → record `import_runs` → refresh normalized
SQLite snapshots for `airbnb`, `booking`, `bank`, and `checkin` sources.

Import events are also surfaced in the audit trail together with document/source metadata
and orchestration outcomes for affected property-months.

## Bank matching
Airbnb → CITIBANK EUROPE PLC, incoming only.
1. `Zpráva pro příjemce` contains `G-XXXXXXX` → direct index lookup
2. No automatic amount/date fallback → unresolved rows remain `CHYBÍ` until a reference-backed match exists

## Hostify quirks
- `listing_id` filter param broken → fetch all by date range, filter locally by `listing_nickname`
- One property can resolve to multiple Hostify child listing nicknames; this is how Marriott / `HVMB` reservations are attached to the same report object.
- "28. Pluku 58": Hostify nickname = `"28. Pluku 58"`, Airbnb listing = `"Modern APT City Hideaway"`
- Master listing 184988, Airbnb child 206426 — both share same `listing_nickname`
- Booking/financial data may arrive before the reservation appears in Hostify → keep normalized Hostify snapshots and re-link later by exact `channel_reservation_id`
- Pagination: API ignores `limit` param, always returns ~20/page. `max_pages=500` → up to 10 000 reservations. Incomplete fetch logs ERROR.
- `transaction_fee` field exists in raw API response alongside `channel_commission`. Both are summed into `channel_commission_eur` during normalization.
- In the current dataset, Marriott arrives from Hostify with source label `HVMB`. UI surfaces it as `Marriott`.

## Booking verification and commission

Verification rule:
- Booking CSV `net_eur` is the payout truth used for calculations when a row is matched.
- Comparable Hostify payout is normalized by removing raw Hostify `city_tax_eur`.
- If the remaining diff is within `±1.00 EUR`, the row is `MATCHED`, not `ROZDÍL`.

Полная провизия = базовая комиссия (15%) + payment service fee (1.5%):
- **С CSV**: `abs(Provize) + abs(Poplatek za platební služby)` → `total_commission_eur`
- **Без CSV (CHYBÍ_V_CSV)**: `channel_commission + transaction_fee` из Hostify API

## Web runtime
- Web auth/session config comes from `RENTERO_USERNAME`, `RENTERO_PASSWORD`, `RENTERO_SESSION_SECRET`
- `RENTERO_ALLOW_INSECURE_DEFAULTS=1` is a localhost/dev-only escape hatch
- All `POST` routes use session-based CSRF validation
- Background generation jobs are tracked in SQLite and stale `PENDING` or `RUNNING` jobs are auto-expired
- Web generation is DB-first and passes `--legacy-autodiscover` to stay aligned with CLI fallback behavior
- Windows launcher defaults to detached background mode; `RENTERO_SHOW_LOGS=1` switches back to attached console logs for debugging

## FastAPI migration path
`calculator.py` — pure functions, JSON-serializable I/O, zero I/O → drop-in for API endpoint.
`loader.py`, `cnb.py` — accept `db_conn` param for connection injection.
