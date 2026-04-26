"""
report/db.py — SQLite database layer for Rentero report system.

Database file: cache/rentero.db

Tables:
  cnb_rates        — persisted EUR/CZK rates from CNB API
  hostify_cache    — cached Hostify API responses (with TTL)
  report_rows      — calculated report rows (for future web UI)
  report_history   — log of generated Excel files
  manual_overrides — per-reservation manual corrections (future)
  source_files     — manually imported source files by type
  payout_batches   — parsed payout batches for Airbnb / Booking
  payout_batch_items — batch breakdown rows for future drill-down UI
  bank_transactions — normalized incoming bank transactions
  payout_batch_bank_matches — batch ↔ bank transaction links
  hostify_reservations — normalized Hostify reservation snapshots for late linking
"""

import hashlib
import json
import os
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from report.db_admin import (
    OVERRIDE_FIELD_LABELS,
    OVERRIDE_SCOPE_RESERVATION,
    VERIFICATION_STATUS_OPTIONS,
    add_expense,
    add_expense_category,
    apply_overrides_to_rows,
    create_override_event,
    delete_expense,
    delete_expense_category,
    get_active_overrides_for_month,
    get_all_clients,
    get_client,
    get_expense,
    get_expense_categories,
    get_expense_total,
    get_expenses,
    get_override_events,
    get_report_object,
    get_report_object_aliases,
    get_report_object_channel_configs,
    list_report_objects,
    normalize_override_value,
    revert_override_event,
    save_client,
    save_report_object_channel_config,
    set_report_object_aliases,
    update_expense,
    upsert_report_object,
)
from report.db_months import (
    BULK_GENERATION_FAILED,
    BULK_GENERATION_PENDING,
    BULK_GENERATION_RUNNING,
    BULK_GENERATION_SUCCEEDED,
    GENERATION_JOB_FAILED,
    GENERATION_JOB_PENDING,
    GENERATION_JOB_RUNNING,
    GENERATION_JOB_SUCCEEDED,
    MONTH_DATA_STATE_EMPTY,
    MONTH_DATA_STATE_GENERATED,
    MONTH_DATA_STATE_READY,
    MONTH_DATA_STATE_STALE,
    MONTH_STATUS_LOCKED,
    MONTH_STATUS_OPEN,
    LockedReportMonthError,
    _assert_report_month_mutable,
    create_bulk_generation_run,
    create_report_generation_job,
    expire_stale_bulk_generation_runs,
    expire_stale_report_generation_jobs,
    finish_bulk_generation_run,
    finish_report_generation_job,
    get_active_bulk_generation_run,
    get_active_report_generation_job,
    get_bulk_generation_run,
    get_latest_bulk_generation_run,
    get_latest_report_generation_job,
    get_report_month_state,
    get_report_month_states,
    is_report_month_locked,
    mark_report_month_has_data,
    mark_report_month_stale,
    mark_report_months_stale,
    set_bulk_generation_run_running,
    set_report_generation_job_running,
    set_report_month_locked,
    touch_report_month_generation,
    update_bulk_generation_run_progress,
)

from report.db_users import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_CLIENT,
    VALID_ROLES,
    authenticate_user,
    get_user_by_id,
    get_user_by_username,
    is_login_locked,
    list_users,
    create_user,
    update_user as update_user_record,
    change_password,
    delete_user,
    get_user_property_slugs,
    set_user_properties,
    get_users_for_property,
    hash_password,
    verify_password,
)

from report.db_controls import (
    create_reservation_month_assignment,
    revert_reservation_month_assignment,
    get_reservation_month_assignments,
    get_codes_assigned_to_month,
    get_assignment_for_code,
    get_all_assignments_for_code,
    create_reservation_exclusion,
    reinstate_reservation,
    get_active_exclusions,
    get_exclusion_for_code,
)

_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "cache", "rentero.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_objects (
    slug                TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL DEFAULT '',
    hostify_listing_id  INTEGER,
    listing_nickname    TEXT NOT NULL DEFAULT '',
    balicky_per_person  REAL DEFAULT 0,
    city_tax_rate       REAL DEFAULT 0,
    vat_rate            REAL DEFAULT 0.21,
    rentero_commission  REAL DEFAULT 0.15,
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_object_channel_config (
    report_object_slug  TEXT NOT NULL REFERENCES report_objects(slug) ON DELETE CASCADE,
    channel             TEXT NOT NULL,
    config_json         TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (report_object_slug, channel)
);

CREATE TABLE IF NOT EXISTS report_object_aliases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_object_slug  TEXT NOT NULL REFERENCES report_objects(slug) ON DELETE CASCADE,
    channel             TEXT NOT NULL,
    alias_type          TEXT NOT NULL,
    alias_value         TEXT NOT NULL,
    valid_from          TEXT,
    valid_to            TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_object_aliases_lookup
    ON report_object_aliases(report_object_slug, channel, alias_type, is_active);

