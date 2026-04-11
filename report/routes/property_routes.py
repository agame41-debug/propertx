import os

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from report.engine import generate_report_in_process


def _parse_optional_float(raw_value) -> float | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Neplatná číselná hodnota: {text}")


def _resolve_expense_amount_czk(
    *,
    amount_czk_raw,
    amount_net_czk_raw="",
    vat_rate_raw="",
) -> float:
    amount_czk = _parse_optional_float(amount_czk_raw)
    if amount_czk is not None:
        if amount_czk <= 0:
            raise HTTPException(status_code=422, detail="Částka musí být větší než 0.")
        return round(amount_czk, 2)

    amount_net_czk = _parse_optional_float(amount_net_czk_raw)
    vat_rate = _parse_optional_float(vat_rate_raw)
    if amount_net_czk is None:
        raise HTTPException(status_code=422, detail="Vyplňte buď Částku celkem, nebo Cena bez DPH + DPH %.")
    if amount_net_czk <= 0:
        raise HTTPException(status_code=422, detail="Cena bez DPH musí být větší než 0.")
    vat_rate = vat_rate or 0.0
    return round(amount_net_czk * (1 + (vat_rate / 100.0)), 2)


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_csrf = state["require_csrf"]
    get_db = state["get_db"]
    get_config = state["get_config"]

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
        prop = props[slug]

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
        properties = state["_get_active_properties"](config)
        categories = state["get_expense_categories"](conn)
        expenses = state["get_expenses"](
            conn,
            property_slug=slug or None,
            year=year or None,
            month=month or None,
        )
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
        vat_rate: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        amount_value = _resolve_expense_amount_czk(
            amount_czk_raw=amount_czk,
            amount_net_czk_raw=amount_net_czk,
            vat_rate_raw=vat_rate,
        )
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
                "amount_czk": amount_value,
            },
        )
        try:
            state["generate_report_in_process"](conn, property_slug, year, month, config)
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
        vat_rate: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        amount_value = _resolve_expense_amount_czk(
            amount_czk_raw=amount_czk,
            amount_net_czk_raw=amount_net_czk,
            vat_rate_raw=vat_rate,
        )
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
                "amount_czk": amount_value,
            },
        )
        try:
            state["generate_report_in_process"](conn, property_slug, year, month, config)
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
        state["delete_expense"](conn, expense_id)
        try:
            state["generate_report_in_process"](conn, prop_slug, exp_year, exp_month, config)
        except Exception:
            state["mark_report_month_stale"](conn, prop_slug, exp_year, exp_month)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)

    @app.post("/months/generate-all")
    async def generate_all_for_month(
        request: Request,
        year: int = Form(...),
        month: int = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
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

    @app.get("/property/{slug}/{year}/{month}/preview", response_class=HTMLResponse)
    async def property_preview_month(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_set_flash"](
            request,
            "info",
            "Náhled je vypnutý. Použijte Generovat a potom pracujte s uloženým reportem.",
        )
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.get("/property/{slug}/{year}/{month}/download")
    async def property_download_month(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _=Depends(require_auth),
        conn=Depends(get_db),
    ):
        latest_report = state["_latest_report_for_month"](conn, slug, year, month)
        if not latest_report:
            state["_set_flash"](
                request,
                "error",
                f"Pro {month:02d}/{year} zatím neexistuje vygenerovaný Excel.",
            )
            return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

        file_path = latest_report.get("file_path") or ""
        if not file_path or not os.path.exists(file_path):
            state["_set_flash"](
                request,
                "error",
                "V DB je záznam o reportu, ale Excel soubor už na disku neexistuje.",
                file_path,
            )
            return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

        return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(file_path),
        )

    @app.post("/property/{slug}/{year}/{month}/lock")
    async def property_lock_month(
        request: Request,
        slug: str,
        year: int,
        month: int,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
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
            state["generate_report_in_process"](conn, slug, year, month, config)
        except Exception:
            pass  # unlock succeeded even if regen fails
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
            state["generate_report_in_process"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Úprava byla uložena.")
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
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["revert_override_event"](conn, event_id, reverted_by=state["_get_actor_username"](request))
        try:
            state["generate_report_in_process"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Hodnota byla obnovena na původní.")
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
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        if not (1 <= target_month <= 12):
            raise HTTPException(400, "Neplatný cílový měsíc")
        if target_year == year and target_month == month:
            raise HTTPException(400, "Cílový měsíc je stejný jako zdrojový")
        rows = state["get_report_rows"](conn, slug=slug, year=year, month=month)
        row = next((r for r in rows if r.get("confirmation_code") == code), None)
        if row is None:
            raise HTTPException(404, "Rezervace nenalezena")
        state["create_reservation_month_assignment"](conn, {
            "slug": slug,
            "confirmation_code": code,
            "target_year": target_year,
            "target_month": target_month,
            "original_year": year,
            "original_month": month,
            "reason": reason.strip(),
            "actor": state["_get_actor_username"](request),
        })
        for _y, _m in [(year, month), (target_year, target_month)]:
            try:
                state["generate_report_in_process"](conn, slug, _y, _m, config)
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
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        assignment = state["get_assignment_for_code"](conn, slug, code)
        state["revert_reservation_month_assignment"](
            conn, slug, code, actor=state["_get_actor_username"](request)
        )
        months_to_regen_set = {(year, month)}
        if assignment:
            months_to_regen_set.add((assignment["target_year"], assignment["target_month"]))
            months_to_regen_set.add((assignment["original_year"], assignment["original_month"]))
        for _y, _m in months_to_regen_set:
            try:
                state["generate_report_in_process"](conn, slug, _y, _m, config)
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
            state["generate_report_in_process"](conn, slug, year, month, config)
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
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        state["_ensure_month_open"](conn, slug, year, month)
        state["reinstate_reservation"](
            conn, slug, code, actor=state["_get_actor_username"](request)
        )
        try:
            state["generate_report_in_process"](conn, slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, slug, year, month)
        state["_set_flash"](request, "success", "Rezervace byla vrácena do výpočtu.")
        return RedirectResponse(f"/property/{slug}/{year}/{month}", status_code=303)

    @app.post("/categories/add")
    async def category_add(
        request: Request,
        name: str = Form(...),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
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
            "property_preview_month": property_preview_month,
            "property_download_month": property_download_month,
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
