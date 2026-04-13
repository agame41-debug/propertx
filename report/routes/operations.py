import json
from datetime import date

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_write_access = state["require_write_access"]
    require_csrf = state["require_csrf"]
    get_db = state["get_db"]
    get_config = state["get_config"]

    @app.get("/inventory", response_class=HTMLResponse)
    async def inventory_page(
        request: Request,
        status: str = "",
        bulk_run_id: int | None = None,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        summary, rows = state["_build_inventory_view"](conn, config, status_filter=status)
        # Filter inventory rows for client role
        user = state["get_current_user"](request)
        if user and user["role"] == state["ROLE_CLIENT"]:
            accessible_slugs = {p["slug"] for p in state["get_accessible_properties"](request, config, conn)}
            rows = [r for r in rows if r.get("slug") in accessible_slugs]
            # Recalculate summary counts from filtered rows
            summary = {
                "total": len(rows),
                "active_count": sum(1 for r in rows if r.get("active")),
                "draft_count": sum(1 for r in rows if not r.get("active")),
                "missing_client_count": sum(1 for r in rows if not r.get("client_name")),
                "review_needed_count": sum(1 for r in rows if r.get("needs_review")),
            }
        bulk_run = state["_resolve_inventory_bulk_run"](conn, bulk_run_id=bulk_run_id)
        return state["templates"].TemplateResponse(
            request,
            "inventory.html",
            {
                "summary": summary,
                "rows": rows,
                "bulk_run": bulk_run,
                "selected_status": str(status or "").strip().lower(),
                "flash": state["_pop_flash"](request),
            },
        )

    @app.post("/inventory/sync")
    async def inventory_sync(
        request: Request,
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
    ):
        try:
            summary = state["sync_hostify_inventory"](conn)
        except state["HostifyHttpError"] as exc:
            state["_set_flash"](request, "error", "Synchronizace Hostify inventory selhala.", str(exc))
            return RedirectResponse("/inventory", status_code=303)

        message, detail = state["_format_inventory_sync_summary"](summary)
        state["_set_flash"](request, "success", message, detail)
        return RedirectResponse("/inventory?status=draft", status_code=303)

    @app.post("/inventory/{slug}/activate")
    async def inventory_activate(
        request: Request,
        slug: str,
        redirect_to: str = Form("/inventory"),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["get_all_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        updated_prop = dict(props[slug])
        updated_prop["active"] = True
        state["sync_property_to_db"](conn, slug, updated_prop, replace_aliases=False)
        state["_set_flash"](request, "success", f"Objekt {slug} byl aktivován.")
        return RedirectResponse(state["_inventory_redirect_path"](redirect_to), status_code=303)

    @app.post("/inventory/{slug}/deactivate")
    async def inventory_deactivate(
        request: Request,
        slug: str,
        redirect_to: str = Form("/inventory"),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["get_all_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        updated_prop = dict(props[slug])
        updated_prop["active"] = False
        state["sync_property_to_db"](conn, slug, updated_prop, replace_aliases=False)
        state["_set_flash"](request, "success", f"Objekt {slug} byl vrácen do draft stavu.")
        return RedirectResponse(state["_inventory_redirect_path"](redirect_to), status_code=303)

    @app.get("/clients", response_class=HTMLResponse)
    async def clients_page(
        request: Request,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        properties = state["get_accessible_properties"](request, config, conn)
        client_map = {client["property_slug"]: client for client in state["get_all_clients"](conn)}
        merged = []
        for prop in properties:
            slug = prop["slug"]
            client = client_map.get(slug) or state["get_client"](conn, slug)
            merged.append({"prop": prop, "client": client})
        return state["templates"].TemplateResponse(
            request,
            "clients.html",
            {"merged": merged, "flash": state["_pop_flash"](request)},
        )

    @app.get("/clients/{slug}", response_class=HTMLResponse)
    async def client_detail(
        request: Request,
        slug: str,
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["get_all_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")
        state["check_property_access"](request, slug, conn)
        prop = props[slug]
        client = state["get_client"](conn, slug)
        aliases = state["get_report_object_aliases"](conn, slug, include_inactive=True)
        return state["templates"].TemplateResponse(
            request,
            "client.html",
            {
                "prop": prop,
                "slug": slug,
                "client": client,
                "aliases": aliases,
                "flash": state["_pop_flash"](request),
            },
        )

    @app.post("/clients/{slug}/save")
    async def client_save(
        request: Request,
        slug: str,
        name: str = Form(""),
        ico: str = Form(""),
        dic: str = Form(""),
        platce_dph: str = Form(""),
        adresa: str = Form(""),
        address: str = Form(""),
        bank_account: str = Form(""),
        email: str = Form(""),
        phone: str = Form(""),
        notes: str = Form(""),
        display_name: str = Form(""),
        listing_id: str = Form(""),
        listing_nickname: str = Form(""),
        balicky_per_person: str = Form(""),
        city_tax_rate: str = Form(""),
        vat_rate: str = Form(""),
        hostify_listing_names: str = Form(""),
        airbnb_listing_names: str = Form(""),
        booking_listing_nickname: str = Form(""),
        booking_property_id: str = Form(""),
        active: str = Form(""),
        config_effective_from: str = Form(""),
        rentero_commission: str = Form(""),
        client_type: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        props = {p["slug"]: p for p in state["get_all_properties"](config)}
        if slug not in props:
            raise HTTPException(404, "Objekt nenalezen")

        state["save_client"](
            conn,
            {
                "property_slug": slug,
                "name": name,
                "ico": ico,
                "dic": dic,
                "platce_dph": 1 if platce_dph else 0,
                "adresa": adresa or address,
                "bank_account": bank_account,
                "email": email,
                "phone": phone,
                "notes": notes,
            },
        )

        updated_prop = dict(props[slug])
        updated_prop["display_name"] = display_name.strip() or updated_prop.get("display_name") or updated_prop.get("listing_nickname", "")
        updated_prop["listing_nickname"] = listing_nickname.strip() or updated_prop.get("listing_nickname", "")
        updated_prop["active"] = bool(active)

        if listing_id.strip():
            try:
                updated_prop["listing_id"] = int(listing_id.strip())
            except ValueError:
                pass

        for field_name, raw_value in (
            ("balicky_per_person", balicky_per_person),
            ("city_tax_rate", city_tax_rate),
            ("vat_rate", vat_rate),
        ):
            if raw_value.strip():
                try:
                    updated_prop[field_name] = float(raw_value.replace(",", ".").strip())
                except ValueError:
                    pass

        channels = json.loads(json.dumps(updated_prop.get("channels") or {}))
        channels.setdefault("hostify", {})
        channels.setdefault("airbnb", {})
        channels.setdefault("booking", {})
        channels["hostify"]["listing_names"] = [
            item.strip()
            for item in hostify_listing_names.replace("\n", ",").split(",")
            if item.strip()
        ]
        channels["airbnb"]["listing_names"] = [
            item.strip()
            for item in airbnb_listing_names.replace("\n", ",").split(",")
            if item.strip()
        ]
        channels["booking"]["listing_nickname"] = booking_listing_nickname.strip()
        channels["booking"]["property_id"] = booking_property_id.strip()
        updated_prop["channels"] = channels

        if rentero_commission:
            try:
                rate = float(rentero_commission.replace(",", ".").replace("%", "").strip()) / 100
            except ValueError:
                rate = None
            if rate is not None and 0 <= rate <= 1:
                updated_prop["rentero_commission"] = rate

        if client_type in ("rentero", "klient", "z_klient"):
            updated_prop["client_type"] = client_type

        effective_from = config_effective_from.strip() or None
        state["sync_property_to_db"](
            conn,
            slug,
            updated_prop,
            replace_aliases=True,
            alias_valid_from=effective_from,
        )

        state["_set_flash"](request, "success", "Konfigurace objektu byla uložena.")
        return RedirectResponse(f"/clients/{slug}", status_code=303)

    @app.get("/bank", response_class=HTMLResponse)
    async def bank_page(
        request: Request,
        year: int = 0,
        month: int = 0,
        channel: str = "",
        match_state: str = "",
        q: str = "",
        _=Depends(require_auth),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        today = date.today()
        y = year or today.year
        m = month or today.month

        all_rows = state["_load_bank_rows_with_drilldown"](conn, year=y, month=m)
        # Filter bank rows for client role — only show transactions linked to their properties
        user = state["get_current_user"](request)
        if user and user["role"] == state["ROLE_CLIENT"]:
            accessible_slugs = {p["slug"] for p in state["get_accessible_properties"](request, config, conn)}
            def _bank_row_matches(row):
                for batch in row.get("drilldown_batches") or []:
                    for item in batch.get("reservations") or batch.get("items") or []:
                        if item.get("slug") in accessible_slugs:
                            return True
                return False
            all_rows = [r for r in all_rows if _bank_row_matches(r)]
        rows = state["_filter_bank_rows"](
            all_rows,
            channel=channel,
            match_state=match_state,
            query=q,
        )

        prev_m, prev_y = (m - 1 or 12), (y if m > 1 else y - 1)
        next_m, next_y = (m % 12 + 1), (y if m < 12 else y + 1)
        matched_total = sum(1 for row in all_rows if row.get("matched_batch_ref"))
        unmatched_total = len(all_rows) - matched_total
        month_total_amount = sum(float(row.get("amount_czk") or 0) for row in all_rows)
        filtered_total_amount = sum(float(row.get("amount_czk") or 0) for row in rows)
        query_suffix = []
        if channel:
            query_suffix.append(f"channel={channel}")
        if match_state:
            query_suffix.append(f"match_state={match_state}")
        if q:
            query_suffix.append(f"q={q}")
        extra_query = "&" + "&".join(query_suffix) if query_suffix else ""

        return state["templates"].TemplateResponse(
            request,
            "bank.html",
            {
                "rows": rows,
                "all_rows_count": len(all_rows),
                "matched_total": matched_total,
                "unmatched_total": unmatched_total,
                "month_total_amount": month_total_amount,
                "filtered_total_amount": filtered_total_amount,
                "year": y,
                "month": m,
                "selected_channel": channel,
                "selected_match_state": match_state,
                "search_query": q,
                "extra_query": extra_query,
                "prev_y": prev_y,
                "prev_m": prev_m,
                "next_y": next_y,
                "next_m": next_m,
            },
        )

    state.update(
        {
            "inventory_page": inventory_page,
            "inventory_sync": inventory_sync,
            "inventory_activate": inventory_activate,
            "inventory_deactivate": inventory_deactivate,
            "clients_page": clients_page,
            "client_detail": client_detail,
            "client_save": client_save,
            "bank_page": bank_page,
        }
    )
