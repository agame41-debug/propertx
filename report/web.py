"""
report/web.py — Rentero webový přehled (FastAPI + Jinja2 + HTMX)

Spuštění:
    python run_web.py
    # nebo
    uvicorn report.web:app --reload --port 8000
"""

# Setup logging first, before any other imports
from report.logging_service import setup_logging
setup_logging()

# Load .env at module import time
import os
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass  # dotenv not installed, skip

import asyncio
import json
import os as _os2
import re
import secrets
import signal
import subprocess
import sys

# Auto-reap zombie child processes (generation runners)
if _os2.name != "nt":
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from report.config import (
    get_airbnb_listing_names,
    get_all_properties,
    get_booking_config,
    get_hostify_listing_names,
    load_runtime_config,
    sync_property_to_db,
)
from hostify_api import HostifyHttpError
from report.hostify_inventory import sync_hostify_inventory
from report.source_registry import SOURCE_TYPES, import_uploaded_source, validate_source_type
from report.summary import build_report_summary
from report import web_support as _web_support
from report.engine import run_generation_background
from report.routes import register_all as register_route_modules
from report.db import (
    get_connection,
    # users (RBAC)
    authenticate_user, get_user_by_id, get_user_by_username,
    list_users, create_user, update_user_record, change_password, delete_user,
    get_user_property_slugs, set_user_properties, get_users_for_property,
    ROLE_ADMIN, ROLE_MANAGER, ROLE_CLIENT,
    # clients
    get_all_clients, get_client, save_client,
    # expenses
    get_expenses, add_expense, update_expense, delete_expense, get_expense,
    # categories
    get_expense_categories, add_expense_category, delete_expense_category,
    # reports
    get_report_rows, get_report_history,
    get_hostify_reservation_counts,
    list_checkin_match_audit,
    list_checkin_reservations,
    get_report_object_aliases,
    get_resolved_pending_payments_for_month,
    get_source_file,
    list_import_runs,
    list_report_month_notifications,
    list_source_files,
    set_source_file_active,
    update_import_run_summary,
    # month state
    get_report_month_state, get_report_month_states, mark_report_month_stale,
    MONTH_DATA_STATE_EMPTY,
    set_report_month_locked,
    MONTH_STATUS_LOCKED,
    create_report_generation_job,
    create_report_month_notification,
    create_bulk_generation_run,
    get_active_report_generation_job,
    get_active_bulk_generation_run,
    get_bulk_generation_run,
    get_latest_bulk_generation_run,
    get_latest_report_generation_job,
    GENERATION_JOB_PENDING,
    GENERATION_JOB_RUNNING,
    finish_report_generation_job,
    GENERATION_JOB_FAILED,
    BULK_GENERATION_PENDING,
    BULK_GENERATION_RUNNING,
    finish_bulk_generation_run,
    # overrides (Phase 4)
    create_override_event, get_override_events, revert_override_event,
    apply_overrides_to_rows,
    OVERRIDE_FIELD_LABELS, VERIFICATION_STATUS_OPTIONS, normalize_override_value,
    # panel lookup
    get_report_row_by_code,
    # reservation controls
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
    # split transactions
    create_split_transaction,
    delete_split_transaction,
    get_split_transactions,
    get_split_transactions_for_code,
    # accounting
    save_accounting_entries,
    get_accounting_entries,
    save_stredisko_map,
    get_stredisko_map,
    list_stredisko_entries,
    upsert_stredisko_entry,
    delete_stredisko_entry,
)
from report.web_support import (
    _build_dashboard_maps,
    _build_dashboard_view_model,
    _create_import_impact_notification,
    _db_path_for_connection,
    _decorate_bulk_generation_run,
    _ensure_month_open,
    _filter_bank_rows,
    _format_bulk_generation_month,
    _format_import_summary,
    _format_inventory_sync_summary,
    _get_active_properties,
    _import_change_lines,
    _inventory_redirect_path,
    _latest_report_for_month,
    _load_all_bank_transactions_for_codes,
    _load_bank_rows_with_drilldown,
    _load_reconciliation_view,
    _month_has_data,
    _pop_flash,
    _prepare_rows_for_display,
    _property_listing_names,
    _render_property_template,
    _resolve_inventory_bulk_run,
    _run_report_generation,
    _set_flash,
    _source_type_label,
    _sources_redirect,
    _start_bulk_generation_runner,
    _start_report_generation_runner,
    _summarize_generation_error,
    _truncate_generation_detail,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES_DIR = os.path.join(_BASE_DIR, "templates")
_CONFIG_PATH = os.path.join(_BASE_DIR, "config", "properties.json")
_DB_PATH = os.path.join(_BASE_DIR, "cache", "rentero.db")
_FALLBACK_SESSION_SECRET = "__rentero_insecure_runtime_secret__"
_FALLBACK_USERNAME = "admin"
_FALLBACK_PASSWORD = "admin"
_CSRF_SESSION_KEY = "_csrf_token"
_CSRF_FORM_FIELD = "csrf_token"
_CSRF_HEADER = "x-csrf-token"


def _enforce_single_worker() -> None:
    """Hard-fail if started under uvicorn workers > 1.

    The Hostify sync loop, the import-triggered regen thread, and the
    bulk_generation_runner subprocess all assume one process per host.
    Multiple workers would race on report_rows writes, send N parallel
    requests to Hostify, and split cnb._rate_cache across processes.

    Detection relies on uvicorn's WEB_CONCURRENCY / UVICORN_NUM_WORKERS
    env vars (set by the launcher) or a count of sibling uvicorn workers
    sharing this binding. We only block on values we recognize as >1.
    """
    for var in ("WEB_CONCURRENCY", "UVICORN_NUM_WORKERS"):
        raw = os.environ.get(var, "").strip()
        if raw.isdigit() and int(raw) > 1:
            raise RuntimeError(
                f"Rentero must run with a single uvicorn worker; {var}={raw} "
                "would start parallel Hostify sync loops and race on SQLite writes."
            )


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    _validate_web_runtime_config()
    _enforce_single_worker()
    from report.hostify_sync import HostifySyncTask
    _sync_config = load_runtime_config(_CONFIG_PATH, db_conn=get_connection(_DB_PATH))
    _sync_task_obj = HostifySyncTask(
        db_path=_DB_PATH,
        config=_sync_config,
        config_path=_CONFIG_PATH,
    )
    _bg_task = asyncio.create_task(_sync_task_obj.run_loop())
    try:
        yield
    finally:
        _bg_task.cancel()
        try:
            await _bg_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Rentero", lifespan=_app_lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("RENTERO_SESSION_SECRET") or _FALLBACK_SESSION_SECRET,
)


