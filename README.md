# Rentero Report Generator

Rentero je interní reporting tool pro měsíční vyúčtování jednotlivých listingů. Systém bere data z Hostify, Airbnb, Booking, banky a ručních vstupů, ukládá provozní stav do SQLite a generuje draft Excel reporty i webový review interface.

## Co umí

- importovat raw source files do SQLite archivu a deduplikovat je podle SHA256
- normalizovat Hostify rezervace a párovat je s Airbnb / Booking payout exporty
- zobrazovat v audit trailu jak ruční zásahy, tak importní události včetně dokumentu, zdroje a dopadů na měsíce / objekty
- dopočítat české finanční výstupy, city tax, provize, DPH a pending payment logiku
- párovat payout batch vs. bankovní transakce
- spravovat měsíční lifecycle: `OPEN`, `LOCKED`, `STALE`
- automaticky generovat reporty při každé změně dat (override, přesun, vyloučení, expense, import CSV)
- používat durable generation jobs v SQLite i pro import-triggered auto-regeneration
- denně synchronizovat Hostify snapshot pro 5 měsíců (předchozí + aktuální + 3 dopředu)
- spravovat klienty, expense categories, expenses a manual overrides
- mapovat více Hostify child listing nicknames na jeden objekt, včetně Marriott / `HVMB`
- spravovat uživatelské účty se třemi rolemi (admin / manager / client) a přiřazovat klientům přístup k jednotlivým objektům
- zobrazovat plně responzivní mobilní rozhraní: bottom bar, sheet overlays, HTMX SPA navigace, light/dark téma

## Quick Start

```bash
# 1. Import source files into SQLite
python -m report.sources import --type airbnb --path source/airbnb/airbnb_03_2026-04_2026.csv
python -m report.sources import --type booking --path source/booking/Payout_from_2024-01-01_until_2026-03-26.csv
python -m report.sources import --type bank --path source/bank/transakce\ CS\ 2024-2026.csv

# 2. Inspect active archived sources
python -m report.sources list --active-only

# 3. Generate reports from DB-backed active sources
python -m report.main --year 2026 --month 3

# Optional: direct file override
python -m report.main --year 2026 --month 3 \
  --airbnb-csv source/airbnb/airbnb_03_2026-04_2026.csv \
  --booking-csv source/booking/Payout_from_2024-01-01_until_2026-03-26.csv \
  --bank-csv source/bank/transakce\ CS\ 2024-2026.csv

# Legacy fallback: scan source/* when registry is empty or incomplete
python -m report.main --year 2026 --month 3 --legacy-autodiscover

# Run web UI
python run_web.py
```

Výstupní Excel reporty se ukládají do `output/reports/`.

Na Windows je preferovaný launcher:

```powershell
.\start_web.bat
```

## Web Runtime

Web vrstva používá cookie session auth s RBAC (Role-Based Access Control).

### Env vars (produkce)

| Proměnná | Popis |
|---|---|
| `RENTERO_SESSION_SECRET` | tajný klíč pro cookie signing |
| `RENTERO_ALLOW_INSECURE_DEFAULTS=1` | pouze lokální vývoj |

> `RENTERO_USERNAME` / `RENTERO_PASSWORD` se používají pouze pro seed prvního admin účtu při prázdné tabulce `users`.

Pouze pro lokální vývoj lze povolit fallback:

- `RENTERO_ALLOW_INSECURE_DEFAULTS=1`

`start_web.bat` / `start_web.ps1` umí tento localhost fallback zapnout automaticky, pokud v `.env`
chybí `RENTERO_SESSION_SECRET`.

Výchozí Windows launcher spouští web na pozadí a zapisuje logy do:

- `cache/web.log`
- `cache/web.err`

Pokud je potřeba debug session s živými logy v konzoli, lze před spuštěním nastavit:

- `RENTERO_SHOW_LOGS=1`

Všechny `POST` routes jsou chráněné session-based CSRF tokenem.

### Uživatelé a role

| Role | Dashboard | Property pages | Finance/Audit | Zdroje/Logy | Mutace | Správa uživatelů |
|---|---|---|---|---|---|---|
| `admin` | vše | vše | vše | ano | ano | ano |
| `manager` | vše | vše | vše | ano | ano | ne |
| `client` | přiřazené | přiřazené | přiřazené | ne | ne | ne |

Správa uživatelů je dostupná na `/admin/users` (pouze `admin` role).

### Produkční deployment (Hetzner)

```bash
./deploy.sh
```

Nebo manuálně:

```bash
git push origin main
ssh rentero@204.168.216.181 "cd ~/rentero && git pull origin main && source venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart rentero"
```

