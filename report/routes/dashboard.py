import re
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse


def _resolve_assignment(state, conn, slug, code, year, month):
    """Find assignment for a reservation row, checking both directions.

    Returns (assignment_dict | None, moved_here: bool).
    moved_here=False means "moved OUT of this month" (original=here).
    moved_here=True  means "moved INTO this month" (target=here).
    """
    # 1. Check if moved OUT of this month
    asgn = state["get_assignment_for_code"](
        conn, slug, code, original_year=year, original_month=month,
    )
    if asgn:
        return asgn, False
    # 2. Check if moved INTO this month (any assignment targeting here)
    moved_in = state["get_codes_assigned_to_month"](conn, slug, year, month)
    for a in moved_in:
        if a["confirmation_code"] == code:
            # Fetch the full assignment record
            full = state["get_assignment_for_code"](
                conn, slug, code,
                original_year=a["original_year"],
                original_month=a["original_month"],
            )
            return full, True
    return None, False


def register(app, state) -> None:
    require_auth = state["require_auth"]
    get_db = state["get_db"]
    get_config = state["get_config"]

    @app.get("/sidebar/objects", response_class=HTMLResponse)
    async def sidebar_objects(
        request: Request,
        year: int = 0,
        month: int = 0,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        properties = state["get_accessible_properties"](request, config, conn)
        active_slug = ""
        current_year = year
        current_month = month
        # Try to get active slug and month from referer (when on a property page)
        ref = request.headers.get("referer", "")
        pm = re.search(r"/property/([^/?#]+)/(\d+)/(\d+)", ref)
        if pm:
            active_slug = pm.group(1)
            if not current_year:
                current_year = int(pm.group(2))
                current_month = int(pm.group(3))
        # Also try referer query params as fallback
        if not current_year and ref:
            from urllib.parse import urlparse, parse_qs
            rqs = parse_qs(urlparse(ref).query)
            ry = int(rqs.get("year", [0])[0] or 0)
            rm = int(rqs.get("month", [0])[0] or 0)
            if ry and 1 <= rm <= 12:
                current_year, current_month = ry, rm
        if not current_year:
            today = date.today()
            current_year, current_month = today.year, today.month

        # Quick health check for current month (single query, no per-property loop)
        health_map: dict[str, str] = {}
        if current_year and current_month:
            rows = conn.execute(
                "SELECT slug, rozdil, ke_kontrole, chybi_hostify, chybi_csv "
                "FROM report_history WHERE year=? AND month=? ORDER BY generated_at DESC",
                (current_year, current_month),
            ).fetchall()
            seen: set[str] = set()
            for row in rows:
                if row["slug"] not in seen:
                    seen.add(row["slug"])
                    issues = (
                        (row["rozdil"] or 0)
                        + (row["ke_kontrole"] or 0)
                        + (row["chybi_hostify"] or 0)
                        + (row["chybi_csv"] or 0)
                    )
                    health_map[row["slug"]] = "issues" if issues > 0 else "ok"

        return state["templates"].TemplateResponse(
            request,
            "partials/sidebar_objects.html",
            {
                "properties": properties,
                "active_slug": active_slug,
                "current_year": current_year,
                "current_month": current_month,
                "health_map": health_map,
            },
        )

    @app.get("/property/{slug}/{year}/{month}/reservation/{code}/detail", response_class=HTMLResponse)
    async def reservation_detail_partial(
        slug: str,
        year: int,
        month: int,
        code: str,
        request: Request,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["_get_active_properties"](config)}
        if slug not in props:
            raise HTTPException(status_code=404)
        state["check_property_access"](request, slug, conn)
        rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
        rows = state["apply_overrides_to_rows"](conn, rows, slug, year, month)
        row = next((item for item in rows if item.get("confirmation_code") == code), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Reservation not found")
        lookup_code = row.get("aircover_parent_code") or code if row.get("is_aircover") else code
        bank_txns_map = state["_load_all_bank_transactions_for_codes"](conn, [lookup_code])
        bank_txns = bank_txns_map.get(lookup_code, [])
        if row.get("is_aircover") and row.get("batch_ref"):
            bank_txns = [t for t in bank_txns if t.get("batch_ref") == row["batch_ref"]]
        month_state = state["get_report_month_state"](conn, slug, year, month)
        assignment, moved_here = _resolve_assignment(
            state, conn, slug, code, year, month,
        )
        exclusion = state["get_exclusion_for_code"](conn, slug, code)
        row = dict(row)
        if assignment:
            row["_assignment"] = assignment
            row["_moved_here"] = moved_here
        if exclusion:
            row["_exclusion"] = exclusion
        return state["templates"].TemplateResponse(
            request,
            "partials/reservation_detail.html",
            {
                "row": row,
                "slug": slug,
                "year": year,
                "month": month,
                "month_state": month_state,
                "bank_txns": bank_txns,
            },
        )

    @app.get("/reservation/{code}/panel", response_class=HTMLResponse)
    async def reservation_panel_partial(
        code: str,
        request: Request,
        year: int = 0,
        month: int = 0,
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        """
        Slug-agnostic reservation detail for the floating panel.
        When year/month are provided, looks up the specific month's row
        (important for split-payout reservations that exist in multiple months).
        Falls back to most recent entry if no year/month given.
        """
        row = state["get_report_row_by_code"](
            conn, code, year=year or None, month=month or None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Reservation not found")

        slug = row["slug"]
        year = row["year"]
        month = row["month"]

        rows_with_overrides = state["apply_overrides_to_rows"](conn, [row], slug, year, month)
        row = rows_with_overrides[0] if rows_with_overrides else row

        lookup_code = row.get("aircover_parent_code") or code if row.get("is_aircover") else code
        bank_txns_map = state["_load_all_bank_transactions_for_codes"](conn, [lookup_code])
        bank_txns = bank_txns_map.get(lookup_code, [])
        if row.get("is_aircover") and row.get("batch_ref"):
            bank_txns = [t for t in bank_txns if t.get("batch_ref") == row["batch_ref"]]
        month_state = state["get_report_month_state"](conn, slug, year, month)
        assignment, moved_here = _resolve_assignment(
            state, conn, slug, code, year, month,
        )
        exclusion = state["get_exclusion_for_code"](conn, slug, code)
        row = dict(row)
        if assignment:
            row["_assignment"] = assignment
            row["_moved_here"] = moved_here
        if exclusion:
            row["_exclusion"] = exclusion

        return state["templates"].TemplateResponse(
            request,
            "partials/reservation_detail.html",
            {
                "row": row,
                "slug": slug,
                "year": year,
                "month": month,
                "month_state": month_state,
                "bank_txns": bank_txns,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request,
        year: int = 0,
        month: int = 0,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        properties = state["get_accessible_properties"](request, config, conn)
        today = date.today()
        selected_year = int(year) if int(year or 0) > 0 else today.year
        selected_month = int(month) if 1 <= int(month or 0) <= 12 else today.month

        # Build slug → owner_name map for owner filter
        all_clients = state["get_all_clients"](conn)
        client_map = {c["property_slug"]: c["name"] for c in all_clients if c.get("name")}
        RENTERO_LABEL = "Rentero Property s.r.o."
        for prop in properties:
            slug = prop["slug"]
            if slug not in client_map:
                client_map[slug] = RENTERO_LABEL

        months = []
        y, m = selected_year, selected_month
        for _ in range(6):
            months.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        months.reverse()

        history_map, month_state_map, data_exists_map, notification_map = state["_build_dashboard_maps"](
            conn, properties, months
        )
        dashboard_summary, dashboard_months, dashboard_rows = state["_build_dashboard_view_model"](
            properties,
            months,
            history_map,
            month_state_map,
            data_exists_map,
            notification_map,
        )

        # Attach owner_name and is_rentero flag to each dashboard row
        for row in dashboard_rows:
            row["owner_name"] = client_map.get(row["slug"], RENTERO_LABEL)
            row["is_rentero"] = row["owner_name"] == RENTERO_LABEL

        # Unique sorted owner names for the filter dropdown
        owner_names = sorted({row["owner_name"] for row in dashboard_rows})

        return state["templates"].TemplateResponse(
            request,
            "dashboard.html",
            {
                "dashboard_summary": dashboard_summary,
                "dashboard_months": dashboard_months,
                "dashboard_rows": dashboard_rows,
                "owner_names": owner_names,
                "flash": state["_pop_flash"](request),
            },
        )

    state.update(
        {
            "sidebar_objects": sidebar_objects,
            "reservation_detail_partial": reservation_detail_partial,
            "reservation_panel_partial": reservation_panel_partial,
            "dashboard": dashboard,
        }
    )