class HTMXPartialMiddleware(BaseHTTPMiddleware):
    """When HTMX requests a boosted page, strip the shell and return only <main> content."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        is_htmx_boost = (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") == "true"
            and response.headers.get("content-type", "").startswith("text/html")
        )
        if not is_htmx_boost:
            return response
        # Read body from streaming response
        body_chunks = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                body_chunks.append(chunk)
            else:
                body_chunks.append(chunk.encode("utf-8"))
        body = b"".join(body_chunks).decode("utf-8")
        import re as _re
        m = _re.search(r'<main[^>]*id="content"[^>]*>(.*)</main>', body, _re.DOTALL)
        if m:
            inner = m.group(1)
            tm = _re.search(r'<title>(.*?)</title>', body)
            title_tag = f'<title>{tm.group(1)}</title>' if tm else ''
            new_body = title_tag + inner
            return HTMLResponse(content=new_body, status_code=response.status_code)
        return HTMLResponse(content=body, status_code=response.status_code)


app.add_middleware(HTMXPartialMiddleware)

# Static files (favicon, etc.)
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _fmt_czk(value, empty: str = "—") -> str:
    if value is None or value == "":
        return empty
    try:
        v = float(value)
        # Czech format: space as thousands separator, comma as decimal point
        formatted = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
        return f"{formatted} Kč"
    except (TypeError, ValueError):
        return empty


def _source_family(source: str | None) -> str:
    normalized = str(source or "").strip().lower()
    if "airbnb" in normalized:
        return "airbnb"
    if "booking" in normalized:
        return "booking"
    if normalized == "hvmb":
        return "marriott"
    return "other"


def _source_label(source: str | None, default: str = "Ostatní") -> str:
    family = _source_family(source)
    if family == "airbnb":
        return "Airbnb"
    if family == "booking":
        return "Booking"
    if family == "marriott":
        return "Marriott"
    return str(source or "").strip() or default

# Jinja2 global helpers
templates.env.globals["now"] = datetime.now
templates.env.globals["current_year"] = date.today().year
templates.env.globals["current_month"] = date.today().month
templates.env.globals["fmt_czk"] = _fmt_czk
templates.env.globals["source_family"] = _source_family
templates.env.globals["source_label"] = _source_label


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _allow_insecure_defaults() -> bool:
    return _env_flag("RENTERO_ALLOW_INSECURE_DEFAULTS")


def _get_session_secret() -> str:
    secret = str(os.environ.get("RENTERO_SESSION_SECRET", "")).strip()
    if secret:
        return secret
    if _allow_insecure_defaults():
        return _FALLBACK_SESSION_SECRET
    raise RuntimeError(
        "Missing RENTERO_SESSION_SECRET. "
        "Set RENTERO_SESSION_SECRET or explicitly opt into localhost-only defaults via "
        "RENTERO_ALLOW_INSECURE_DEFAULTS=1."
    )


def _get_auth_credentials() -> tuple[str, str]:
    username = str(os.environ.get("RENTERO_USERNAME", "")).strip()
    password = str(os.environ.get("RENTERO_PASSWORD", ""))
    if username and password:
        return username, password
    if _allow_insecure_defaults():
        return _FALLBACK_USERNAME, _FALLBACK_PASSWORD
    raise RuntimeError(
        "Missing RENTERO_USERNAME / RENTERO_PASSWORD. "
        "Set both variables or explicitly opt into localhost-only defaults via "
        "RENTERO_ALLOW_INSECURE_DEFAULTS=1."
    )


def _validate_web_runtime_config() -> None:
    _get_session_secret()
    _get_auth_credentials()


def _get_or_create_csrf_token(request: Request) -> str:
    token = str(request.session.get(_CSRF_SESSION_KEY) or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    request.session[_CSRF_SESSION_KEY] = token
    return token


def _csrf_token(request: Request) -> str:
    return _get_or_create_csrf_token(request)


def _csrf_input(request: Request) -> Markup:
    token = _get_or_create_csrf_token(request)
    return Markup(
        f'<input type="hidden" name="{_CSRF_FORM_FIELD}" value="{token}">'
    )


async def require_csrf(request: Request) -> None:
    session_token = _get_or_create_csrf_token(request)
    candidate = str(request.headers.get(_CSRF_HEADER, "") or "").strip()
    if not candidate:
        form = await request.form()
        candidate = str(form.get(_CSRF_FORM_FIELD) or "").strip()
    if not candidate or not secrets.compare_digest(session_token, candidate):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


templates.env.globals["csrf_token"] = _csrf_token
templates.env.globals["csrf_input"] = _csrf_input


def _get_actor_username(request: Request | None = None) -> str:
    if request is not None:
        username = str(request.session.get("username") or "").strip()
        if username:
            return username
    try:
        return _get_auth_credentials()[0]
    except RuntimeError:
        return "web"


def require_auth(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    user_id = request.session.get("user_id")
    if not user_id:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    conn = get_connection()
    try:
        user = get_user_by_id(conn, int(user_id))
    finally:
        conn.close()
    if not user or not user.get("is_active"):
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    request.state.user = user


def require_admin(request: Request, _=Depends(require_auth)):
    if request.state.user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Nedostatečná oprávnění")


def require_admin_or_manager(request: Request, _=Depends(require_auth)):
    if request.state.user["role"] not in (ROLE_ADMIN, ROLE_MANAGER):
        raise HTTPException(status_code=403, detail="Nedostatečná oprávnění")


def require_write_access(request: Request, _=Depends(require_auth)):
    if request.state.user["role"] == ROLE_CLIENT:
        raise HTTPException(status_code=403, detail="Klient nemá oprávnění k zápisu")


def check_property_access(request: Request, slug: str, conn) -> None:
    user = request.state.user
    if user["role"] in (ROLE_ADMIN, ROLE_MANAGER):
        return
    allowed = get_user_property_slugs(conn, user["id"])
    if slug not in allowed:
        raise HTTPException(status_code=403, detail="Nemáte přístup k tomuto objektu")


def get_accessible_properties(request: Request, config: dict, conn) -> list[dict]:
    all_props = _get_active_properties(config)
    user = request.state.user
    if user["role"] in (ROLE_ADMIN, ROLE_MANAGER):
        return all_props
    allowed = set(get_user_property_slugs(conn, user["id"]))
    return [p for p in all_props if p["slug"] in allowed]


def get_current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


templates.env.globals["get_current_user"] = get_current_user


def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_config(conn=Depends(get_db)):
    return load_runtime_config(_CONFIG_PATH, db_conn=conn)


def _build_inventory_view(conn, config: dict, *, status_filter: str = "") -> tuple[dict, list[dict]]:
    _web_support.get_all_clients = get_all_clients
    _web_support.get_all_properties = get_all_properties
    _web_support.get_hostify_listing_names = get_hostify_listing_names
    _web_support.get_booking_config = get_booking_config
    _web_support.get_airbnb_listing_names = get_airbnb_listing_names
    return _web_support._build_inventory_view(conn, config, status_filter=status_filter)


def _display_verification_status(row: dict) -> tuple[str, str]:
    return _web_support._display_verification_status(row)


def _show_recent_generation_success(generation_job: dict | None, *, window_seconds: int = 60) -> bool:
    return _web_support._show_recent_generation_success(generation_job, window_seconds=window_seconds)


def _run_report_generation(slug: str, year: int, month: int) -> None:
    _web_support.subprocess = subprocess
    return _web_support._run_report_generation(slug, year, month)


def _start_report_generation_runner(job_id: int, slug: str, year: int, month: int, *, db_path: str) -> None:
    _web_support.subprocess = subprocess
    return _web_support._start_report_generation_runner(job_id, slug, year, month, db_path=db_path)


def _start_bulk_generation_runner(run_id: int, year: int, month: int, *, db_path: str) -> None:
    _web_support.subprocess = subprocess
    return _web_support._start_bulk_generation_runner(run_id, year, month, db_path=db_path)


def _checkin_match_status_label(status: str) -> tuple[str, str]:
    normalized = str(status or "").strip().upper()
    mapping = {
        "MATCHED": ("Spárováno", "green"),
        "NO_EVIDENCE": ("Bez evidence", "amber"),
        "AMBIGUOUS": ("Nejednoznačné", "orange"),
        "UNMATCHED_GROUP": ("Bez rezervace", "red"),
    }
    return mapping.get(normalized, (normalized or "—", "gray"))


def _enqueue_report_generation(
    conn,
    *,
    slug: str,
    year: int,
    month: int,
    requested_by: str,
) -> tuple[str, dict | None]:
    active_job = get_active_report_generation_job(conn, slug, year, month)
    if active_job:
        return "already_running", active_job

    job = create_report_generation_job(
        conn,
        slug,
        year,
        month,
        requested_by=requested_by,
    )
    try:
        _start_report_generation_runner(
            int(job["id"]),
            slug,
            year,
            month,
            db_path=_db_path_for_connection(conn),
        )
    except Exception as exc:
        detail = _truncate_generation_detail(str(exc))
        finish_report_generation_job(
            conn,
            int(job["id"]),
            status=GENERATION_JOB_FAILED,
            message="Nepodařilo se spustit background generování reportu.",
            detail=detail,
        )
        raise RuntimeError(detail) from exc
    return "started", job


def _apply_import_impacts(
    conn,
    summary: dict,
    *,
    requested_by: str,
    background_tasks,          # legacy arg; retained for route compatibility
    config: dict,
) -> dict:
    if summary.get("is_duplicate"):
        return {
            "auto_started": [],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
        }
    affected = {
        (str(slug), int(year), int(month))
        for slug, year, month in (summary.get("affected_month_keys") or [])
        if slug
    }
    result = {
        "auto_started": [],
        "already_running": [],
        "open_without_report": [],
        "locked_notified": [],
    }
    source_type = str(summary.get("source_type") or "")
    import_run_id = summary.get("import_run_id")

    for slug, year, month in sorted(affected):
        state = get_report_month_state(conn, slug, year, month)
        if state.get("status") == MONTH_STATUS_LOCKED:
            _create_import_impact_notification(
                conn,
                slug=slug,
                year=year,
                month=month,
                event_type="IMPORT_IMPACT_LOCKED_MONTH",
                source_type=source_type,
                message=(
                    f"Nová importovaná data ({source_type}) ovlivňují uzamčený měsíc "
                    f"{month:02d}/{year}. Měsíc nebyl přepsán automaticky."
                ),
                summary=summary,
            )
            result["locked_notified"].append((slug, year, month))
            continue

        mark_report_month_stale(conn, slug, year, month)
        result["auto_started"].append((slug, year, month))

    # Run all regenerations sequentially in a single background thread
    # instead of spawning N subprocesses (which caused OOM).
    if result["auto_started"]:
        import threading
        jobs = list(result["auto_started"])
        db_path = _db_path_for_connection(conn)

        def _run_sequential():
            import sqlite3 as _sqlite3
            from report.engine import generate_report_in_process
            from report.config import load_runtime_config
            _conn = _sqlite3.connect(db_path, timeout=30)
            _conn.row_factory = _sqlite3.Row
            _cfg = load_runtime_config(_CONFIG_PATH, db_conn=_conn)
            for _slug, _year, _month in jobs:
                try:
                    generate_report_in_process(_conn, _slug, _year, _month, _cfg)
                except Exception as exc:
                    _logger.warning("Import-triggered regen failed for %s %d/%d: %s", _slug, _month, _year, exc)
            _conn.close()

        t = threading.Thread(target=_run_sequential, daemon=True)
        t.start()

    return result


def _load_import_run_summary_for_source_file(conn, file_id: int) -> dict:
    row = conn.execute(
        """SELECT summary_json
             FROM import_runs
            WHERE source_file_id = ?
            ORDER BY imported_at DESC, id DESC
            LIMIT 1""",
        (int(file_id),),
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["summary_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def _apply_source_file_state_change_impacts(
    conn,
    source_file: dict,
    *,
    is_active: bool,
    requested_by: str,
    background_tasks,          # FastAPI BackgroundTasks
    config: dict,
) -> dict:
    summary = _load_import_run_summary_for_source_file(conn, int(source_file["id"]))
    base_message = (
        f"Source file '{source_file['original_name']}' byl znovu aktivován."
        if is_active
        else f"Source file '{source_file['original_name']}' byl deaktivován."
    )
    if not summary:
        return {
            "auto_started": [],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
            "message": base_message,
        }
    impact_result = _apply_import_impacts(
        conn,
        {
            **summary,
            "source_type": source_file.get("source_type") or summary.get("source_type") or "",
            "message": base_message,
        },
        requested_by=requested_by,
        background_tasks=background_tasks,
        config=config,
    )
    impact_result["message"] = base_message
    return impact_result


def _format_impact_result_lines(impact_result: dict) -> list[str]:
    lines: list[str] = []
    if impact_result.get("auto_started"):
        formatted = ", ".join(
            f"{slug} {month:02d}/{year}"
            for slug, year, month in impact_result["auto_started"]
        )
        lines.append(f"Auto-regenerace spuštěna: {formatted}")
    if impact_result.get("already_running"):
        formatted = ", ".join(
            f"{slug} {month:02d}/{year}"
            for slug, year, month in impact_result["already_running"]
        )
        lines.append(f"Generování už běží: {formatted}")
    if impact_result.get("open_without_report"):
        formatted = ", ".join(
            f"{slug} {month:02d}/{year}"
            for slug, year, month in impact_result["open_without_report"]
        )
        lines.append(f"Ovlivněné open měsíce bez hotového reportu: {formatted}")
    if impact_result.get("locked_notified"):
        formatted = ", ".join(
            f"{slug} {month:02d}/{year}"
            for slug, year, month in impact_result["locked_notified"]
        )
        lines.append(f"Uzamčené měsíce pouze notifikovány: {formatted}")
    return lines


def _enqueue_generate_all_for_month(
    conn,
    *,
    properties: list[dict],
    year: int,
    month: int,
    requested_by: str,
) -> dict:
    result = {
        "started": [],
        "already_running": [],
        "locked": [],
        "no_data": [],
    }
    for prop in properties:
        slug = str(prop.get("slug") or "").strip()
        if not slug:
            continue
        state = get_report_month_state(conn, slug, year, month)
        latest_report = _latest_report_for_month(conn, slug, year, month)
        has_data = _month_has_data(conn, prop, year, month)
        if state.get("status") == MONTH_STATUS_LOCKED:
            result["locked"].append((slug, year, month))
            continue
        if not has_data and not latest_report:
            result["no_data"].append((slug, year, month))
            continue
        status, _job = _enqueue_report_generation(
            conn,
            slug=slug,
            year=year,
            month=month,
            requested_by=requested_by,
        )
        if status == "started":
            result["started"].append((slug, year, month))
        else:
            result["already_running"].append((slug, year, month))
    return result


def _format_generate_all_month_result(result: dict, *, year: int, month: int) -> tuple[str, str]:
    message = (
        f"Hromadné generování pro {month:02d}/{year}: "
        f"spuštěno {len(result.get('started') or [])}, "
        f"už běží {len(result.get('already_running') or [])}, "
        f"uzamčeno {len(result.get('locked') or [])}, "
        f"bez dat {len(result.get('no_data') or [])}."
    )
    lines: list[str] = []
    if result.get("started"):
        lines.append("Spuštěno: " + ", ".join(slug for slug, _, _ in result["started"]))
    if result.get("already_running"):
        lines.append("Už běží: " + ", ".join(slug for slug, _, _ in result["already_running"]))
    if result.get("locked"):
        lines.append("Uzamčeno: " + ", ".join(slug for slug, _, _ in result["locked"]))
    if result.get("no_data"):
        lines.append("Bez dat: " + ", ".join(slug for slug, _, _ in result["no_data"]))
    return message, "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

register_route_modules(app, globals())