Služba běží jako systemd unit `rentero`. Status: `systemctl status rentero`.

## Architektura

### High-level flow

```text
raw source files / Hostify API
        │
        ├─ source_registry + db archive
        ├─ loader.py
        ├─ verifier.py
        ├─ bank.py
        ▼
calculator.py
        ▼
summary.py + excel.py
        ▼
SQLite persisted state + web UI
```

### Kódové vrstvy

- `report/main.py`
  CLI entrypoint pro generování reportů (píše Excel, čte z API i souborů).
- `report/engine.py`
  In-process generovací engine: čte Hostify snapshot z DB, nepíše Excel. Volán webem při každé mutaci.
- `report/hostify_sync.py`
  Denní asyncio background task: fetchuje 5 měsíců z Hostify API, ukládá snapshoty, regeneruje otevřené měsíce.
- `report/web.py`
  Thin FastAPI entrypoint: app setup, auth, CSRF, dependency wiring, route registration, spouštění sync loopu.
- `report/routes/`
  HTTP routes rozdělené po doménách: auth, dashboard, property, sources, operations, reconciliation, bank, logs, audit, admin.
- `report/web_support.py`
  Web orchestration helpers a view-model assembly mimo route handlery.
- `report/db.py`
  SQLite connection, schema, migrations a historický facade modul.
- `report/db_months.py`
  Month lifecycle a background generation jobs.
- `report/db_admin.py`
  Report objects, aliases, clients, expenses, overrides.
- `report/db_users.py`
  User accounts: password hashing (SHA-256 + salt), CRUD, property assignments.
- `report/config.py`
  Runtime config loader a JSON/DB sync logika.
- `report/source_registry.py`
  Import pipeline a delta summary pro source files.
- `report/loader.py`
  Hostify fetch + normalizace snapshotů.
- `report/verifier.py`
  CSV cross-check pro Airbnb / Booking včetně Booking payout normalization a tolerance rules.
- `report/calculator.py`
  Pure calculation engine.
- `report/bank.py`
  Bank reconciliation a payout-batch matching.
- `report/summary.py`
  Canonical property/month summary pro web i Excel.
- `report/excel.py`
  Export finálního `.xlsx`.

### Web vrstva

```text
report/web.py
  ├─ auth/session/csrf
  ├─ dependency providers
  └─ register_route_modules(...)

report/routes/
  ├─ auth.py
  ├─ dashboard.py
  ├─ property_routes.py
  ├─ operations.py
  ├─ sources.py
  ├─ reconciliation.py
  ├─ logs.py
  ├─ audit_routes.py
  └─ admin.py          ← RBAC user management (admin only)

report/web_support.py
  ├─ dashboard view models
  ├─ inventory assembly
  ├─ generation runner commands
  ├─ bank drilldown helpers
  └─ property template assembly
```

### Data vrstva

```text
report/db.py
  ├─ connection + schema + migrations
  ├─ source archive / imports
  ├─ payout batches / bank / hostify snapshots
  └─ facade exports

report/db_months.py
  ├─ report_month_state
  ├─ report_generation_jobs
  └─ bulk_generation_runs

report/db_admin.py
  ├─ report_objects + aliases
  ├─ clients
  ├─ expenses + categories
  └─ override_events
```

## Projektová struktura

```text
report/                  Python application code
report/routes/           FastAPI route modules
templates/               Jinja templates
templates/partials/      shared UI partials
config/                  seed/runtime config files
source/                  local source file drop folders
cache/                   SQLite DB + runtime logs
output/reports/          generated Excel reports
output/legacy/           old/manual spreadsheet artifacts
docs/                    project documentation
docs/architecture/       architecture notes and changelog
docs/specs/              product and implementation specs
docs/plans/              implementation plans
docs/reference/hostify/  saved Hostify API reference snapshots
docs/assets/             supporting images and plan assets
archive/                 legacy scripts and historical materials
tests/                   pytest suite
```

## Důležitá doménová pravidla

