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


def _bank_lookup_code(row, code):
    """Return the confirmation_code to use for bank transaction lookup.
    AirCover, adjustment, and split rows use their parent code since
    payout_batch_items stores the original confirmation_code without suffix."""
    if row.get("is_aircover") and row.get("aircover_parent_code"):
        return row["aircover_parent_code"]
    if row.get("is_payout_adjustment") and row.get("adjustment_parent_code"):
        return row["adjustment_parent_code"]
    if row.get("is_split_transaction") and row.get("split_parent_code"):
        return row["split_parent_code"]
    return code


def _filter_bank_txns_for_row(row, bank_txns, state, conn):
    """Filter bank transactions to show only those relevant to this row.

    For payout adjustments and AirCover: show only the transaction matching
    this row's batch_ref.
    For main reservations: show all transactions EXCEPT those claimed by
    sibling rows (adjustments/aircover) of the same confirmation_code.
    """
    if not bank_txns:
        return bank_txns

    row_batch_ref = row.get("batch_ref") or ""
    is_secondary = row.get("is_payout_adjustment") or row.get("is_aircover") or row.get("is_split_transaction")

    if is_secondary and row_batch_ref:
        return [t for t in bank_txns if t.get("batch_ref") == row_batch_ref]

    if not is_secondary:
        # Main reservation — exclude batch_refs claimed by sibling rows
        # Siblings have codes like CODE__ADJ, CODE__ADJ2, CODE__AC etc.
        code = row.get("confirmation_code", "")
        sibling_batch_refs = set()
        if code:
            siblings = conn.execute(
                """SELECT json_extract(data, '$.batch_ref') AS br
                   FROM report_rows
                   WHERE (confirmation_code LIKE ? OR confirmation_code = ?)
                     AND confirmation_code != ?
                     AND (json_extract(data, '$.is_payout_adjustment') = 1
                          OR json_extract(data, '$.is_aircover') = 1
                          OR json_extract(data, '$.is_split_transaction') = 1)""",
                (code + "__%", code, code),
            ).fetchall()
            for s in siblings:
                if s["br"]:
                    sibling_batch_refs.add(s["br"])
        if sibling_batch_refs:
            return [t for t in bank_txns if t.get("batch_ref") not in sibling_batch_refs]

    return bank_txns


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
            try:
                ry = int(rqs.get("year", [0])[0] or 0)
                rm = int(rqs.get("month", [0])[0] or 0)
            except (TypeError, ValueError):
                # Referer is attacker/browser-controlled — a malformed
                # ?year=abc must not 500 the sidebar partial.
                ry = rm = 0
            if ry and 1 <= rm <= 12:
                current_year, current_month = ry, rm
        if not current_year:
            today = date.today()
            current_year, current_month = today.year, today.month

        # Health check for current month. Must match the dashboard, which
        # recomputes issue counts LIVE from report_rows (see
        # _build_dashboard_maps). Reading the stored report_history snapshot
        # here would go stale: issues resolved after generation (late bank/CSV
        # import, re-verification) stay yellow in the sidebar while the
        # dashboard shows green. So we recompute from report_rows too, gated by
        # "a report exists for this month" (history row) — matching the
        # dashboard's ok/issues vs action/empty distinction.
        health_map: dict[str, str] = {}
        report_slugs: set[str] = set()
        live_issues: dict[str, int] = {}
        if current_year and current_month:
            report_slugs = {
                r["slug"]
                for r in conn.execute(
                    "SELECT DISTINCT slug FROM report_history WHERE year=? AND month=?",
                    (current_year, current_month),
                ).fetchall()
            }
            for row in conn.execute(
                """SELECT slug,
                        SUM(CASE WHEN json_extract(data, '$.verification_status') = 'ROZDÍL' THEN 1 ELSE 0 END)
                      + SUM(CASE WHEN json_extract(data, '$.verification_status') = 'CHYBÍ_V_CSV' THEN 1 ELSE 0 END)
                      + SUM(CASE WHEN json_extract(data, '$.verification_status') = 'CHYBÍ_V_HOSTIFY' THEN 1 ELSE 0 END)
                      + SUM(CASE WHEN json_extract(data, '$.verification_status') = 'MATCHED'
                                 AND json_extract(data, '$.tax_verification_required')
                                 AND (COALESCE(CAST(json_extract(data, '$.checkin_missing_age_guests') AS INTEGER), 0) > 0
                                      OR NOT json_extract(data, '$.checkin_verified'))
                            THEN 1 ELSE 0 END) AS issues
                   FROM report_rows WHERE year=? AND month=? GROUP BY slug""",
                (current_year, current_month),
            ).fetchall():
                live_issues[row["slug"]] = row["issues"] or 0
            for slug in report_slugs:
                health_map[slug] = "issues" if live_issues.get(slug, 0) > 0 else "ok"

        # Hide objects with no reservations in the selected month, matching the
        # dashboard's per-month list. "Has reservations" = a report exists
        # (history row) AND there are live report_rows. The currently-open
        # object (active_slug) is always kept so the sidebar still reflects
        # where you are. If nothing qualifies (e.g. an empty month opened from
        # the dashboard), fall back to showing all so navigation never breaks.
        visible_slugs = report_slugs & set(live_issues)
        if active_slug:
            visible_slugs.add(active_slug)
        if visible_slugs:
            properties = [p for p in properties if p["slug"] in visible_slugs]

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
        lookup_code = _bank_lookup_code(row, code)
        bank_txns_map = state["_load_all_bank_transactions_for_codes"](conn, [lookup_code])
        bank_txns = bank_txns_map.get(lookup_code, [])
        bank_txns = _filter_bank_txns_for_row(row, bank_txns, state, conn)
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

        state["check_property_access"](request, slug, conn)

        rows_with_overrides = state["apply_overrides_to_rows"](conn, [row], slug, year, month)
        row = rows_with_overrides[0] if rows_with_overrides else row

        lookup_code = _bank_lookup_code(row, code)
        bank_txns_map = state["_load_all_bank_transactions_for_codes"](conn, [lookup_code])
        bank_txns = bank_txns_map.get(lookup_code, [])
        bank_txns = _filter_bank_txns_for_row(row, bank_txns, state, conn)
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

        # Month-resolve the profile segment covering the SELECTED month (reused below
        # for the client_type/owner overlay too). Drop objects the profile marks
        # inactive as of that month: a deactivated-from-month-M object must disappear
        # from the board for M onward even though base report_objects.active stays 1.
        # Objects with no covering segment keep their base active (already filtered by
        # get_accessible_properties).
        _profile_overlay = state["_resolve_dashboard_profile_overlay"](
            conn, [p["slug"] for p in properties], selected_year, selected_month
        )
        properties = [
            p for p in properties
            if _profile_overlay.get(p["slug"], {}).get("active", 1) != 0
        ]

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

        # Build slug → client_type map. Base values from report_objects are
        # month-agnostic; overlay the versioned profile segment covering the
        # SELECTED month so an Objekty import (which writes report_object_profiles,
        # not report_objects/clients) is reflected in the displayed type AND the
        # owner name (which drives the rentero/klient grouping below).
        client_type_map = {}
        for obj in conn.execute("SELECT slug, client_type FROM report_objects").fetchall():
            client_type_map[obj["slug"]] = obj["client_type"] or "rentero"
        # _profile_overlay already month-resolved above (and used for the active filter).
        for _slug, _seg in _profile_overlay.items():
            if _seg.get("client_type"):
                client_type_map[_slug] = _seg["client_type"]
            if (_seg.get("owner_name") or "").strip():
                client_map[_slug] = _seg["owner_name"]

        # Real owner = month-resolved profile owner, else a real client name.
        # Deliberately NOT defaulted to the RENTERO_LABEL placeholder: a klient
        # with no owner entered (e.g. Opletalova_45/1 Kenji) must NOT look
        # Rentero-owned, or the row shows the (zero) model fee instead of the
        # real klient odměna.
        _real_owner = {c["property_slug"]: c["name"] for c in all_clients if c.get("name")}
        for _slug, _seg in _profile_overlay.items():
            if (_seg.get("owner_name") or "").strip():
                _real_owner[_slug] = _seg["owner_name"]

        # Rentero-owned heuristic: client_type 'rentero', or klient/z_klient owned
        # by a Rentero entity (Rentero Investments / Rentero Home A → still
        # Rentero-side). See web_support._is_rentero_side.
        def _is_rentero_owned_slug(slug: str) -> bool:
            return state["_is_rentero_side"](client_type_map.get(slug, "rentero"), _real_owner.get(slug))

        # Attach owner_name, client_type and is_rentero flag to each dashboard row
        for row in dashboard_rows:
            row["owner_name"] = client_map.get(row["slug"], RENTERO_LABEL)
            row["client_type"] = client_type_map.get(row["slug"], "rentero")
            row["is_rentero"] = _is_rentero_owned_slug(row["slug"])

        # Per-property expense totals for the current month, fetched in a
        # single SQL aggregation so we don't pay per-row queries.
        cur_y, cur_m = months[-1]
        slug_to_expenses_total: dict[str, float] = {}
        if dashboard_rows:
            slug_list = [r["slug"] for r in dashboard_rows]
            ph = ",".join("?" * len(slug_list))
            for row in conn.execute(
                f"SELECT property_slug, COALESCE(SUM(amount_czk), 0) AS s "
                f"FROM expenses WHERE property_slug IN ({ph}) "
                f"AND year=? AND month=? GROUP BY property_slug",
                [*slug_list, cur_y, cur_m],
            ).fetchall():
                slug_to_expenses_total[row["property_slug"]] = float(row["s"] or 0)

        # Attach expenses_sum_czk to the current-month cell so the template
        # can emit it as a data attribute and JS can aggregate on filter.
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    cell["expenses_sum_czk"] = slug_to_expenses_total.get(row["slug"], 0.0)
                    break

        # ── Per-property DPH + zisk for current month ──────────────────────
        # Single sweep over ALL properties. For each, build_report_summary
        # gives:
        #  - vat_output_czk (DPH Rentero collects on this object — fee + room prep)
        #  - vat_input_czk  (DPH Rentero deducts from rated expenses)
        # Both are Rentero's DPH responsibility regardless of property type
        # (Rentero invoices the client with DPH on commissions, and Rentero
        # claims input DPH on the expenses it handles), so we aggregate
        # ACROSS ALL properties to get Rentero's total DPH position. The
        # KPI 2 "Bilance DPH" shown under the Rentero filter is exactly
        # that — what Rentero owes / will get back from the state across
        # everything it manages.
        # zisk_czk is collected only for Rentero-owned objects (used by
        # the per-row "rentero ZISK" indicator and the KPI 4 aggregate).
        slug_to_prop = {p["slug"]: p for p in properties}
        slug_to_zisk: dict[str, float] = {}
        slug_to_vat: dict[str, dict] = {}
        rentero_vat_output = 0.0
        rentero_vat_input = 0.0
        rentero_vat_balance = 0.0
        for row in dashboard_rows:
            slug = row["slug"]
            prop = slug_to_prop.get(slug)
            if not prop:
                continue
            # Month-resolve owner/type/rates so the per-property summary matches
            # the selected month's object profile (not just current values).
            prop = state["resolve_property_config"](conn, slug, cur_y, cur_m, config)
            raw_rows = state["get_report_rows"](conn, slug, cur_y, cur_m)
            rows_for_summary = state["_prepare_rows_for_display"](
                state["apply_overrides_to_rows"](conn, raw_rows, slug, cur_y, cur_m)
            )
            expenses_for_slug = state["get_expenses"](conn, slug, cur_y, cur_m)
            s = state["build_report_summary"](
                rows_for_summary, prop, expenses=expenses_for_slug
            )
            # DPH from every property — Rentero collects/deducts on all of them
            rentero_vat_output  += s.get("vat_output_czk", 0)  or 0
            rentero_vat_input   += s.get("vat_input_czk", 0)   or 0
            rentero_vat_balance += s.get("vat_balance_czk", 0) or 0
            slug_to_vat[slug] = {
                "output":  round(float(s.get("vat_output_czk")  or 0), 2),
                "input":   round(float(s.get("vat_input_czk")   or 0), 2),
                "balance": round(float(s.get("vat_balance_czk") or 0), 2),
            }
            # Zisk only for Rentero-owned objects (per-row indicator). Use
            # summary.zisk_czk when set; otherwise the property-page
            # fallback formula (covers Rentero-as-z_klient).
            if _is_rentero_owned_slug(slug):
                zisk = s.get("zisk_czk")
                if zisk is None:
                    zisk = (s.get("gross_payout_czk") or 0) \
                         - (s.get("expenses_total_czk") or 0) \
                         - (s.get("vat_balance_czk") or 0)
                slug_to_zisk[slug] = round(float(zisk), 2)

        # Attach zisk_czk to current-month cell so the template can emit it
        # as a data attribute for JS aggregation on filter changes.
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    cell["zisk_czk"] = slug_to_zisk.get(row["slug"], 0.0)
                    _vat = slug_to_vat.get(row["slug"], {})
                    cell["vat_output_czk"]  = _vat.get("output", 0.0)
                    cell["vat_input_czk"]   = _vat.get("input", 0.0)
                    cell["vat_balance_czk"] = _vat.get("balance", 0.0)
                    break

        dashboard_summary["rentero_vat_output_czk"]  = round(rentero_vat_output, 2)
        dashboard_summary["rentero_vat_input_czk"]   = round(rentero_vat_input, 2)
        dashboard_summary["rentero_vat_balance_czk"] = round(rentero_vat_balance, 2)

        # ── KPI 4 Rentero fee + KPI 2 client payout aggregates ─────────────
        # KPI 4 "Odměna Rentero" = total fee Rentero earns this month, summed
        # across all objects. rentero_fee_sum_czk already encodes the fee per
        # client_type (klient → commission, z_klient → 3 %, rentero → 0), so
        # we just sum it. client_payout_total stays as the rentero → klienti
        # cash-flow that KPI 2 "Výplata klientům" shows (excludes Rentero).
        client_payout_total = 0.0
        rentero_fee_total = 0.0
        for row in dashboard_rows:
            for cell in row.get("cells", []):
                if cell.get("year") == cur_y and cell.get("month") == cur_m:
                    rentero_fee_total += cell.get("rentero_fee_sum_czk", 0) or 0
                    if not row["is_rentero"]:
                        client_payout_total += cell.get("client_payout_sum_czk", 0) or 0
                    break
        dashboard_summary["total_client_payout_czk"] = round(client_payout_total, 2)
        dashboard_summary["total_rentero_fee_czk"] = round(rentero_fee_total, 2)
        # "Including model" = real fee (clients) + modelová odměna on Rentero-owned
        # objects. The model total is 0 for klient/z_klient (computed in the view
        # model), so this never double-counts a real fee.
        dashboard_summary["total_rentero_fee_with_model_czk"] = round(
            rentero_fee_total + (dashboard_summary.get("total_model_rentero_fee_czk") or 0), 2
        )

        return state["templates"].TemplateResponse(
            request,
            "dashboard.html",
            {
                "dashboard_summary": dashboard_summary,
                "dashboard_months": dashboard_months,
                "dashboard_rows": dashboard_rows,
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
