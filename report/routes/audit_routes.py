"""
report/routes/audit_routes.py — Change history / audit log page.

Shows all significant changes across properties:
  - override_events:                field-level overrides (payout, balicky, etc.)
  - reservation_month_assignments:  manual moves between months
  - import_runs:                    archived source imports and their downstream effects
"""
import json
from datetime import date

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_admin = state["require_admin"]
    get_db = state["get_db"]
    get_config = state["get_config"]

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_page(
        request: Request,
        slug: str = "",
        year: int = 0,
        month: int = 0,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = state["get_accessible_properties"](request, config, conn)
        # Normalize empty params
        slug = slug.strip() or ""
        year = year or 0
        month = month or 0

        events = _load_events(conn, slug=slug, year=year, month=month)

        # Filter events for client role
        user = state["get_current_user"](request)
        if user and user["role"] == state["ROLE_CLIENT"]:
            accessible_slugs = {p["slug"] for p in props}
            filtered = []
            for ev in events:
                ev_type = ev.get("event_type")
                if ev_type == "override" and ev.get("slug") in accessible_slugs:
                    filtered.append(ev)
                elif ev_type == "move" and ev.get("slug") in accessible_slugs:
                    filtered.append(ev)
                elif ev_type == "import":
                    ev_slugs = set(ev.get("affected_slugs") or [])
                    if ev_slugs & accessible_slugs:
                        filtered.append(ev)
            events = filtered

        return state["templates"].TemplateResponse(
            request,
            "audit.html",
            {
                "props": props,
                "filter_slug": slug,
                "filter_year": year,
                "filter_month": month,
                "events": events,
                "current_year": date.today().year,
                "integrity_rows": [],
            },
        )

    @app.get("/admin/integrity", response_class=HTMLResponse)
    async def admin_integrity_page(
        request: Request,
        _=Depends(require_admin),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        """Show recent L3 integrity-audit findings (cross-snapshot duplicate
        confirmation_codes). Reuses audit.html — only the integrity section
        renders because we pass empty events/props.
        """
        rows = conn.execute(
            """
            SELECT id, confirmation_code, occurrences, detected_at
              FROM integrity_audit
             ORDER BY detected_at DESC, id DESC
             LIMIT 200
            """
        ).fetchall()
        integrity_rows = [dict(r) for r in rows]

        return state["templates"].TemplateResponse(
            request,
            "audit.html",
            {
                "props": [],
                "filter_slug": "",
                "filter_year": 0,
                "filter_month": 0,
                "events": [],
                "current_year": date.today().year,
                "integrity_rows": integrity_rows,
            },
        )


# --------------------------------------------------------------------------- #
#  Data loading                                                                 #
# --------------------------------------------------------------------------- #

def _load_events(conn, *, slug: str, year: int, month: int) -> list[dict]:
    """Load and merge override_events, reservation_month_assignments and import_runs."""
    moves = _load_moves(conn, slug=slug, year=year, month=month)
    overrides = _load_overrides(conn, slug=slug, year=year, month=month)
    imports = _load_imports(conn, slug=slug, year=year, month=month)
    combined = moves + overrides + imports
    combined.sort(key=lambda e: e["created_at"], reverse=True)
    return combined


def _load_moves(conn, *, slug: str, year: int, month: int) -> list[dict]:
    """Load reservation_month_assignments as audit events."""
    conditions = []
    params: list = []

    if slug:
        conditions.append("slug = ?")
        params.append(slug)
    if year and month:
        # Show moves that involve this year/month either as target or original
        conditions.append("(target_year = ? AND target_month = ?) OR (original_year = ? AND original_month = ?)")
        params.extend([year, month, year, month])
    elif year:
        conditions.append("(target_year = ? OR original_year = ?)")
        params.extend([year, year])

    where = ("WHERE " + " AND ".join(f"({c})" for c in conditions)) if conditions else ""
    sql = f"""
        SELECT
            id,
            slug,
            confirmation_code,
            target_year,
            target_month,
            original_year,
            original_month,
            reason,
            actor,
            created_at,
            reverted_at,
            reverted_by
        FROM reservation_month_assignments
        {where}
        ORDER BY created_at DESC
        LIMIT 500
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r["event_type"] = "move"
        r["is_active"] = r["reverted_at"] is None
        result.append(r)
    return result


def _load_overrides(conn, *, slug: str, year: int, month: int) -> list[dict]:
    """Load override_events as audit events."""
    conditions = []
    params: list = []

    if slug:
        conditions.append("slug = ?")
        params.append(slug)
    if year:
        conditions.append("year = ?")
        params.append(year)
    if month:
        conditions.append("month = ?")
        params.append(month)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            id,
            slug,
            year,
            month,
            scope_id,
            field,
            old_value,
            new_value,
            reason,
            actor,
            created_at,
            reverted_at,
            reverted_by,
            is_active
        FROM override_events
        {where}
        ORDER BY created_at DESC
        LIMIT 500
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r["event_type"] = "override"
        result.append(r)
    return result


def _load_imports(conn, *, slug: str, year: int, month: int) -> list[dict]:
    """Load import_runs as audit events with affected-month metadata."""
    rows = conn.execute(
        """
        SELECT ir.*,
               sf.original_name AS source_file_name,
               dsf.original_name AS duplicate_source_file_name
          FROM import_runs ir
          LEFT JOIN source_files sf ON sf.id = ir.source_file_id
          LEFT JOIN source_files dsf ON dsf.id = ir.duplicate_of_source_file_id
         ORDER BY ir.imported_at DESC, ir.id DESC
         LIMIT 500
        """
    ).fetchall()
    result = []
    for row in rows:
        event = _build_import_event(dict(row), slug=slug, year=year, month=month)
        if event is not None:
            result.append(event)
    return result


def _source_type_label(source_type: str) -> str:
    labels = {
        "airbnb": "Airbnb",
        "booking": "Booking",
        "bank": "Banka",
        "accounting": "Účetnictví",
        "checkin": "Checkin report",
    }
    return labels.get(str(source_type or "").strip().lower(), str(source_type or ""))


def _safe_summary(summary_json: str) -> dict:
    try:
        data = json.loads(summary_json or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _normalize_affected_month_keys(summary: dict) -> list[tuple[str, int, int]]:
    seen: set[tuple[str, int, int]] = set()
    normalized: list[tuple[str, int, int]] = []
    for item in summary.get("affected_month_keys") or []:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        slug, year, month = item
        try:
            key = (str(slug or "").strip(), int(year), int(month))
        except (TypeError, ValueError):
            continue
        if not key[0] or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    normalized.sort()
    return normalized


def _filter_matching_keys(
    affected_month_keys: list[tuple[str, int, int]],
    *,
    slug: str,
    year: int,
    month: int,
) -> list[tuple[str, int, int]]:
    matches: list[tuple[str, int, int]] = []
    for item_slug, item_year, item_month in affected_month_keys:
        if slug and item_slug != slug:
            continue
        if year and item_year != year:
            continue
        if month and item_month != month:
            continue
        matches.append((item_slug, item_year, item_month))
    return matches


def _matches_import_filters(
    *,
    affected_month_keys: list[tuple[str, int, int]],
    imported_at: str,
    slug: str,
    year: int,
    month: int,
) -> bool:
    if affected_month_keys:
        return bool(
            _filter_matching_keys(
                affected_month_keys,
                slug=slug,
                year=year,
                month=month,
            )
        )

    if slug:
        return False

    imported_year = 0
    imported_month = 0
    try:
        imported_year = int((imported_at or "")[:4] or 0)
        imported_month = int((imported_at or "")[5:7] or 0)
    except ValueError:
        imported_year = 0
        imported_month = 0

    if year and imported_year != year:
        return False
    if month and imported_month != month:
        return False
    return True


def _import_change_lines(summary: dict) -> list[str]:
    source_type = str(summary.get("source_type") or "").strip().lower()
    lines: list[str] = []
    if source_type == "airbnb":
        lines.append(
            f"Airbnb: +{int(summary.get('new_reservations_count') or 0)} rezervací, "
            f"+{int(summary.get('new_batches_count') or 0)} payout batchů"
        )
    elif source_type == "booking":
        lines.append(
            f"Booking: +{int(summary.get('new_reservations_count') or 0)} rezervací, "
            f"+{int(summary.get('new_batches_count') or 0)} payout batchů"
        )
    elif source_type == "bank":
        lines.append(
            f"Banka: +{int(summary.get('new_transactions_count') or 0)} transakcí "
            f"(Airbnb {int(summary.get('new_airbnb_transactions_count') or 0)}, "
            f"Booking {int(summary.get('new_booking_transactions_count') or 0)})"
        )
    elif source_type == "checkin":
        lines.append(
            f"Checkin: +{int(summary.get('new_reservations_count') or 0)} nových skupin, "
            f"{int(summary.get('changed_groups_count') or 0)} změněných"
        )
    elif summary.get("message"):
        lines.append(str(summary.get("message")))

    preview_fields = (
        ("new_confirmation_codes", "Kódy"),
        ("new_batch_refs", "Batch refs"),
        ("new_transaction_keys", "Nové transakce"),
        ("new_checkin_reservation_ids", "Nové reservation IDs"),
        ("changed_checkin_reservation_ids", "Změněné reservation IDs"),
    )
    for field_name, label in preview_fields:
        values = [str(value).strip() for value in (summary.get(field_name) or []) if str(value).strip()]
        if values:
            suffix = " …" if len(values) >= 5 else ""
            lines.append(f"{label}: {', '.join(values[:4])}{suffix}")
    return lines


def _impact_result_lines(summary: dict) -> list[str]:
    impact_result = summary.get("impact_result") or {}
    if not isinstance(impact_result, dict):
        return []

    lines: list[str] = []
    labels = (
        ("auto_started", "Auto-regenerace spuštěna"),
        ("already_running", "Generování už běží"),
        ("open_without_report", "Open měsíce bez reportu"),
        ("locked_notified", "Uzamčené měsíce pouze notifikovány"),
    )
    for field_name, label in labels:
        items = []
        for item in impact_result.get(field_name) or []:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                continue
            item_slug, item_year, item_month = item
            try:
                items.append(f"{str(item_slug)} {int(item_month):02d}/{int(item_year)}")
            except (TypeError, ValueError):
                continue
        if items:
            lines.append(f"{label}: {', '.join(items)}")
    return lines


def _build_import_event(run: dict, *, slug: str, year: int, month: int) -> dict | None:
    summary = _safe_summary(run.get("summary_json") or "{}")
    affected_month_keys = _normalize_affected_month_keys(summary)
    if not _matches_import_filters(
        affected_month_keys=affected_month_keys,
        imported_at=str(run.get("imported_at") or ""),
        slug=slug,
        year=year,
        month=month,
    ):
        return None

    matching_keys = _filter_matching_keys(
        affected_month_keys,
        slug=slug,
        year=year,
        month=month,
    )
    affected_slugs = sorted({item_slug for item_slug, _, _ in affected_month_keys})
    affected_periods = sorted({(item_year, item_month) for _, item_year, item_month in affected_month_keys})
    display_month_keys = matching_keys or affected_month_keys
    display_slugs = sorted({item_slug for item_slug, _, _ in display_month_keys}) or affected_slugs
    display_periods = sorted({(item_year, item_month) for _, item_year, item_month in display_month_keys}) or affected_periods
    link_target = display_month_keys[0] if len(display_month_keys) == 1 else None
    primary_key = matching_keys[0] if matching_keys else (affected_month_keys[0] if affected_month_keys else None)
    source_name = (
        str(summary.get("source_name") or "").strip()
        or str(run.get("source_file_name") or "").strip()
        or str(run.get("duplicate_source_file_name") or "").strip()
        or f"import #{int(run.get('id') or 0)}"
    )
    orchestration_error = str(summary.get("orchestration_error") or "").strip()
    detail_lines = _import_change_lines(summary)
    detail_lines.extend(_impact_result_lines(summary))
    if orchestration_error:
        detail_lines.append(f"Chyba navazující orchestrace: {orchestration_error}")

    imported_period_label = "—"
    imported_at = str(run.get("imported_at") or "")
    try:
        imported_period_label = f"{int(imported_at[5:7]):02d}/{int(imported_at[:4])}"
    except (TypeError, ValueError):
        imported_period_label = "—"

    return {
        "id": int(run.get("id") or 0),
        "event_type": "import",
        "is_active": True,
        "created_at": imported_at,
        "source_type": str(run.get("source_type") or ""),
        "source_type_label": _source_type_label(run.get("source_type") or ""),
        "source_name": source_name,
        "source_file_name": str(run.get("source_file_name") or "").strip(),
        "duplicate_source_file_name": str(run.get("duplicate_source_file_name") or "").strip(),
        "imported_by": str(run.get("imported_by") or "").strip() or "system",
        "message": str(summary.get("message") or f"Soubor '{source_name}' byl importován."),
        "detail_lines": detail_lines,
        "is_duplicate": bool(summary.get("is_duplicate") or run.get("duplicate_of_source_file_id")),
        "duplicate_of_source_file_id": run.get("duplicate_of_source_file_id"),
        "orchestration_error": orchestration_error,
        "affected_month_keys": affected_month_keys,
        "affected_slugs": affected_slugs,
        "affected_periods": affected_periods,
        "display_slugs": display_slugs,
        "display_periods": display_periods,
        "link_slug": link_target[0] if link_target else "",
        "link_year": link_target[1] if link_target else 0,
        "link_month": link_target[2] if link_target else 0,
        "primary_slug": primary_key[0] if primary_key else "",
        "primary_year": primary_key[1] if primary_key else 0,
        "primary_month": primary_key[2] if primary_key else 0,
        "imported_period_label": imported_period_label,
    }