- Airbnb payout a úklid používají implied batch rate z Airbnb payout exportu.
- Airbnb provize používá CNB kurz ke dni rezervace, ne Airbnb batch rate.
- Booking kurz se bere jako `Splatná částka v CZK / Hodnota transakce` z Booking payout CSV.
- Booking provize se počítá jako `(Provize + Poplatek za platební služby) * booking kurz`.
- Booking porovnání s CSV: Hostify hlásí `city_tax_eur=0` pro Booking, ale city tax je zahrnut v payoutu. Systém odečítá paušálně **2 EUR za osobu za noc** (dospělí + děti + kojenci) před porovnáním.
- `Balíčky` se počítají podle guest countu z city-tax / Checkin vrstvy, ne podle syrové occupancy.
- `Cena ubytování` nikdy nesmí spadnout pod `0 Kč`.
- Manual override `payout_czk` okamžitě přepočítává i `cena_ubytovani_czk`.
- `ROZDÍL` se neukazuje pro mikro-odchylky. Pokud `abs(diff) <= 1.00 EUR`, reservation je `MATCHED`.
- Manual month move je canonical součást pipeline: reservation přesunutá do jiného měsíce je skrytá i z `CSV-only / CHYBÍ_V_HOSTIFY` větve původního měsíce, takže se po regeneraci nevrací zpět jako phantom row.
- `ZRUŠENO` reservation se v kalkulaci nechová jako normální pobyt: nepočítá se `city tax`, `úklid`, `balíčky`, `DPH z přípravy pokoje` ani Checkin verification badge.
- Bank matching je referenční, ne heuristický fallback podle částky/dne.
- Closed month (`LOCKED`) je read-only, dokud není explicitně unlocknut.
- Marriott rezervace dnes přicházejí z Hostify jako source `HVMB`, v UI se ale zobrazují jako `Marriott`.
- Marriott / jiné non-Airbnb non-Booking Hostify rezervace se zahrnou do reportu přes Hostify aliases a zatím standardně končí jako `CHYBÍ_V_CSV`, dokud pro ně neexistuje dedikovaný CSV import.
- `report_objects.client_type` rozlišuje tři typy: `rentero` (vlastní objekty), `klient` (standardní klient), `z_klient` (3% odměna z payoutu, výplata = cena_ubytování + city_tax).
- AirCover (`__AC`) řádky: zachovávají původní znaménko, dostávají `_no_fees` režim (bez city_tax, úklid, balíčky), podléhají striktnímu payout-date window placement.
- "Vyrovnání z řešení" = adjustment (`__ADJ`), "Výplata jako výsledek řešení" = AirCover (`__AC`).
- Bank match ownership: stejná bankovní transakce nemůže být DORAZILO ve dvou různých měsících. `payout_batch_bank_matches` obsahuje `slug`, `year`, `month` pro per-month scoping.
- Reconciliation (`get_accounting_entries`) filtruje pouze aktivní source soubory.

## Operační poznámky

- Import impacts už nespouští efemérní FastAPI `BackgroundTasks`.
  Každý affected month jde přes persisted `report_generation_jobs`, takže restart web procesu
  nenechá měsíc ve `STALE` jen proto, že se ztratila in-memory task.
- Sekvenční hromadné generování běží v odděleném runner procesu.
  Pokud proces zmizí mimo aplikaci, stale run se automaticky uzavře a v UI nezůstane viset starý `current_slug`.
- Floating reservation panel lookup používá přímý indexed lookup přes
  `report_rows.confirmation_code`, ne `json_extract(...)` nad JSON blobem.
  To výrazně snižuje CPU load web procesu při práci s panelem.
- Bank matches se mažou per slug/month před regenerací, ne globálně.
  MATCHED status se degraduje na KE KONTROLE, pokud `bank_status=CHYBÍ`.
- Dashboard filtrování: Vše / Rentero / Klienti / Z Klienti — KPI se přepočítávají client-side.
- Flash bannery se automaticky zavírají po 8 sekundách.

## SQLite

Hlavní DB je `cache/rentero.db`.

Klíčové tabulky:

- `source_files`, `import_runs`
- `hostify_reservations`
- `report_rows`, `report_history`
- `report_month_state`
- `report_generation_jobs`, `bulk_generation_runs`
- `clients`, `expenses`, `expense_categories`
- `report_objects`, `report_object_aliases`, `report_object_channel_config`
- `override_events`
- `payout_batches`, `payout_batch_items`
- `bank_transactions`, `payout_batch_bank_matches`
- `users`, `user_properties`   ← RBAC

## Dokumentace

- [Docs Overview](docs/README.md)
- [Architecture Notes](docs/architecture/architecture.md)
- [Architecture Changelog](docs/architecture/changelog.md)
- [Implementation Spec](docs/specs/implementation-spec.md)
- [Implementation Plan](docs/plans/implementation-plan.md)
- [Hostify Reference Index](docs/reference/hostify/INDEX.md)

## Testy

```bash
pytest -q
```

Při lokální práci je bezpečnější spoléhat na cílené testy podle změněného modulu, protože některé webové testy mohou záviset na plném lokálním Python prostředí.
