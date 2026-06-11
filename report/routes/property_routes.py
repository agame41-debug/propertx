import logging
import re as _re

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from report.engine import generate_report_in_process

logger = logging.getLogger(__name__)


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_csrf = state["require_csrf"]
    require_write_access = state["require_write_access"]
    get_db = state["get_db"]
    get_config = state["get_config"]

    import math

    def _parse_decimal(raw: str | None) -> float | None:
        """Parse a Czech-locale decimal string. Returns None on empty/invalid input.

        Rejects NaN/Inf — these can come from edge inputs like '"NaN"' or '"inf"' and
        would silently bypass downstream validation. Returns None for those.
        """
        s = (raw or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if not s:
            return None
        try:
            n = float(s)
        except ValueError:
            return None
        if not math.isfinite(n):
            return None
        return n

    @app.get("/property/{slug}/{year}/{month}", response_class=HTMLResponse)
    async def property_detail(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["_get_active_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        state["check_property_access"](request, slug, conn)
        # Use the object profile as of THIS month (owner/type/rates), matching
        # how the engine generated the rows — so summary math is month-correct.
        prop = state["resolve_property_config"](conn, slug, year, month, config)

        raw_rows = state["get_report_rows"](conn, slug, year, month)
        rows = state["_prepare_rows_for_display"](
            state["apply_overrides_to_rows"](conn, raw_rows, slug, year, month)
        )
        expenses = state["get_expenses"](conn, slug, year, month)
        transferred_rows = state["get_resolved_pending_payments_for_month"](conn, slug, year, month)
        summary = state["build_report_summary"](
            rows,
            prop,
            expenses=expenses,
            transferred_rows=transferred_rows,
        )
        data_exists = bool(rows) or state["_month_has_data"](conn, prop, year, month)
        flash = state["_pop_flash"](request)
        return state["_render_property_template"](
            request,
            templates=state["templates"],
            conn=conn,
            prop=prop,
            slug=slug,
            year=year,
            month=month,
            rows=rows,
            expenses=expenses,
            summary=summary,
            data_exists=data_exists,
            flash=flash,
            is_preview=False,
        )

    @app.get("/property/{slug}/{year}/{month}/evidence-hostu", response_class=HTMLResponse)
    async def property_guest_evidence(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["_get_active_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        state["check_property_access"](request, slug, conn)
        prop = props[slug]
        month_state = state["get_report_month_state"](conn, slug, year, month)
        audit_rows = state["list_checkin_match_audit"](conn, slug=slug, year=year, month=month, limit=1000)
        evidence_groups = state["list_checkin_reservations"](
            conn,
            active_only=True,
            overlap_year=year,
            overlap_month=month,
            latest_only=True,
        )
        evidence_groups = [row for row in evidence_groups if str(row.get("property_slug") or "") == slug]
        reservation_audits = [row for row in audit_rows if row.get("record_type") == "reservation"]
        group_audits = [row for row in audit_rows if row.get("record_type") == "evidence_group"]
        audit_summary = {
            "matched_reservations": sum(1 for row in reservation_audits if row.get("match_status") == "MATCHED"),
            "reservation_issues": sum(1 for row in reservation_audits if row.get("match_status") != "MATCHED"),
            "unmatched_groups": sum(1 for row in group_audits if row.get("match_status") != "MATCHED"),
            "active_groups": len(evidence_groups),
        }
        return state["templates"].TemplateResponse(
            request,
            "guest_evidence.html",
            {
                "prop": prop,
                "slug": slug,
                "year": year,
                "month": month,
                "month_state": month_state,
                "audit_rows": audit_rows,
                "reservation_audits": reservation_audits,
                "group_audits": group_audits,
                "audit_groups": group_audits,
                "evidence_groups": evidence_groups,
                "audit_summary": audit_summary,
                "status_label": state["_checkin_match_status_label"],
            },
        )

    @app.get("/expenses", response_class=HTMLResponse)
    async def expenses_page(
        request: Request,
        slug: str = "",
        year: int = 0,
        month: int = 0,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        properties = state["get_accessible_properties"](request, config, conn)
        accessible_slugs = {p["slug"] for p in properties}
        categories = state["get_expense_categories"](conn)
        expenses = state["get_expenses"](
            conn,
            property_slug=slug or None,
            year=year or None,
            month=month or None,
        )
        # Filter expenses to only accessible properties for client role
        user = state["get_current_user"](request)
        if user and user["role"] == state["ROLE_CLIENT"]:
            expenses = [e for e in expenses if e.get("property_slug") in accessible_slugs]
        return state["templates"].TemplateResponse(
            request,
            "expenses.html",
            {
                "properties": properties,
                "categories": categories,
                "expenses": expenses,
                "filter_slug": slug,
                "filter_year": year,
                "filter_month": month,
            },
        )

    @app.post("/expenses/add")
    async def expense_add(
        request: Request,
        property_slug: str = Form(...),
        year: int = Form(...),
        month: int = Form(...),
        date_str: str = Form(""),
        category_id: str = Form(""),
        description: str = Form(...),
        amount_czk: str = Form(""),
        amount_net_czk: str = Form(""),
        amount_dph_czk: str = Form(""),
        vat_rate: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        from report.expenses_validation import validate_and_canonicalize, ExpenseValidationError
        raw_rate = _parse_decimal(vat_rate)
        # Empty vat_rate field defaults to 0% (legacy compat — non-VAT-payer flow).
        if raw_rate is None:
            raw_rate = 0.0
        try:
            gross, net, dph, rate = validate_and_canonicalize(
                gross=_parse_decimal(amount_czk),
                net=_parse_decimal(amount_net_czk),
                dph=_parse_decimal(amount_dph_czk),
                # vat_rate from current form is a percent integer ("21"); validator expects
                # decimal fraction (0.21). The /100 conversion is permanent — Task 18's new
                # form continues to send percentage integers (the segment-control labels
                # "0% / 12% / 21%" map naturally to data-rate="21" etc).
                vat_rate=round(raw_rate / 100.0, 4),
            )
        except ExpenseValidationError as e:
            state["_set_flash"](request, "error", str(e))
            referer = request.headers.get("referer", "/expenses")
            return RedirectResponse(referer, status_code=303)

        state["_ensure_month_open"](conn, property_slug, year, month)
        state["add_expense"](
            conn,
            {
                "property_slug": property_slug,
                "year": year,
                "month": month,
                "date": date_str or None,
                "category_id": int(category_id) if category_id else None,
                "description": description,
                "amount_czk": gross,
                "amount_net_czk": net,
                "amount_dph_czk": dph,
                "vat_rate": rate,
            },
        )
        try:
            await state["_run_regen_async"](conn, property_slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, property_slug, year, month)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    @app.post("/expenses/{expense_id}/edit")
    async def expense_edit(
        request: Request,
        expense_id: int,
        property_slug: str = Form(...),
        year: int = Form(...),
        month: int = Form(...),
        date_str: str = Form(""),
        category_id: str = Form(""),
        description: str = Form(...),
        amount_czk: str = Form(""),
        amount_net_czk: str = Form(""),
        amount_dph_czk: str = Form(""),
        vat_rate: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        from report.expenses_validation import validate_and_canonicalize, ExpenseValidationError
        raw_rate = _parse_decimal(vat_rate)
        # Empty vat_rate field defaults to 0% (legacy compat — non-VAT-payer flow).
        if raw_rate is None:
            raw_rate = 0.0
        try:
            gross, net, dph, rate = validate_and_canonicalize(
                gross=_parse_decimal(amount_czk),
                net=_parse_decimal(amount_net_czk),
                dph=_parse_decimal(amount_dph_czk),
                # vat_rate from current form is a percent integer ("21"); validator expects
                # decimal fraction (0.21). The /100 conversion is permanent — Task 18's new
                # form continues to send percentage integers (the segment-control labels
                # "0% / 12% / 21%" map naturally to data-rate="21" etc).
                vat_rate=round(raw_rate / 100.0, 4),
            )
        except ExpenseValidationError as e:
            state["_set_flash"](request, "error", str(e))
            referer = request.headers.get("referer", "/expenses")
            return RedirectResponse(referer, status_code=303)

        state["_ensure_month_open"](conn, property_slug, year, month)
        state["update_expense"](
            conn,
            expense_id,
            {
                "property_slug": property_slug,
                "year": year,
                "month": month,
                "date": date_str or None,
                "category_id": int(category_id) if category_id else None,
                "description": description,
                "amount_czk": gross,
                "amount_net_czk": net,
                "amount_dph_czk": dph,
                "vat_rate": rate,
            },
        )
        try:
            await state["_run_regen_async"](conn, property_slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, property_slug, year, month)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    @app.post("/expenses/{expense_id}/delete")
    async def expense_delete(
        request: Request,
        expense_id: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        expense = state["get_expense"](conn, expense_id)
        if not expense:
            raise HTTPException(404, "Výdaj nenalezen")
        prop_slug = expense["property_slug"]
        exp_year = expense["year"]
        exp_month = expense["month"]
        state["_ensure_month_open"](conn, prop_slug, exp_year, exp_month)
        # If this row came from a recurring template, tombstone the month so
        # re-materialization (incl. the regen just below) won't recreate it.
        if expense.get("template_id"):
            state["add_template_skip"](conn, int(expense["template_id"]), exp_year, exp_month)
        state["delete_expense"](conn, expense_id)
        try:
            await state["_run_regen_async"](conn, prop_slug, exp_year, exp_month, config)
        except Exception:
            state["mark_report_month_stale"](conn, prop_slug, exp_year, exp_month)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    @app.post("/expense-templates/add")
    async def expense_template_add(
        request: Request,
        property_slug: str = Form(...),
        year: int = Form(...),
        month: int = Form(...),
        description: str = Form(...),
        category_id: str = Form(""),
        amount_czk: str = Form(""),
        amount_net_czk: str = Form(""),
        amount_dph_czk: str = Form(""),
        vat_rate: str = Form(""),
        start_ym: str = Form(""),
        end_ym: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        from report.expenses_validation import validate_and_canonicalize, ExpenseValidationError
        raw_rate = _parse_decimal(vat_rate)
        if raw_rate is None:
            raw_rate = 0.0
        try:
            gross, net, dph, rate = validate_and_canonicalize(
                gross=_parse_decimal(amount_czk),
                net=_parse_decimal(amount_net_czk),
                dph=_parse_decimal(amount_dph_czk),
                vat_rate=round(raw_rate / 100.0, 4),
            )
        except ExpenseValidationError as e:
            state["_set_flash"](request, "error", str(e))
            return RedirectResponse(request.headers.get("referer", "/expenses"), status_code=303)

        start = (start_ym or "").strip() or f"{int(year):04d}-{int(month):02d}"
        end = (end_ym or "").strip() or None
        state["create_expense_template"](conn, {
            "property_slug": property_slug,
            "category_id": int(category_id) if category_id else None,
            "description": description,
            "amount_czk": gross, "amount_net_czk": net, "amount_dph_czk": dph,
            "vat_rate": rate, "start_ym": start, "end_ym": end, "source": "ui",
        })
        # Materialize into the current page month so the row appears immediately.
        state["_ensure_month_open"](conn, property_slug, year, month)
        try:
            state["materialize_templates_for_month"](conn, property_slug, year, month)
            await state["_run_regen_async"](conn, property_slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, property_slug, year, month)
        state["_set_flash"](request, "success", "Pravidelný výdaj byl uložen.")
        return RedirectResponse(request.headers.get("referer", "/expenses"), status_code=303)

    @app.post("/expense-templates/{template_id}/delete")
    async def expense_template_delete(
        request: Request,
        template_id: int,
        property_slug: str = Form(""),
        year: int = Form(0),
        month: int = Form(0),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["delete_expense_template"](conn, template_id)
        if property_slug and year and month:
            try:
                await state["_run_regen_async"](conn, property_slug, year, month, config)
            except Exception:
                pass
        state["_set_flash"](request, "success", "Pravidelný výdaj byl smazán.")
        return RedirectResponse(request.headers.get("referer", "/expenses"), status_code=303)

    @app.post("/months/generate-all")
    async def generate_all_for_month(
        request: Request,
        year: int = Form(...),
        month: int = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        if not (1 <= int(month) <= 12):
            state["_set_flash"](request, "error", f"Neplatný měsíc: {month}")
            return RedirectResponse("/inventory", status_code=303)

        active_run = state["get_active_bulk_generation_run"](conn)
        if active_run:
            active_label = state["_format_bulk_generation_month"](active_run.get("year"), active_run.get("month"))
            state["_set_flash"](
                request,
                "info",
                f"Sekvenční generování už běží pro {active_label}.",
                "Počkejte na dokončení aktuální dávky, pak spusťte další měsíc.",
            )
            return RedirectResponse(f"/inventory?bulk_run_id={int(active_run['id'])}", status_code=303)

        properties = state["_get_active_properties"](config)
        run = state["create_bulk_generation_run"](
            conn,
            int(year),
            int(month),
            total_objects=len(properties),
            requested_by=state["_get_actor_username"](request),
        )
        try:
            state["_start_bulk_generation_runner"](
                int(run["id"]),
                int(year),
                int(month),
                db_path=state["_db_path_for_connection"](conn),
            )
        except Exception as exc:
            detail = state["_truncate_generation_detail"](str(exc))
            state["finish_bulk_generation_run"](
                conn,
                int(run["id"]),
                status="FAILED",
                message="Nepodařilo se spustit sekvenční hromadné generování.",
                detail=detail,
            )
            state["_set_flash"](
                request,
                "error",
                f"Nepodařilo se spustit hromadné generování pro {int(month):02d}/{int(year)}.",
                detail,
            )
            return RedirectResponse("/inventory", status_code=303)

        state["_set_flash"](
            request,
            "success",
            f"Sekvenční generování pro {int(month):02d}/{int(year)} bylo spuštěno.",
            f"Objektů ve frontě: {len(properties)}",
        )
        return RedirectResponse(f"/inventory?bulk_run_id={int(run['id'])}", status_code=303)

    @app.post("/months/lock-all")
    async def lock_all_for_month(
        request: Request,
        year: int = Form(...),
        month: int = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        if not (1 <= int(month) <= 12):
            state["_set_flash"](request, "error", f"Neplatný měsíc: {month}")
            return RedirectResponse("/inventory", status_code=303)
        actor = state["_get_actor_username"](request)
        properties = state["_get_active_properties"](config)
        locked = 0
        for prop in properties:
            slug = prop["slug"]
            st = state["get_report_month_state"](conn, slug, year, month)
            if not st or st.get("status") != "LOCKED":
                state["set_report_month_locked"](conn, slug, year, month, locked=True, actor=actor)
                locked += 1
        state["_set_flash"](request, "success", f"Zamknuto {locked} objektů pro {int(month):02d}/{int(year)}.")
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/months/unlock-all")
    async def unlock_all_for_month(
        request: Request,
        year: int = Form(...),
        month: int = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        if not (1 <= int(month) <= 12):
            state["_set_flash"](request, "error", f"Neplatný měsíc: {month}")
            return RedirectResponse("/inventory", status_code=303)
        actor = state["_get_actor_username"](request)
        properties = state["_get_active_properties"](config)
        unlocked = 0
        for prop in properties:
            slug = prop["slug"]
            st = state["get_report_month_state"](conn, slug, year, month)
            if st and st.get("status") == "LOCKED":
                state["set_report_month_locked"](conn, slug, year, month, locked=False, actor=actor)
                unlocked += 1
        state["_set_flash"](request, "success", f"Odemknuto {unlocked} objektů pro {int(month):02d}/{int(year)}.")
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/lock")
    async def property_lock_month(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
    ):
        state["set_report_month_locked"](
            conn,
            slug,
            year,
            month,
            locked=True,
            actor=state["_get_actor_username"](request),
        )
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/unlock")
    async def property_unlock_month(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["set_report_month_locked"](
            conn,
            slug,
            year,
            month,
            locked=False,
            actor=state["_get_actor_username"](request),
        )
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
        except Exception:
            # Unlock has already succeeded; mark the month STALE so the UI
            # shows "data newer than report" instead of silently serving the
            # pre-unlock rows.
            logger.exception(
                "Post-unlock regen failed for %s/%d/%d", slug, year, month,
            )
            state["mark_report_month_stale"](conn, slug, year, month)
            state["_set_flash"](
                request, "error",
                "Měsíc byl odemčen, ale přepočet selhal — měsíc označen jako "
                "zastaralý. Zkuste report vygenerovat znovu.",
            )
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{confirmation_code}/override")
    async def reservation_override(
        request: Request,
        slug: str,
        year: int,
        month: int,
        confirmation_code: str,
        field: str = Form(...),
        new_value: str = Form(...),
        reason: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["_get_active_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        state["_ensure_month_open"](conn, slug, year, month)

        if field not in state["OVERRIDE_FIELD_LABELS"]:
            raise HTTPException(400, f"Nepovolené pole: {field}")
        try:
            normalized_value = state["normalize_override_value"](field, new_value.strip())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        raw_rows = state["get_report_rows"](conn, slug, year, month)
        row = next((item for item in raw_rows if item.get("confirmation_code") == confirmation_code), None)
        old_value = str(row.get(field, "") if row else "")

        state["create_override_event"](
            conn,
            {
                "scope_type": "reservation",
                "scope_id": confirmation_code,
                "slug": slug,
                "year": year,
                "month": month,
                "field": field,
                "old_value": old_value,
                "new_value": normalized_value,
                "reason": reason.strip(),
                "actor": state["_get_actor_username"](request),
            },
        )
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
            state["_set_flash"](request, "success", "Úprava byla uložena.")
        except Exception:
            logger.exception(
                "Override regen failed for %s/%d/%d code=%s field=%s",
                slug, year, month, confirmation_code, field,
            )
            state["mark_report_month_stale"](conn, slug, year, month)
            state["_set_flash"](
                request,
                "error",
                "Úprava byla uložena, ale přepočet selhal — měsíc označen jako zastaralý. "
                "Zkuste znovu vygenerovat report.",
            )
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/override/{event_id}/revert")
    async def reservation_override_revert(
        request: Request,
        slug: str,
        year: int,
        month: int,
        event_id: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["revert_override_event"](conn, event_id, reverted_by=state["_get_actor_username"](request))
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
            state["_set_flash"](request, "success", "Hodnota byla obnovena na původní.")
        except Exception:
            logger.exception(
                "Revert regen failed for %s/%d/%d event_id=%s",
                slug, year, month, event_id,
            )
            state["mark_report_month_stale"](conn, slug, year, month)
            state["_set_flash"](
                request,
                "error",
                "Vrácení bylo uloženo, ale přepočet selhal — měsíc označen jako zastaralý. "
                "Zkuste znovu vygenerovat report.",
            )
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/move")
    async def reservation_move(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        target_year: int = Form(...),
        target_month: int = Form(...),
        reason: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        if not (1 <= target_month <= 12):
            raise HTTPException(400, "Neplatný cílový měsíc")
        if target_year == year and target_month == month:
            raise HTTPException(400, "Cílový měsíc je stejný jako zdrojový")
        # Target must be open too: the engine silently skips locked months,
        # so a move into a locked month would vanish from the source report
        # without ever appearing in the target one.
        state["_ensure_month_open"](conn, slug, target_year, target_month)
        rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
        row = next((r for r in rows if r.get("confirmation_code") == code), None)
        if row is None:
            raise HTTPException(404, "Rezervace nenalezena")
        # Synthetic rows (ADJ / AC / SP) carry an independent batch_ref and
        # the engine's move-OUT/IN suppression keys on (base_code, batch_ref).
        # Treat all three as "is_adjustment" for storage so engine.py:592 picks
        # them up via the __(ADJ|SP|AC)\d*$ regex regardless of which kind.
        is_synthetic = bool(
            row.get("is_payout_adjustment")
            or row.get("is_aircover")
            or row.get("is_split_transaction")
        )
        state["create_reservation_month_assignment"](conn, {
            "slug": slug,
            "confirmation_code": code,
            "target_year": target_year,
            "target_month": target_month,
            "original_year": year,
            "original_month": month,
            "reason": reason.strip(),
            "actor": state["_get_actor_username"](request),
            "is_adjustment": is_synthetic,
            "batch_ref": row.get("batch_ref", "") if is_synthetic else "",
        })
        # Regenerate in chronological order so past rows exist for adjustments
        months_to_regen = sorted({(year, month), (target_year, target_month)})
        for _y, _m in months_to_regen:
            try:
                await state["_run_regen_async"](conn, slug, _y, _m, config)
            except Exception:
                state["mark_report_month_stale"](conn, slug, _y, _m)
        state["_set_flash"](request, "success",
            f"Rezervace přesunuta do {target_month:02d}/{target_year}.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/move-revert")
    async def reservation_move_revert(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        assignment = state["get_assignment_for_code"](
            conn, slug, code, original_year=year, original_month=month,
        )
        if assignment:
            # Both affected months must be open: a locked one would skip its
            # regen and the reservation would stay in (or vanish from) it.
            for _ty, _tm in {
                (assignment["target_year"], assignment["target_month"]),
                (assignment["original_year"], assignment["original_month"]),
            } - {(year, month)}:
                state["_ensure_month_open"](conn, slug, _ty, _tm)
        actor = state["_get_actor_username"](request)
        state["revert_reservation_month_assignment"](
            conn, slug, code,
            original_year=year, original_month=month, actor=actor,
        )
        months_to_regen = {(year, month)}
        if assignment:
            months_to_regen.add((assignment["target_year"], assignment["target_month"]))
            months_to_regen.add((assignment["original_year"], assignment["original_month"]))
        # Regenerate in chronological order so past rows exist for adjustments
        for _y, _m in sorted(months_to_regen):
            try:
                await state["_run_regen_async"](conn, slug, _y, _m, config)
            except Exception:
                state["mark_report_month_stale"](conn, slug, _y, _m)
        state["_set_flash"](request, "success", "Přesun byl vrácen zpět.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/exclude")
    async def reservation_exclude(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        reason: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["create_reservation_exclusion"](conn, {
            "slug": slug,
            "confirmation_code": code,
            "reason": reason.strip(),
            "actor": state["_get_actor_username"](request),
        })
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Rezervace byla vyřazena z výpočtu.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/reinstate")
    async def reservation_reinstate(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["reinstate_reservation"](
            conn, slug, code, actor=state["_get_actor_username"](request)
        )
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Rezervace byla vrácena do výpočtu.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/split-transaction")
    async def reservation_split_transaction(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        batch_ref: str = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
        row = next((r for r in rows if r.get("confirmation_code") == code), None)
        if row is None:
            raise HTTPException(404, "Rezervace nenalezena")
        if not batch_ref.strip():
            raise HTTPException(400, "Chybí batch_ref")
        base_code = _re.sub(r"__(SP|ADJ|AC)\d*$", "", code)
        state["create_split_transaction"](
            conn, slug, base_code, batch_ref.strip(),
            actor=state["_get_actor_username"](request),
        )
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Transakce byla oddělena.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/property/{slug}/{year}/{month}/reservation/{code}/merge-transaction")
    async def reservation_merge_transaction(
        request: Request,
        slug: str,
        year: int,
        month: int,
        code: str,
        batch_ref: str = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        base_code = _re.sub(r"__(SP|ADJ|AC)\d*$", "", code)
        state["delete_split_transaction"](conn, slug, base_code, batch_ref.strip())
        try:
            await state["_run_regen_async"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Transakce byla vrácena do hlavní rezervace.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/categories/add")
    async def category_add(
        request: Request,
        name: str = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
    ):
        state["add_expense_category"](conn, name)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    @app.post("/categories/{category_id}/delete")
    async def category_delete(
        request: Request,
        category_id: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
    ):
        state["delete_expense_category"](conn, category_id)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    state.update(
        {
            "generate_report_in_process": generate_report_in_process,
            "property_detail": property_detail,
            "property_guest_evidence": property_guest_evidence,
            "expenses_page": expenses_page,
            "expense_add": expense_add,
            "expense_edit": expense_edit,
            "expense_delete": expense_delete,
            "generate_all_for_month": generate_all_for_month,
            "property_lock_month": property_lock_month,
            "property_unlock_month": property_unlock_month,
            "reservation_override": reservation_override,
            "reservation_override_revert": reservation_override_revert,
            "category_add": category_add,
            "category_delete": category_delete,
            "reservation_move": reservation_move,
            "reservation_move_revert": reservation_move_revert,
            "reservation_exclude": reservation_exclude,
            "reservation_reinstate": reservation_reinstate,
        }
    )