CREATE TABLE IF NOT EXISTS clients (
    property_slug   TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    ico             TEXT DEFAULT '',
    dic             TEXT DEFAULT '',
    platce_dph      INTEGER DEFAULT 0,
    adresa          TEXT DEFAULT '',
    bank_account    TEXT DEFAULT '',
    email           TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS expense_categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    property_slug   TEXT NOT NULL,
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    date            TEXT DEFAULT '',
    category_id     INTEGER REFERENCES expense_categories(id) ON DELETE SET NULL,
    description     TEXT NOT NULL,
    amount_czk      REAL NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cnb_rates (
    date       TEXT PRIMARY KEY,   -- "YYYY-MM-DD"
    rate       REAL NOT NULL,
    fetched_at TEXT NOT NULL       -- ISO datetime
);

CREATE TABLE IF NOT EXISTS hostify_cache (
    cache_key  TEXT PRIMARY KEY,   -- e.g. "reservations:2026-02-01:2026-04-30"
    data       TEXT NOT NULL,      -- JSON blob
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL       -- ISO datetime, after which re-fetch
);

CREATE TABLE IF NOT EXISTS report_rows (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    year              INTEGER NOT NULL,
    month             INTEGER NOT NULL,
    confirmation_code TEXT NOT NULL,
    data              TEXT NOT NULL,   -- JSON blob of CalculatedRow
    generated_at      TEXT NOT NULL,
    UNIQUE(slug, year, month, confirmation_code)
);

CREATE INDEX IF NOT EXISTS idx_report_rows_code_lookup
    ON report_rows(confirmation_code, year DESC, month DESC);

CREATE TABLE IF NOT EXISTS report_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL,
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    file_path    TEXT NOT NULL,
    rows_count   INTEGER,
    matched      INTEGER DEFAULT 0,
    rozdil       INTEGER DEFAULT 0,
    chybi_csv    INTEGER DEFAULT 0,
    chybi_hostify INTEGER DEFAULT 0,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_month_state (
    slug                       TEXT NOT NULL,
    year                       INTEGER NOT NULL,
    month                      INTEGER NOT NULL,
    status                     TEXT NOT NULL DEFAULT 'OPEN',
    locked_at                  TEXT,
    locked_by                  TEXT,
    unlocked_at                TEXT,
    unlocked_by                TEXT,
    last_generated_at          TEXT,
    last_recalculated_at       TEXT,
    data_state                 TEXT NOT NULL DEFAULT 'EMPTY',
    has_new_data_since_generation INTEGER NOT NULL DEFAULT 0,
    notes                      TEXT DEFAULT '',
    PRIMARY KEY (slug, year, month)
);

CREATE TABLE IF NOT EXISTS report_generation_jobs (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                       TEXT NOT NULL,
    year                       INTEGER NOT NULL,
    month                      INTEGER NOT NULL,
    status                     TEXT NOT NULL,
    requested_by               TEXT NOT NULL DEFAULT '',
    pid                        INTEGER,
    message                    TEXT NOT NULL DEFAULT '',
    detail                     TEXT NOT NULL DEFAULT '',
    created_at                 TEXT NOT NULL,
    started_at                 TEXT,
    finished_at                TEXT,
    updated_at                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_generation_jobs_lookup
    ON report_generation_jobs(slug, year, month, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_report_generation_jobs_active_unique
    ON report_generation_jobs(slug, year, month)
    WHERE status IN ('PENDING', 'RUNNING');

CREATE TABLE IF NOT EXISTS bulk_generation_runs (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    year                       INTEGER NOT NULL,
    month                      INTEGER NOT NULL,
    status                     TEXT NOT NULL,
    requested_by               TEXT NOT NULL DEFAULT '',
    pid                        INTEGER,
    current_slug               TEXT NOT NULL DEFAULT '',
    total_objects              INTEGER NOT NULL DEFAULT 0,
    processed_objects          INTEGER NOT NULL DEFAULT 0,
    succeeded_objects          INTEGER NOT NULL DEFAULT 0,
    failed_objects             INTEGER NOT NULL DEFAULT 0,
    skipped_locked_objects     INTEGER NOT NULL DEFAULT 0,
    skipped_no_data_objects    INTEGER NOT NULL DEFAULT 0,
    skipped_running_objects    INTEGER NOT NULL DEFAULT 0,
    message                    TEXT NOT NULL DEFAULT '',
    detail                     TEXT NOT NULL DEFAULT '',
    created_at                 TEXT NOT NULL,
    started_at                 TEXT,
    finished_at                TEXT,
    updated_at                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulk_generation_runs_lookup
    ON bulk_generation_runs(id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_generation_runs_active_unique
    ON bulk_generation_runs(status)
    WHERE status IN ('PENDING', 'RUNNING');

CREATE TABLE IF NOT EXISTS manual_overrides (
    slug              TEXT NOT NULL,
    year              INTEGER NOT NULL,
    month             INTEGER NOT NULL,
    confirmation_code TEXT NOT NULL,
    field             TEXT NOT NULL,   -- e.g. "payout_eur", "balicky_czk"
    value             TEXT NOT NULL,   -- stored as string
    note              TEXT,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (slug, year, month, confirmation_code, field)
);

CREATE TABLE IF NOT EXISTS pending_payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    original_year     INTEGER NOT NULL,
    original_month    INTEGER NOT NULL,
    confirmation_code TEXT NOT NULL,
    guest_name        TEXT,
    check_in          TEXT,
    check_out         TEXT,
    stay_label        TEXT,
    source            TEXT,
    expected_czk      REAL,            -- Splatná částka / payout_czk from calc
    gref              TEXT,            -- G-REF (Airbnb) or NO-ref (Booking)
    resolved_year     INTEGER,
    resolved_month    INTEGER,
    bank_datum        TEXT,
    bank_amount_czk   REAL,
    batch_expected_czk REAL,
    batch_payout_date TEXT,
    status            TEXT DEFAULT 'PENDING',  -- PENDING / RESOLVED
    created_at        TEXT NOT NULL,
    UNIQUE(slug, confirmation_code)    -- upsert-safe: one record per reservation
);

CREATE TABLE IF NOT EXISTS source_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type   TEXT NOT NULL,
    original_name TEXT NOT NULL,
    content       BLOB NOT NULL,
    sha256        TEXT NOT NULL,
    imported_at   TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_type_sha256_unique
    ON source_files(source_type, sha256);

CREATE TABLE IF NOT EXISTS import_runs (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type                TEXT NOT NULL,
    source_file_id             INTEGER REFERENCES source_files(id) ON DELETE SET NULL,
    imported_by                TEXT NOT NULL DEFAULT '',
    imported_at                TEXT NOT NULL,
    duplicate_of_source_file_id INTEGER REFERENCES source_files(id) ON DELETE SET NULL,
    new_rows_count             INTEGER NOT NULL DEFAULT 0,
    new_transactions_count     INTEGER NOT NULL DEFAULT 0,
    new_reservations_count     INTEGER NOT NULL DEFAULT 0,
    summary_json               TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_import_runs_recent
    ON import_runs(imported_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS checkin_guest_rows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id      INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    reservation_id      TEXT NOT NULL,
    property_name       TEXT NOT NULL,
    property_slug       TEXT NOT NULL DEFAULT '',
    check_in            TEXT NOT NULL,
    check_out           TEXT NOT NULL,
    guest_name          TEXT NOT NULL,
    guest_age           INTEGER,
    is_minor            INTEGER NOT NULL DEFAULT 0,
    exempt_by_long_stay INTEGER NOT NULL DEFAULT 0,
    imported_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkin_guest_rows_lookup
    ON checkin_guest_rows(source_file_id, reservation_id, check_in, check_out);

CREATE TABLE IF NOT EXISTS checkin_reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id      INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    reservation_id      TEXT NOT NULL,
    property_name       TEXT NOT NULL,
    property_slug       TEXT NOT NULL DEFAULT '',
    check_in            TEXT NOT NULL,
    check_out           TEXT NOT NULL,
    assigned_year       INTEGER,
    assigned_month      INTEGER,
    stay_nights         INTEGER NOT NULL DEFAULT 0,
    total_guests        INTEGER NOT NULL DEFAULT 0,
    paying_guests       INTEGER NOT NULL DEFAULT 0,
    exempt_guests       INTEGER NOT NULL DEFAULT 0,
    missing_age_guests  INTEGER NOT NULL DEFAULT 0,
    guest_names_json    TEXT NOT NULL DEFAULT '[]',
    imported_at         TEXT NOT NULL,
    UNIQUE(source_file_id, reservation_id)
);

CREATE INDEX IF NOT EXISTS idx_checkin_reservations_lookup
    ON checkin_reservations(property_slug, assigned_year, assigned_month, check_in, check_out);

CREATE TABLE IF NOT EXISTS checkin_match_audit (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                      TEXT NOT NULL,
    year                      INTEGER NOT NULL,
    month                     INTEGER NOT NULL,
    record_type               TEXT NOT NULL,
    confirmation_code         TEXT NOT NULL DEFAULT '',
    guest_name                TEXT NOT NULL DEFAULT '',
    source                    TEXT NOT NULL DEFAULT '',
    check_in                  TEXT NOT NULL DEFAULT '',
    check_out                 TEXT NOT NULL DEFAULT '',
    hostify_reservation_id    TEXT NOT NULL DEFAULT '',
    checkin_source_file_id    INTEGER REFERENCES source_files(id) ON DELETE SET NULL,
    checkin_reservation_id    TEXT NOT NULL DEFAULT '',
    checkin_property_name     TEXT NOT NULL DEFAULT '',
    match_status              TEXT NOT NULL,
    before_paying_guests      INTEGER,
    before_exempt_guests      INTEGER,
    after_paying_guests       INTEGER,
    after_exempt_guests       INTEGER,
    checkin_total_guests      INTEGER,
    checkin_missing_age_guests INTEGER,
    overwritten_fields_json   TEXT NOT NULL DEFAULT '{}',
    detail_json               TEXT NOT NULL DEFAULT '{}',
    created_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkin_match_audit_lookup
    ON checkin_match_audit(slug, year, month, record_type, match_status, check_in, id DESC);

CREATE TABLE IF NOT EXISTS report_month_notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL,
    year          INTEGER NOT NULL,
    month         INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    source_type   TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_month_notifications_lookup
    ON report_month_notifications(slug, year, month, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS integrity_audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT NOT NULL,
    occurrences       TEXT NOT NULL,
    detected_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payout_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL,
    batch_ref       TEXT NOT NULL,
    batch_match_ref TEXT,
    payout_date     TEXT,
    credited_date   TEXT,
    amount_czk      REAL,
    amount_eur      REAL,
    implied_rate    REAL,
    source_name     TEXT,
    updated_at      TEXT NOT NULL,
    UNIQUE(channel, batch_ref)
);

CREATE TABLE IF NOT EXISTS payout_batch_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel           TEXT NOT NULL,
    batch_ref         TEXT NOT NULL,
    item_index        INTEGER NOT NULL,
    item_type         TEXT,
    confirmation_code TEXT,
    guest_name        TEXT,
    listing_name      TEXT,
    property_id       TEXT,
    amount_eur        REAL,
    amount_czk        REAL,
    check_in          TEXT,
    check_out         TEXT,
    source_name       TEXT,
    UNIQUE(channel, batch_ref, item_index)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    tx_key        TEXT PRIMARY KEY,
    channel       TEXT NOT NULL,
    tx_id         TEXT,
    datum         TEXT,
    amount_czk    REAL,
    gref          TEXT,
    property_id   TEXT,
    zprava        TEXT,
    source_name   TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payout_batch_bank_matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL,
    batch_ref       TEXT NOT NULL,
    tx_key          TEXT NOT NULL,
    match_method    TEXT,
    matched_amount_czk REAL,
    matched_at      TEXT NOT NULL,
    UNIQUE(channel, batch_ref, tx_key)
);

CREATE TABLE IF NOT EXISTS accounting_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id  INTEGER,
    doc             TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    datum           TEXT,
    popis           TEXT,
    castka          REAL NOT NULL,
    objekt          TEXT,
    objekt_raw      TEXT,
    mesic           TEXT,
    channel         TEXT,
    stredisko       TEXT,
    ucet            TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ae_lookup ON accounting_entries(channel, mesic);
CREATE INDEX IF NOT EXISTS idx_ae_source ON accounting_entries(source_file_id);

CREATE TABLE IF NOT EXISTS stredisko_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id  INTEGER,
    zkratka         TEXT NOT NULL UNIQUE,
    popis           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stredisko_zkratka ON stredisko_map(zkratka);

CREATE TABLE IF NOT EXISTS hostify_reservations (
    confirmation_code TEXT PRIMARY KEY,
    reservation_id    TEXT,
    source            TEXT,
    status            TEXT,
    guest_name        TEXT,
    check_in          TEXT,
    check_out         TEXT,
    assigned_year     INTEGER,
    assigned_month    INTEGER,
    listing_nickname  TEXT,
    payload_json      TEXT NOT NULL,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS override_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type  TEXT NOT NULL DEFAULT 'reservation',
    scope_id    TEXT NOT NULL,
    slug        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT NOT NULL DEFAULT '',
    new_value   TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT 'admin',
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    reverted_at TEXT,
    reverted_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_override_events_lookup
    ON override_events(slug, year, month, scope_id, is_active);

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
    is_adjustment     INTEGER NOT NULL DEFAULT 0,
    batch_ref         TEXT NOT NULL DEFAULT '',
    UNIQUE(slug, confirmation_code, original_year, original_month)
);

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

CREATE TABLE IF NOT EXISTS split_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    confirmation_code TEXT NOT NULL,
    batch_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    UNIQUE(slug, confirmation_code, batch_ref)
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    password_salt   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'client',
    display_name    TEXT NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_properties (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_slug   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, property_slug)
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL DEFAULT '',
    ip              TEXT NOT NULL DEFAULT '',
    attempted_at    TEXT NOT NULL,
    success         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time
    ON login_attempts(username, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
    ON login_attempts(ip, attempted_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alias_valid_to_before(valid_from: str) -> str:
    text = str(valid_from or "").strip()
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed.date() - timedelta(days=1)).isoformat()
    except ValueError:
        pass
    try:
        parsed_date = datetime.strptime(text[:10], "%Y-%m-%d").date()
        return (parsed_date - timedelta(days=1)).isoformat()
    except ValueError:
        return text


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database, run migrations, return connection.
    Thread-safe: each call returns a new connection with WAL mode enabled.
    WAL (Write-Ahead Logging) allows concurrent reads while one writer is active.
    """
    path = db_path or _DB_PATH
    if path != ":memory:":
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")        # concurrent reads + one writer
    conn.execute("PRAGMA busy_timeout = 30000")    # retry up to 30s on lock contention
    conn.execute("PRAGMA wal_autocheckpoint = 1000")  # checkpoint every 1000 pages
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _run_migrations(conn)
    conn.commit()
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _drop_ownership_columns_from_payout_batch_bank_matches(conn: sqlite3.Connection) -> None:
    """One-shot migration: remove slug/year/month columns added 2026-04-13.

    These columns powered the buggy ownership/downgrade mechanism that
    silently flipped DORAZILO → CHYBÍ on locked months. Removing them is
    safe because no callers read them after this fix lands.

    Idempotent: detects already-migrated schema and returns immediately.
    """
    cols = {row["name"] for row in conn.execute(
        "PRAGMA table_info(payout_batch_bank_matches)"
    )}
    if not {"slug", "year", "month"} & cols:
        return
    conn.executescript("""
        DROP TABLE IF EXISTS payout_batch_bank_matches__new;
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


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Small additive migrations for existing local SQLite databases."""
    _ensure_column(conn, "pending_payments", "batch_expected_czk", "batch_expected_czk REAL")
    _ensure_column(conn, "pending_payments", "batch_payout_date", "batch_payout_date TEXT")
    _ensure_column(conn, "report_history", "ke_kontrole", "ke_kontrole INTEGER DEFAULT 0")
    _ensure_column(conn, "report_month_state", "data_state", "data_state TEXT NOT NULL DEFAULT 'EMPTY'")
    _ensure_column(conn, "report_month_state", "has_new_data_since_generation", "has_new_data_since_generation INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "report_month_state", "notes", "notes TEXT DEFAULT ''")
    _dedupe_source_files_by_type_sha256(conn)
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_report_rows_code_lookup
           ON report_rows(confirmation_code, year DESC, month DESC)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_type_sha256_unique
           ON source_files(source_type, sha256)"""
    )
    # Performance indexes for dashboard queries
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_report_history_slug_month
           ON report_history(slug, year, month, generated_at DESC)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_report_rows_slug_month
           ON report_rows(slug, year, month)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_report_month_notifications_slug_month
           ON report_month_notifications(slug, year, month, created_at DESC)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_integrity_audit_detected_at
           ON integrity_audit(detected_at DESC)"""
    )
    _backfill_checkin_source_snapshots(conn)
    _backfill_booking_payout_item_guest_names(conn)
    _migrate_month_assignments_scope(conn)
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
    _ensure_column(conn, "report_objects", "client_type", "client_type TEXT NOT NULL DEFAULT 'rentero'")
    _drop_ownership_columns_from_payout_batch_bank_matches(conn)
    _deactivate_legacy_checkin_source_files(conn)
    _backfill_payout_batches_from_active_sources(conn)
    _seed_admin_user(conn)


def _deactivate_legacy_checkin_source_files(conn: sqlite3.Connection) -> None:
    """Mark active checkin source_files with the pre-Birth-Date header as inactive.

    The checkin CSV format gained a 'Birth Date' column in commit e8c6462
    and the legacy header is no longer parseable (the parser silently
    skips such files). Leaving them is_active=1 makes the active-only
    filter in the engine return zero checkin rows for any month that
    only has a legacy file, with no warning surfaced to the operator.
    Deactivate them so the Sources UI shows the issue clearly.
    """
    from report.checkin import _EXPECTED_HEADER, _decode_source_content

    rows = conn.execute(
        "SELECT id, original_name, content FROM source_files "
        "WHERE source_type = 'checkin' AND is_active = 1"
    ).fetchall()
    deactivated_ids: list[int] = []
    for row in rows:
        content = row["content"]
        if isinstance(content, memoryview):
            content = content.tobytes()
        text = _decode_source_content(bytes(content or b""))
        first_line = next(
            (line.strip("﻿") for line in text.splitlines() if line.strip()),
            "",
        )
        header = [part.strip() for part in first_line.split(";")]
        if header[: len(_EXPECTED_HEADER)] != _EXPECTED_HEADER:
            deactivated_ids.append(int(row["id"]))
    if not deactivated_ids:
        return
    placeholders = ",".join("?" for _ in deactivated_ids)
    conn.execute(
        f"UPDATE source_files SET is_active = 0 WHERE id IN ({placeholders})",
        deactivated_ids,
    )
    conn.commit()


_payout_batches_backfill_done = False
_checkin_snapshots_backfill_done = False
_booking_guest_names_backfill_done = False


def _reset_payout_batches_backfill_guard_for_tests() -> None:
    """Test hook: reset the once-per-process guard so each test gets a fresh run."""
    global _payout_batches_backfill_done
    _payout_batches_backfill_done = False


def _reset_one_shot_migration_guards_for_tests() -> None:
    """Test hook: reset all once-per-process migration guards.

    Use this in tests that exercise migration helpers across multiple
    invocations. In production each guard flips True after its helper's
    first successful (or no-op) run and stays True until the process
    restarts.
    """
    global _payout_batches_backfill_done, _checkin_snapshots_backfill_done
    global _booking_guest_names_backfill_done
    _payout_batches_backfill_done = False
    _checkin_snapshots_backfill_done = False
    _booking_guest_names_backfill_done = False


def _backfill_payout_batches_from_active_sources(conn: sqlite3.Connection) -> None:
    """Re-parse every active airbnb/booking source_file and re-persist its
    payout_batches / payout_batch_items / bank_transactions snapshot.

    Needed because the engine path historically did not write these tables;
    only the legacy report.main CLI path wrote them. Without this backfill,
    a prod DB whose CSVs were imported before the engine took over would
    show empty bank-drilldown data until the next CSV upload.

    Runs at most once per process — `get_connection()` (and therefore
    `_run_migrations()`) fires on every web request, but this helper is
    only useful as a one-shot catch-up. After the first invocation the
    payout_batches table is aligned, and any further drift is healed by
    the engine on regen (Task 2) or by source_registry on import (Task 3).
    Repeating the regex-heavy CSV parse per request would add hundreds of
    ms to every page load.

    Bank transactions are intentionally excluded — they are populated by
    `source_registry.import_uploaded_source` on bank-CSV upload. If prod's
    `bank_transactions` is ever out of sync, re-uploading the bank CSV is
    the documented remediation.
    """
    global _payout_batches_backfill_done
    if _payout_batches_backfill_done:
        return

    from report.engine import _persist_csv_payout_artifacts
    from report.verifier import (
        build_airbnb_payout_data,
        build_booking_payout_data,
        load_booking_csv,
    )

    rows = conn.execute(
        "SELECT id, source_type, original_name, content "
        "FROM source_files "
        "WHERE source_type IN ('airbnb', 'booking') AND is_active = 1"
    ).fetchall()
    if not rows:
        _payout_batches_backfill_done = True
        return

    try:
        airbnb_sources, booking_sources = [], []
        for row in rows:
            content = row["content"]
            if isinstance(content, memoryview):
                content = content.tobytes()
            source = {
                "id": row["id"],
                "original_name": row["original_name"],
                "content": bytes(content),
            }
            if row["source_type"] == "airbnb":
                airbnb_sources.append(source)
            else:
                booking_sources.append(source)

        airbnb_payout = (
            build_airbnb_payout_data(airbnb_sources)
            if airbnb_sources
            else {"reservation_map": {}, "all_batches_map": {}, "batches": [], "items": []}
        )
        booking_payout = (
            build_booking_payout_data(booking_sources)
            if booking_sources
            else {"reservation_map": {}, "batches": [], "items": []}
        )
        booking_index = load_booking_csv(booking_sources) if booking_sources else {}

        _persist_csv_payout_artifacts(
            conn,
            airbnb_payout_data=airbnb_payout,
            booking_payout_data=booking_payout,
            booking_index=booking_index,
            bank_rows_all=[],
            booking_bank_idx_all={},
        )
        conn.commit()
        _payout_batches_backfill_done = True
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "payout_batches backfill failed; bank UI may be stale until next regen",
            exc_info=True,
        )
        # Don't set the guard — let a later request retry, in case the
        # parse failure was transient (e.g., a partially-written CSV blob).
        conn.rollback()


def _seed_admin_user(conn: sqlite3.Connection) -> None:
    """Create initial admin user from env vars if users table is empty.

    Refuses to seed unless both RENTERO_USERNAME and RENTERO_PASSWORD are
    explicitly set. The legacy admin/admin fallback and the client1/client2
    test seeds were security hazards in production and have been removed —
    web.py's _validate_web_runtime_config raises a clear error if the env
    vars are missing.
    """
    row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
    if row["cnt"] > 0:
        return
    username = os.environ.get("RENTERO_USERNAME", "").strip()
    password = os.environ.get("RENTERO_PASSWORD", "")
    if not username or not password:
        return
    now_str = _now()
    pw_hash, pw_salt = hash_password(password)
    conn.execute(
        """INSERT INTO users (username, password_hash, password_salt, role, display_name,
           is_active, created_at, updated_at)
           VALUES (?, ?, ?, 'admin', 'Administrator', 1, ?, ?)""",
        (username, pw_hash, pw_salt, now_str, now_str),
    )


def _dedupe_source_files_by_type_sha256(conn: sqlite3.Connection) -> None:
    duplicate_groups = conn.execute(
        """SELECT source_type, sha256, MIN(id) AS keep_id, COUNT(*) AS row_count
           FROM source_files
           GROUP BY source_type, sha256
           HAVING COUNT(*) > 1"""
    ).fetchall()
    for group in duplicate_groups:
        source_type = group["source_type"]
        sha256 = group["sha256"]
        keep_id = int(group["keep_id"])
        duplicate_rows = conn.execute(
            """SELECT id FROM source_files
               WHERE source_type = ? AND sha256 = ? AND id <> ?""",
            (source_type, sha256, keep_id),
        ).fetchall()
        duplicate_ids = [int(row["id"]) for row in duplicate_rows]
        for dup_id in duplicate_ids:
            conn.execute(
                "UPDATE import_runs SET source_file_id = ? WHERE source_file_id = ?",
                (keep_id, dup_id),
            )
            conn.execute(
                "UPDATE import_runs SET duplicate_of_source_file_id = ? WHERE duplicate_of_source_file_id = ?",
                (keep_id, dup_id),
            )
        if duplicate_ids:
            conn.execute(
                f"DELETE FROM source_files WHERE id IN ({','.join('?' for _ in duplicate_ids)})",
                duplicate_ids,
            )
    conn.commit()


# --------------------------------------------------------------------------- #
#  CNB rates                                                                   #
# --------------------------------------------------------------------------- #

def get_cnb_rate(conn: sqlite3.Connection, date_str: str) -> float | None:
    """Return cached CNB rate for date, or None if not in DB."""
    row = conn.execute(
        "SELECT rate FROM cnb_rates WHERE date = ?", (date_str,)
    ).fetchone()
    return float(row["rate"]) if row else None


def save_cnb_rate(conn: sqlite3.Connection, date_str: str, rate: float) -> None:
    """Insert or replace a CNB rate."""
    conn.execute(
        "INSERT OR REPLACE INTO cnb_rates (date, rate, fetched_at) VALUES (?, ?, ?)",
        (date_str, rate, _now())
    )
    conn.commit()


def save_cnb_rates_bulk(conn: sqlite3.Connection, rates: dict[str, float]) -> None:
    """Bulk insert CNB rates: {date_str: rate}."""
    now = _now()
    conn.executemany(
        "INSERT OR REPLACE INTO cnb_rates (date, rate, fetched_at) VALUES (?, ?, ?)",
        [(d, r, now) for d, r in rates.items()]
    )
    conn.commit()


def get_all_cnb_rates(conn: sqlite3.Connection) -> dict[str, float]:
    """Return all stored CNB rates as {date_str: rate}."""
    rows = conn.execute("SELECT date, rate FROM cnb_rates").fetchall()
    return {r["date"]: float(r["rate"]) for r in rows}


# --------------------------------------------------------------------------- #
#  Hostify cache                                                               #
# --------------------------------------------------------------------------- #

def get_hostify_cache(
    conn: sqlite3.Connection, cache_key: str
) -> list[dict] | None:
    """
    Return cached Hostify data if not expired, else None.
    """
    row = conn.execute(
        "SELECT data, expires_at FROM hostify_cache WHERE cache_key = ?",
        (cache_key,)
    ).fetchone()
    if not row:
        return None
    if _now() > row["expires_at"]:
        return None  # expired
    return json.loads(row["data"])


def save_hostify_cache(
    conn: sqlite3.Connection,
    cache_key: str,
    data: list[dict],
    ttl_hours: int = 2,
) -> None:
    """Save Hostify API response to cache with TTL."""
    from datetime import timedelta
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO hostify_cache
           (cache_key, data, fetched_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (cache_key, json.dumps(data, default=str), _now(), expires_at)
    )
    conn.commit()


def invalidate_hostify_cache(conn: sqlite3.Connection, cache_key: str) -> None:
    conn.execute("DELETE FROM hostify_cache WHERE cache_key = ?", (cache_key,))
    conn.commit()


# --------------------------------------------------------------------------- #
#  Report rows                                                                 #
# --------------------------------------------------------------------------- #

def save_report_rows(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    rows: list[dict],
) -> None:
    """Replace the persisted report snapshot for a property-month."""
    _assert_report_month_mutable(conn, slug, year, month)
    now = _now()
    conn.execute(
        "DELETE FROM report_rows WHERE slug = ? AND year = ? AND month = ?",
        (slug, year, month),
    )
    if not rows:
        conn.commit()
        return
    conn.executemany(
        """INSERT OR REPLACE INTO report_rows
           (slug, year, month, confirmation_code, data, generated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (slug, year, month, r.get("confirmation_code", ""), json.dumps(r, default=str), now)
            for r in rows
        ]
    )
    conn.commit()


def get_report_rows(
    conn: sqlite3.Connection, slug: str, year: int, month: int
) -> list[dict]:
    """Retrieve saved report rows for a property-month."""
    rows = conn.execute(
        """SELECT data FROM report_rows
           WHERE slug = ? AND year = ? AND month = ?
           ORDER BY rowid""",
        (slug, year, month)
    ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def get_report_row_by_code(
    conn: sqlite3.Connection,
    confirmation_code: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict | None:
    """
    Find a report row by confirmation_code.
    When year/month are given, returns the exact match for that period
    (important for split-payout reservations in multiple months).
    Otherwise returns the most recent entry (highest year, then month).
    Returns a dict with the row data plus 'slug', 'year', 'month' keys.
    Returns None if not found.
    """
    if year and month:
        row = conn.execute(
            """SELECT slug, year, month, data
                 FROM report_rows
                WHERE confirmation_code = ? AND year = ? AND month = ?
                LIMIT 1""",
            (confirmation_code, year, month),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT slug, year, month, data
                 FROM report_rows
                WHERE confirmation_code = ?
                ORDER BY year DESC, month DESC
                LIMIT 1""",
            (confirmation_code,),
        ).fetchone()
    if row is None:
        return None
    result = json.loads(row["data"])
    result["slug"] = row["slug"]
    result["year"] = row["year"]
    result["month"] = row["month"]
    return result


# --------------------------------------------------------------------------- #
#  Report history                                                              #
# --------------------------------------------------------------------------- #

def log_report_generated(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    file_path: str,
    rows: list[dict],
) -> None:
    """Record a generated report in history."""
    _assert_report_month_mutable(conn, slug, year, month)
    from report.status import count_effective_verification_statuses
    counts = count_effective_verification_statuses(rows)
    conn.execute(
        """INSERT INTO report_history
           (slug, year, month, file_path, rows_count,
            matched, rozdil, chybi_csv, chybi_hostify, ke_kontrole, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug, year, month, file_path, len(rows),
            counts.get("MATCHED", 0),
            counts.get("ROZDÍL", 0),
            counts.get("CHYBÍ_V_CSV", 0),
            counts.get("CHYBÍ_V_HOSTIFY", 0),
            counts.get("KE KONTROLE", 0),
            _now(),
        )
    )
    conn.commit()


def get_report_history(
    conn: sqlite3.Connection,
    slug: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent report history, optionally filtered by slug."""
    if slug:
        rows = conn.execute(
            """SELECT * FROM report_history WHERE slug = ?
               ORDER BY generated_at DESC LIMIT ?""",
            (slug, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM report_history ORDER BY generated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
#  Report month state                                                          #
# --------------------------------------------------------------------------- #

def get_manual_overrides(
    conn: sqlite3.Connection, slug: str, year: int, month: int
) -> dict[str, dict]:
    """
    Return manual overrides for a property-month.
    Returns {confirmation_code: {field: value, ...}}
    """
    rows = conn.execute(
        """SELECT confirmation_code, field, value, note
           FROM manual_overrides
           WHERE slug = ? AND year = ? AND month = ?""",
        (slug, year, month)
    ).fetchall()
    result: dict[str, dict] = {}
    for r in rows:
        code = r["confirmation_code"]
        if code not in result:
            result[code] = {}
        result[code][r["field"]] = r["value"]
    return result


# --------------------------------------------------------------------------- #
#  Pending payments                                                            #
# --------------------------------------------------------------------------- #

def save_pending_payments(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    rows: list[dict],
) -> None:
    """
    Sync the month snapshot of CHYBÍ rows into pending_payments.

    Rows that are no longer missing for this original month are removed from the
    open pending set. Previously resolved records are preserved and never
    downgraded back to PENDING on re-generation.
    """
    now = _now()
    normalized_rows = []
    active_codes: set[str] = set()
    for r in rows:
        source = (r.get("source") or "").lower()
        if "airbnb" not in source and "booking" not in source:
            continue
        code = str(r.get("confirmation_code", "") or "").strip()
        if not code:
            continue
        active_codes.add(code)
        normalized_rows.append(
            (
                code,
                r.get("guest_name", ""),
                r.get("check_in", ""),
                r.get("check_out", ""),
                r.get("stay_label", ""),
                r.get("source", ""),
                r.get("czk_booked") or r.get("payout_czk") or 0.0,
                r.get("payout_gref", ""),
                r.get("batch_amount_czk_expected") or r.get("bank_amount_czk") or 0.0,
                r.get("batch_payout_date", "") or r.get("booking_payout_date", ""),
            )
        )

    existing_rows = conn.execute(
        """SELECT confirmation_code, status
             FROM pending_payments
            WHERE slug = ? AND original_year = ? AND original_month = ?""",
        (slug, year, month),
    ).fetchall()
    resolved_codes = {
        str(row["confirmation_code"])
        for row in existing_rows
        if str(row["status"] or "") == "RESOLVED"
    }
    stale_pending_codes = [
        str(row["confirmation_code"])
        for row in existing_rows
        if str(row["status"] or "") == "PENDING"
        and str(row["confirmation_code"] or "") not in active_codes
    ]
    if stale_pending_codes:
        conn.executemany(
            """DELETE FROM pending_payments
               WHERE slug = ? AND original_year = ? AND original_month = ?
                 AND confirmation_code = ? AND status = 'PENDING'""",
            [
                (slug, year, month, code)
                for code in stale_pending_codes
            ],
        )

    for (
        code,
        guest_name,
        check_in,
        check_out,
        stay_label,
        source,
        expected_czk,
        gref,
        batch_expected_czk,
        batch_payout_date,
    ) in normalized_rows:
        if code in resolved_codes:
            conn.execute(
                """UPDATE pending_payments
                   SET guest_name = ?, check_in = ?, check_out = ?, stay_label = ?,
                       source = ?, expected_czk = ?, gref = ?, batch_expected_czk = ?,
                       batch_payout_date = ?
                   WHERE slug = ? AND confirmation_code = ?""",
                (
                    guest_name,
                    check_in,
                    check_out,
                    stay_label,
                    source,
                    expected_czk,
                    gref,
                    batch_expected_czk,
                    batch_payout_date,
                    slug,
                    code,
                ),
            )
            continue
        conn.execute(
            """INSERT OR REPLACE INTO pending_payments
               (slug, original_year, original_month, confirmation_code,
                guest_name, check_in, check_out, stay_label, source,
                expected_czk, gref, batch_expected_czk, batch_payout_date, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
            (
                slug, year, month,
                code,
                guest_name,
                check_in,
                check_out,
                stay_label,
                source,
                expected_czk,
                gref,
                batch_expected_czk,
                batch_payout_date,
                now,
            )
        )
    conn.commit()


def get_pending_payments(
    conn: sqlite3.Connection,
    slug: str,
    status: str = "PENDING",
) -> list[dict]:
    """Return pending (or resolved) payment records for a property."""
    rows = conn.execute(
        """SELECT * FROM pending_payments
           WHERE slug = ? AND status = ?
           ORDER BY original_year, original_month, check_in""",
        (slug, status)
    ).fetchall()
    return [dict(r) for r in rows]


def get_resolved_pending_payments_for_month(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM pending_payments
           WHERE slug = ?
             AND status = 'RESOLVED'
             AND resolved_year = ?
             AND resolved_month = ?
           ORDER BY original_year, original_month, check_in""",
        (slug, int(year), int(month)),
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_pending_payment(
    conn: sqlite3.Connection,
    slug: str,
    confirmation_code: str,
    resolved_year: int,
    resolved_month: int,
    bank_datum: str,
    bank_amount_czk: float,
) -> None:
    """Mark a pending payment as RESOLVED once bank transaction is confirmed."""
    conn.execute(
        """UPDATE pending_payments
           SET status = 'RESOLVED',
               resolved_year = ?, resolved_month = ?,
               bank_datum = ?, bank_amount_czk = ?
           WHERE slug = ? AND confirmation_code = ?""",
        (resolved_year, resolved_month, bank_datum, bank_amount_czk,
         slug, confirmation_code)
    )
    conn.commit()


def set_manual_override(
    conn: sqlite3.Connection,
    slug: str, year: int, month: int,
    confirmation_code: str,
    field: str, value: str,
    note: str = "",
) -> None:
    """Set a manual override for a specific field of a reservation."""
    conn.execute(
        """INSERT OR REPLACE INTO manual_overrides
           (slug, year, month, confirmation_code, field, value, note, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (slug, year, month, confirmation_code, field, value, note, _now())
    )
    conn.commit()


# --------------------------------------------------------------------------- #
#  Source registry                                                             #
# --------------------------------------------------------------------------- #

def import_source_file(
    conn: sqlite3.Connection,
    source_type: str,
    original_name: str,
    content: bytes,
    *,
    active: bool = True,
) -> int:
    """
    Store one uploaded/imported source file in SQLite and return its row id.
    Deduplicates by SHA256: if the same content was already imported, returns
    the existing row id without creating a duplicate.
    """
    result = import_source_file_with_result(
        conn,
        source_type,
        original_name,
        content,
        active=active,
    )
    return int(result["source_file_id"])


def import_source_file_with_result(
    conn: sqlite3.Connection,
    source_type: str,
    original_name: str,
    content: bytes,
    *,
    active: bool = True,
    commit: bool = True,
) -> dict:
    """
    Store one uploaded/imported source file in SQLite.

    Returns:
      {
        "source_file_id": int,
        "is_duplicate": bool,
        "duplicate_of_source_file_id": int | None,
      }
    """
    import logging as _logging
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("content must be bytes-like")
    blob = bytes(content)
    sha256 = hashlib.sha256(blob).hexdigest()

    existing = conn.execute(
        "SELECT id FROM source_files WHERE source_type = ? AND sha256 = ?",
        (source_type, sha256),
    ).fetchone()
    if existing:
        existing_id = int(existing["id"])
        _logging.getLogger(__name__).info(
            "Source file '%s' already imported (sha256=%s…), skipping duplicate.",
            original_name, sha256[:12],
        )
        return {
            "source_file_id": existing_id,
            "is_duplicate": True,
            "duplicate_of_source_file_id": existing_id,
        }
    try:
        cur = conn.execute(
            """INSERT INTO source_files
               (source_type, original_name, content, sha256, imported_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_type, original_name, blob, sha256, _now(), 1 if active else 0),
        )
        if commit:
            conn.commit()
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT id FROM source_files WHERE source_type = ? AND sha256 = ?",
            (source_type, sha256),
        ).fetchone()
        if not existing:
            raise
        existing_id = int(existing["id"])
        return {
            "source_file_id": existing_id,
            "is_duplicate": True,
            "duplicate_of_source_file_id": existing_id,
        }
    return {
        "source_file_id": int(cur.lastrowid),
        "is_duplicate": False,
        "duplicate_of_source_file_id": None,
    }


def list_source_files(
    conn: sqlite3.Connection,
    source_type: str | None = None,
    *,
    active_only: bool = False,
) -> list[dict]:
    """Return imported source files, newest first."""
    sql = """SELECT id, source_type, original_name, sha256, imported_at, is_active,
                    length(content) AS size_bytes
             FROM source_files"""
    conditions = []
    params: list = []
    if source_type:
        conditions.append("source_type = ?")
        params.append(source_type)
    if active_only:
        conditions.append("is_active = 1")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY imported_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_source_file(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    include_content: bool = False,
) -> dict | None:
    fields = "id, source_type, original_name, sha256, imported_at, is_active, length(content) AS size_bytes"
    if include_content:
        fields = "id, source_type, original_name, sha256, imported_at, is_active, content, length(content) AS size_bytes"
    row = conn.execute(
        f"SELECT {fields} FROM source_files WHERE id = ?",
        (int(file_id),),
    ).fetchone()
    return dict(row) if row else None


def get_active_source_files(conn: sqlite3.Connection, source_type: str) -> list[dict]:
    """Return active source files including BLOB content, newest first."""
    rows = conn.execute(
        """SELECT id, source_type, original_name, content, sha256, imported_at
           FROM source_files
           WHERE source_type = ? AND is_active = 1
           ORDER BY imported_at DESC, id DESC""",
        (source_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_source_file_active(
    conn: sqlite3.Connection,
    file_id: int,
    is_active: bool,
) -> None:
    conn.execute(
        "UPDATE source_files SET is_active = ? WHERE id = ?",
        (1 if is_active else 0, file_id),
    )
    conn.commit()


def log_import_run(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_file_id: int | None,
    imported_by: str = "",
    duplicate_of_source_file_id: int | None = None,
    new_rows_count: int = 0,
    new_transactions_count: int = 0,
    new_reservations_count: int = 0,
    summary: dict | None = None,
    commit: bool = True,
) -> dict:
    now = _now()
    cur = conn.execute(
        """INSERT INTO import_runs
           (source_type, source_file_id, imported_by, imported_at,
            duplicate_of_source_file_id, new_rows_count, new_transactions_count,
            new_reservations_count, summary_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_type,
            source_file_id,
            imported_by,
            now,
            duplicate_of_source_file_id,
            int(new_rows_count or 0),
            int(new_transactions_count or 0),
            int(new_reservations_count or 0),
            json.dumps(summary or {}, default=str),
        ),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "SELECT * FROM import_runs WHERE id = ?",
        (int(cur.lastrowid),),
    ).fetchone()
    return dict(row) if row else {}


def list_import_runs(
    conn: sqlite3.Connection,
    source_type: str | None = None,
    *,
    limit: int = 50,
) -> list[dict]:
    sql = """
        SELECT ir.*,
               sf.original_name AS source_file_name,
               dsf.original_name AS duplicate_source_file_name
          FROM import_runs ir
          LEFT JOIN source_files sf ON sf.id = ir.source_file_id
          LEFT JOIN source_files dsf ON dsf.id = ir.duplicate_of_source_file_id
    """
    params: list = []
    if source_type:
        sql += " WHERE ir.source_type = ?"
        params.append(source_type)
    sql += " ORDER BY ir.imported_at DESC, ir.id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["summary"] = json.loads(item.get("summary_json") or "{}")
        except json.JSONDecodeError:
            item["summary"] = {}
        result.append(item)
    return result


def update_import_run_summary(
    conn: sqlite3.Connection,
    import_run_id: int,
    summary: dict,
) -> dict | None:
    conn.execute(
        "UPDATE import_runs SET summary_json = ? WHERE id = ?",
        (json.dumps(summary or {}, default=str), int(import_run_id)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM import_runs WHERE id = ?",
        (int(import_run_id),),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["summary"] = json.loads(item.get("summary_json") or "{}")
    except json.JSONDecodeError:
        item["summary"] = {}
    return item


def save_checkin_source_snapshot(
    conn: sqlite3.Connection,
    source_file_id: int,
    guest_rows: list[dict],
    groups: list[dict],
    *,
    commit: bool = True,
) -> None:
    """Persist parsed Evidence hostů rows/groups for one archived source file."""
    now = _now()
    clean_source_file_id = int(source_file_id)
    conn.execute("DELETE FROM checkin_guest_rows WHERE source_file_id = ?", (clean_source_file_id,))
    conn.execute("DELETE FROM checkin_reservations WHERE source_file_id = ?", (clean_source_file_id,))

    long_stay_ids = {
        str(group.get("reservation_id") or "")
        for group in groups
        if int(group.get("stay_nights") or 0) > 60
    }

    if guest_rows:
        conn.executemany(
            """INSERT INTO checkin_guest_rows
               (source_file_id, reservation_id, property_name, property_slug, check_in, check_out,
                guest_name, guest_age, is_minor, exempt_by_long_stay, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    clean_source_file_id,
                    str(row.get("reservation_id") or ""),
                    str(row.get("property_name") or ""),
                    str(row.get("property_slug") or ""),
                    str(row.get("check_in") or ""),
                    str(row.get("check_out") or ""),
                    str(row.get("guest_name") or ""),
                    row.get("guest_age"),
                    1 if row.get("guest_age") is not None and int(row.get("guest_age") or 0) < 18 else 0,
                    1 if str(row.get("reservation_id") or "") in long_stay_ids else 0,
                    now,
                )
                for row in guest_rows
                if row.get("reservation_id") and row.get("property_name") and row.get("check_in") and row.get("check_out")
            ],
        )

    if groups:
        conn.executemany(
            """INSERT INTO checkin_reservations
               (source_file_id, reservation_id, property_name, property_slug, check_in, check_out,
                assigned_year, assigned_month, stay_nights, total_guests, paying_guests, exempt_guests,
                missing_age_guests, guest_names_json, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_file_id, reservation_id) DO UPDATE SET
                 property_name=excluded.property_name,
                 property_slug=excluded.property_slug,
                 check_in=excluded.check_in,
                 check_out=excluded.check_out,
                 assigned_year=excluded.assigned_year,
                 assigned_month=excluded.assigned_month,
                 stay_nights=excluded.stay_nights,
                 total_guests=excluded.total_guests,
                 paying_guests=excluded.paying_guests,
                 exempt_guests=excluded.exempt_guests,
                 missing_age_guests=excluded.missing_age_guests,
                 guest_names_json=excluded.guest_names_json,
                 imported_at=excluded.imported_at""",
            [
                (
                    clean_source_file_id,
                    str(group.get("reservation_id") or ""),
                    str(group.get("property_name") or ""),
                    str(group.get("property_slug") or ""),
                    str(group.get("check_in") or ""),
                    str(group.get("check_out") or ""),
                    group.get("assigned_year"),
                    group.get("assigned_month"),
                    int(group.get("stay_nights") or 0),
                    int(group.get("total_guests") or 0),
                    int(group.get("paying_guests") or 0),
                    int(group.get("exempt_guests") or 0),
                    int(group.get("missing_age_guests") or 0),
                    json.dumps(group.get("guest_names") or [], ensure_ascii=True),
                    now,
                )
                for group in groups
                if group.get("reservation_id") and group.get("property_name") and group.get("check_in") and group.get("check_out")
            ],
        )
    if commit:
        conn.commit()


def _backfill_checkin_source_snapshots(conn: sqlite3.Connection) -> None:
    """Once-per-process: materialize legacy checkin source snapshots.

    `_run_migrations()` runs on every `get_connection()`, which fires per
    web request. Without the guard, every request would issue write
    statements and contend with the bulk_generation_runner subprocess
    for the SQLite write lock — surfacing as 'database is locked' 500s
    under load.
    """
    global _checkin_snapshots_backfill_done
    if _checkin_snapshots_backfill_done:
        return

    rows = conn.execute(
        """SELECT sf.id, sf.original_name, sf.content
             FROM source_files sf
            WHERE sf.source_type = 'checkin'
              AND NOT EXISTS (
                    SELECT 1
                      FROM checkin_reservations cr
                     WHERE cr.source_file_id = sf.id
                )"""
    ).fetchall()
    if not rows:
        _checkin_snapshots_backfill_done = True
        return
    try:
        from report.checkin import load_checkin_groups, load_checkin_guest_rows, prepare_checkin_groups_for_storage
        from report.config import get_all_properties, load_runtime_config
    except Exception:
        return

    try:
        try:
            properties = get_all_properties(load_runtime_config(None, db_conn=conn), active_only=True)
        except Exception:
            properties = []
        for row in rows:
            source = {
                "id": int(row["id"]),
                "original_name": row["original_name"],
                "content": row["content"],
            }
            groups = prepare_checkin_groups_for_storage(load_checkin_groups([source]), properties)
            property_slug_by_reservation = {
                str(group.get("reservation_id") or ""): str(group.get("property_slug") or "")
                for group in groups
            }
            guest_rows = [
                {
                    **guest_row,
                    "property_slug": property_slug_by_reservation.get(str(guest_row.get("reservation_id") or ""), ""),
                }
                for guest_row in load_checkin_guest_rows([source])
            ]
            save_checkin_source_snapshot(
                conn,
                int(row["id"]),
                guest_rows,
                groups,
                commit=False,
            )
        conn.commit()
        _checkin_snapshots_backfill_done = True
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "checkin source-snapshot backfill failed; will retry on next request",
            exc_info=True,
        )
        conn.rollback()


def _backfill_booking_payout_item_guest_names(conn: sqlite3.Connection) -> None:
    """Once-per-process: backfill legacy Booking payout items with guest names.

    Same per-request-write contention story as the checkin backfill above.
    """
    global _booking_guest_names_backfill_done
    if _booking_guest_names_backfill_done:
        return
    try:
        fill_missing_payout_item_guest_names(conn, "booking", commit=True)
        _booking_guest_names_backfill_done = True
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "booking guest-names backfill failed; will retry on next request",
            exc_info=True,
        )
        conn.rollback()


def _migrate_month_assignments_scope(conn: sqlite3.Connection) -> None:
    """Migrate reservation_month_assignments to month-scoped unique constraint.

    Old schema: UNIQUE(slug, confirmation_code)  — one assignment per code
    New schema: UNIQUE(slug, confirmation_code, original_year, original_month)
                + is_adjustment, batch_ref columns

    This allows separate assignments for a main reservation and its payout
    adjustments (which may live in different months under the same code).
    """
    cols = {
        r["name"]
        for r in conn.execute(
            "PRAGMA table_info(reservation_month_assignments)"
        ).fetchall()
    }
    if "is_adjustment" in cols:
        return  # already migrated

    conn.execute("""CREATE TABLE IF NOT EXISTS _rma_new (
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
        is_adjustment     INTEGER NOT NULL DEFAULT 0,
        batch_ref         TEXT NOT NULL DEFAULT '',
        UNIQUE(slug, confirmation_code, original_year, original_month)
    )""")
    conn.execute("""INSERT INTO _rma_new
        (id, slug, confirmation_code, target_year, target_month,
         original_year, original_month, reason, actor, created_at,
         reverted_at, reverted_by, is_adjustment, batch_ref)
        SELECT id, slug, confirmation_code, target_year, target_month,
               original_year, original_month, reason, actor, created_at,
               reverted_at, reverted_by, 0, ''
          FROM reservation_month_assignments""")
    conn.execute("DROP TABLE reservation_month_assignments")
    conn.execute(
        "ALTER TABLE _rma_new RENAME TO reservation_month_assignments"
    )
    conn.commit()


def list_checkin_reservations(
    conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    year: int | None = None,
    month: int | None = None,
    active_only: bool = True,
    source_file_id: int | None = None,
    overlap_year: int | None = None,
    overlap_month: int | None = None,
    latest_only: bool = False,
) -> list[dict]:
    sql = """
        SELECT cr.*,
               sf.original_name AS source_file_name,
               sf.is_active AS source_file_active
          FROM checkin_reservations cr
          JOIN source_files sf ON sf.id = cr.source_file_id
    """
    clauses: list[str] = []
    params: list = []
    if active_only:
        clauses.append("sf.is_active = 1")
    if slug:
        clauses.append("cr.property_slug = ?")
        params.append(slug)
    if year is not None:
        clauses.append("cr.assigned_year = ?")
        params.append(int(year))
    if month is not None:
        clauses.append("cr.assigned_month = ?")
        params.append(int(month))
    if source_file_id is not None:
        clauses.append("cr.source_file_id = ?")
        params.append(int(source_file_id))
    if overlap_year is not None and overlap_month is not None:
        month_start = date(int(overlap_year), int(overlap_month), 1)
        last_day = monthrange(int(overlap_year), int(overlap_month))[1]
        month_end = date(int(overlap_year), int(overlap_month), last_day) + timedelta(days=1)
        clauses.append("cr.check_in < ?")
        params.append(month_end.isoformat())
        clauses.append("cr.check_out > ?")
        params.append(month_start.isoformat())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY sf.imported_at DESC, cr.source_file_id DESC, cr.reservation_id, cr.check_in, cr.check_out"
    rows = conn.execute(sql, params).fetchall()
    result = []
    seen_reservation_ids: set[str] = set()
    for row in rows:
        item = dict(row)
        reservation_id = str(item.get("reservation_id") or "")
        if latest_only and active_only and reservation_id:
            if reservation_id in seen_reservation_ids:
                continue
            seen_reservation_ids.add(reservation_id)
        try:
            item["guest_names"] = json.loads(item.get("guest_names_json") or "[]")
        except json.JSONDecodeError:
            item["guest_names"] = []
        result.append(item)
    return result


def get_active_checkin_reservation_ids(conn: sqlite3.Connection) -> set[str]:
    rows = list_checkin_reservations(conn, active_only=True, latest_only=True)
    return {str(row["reservation_id"]) for row in rows if row.get("reservation_id")}


def replace_checkin_match_audit(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    records: list[dict],
) -> None:
    """Replace persisted Evidence hostů audit snapshot for one property-month."""
    now = _now()
    conn.execute(
        "DELETE FROM checkin_match_audit WHERE slug = ? AND year = ? AND month = ?",
        (slug, int(year), int(month)),
    )
    if records:
        conn.executemany(
            """INSERT INTO checkin_match_audit
               (slug, year, month, record_type, confirmation_code, guest_name, source, check_in, check_out,
                hostify_reservation_id, checkin_source_file_id, checkin_reservation_id, checkin_property_name,
                match_status, before_paying_guests, before_exempt_guests, after_paying_guests, after_exempt_guests,
                checkin_total_guests, checkin_missing_age_guests, overwritten_fields_json, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    slug,
                    int(year),
                    int(month),
                    str(record.get("record_type") or ""),
                    str(record.get("confirmation_code") or ""),
                    str(record.get("guest_name") or ""),
                    str(record.get("source") or ""),
                    str(record.get("check_in") or ""),
                    str(record.get("check_out") or ""),
                    str(record.get("hostify_reservation_id") or ""),
                    record.get("checkin_source_file_id"),
                    str(record.get("checkin_reservation_id") or ""),
                    str(record.get("checkin_property_name") or ""),
                    str(record.get("match_status") or ""),
                    record.get("before_paying_guests"),
                    record.get("before_exempt_guests"),
                    record.get("after_paying_guests"),
                    record.get("after_exempt_guests"),
                    record.get("checkin_total_guests"),
                    record.get("checkin_missing_age_guests"),
                    json.dumps(record.get("overwritten_fields") or {}, default=str),
                    json.dumps(record.get("detail") or {}, default=str),
                    now,
                )
                for record in records
            ],
        )
    conn.commit()


def list_checkin_match_audit(
    conn: sqlite3.Connection,
    *,
    slug: str,
    year: int,
    month: int,
    limit: int = 500,
) -> list[dict]:
    rows = conn.execute(
        """SELECT *
             FROM checkin_match_audit
            WHERE slug = ? AND year = ? AND month = ?
            ORDER BY record_type, check_in, check_out, checkin_reservation_id, confirmation_code, id""",
        (slug, int(year), int(month)),
    ).fetchall()
    result = []
    for row in rows[: int(limit)]:
        item = dict(row)
        try:
            item["overwritten_fields"] = json.loads(item.get("overwritten_fields_json") or "{}")
        except json.JSONDecodeError:
            item["overwritten_fields"] = {}
        try:
            item["detail"] = json.loads(item.get("detail_json") or "{}")
        except json.JSONDecodeError:
            item["detail"] = {}
        result.append(item)
    return result


def create_report_month_notification(
    conn: sqlite3.Connection,
    *,
    slug: str,
    year: int,
    month: int,
    event_type: str,
    source_type: str = "",
    message: str,
    payload: dict | None = None,
) -> dict:
    now = _now()
    cur = conn.execute(
        """INSERT INTO report_month_notifications
           (slug, year, month, event_type, source_type, message, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            int(year),
            int(month),
            event_type,
            source_type,
            message,
            json.dumps(payload or {}, default=str),
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM report_month_notifications WHERE id = ?",
        (int(cur.lastrowid),),
    ).fetchone()
    return dict(row) if row else {}


def list_report_month_notifications(
    conn: sqlite3.Connection,
    *,
    slug: str | None = None,
    year: int | None = None,
    month: int | None = None,
    created_after: str | None = None,
    limit: int = 20,
) -> list[dict]:
    sql = "SELECT * FROM report_month_notifications"
    clauses: list[str] = []
    params: list = []
    if slug:
        clauses.append("slug = ?")
        params.append(slug)
    if year is not None:
        clauses.append("year = ?")
        params.append(int(year))
    if month is not None:
        clauses.append("month = ?")
        params.append(int(month))
    if created_after:
        clauses.append("created_at > ?")
        params.append(str(created_after))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        result.append(item)
    return result


# --------------------------------------------------------------------------- #
#  Batch / bank drill-down storage                                             #
# --------------------------------------------------------------------------- #

def save_payout_batches(
    conn: sqlite3.Connection,
    channel: str,
    batches: list[dict],
    *,
    commit: bool = True,
) -> None:
    """Upsert parsed payout batches for later UI drill-down."""
    now = _now()
    conn.executemany(
        """INSERT INTO payout_batches
           (channel, batch_ref, batch_match_ref, payout_date, credited_date,
            amount_czk, amount_eur, implied_rate, source_name, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel, batch_ref) DO UPDATE SET
             batch_match_ref=excluded.batch_match_ref,
             payout_date=excluded.payout_date,
             credited_date=excluded.credited_date,
             amount_czk=excluded.amount_czk,
             amount_eur=excluded.amount_eur,
             implied_rate=excluded.implied_rate,
             source_name=excluded.source_name,
             updated_at=excluded.updated_at""",
        [
            (
                channel,
                b.get("batch_ref", ""),
                b.get("batch_match_ref", ""),
                b.get("payout_date", ""),
                b.get("credited_date", ""),
                b.get("amount_czk"),
                b.get("amount_eur"),
                b.get("implied_rate"),
                b.get("source_name", ""),
                now,
            )
            for b in batches
            if b.get("batch_ref")
        ],
    )
    if commit:
        conn.commit()


def save_payout_batch_items(
    conn: sqlite3.Connection,
    channel: str,
    items: list[dict],
    *,
    commit: bool = True,
) -> None:
    """Replace known item slots for a payout batch snapshot."""
    if not items:
        return
    rows = [
        (
            channel,
            item.get("batch_ref", ""),
            int(item.get("item_index", 0)),
            item.get("item_type", ""),
            item.get("confirmation_code", ""),
            item.get("guest_name", ""),
            item.get("listing_name", ""),
            item.get("property_id", ""),
            item.get("amount_eur"),
            item.get("amount_czk"),
            item.get("check_in", ""),
            item.get("check_out", ""),
            item.get("source_name", ""),
        )
        for item in items
        if item.get("batch_ref")
    ]
    conn.executemany(
        """INSERT INTO payout_batch_items
           (channel, batch_ref, item_index, item_type, confirmation_code,
            guest_name, listing_name, property_id, amount_eur, amount_czk,
            check_in, check_out, source_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel, batch_ref, item_index) DO UPDATE SET
             item_type=excluded.item_type,
             confirmation_code=excluded.confirmation_code,
             guest_name=excluded.guest_name,
             listing_name=excluded.listing_name,
             property_id=excluded.property_id,
             amount_eur=excluded.amount_eur,
             amount_czk=excluded.amount_czk,
             check_in=excluded.check_in,
             check_out=excluded.check_out,
             source_name=excluded.source_name""",
        rows,
    )
    if commit:
        conn.commit()


def fill_missing_payout_item_guest_names(
    conn: sqlite3.Connection,
    channel: str,
    *,
    guest_names_by_code: dict[str, str] | None = None,
    commit: bool = True,
) -> int:
    """Materialize missing payout-item guest names from known reservation identity."""
    normalized_channel = str(channel or "").strip().lower()
    if not normalized_channel:
        return 0

    missing_code_rows = conn.execute(
        """SELECT DISTINCT confirmation_code
             FROM payout_batch_items
            WHERE channel = ?
              AND confirmation_code <> ''
              AND COALESCE(TRIM(guest_name), '') = ''""",
        (normalized_channel,),
    ).fetchall()
    missing_codes = [str(row["confirmation_code"]) for row in missing_code_rows if row["confirmation_code"]]
    if not missing_codes:
        return 0

    resolved_names: dict[str, str] = {}
    for code, name in (guest_names_by_code or {}).items():
        normalized_code = str(code or "").strip()
        normalized_name = str(name or "").strip()
        if normalized_code and normalized_name:
            resolved_names[normalized_code] = normalized_name

    unresolved_codes = [code for code in missing_codes if code not in resolved_names]
    if unresolved_codes:
        placeholders = ",".join("?" for _ in unresolved_codes)
        hostify_rows = conn.execute(
            f"""SELECT confirmation_code, guest_name
                  FROM hostify_reservations
                 WHERE confirmation_code IN ({placeholders})
                   AND COALESCE(TRIM(guest_name), '') <> ''""",
            unresolved_codes,
        ).fetchall()
        for row in hostify_rows:
            code = str(row["confirmation_code"] or "").strip()
            name = str(row["guest_name"] or "").strip()
            if code and name and code not in resolved_names:
                resolved_names[code] = name

    updated = 0
    for code in missing_codes:
        name = resolved_names.get(code, "")
        if not name:
            continue
        cursor = conn.execute(
            """UPDATE payout_batch_items
                  SET guest_name = ?
                WHERE channel = ?
                  AND confirmation_code = ?
                  AND COALESCE(TRIM(guest_name), '') = ''""",
            (name, normalized_channel, code),
        )
        updated += int(cursor.rowcount or 0)

    if commit and updated:
        conn.commit()
    return updated


def save_bank_transactions(
    conn: sqlite3.Connection,
    channel: str,
    rows: list[dict],
    *,
    commit: bool = True,
) -> None:
    """Upsert normalized bank transactions used for matching."""
    now = _now()
    conn.executemany(
        """INSERT INTO bank_transactions
           (tx_key, channel, tx_id, datum, amount_czk, gref, property_id,
            zprava, source_name, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tx_key) DO UPDATE SET
             channel=excluded.channel,
             tx_id=excluded.tx_id,
             datum=excluded.datum,
             amount_czk=excluded.amount_czk,
             gref=excluded.gref,
             property_id=excluded.property_id,
             zprava=excluded.zprava,
             source_name=excluded.source_name,
             updated_at=excluded.updated_at""",
        [
            (
                r.get("tx_key", ""),
                channel,
                r.get("tx_id", ""),
                r.get("datum").isoformat() if hasattr(r.get("datum"), "isoformat") else (r.get("datum") or ""),
                r.get("amount_czk"),
                r.get("gref", ""),
                r.get("property_id", ""),
                r.get("zprava", ""),
                r.get("source_name", ""),
                now,
            )
            for r in rows
            if r.get("tx_key")
        ],
    )
    if commit:
        conn.commit()


def save_payout_batch_bank_matches(
    conn: sqlite3.Connection,
    channel: str,
    matches: list[dict],
) -> None:
    """Persist batch ↔ bank transaction links for future drill-down screens."""
    if not matches:
        return
    now = _now()
    conn.executemany(
        """INSERT INTO payout_batch_bank_matches
           (channel, batch_ref, tx_key, match_method, matched_amount_czk, matched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel, batch_ref, tx_key) DO UPDATE SET
             match_method=excluded.match_method,
             matched_amount_czk=excluded.matched_amount_czk,
             matched_at=excluded.matched_at""",
        [
            (
                channel,
                m.get("batch_ref", ""),
                m.get("tx_key", ""),
                m.get("match_method", ""),
                m.get("matched_amount_czk"),
                now,
            )
            for m in matches
            if m.get("batch_ref") and m.get("tx_key")
        ],
    )
    conn.commit()


def run_integrity_audit(conn: sqlite3.Connection) -> list[dict]:
    """Find confirmation_codes appearing in multiple report_rows snapshots.

    Writes findings to integrity_audit (event log — one row per call when a
    dupe exists) and returns the same list. Empty codes are ignored.
    """
    rows = conn.execute("""
        SELECT confirmation_code,
               GROUP_CONCAT(slug || '/' || year || '-' || printf('%02d', month), ',') AS occurrences,
               COUNT(*) AS occ_count
        FROM report_rows
        WHERE confirmation_code <> ''
        GROUP BY confirmation_code
        HAVING occ_count > 1
        ORDER BY occ_count DESC, confirmation_code
    """).fetchall()
    findings = [dict(r) for r in rows]
    if findings:
        now = _now()
        conn.executemany(
            "INSERT INTO integrity_audit (confirmation_code, occurrences, detected_at) VALUES (?, ?, ?)",
            [(f["confirmation_code"], f["occurrences"], now) for f in findings],
        )
        conn.commit()
    return findings


# --------------------------------------------------------------------------- #
#  Accounting entries (Hlavní kniha účet 315)                                   #
# --------------------------------------------------------------------------- #

def save_accounting_entries(
    conn: sqlite3.Connection,
    entries: list[dict],
    source_file_id: int,
    *,
    commit: bool = True,
) -> None:
    if not entries:
        return
    now = _now()
    # Clear previous entries from the same source file to allow re-import
    conn.execute("DELETE FROM accounting_entries WHERE source_file_id = ?", (source_file_id,))
    conn.executemany(
        """INSERT INTO accounting_entries
           (source_file_id, doc, doc_type, datum, popis, castka,
            objekt, objekt_raw, mesic, channel, stredisko, ucet, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                source_file_id,
                e.get("doc", ""),
                e.get("doc_type", ""),
                e.get("datum"),
                e.get("popis"),
                e.get("castka", 0.0),
                e.get("objekt"),
                e.get("objekt_raw"),
                e.get("mesic"),
                e.get("channel"),
                e.get("stredisko"),
                e.get("ucet"),
                now,
            )
            for e in entries
        ],
    )
    if commit:
        conn.commit()


def get_accounting_entries(
    conn: sqlite3.Connection,
    *,
    channel: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    clauses = [
        "ae.source_file_id IN (SELECT id FROM source_files WHERE is_active = 1)"
    ]
    params: list = []
    if channel:
        clauses.append("ae.channel = ?")
        params.append(channel)
    if year is not None and month is not None:
        mesic = f"{year:04d}-{month:02d}"
        clauses.append("ae.mesic = ?")
        params.append(mesic)
    where = " WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT ae.* FROM accounting_entries ae{where} ORDER BY ae.mesic, ae.objekt, ae.datum",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
#  Středisko map                                                               #
# --------------------------------------------------------------------------- #

def save_stredisko_map(
    conn: sqlite3.Connection,
    entries: list[dict],
    source_file_id: int,
    *,
    commit: bool = True,
) -> None:
    if not entries:
        return
    now = _now()
    conn.execute("DELETE FROM stredisko_map WHERE source_file_id = ?", (source_file_id,))
    conn.executemany(
        """INSERT INTO stredisko_map (source_file_id, zkratka, popis, updated_at)
           VALUES (?, ?, ?, ?)""",
        [(source_file_id, e["zkratka"], e["popis"], now) for e in entries],
    )
    if commit:
        conn.commit()


def get_stredisko_map(conn: sqlite3.Connection) -> dict:
    """Return {normalized_zkratka: popis} from the latest stredisko import."""
    from report.accounting import normalize_objekt
    rows = conn.execute("SELECT zkratka, popis FROM stredisko_map").fetchall()
    return {normalize_objekt(r["zkratka"]): r["popis"] for r in rows}


def list_stredisko_entries(conn: sqlite3.Connection) -> list[dict]:
    """Return all stredisko entries as list of dicts {zkratka, popis}."""
    rows = conn.execute(
        "SELECT zkratka, popis FROM stredisko_map ORDER BY zkratka"
    ).fetchall()
    return [{"zkratka": r["zkratka"], "popis": r["popis"]} for r in rows]


def upsert_stredisko_entry(
    conn: sqlite3.Connection, zkratka: str, popis: str, *, commit: bool = True
) -> None:
    """Insert or replace a single stredisko entry (source_file_id=0 = manual)."""
    now = _now()
    conn.execute("DELETE FROM stredisko_map WHERE zkratka = ?", (zkratka,))
    conn.execute(
        "INSERT INTO stredisko_map (source_file_id, zkratka, popis, updated_at) VALUES (0, ?, ?, ?)",
        (zkratka, popis, now),
    )
    if commit:
        conn.commit()


def delete_stredisko_entry(
    conn: sqlite3.Connection, zkratka: str, *, commit: bool = True
) -> None:
    """Delete a stredisko entry by zkratka."""
    conn.execute("DELETE FROM stredisko_map WHERE zkratka = ?", (zkratka,))
    if commit:
        conn.commit()


# --------------------------------------------------------------------------- #
#  Hostify reservation snapshots                                               #
# --------------------------------------------------------------------------- #

def save_hostify_reservations(
    conn: sqlite3.Connection,
    reservations: list[dict],
) -> None:
    """Persist normalized Hostify reservations for late linking by booking id."""
    if not reservations:
        return
    now = _now()
    conn.executemany(
        """INSERT INTO hostify_reservations
           (confirmation_code, reservation_id, source, status, guest_name,
            check_in, check_out, assigned_year, assigned_month, listing_nickname,
            payload_json, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(confirmation_code) DO UPDATE SET
             reservation_id=excluded.reservation_id,
             source=excluded.source,
             status=excluded.status,
             guest_name=excluded.guest_name,
             check_in=excluded.check_in,
             check_out=excluded.check_out,
             assigned_year=excluded.assigned_year,
             assigned_month=excluded.assigned_month,
             listing_nickname=excluded.listing_nickname,
             payload_json=excluded.payload_json,
             last_seen_at=excluded.last_seen_at""",
        [
            (
                r.get("confirmation_code", ""),
                r.get("reservation_id", ""),
                r.get("source", ""),
                r.get("status", ""),
                r.get("guest_name", ""),
                r.get("check_in", ""),
                r.get("check_out", ""),
                r.get("assigned_year"),
                r.get("assigned_month"),
                r.get("listing_nickname", ""),
                json.dumps(r, default=str),
                now,
                now,
            )
            for r in reservations
            if r.get("confirmation_code")
        ],
    )
    conn.commit()


def get_hostify_reservations_by_codes(
    conn: sqlite3.Connection,
    codes: list[str],
    *,
    listing_nicknames: list[str] | None = None,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, dict]:
    """Return stored Hostify reservations keyed by confirmation_code."""
    clean_codes = [c for c in dict.fromkeys(codes) if c]
    if not clean_codes:
        return {}

    clauses = [f"confirmation_code IN ({','.join('?' for _ in clean_codes)})"]
    params: list = list(clean_codes)

    if listing_nicknames:
        clean_names = [n for n in dict.fromkeys(listing_nicknames) if n]
        if clean_names:
            clauses.append(f"listing_nickname IN ({','.join('?' for _ in clean_names)})")
            params.extend(clean_names)
    if year is not None:
        clauses.append("assigned_year = ?")
        params.append(year)
    if month is not None:
        clauses.append("assigned_month = ?")
        params.append(month)

    rows = conn.execute(
        f"""SELECT confirmation_code, payload_json
            FROM hostify_reservations
            WHERE {' AND '.join(clauses)}""",
        params,
    ).fetchall()
    return {
        r["confirmation_code"]: json.loads(r["payload_json"])
        for r in rows
    }


def get_hostify_reservations_for_listing_month(
    conn: sqlite3.Connection,
    *,
    listing_nicknames: list[str],
    year: int,
    month: int,
) -> list[dict]:
    """Return stored Hostify reservation payloads for listing nicknames in a month."""
    clean_names = [n for n in dict.fromkeys(listing_nicknames) if n]
    if not clean_names:
        return []
    rows = conn.execute(
        f"""SELECT payload_json
            FROM hostify_reservations
            WHERE listing_nickname IN ({','.join('?' for _ in clean_names)})
              AND assigned_year = ?
              AND assigned_month = ?
            ORDER BY check_in, confirmation_code""",
        [*clean_names, int(year), int(month)],
    ).fetchall()
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["payload_json"]))
        except json.JSONDecodeError:
            continue
    return result


def get_hostify_reservation_counts(
    conn: sqlite3.Connection,
    *,
    listing_nicknames: list[str] | None = None,
    months: list[tuple[int, int]] | None = None,
) -> list[dict]:
    """
    Return grouped Hostify reservation counts by listing nickname and month.

    Output rows:
      {
        "listing_nickname": str,
        "year": int,
        "month": int,
        "reservation_count": int,
      }
    """
    sql = """SELECT listing_nickname,
                    assigned_year AS year,
                    assigned_month AS month,
                    COUNT(*) AS reservation_count
             FROM hostify_reservations"""
    clauses: list[str] = []
    params: list = []

    if listing_nicknames:
        clean_names = [n for n in dict.fromkeys(listing_nicknames) if n]
        if clean_names:
            clauses.append(f"listing_nickname IN ({','.join('?' for _ in clean_names)})")
            params.extend(clean_names)

    if months:
        clean_months = [(int(y), int(m)) for y, m in months]
        if clean_months:
            clauses.append(
                "(" + " OR ".join("(assigned_year = ? AND assigned_month = ?)" for _ in clean_months) + ")"
            )
            for year, month in clean_months:
                params.extend([year, month])

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += """
        GROUP BY listing_nickname, assigned_year, assigned_month
        ORDER BY listing_nickname, assigned_year, assigned_month
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
#  Report object config                                                        #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  Split transactions                                                          #
# --------------------------------------------------------------------------- #

def get_split_transactions(conn, slug):
    """Return all split transaction records for a property."""
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
