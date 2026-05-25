from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_admin_or_manager = state["require_admin_or_manager"]
    require_csrf = state["require_csrf"]
    get_db = state["get_db"]

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_page(
        request: Request,
        source_type: str = "",
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
    ):
        selected_type = ""
        if source_type:
            try:
                selected_type = state["validate_source_type"](source_type)
            except ValueError as exc:
                state["_set_flash"](request, "error", str(exc))
                return RedirectResponse("/sources", status_code=303)
        source_files = state["list_source_files"](conn, selected_type or None)
        import_runs = state["list_import_runs"](conn, selected_type or None, limit=30)
        flash = state["_pop_flash"](request)
        return state["templates"].TemplateResponse(
            request,
            "sources.html",
            {
                "source_types": sorted(state["SOURCE_TYPES"]),
                "source_type_label": state["_source_type_label"],
                "selected_type": selected_type,
                "source_files": source_files,
                "import_runs": import_runs,
                "flash": flash,
            },
        )

    @app.post("/sources/import")
    async def sources_import(
        request: Request,
        background_tasks: BackgroundTasks,
        source_type: str = Form(...),
        upload: UploadFile | None = File(None),
        file: UploadFile | None = File(None),
        effective_ym: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
        config=Depends(state["get_config"]),
    ):
        try:
            source_type = state["validate_source_type"](source_type)
        except ValueError as exc:
            state["_set_flash"](request, "error", str(exc))
            return RedirectResponse("/sources", status_code=303)

        selected_upload = upload or file
        if selected_upload is None:
            state["_set_flash"](request, "error", "Soubor nebyl přiložen.")
            return RedirectResponse(state["_sources_redirect"](source_type), status_code=303)

        content = await selected_upload.read()
        if not content:
            state["_set_flash"](request, "error", "Vybraný soubor je prázdný.")
            return RedirectResponse(state["_sources_redirect"](source_type), status_code=303)

        original_name = (selected_upload.filename or "upload.bin").strip() or "upload.bin"
        try:
            summary = state["import_uploaded_source"](
                conn,
                source_type,
                original_name,
                content,
                imported_by=state["_get_actor_username"](request),
                active=True,
                effective_ym=((effective_ym.strip() if isinstance(effective_ym, str) else "") or None),
            )
        except Exception as exc:
            state["_set_flash"](request, "error", f"Import souboru '{original_name}' selhal.", str(exc))
            return RedirectResponse(state["_sources_redirect"](source_type), status_code=303)

        impact_result = {
            "auto_started": [],
            "already_running": [],
            "open_without_report": [],
            "locked_notified": [],
        }
        orchestration_error = ""
        try:
            impact_result = state["_apply_import_impacts"](
                conn,
                summary,
                requested_by=state["_get_actor_username"](request),
                background_tasks=background_tasks,
                config=config,
            )
            if summary.get("import_run_id"):
                state["update_import_run_summary"](
                    conn,
                    int(summary["import_run_id"]),
                    {
                        **summary,
                        "impact_result": impact_result,
                    },
                )
        except Exception as exc:
            orchestration_error = str(exc).strip()
            if summary.get("import_run_id"):
                try:
                    state["update_import_run_summary"](
                        conn,
                        int(summary["import_run_id"]),
                        {
                            **summary,
                            "impact_result": impact_result,
                            "orchestration_error": orchestration_error,
                        },
                    )
                except Exception:
                    pass
        message, detail = state["_format_import_summary"](summary)
        extra_lines = state["_format_impact_result_lines"](impact_result)
        flash_level = "info" if summary.get("is_duplicate") else "success"
        if orchestration_error:
            flash_level = "error" if not summary.get("is_duplicate") else flash_level
            extra_lines.append("Import byl uložen, ale navazující přepočty/notifikace selhaly.")
            extra_lines.append(orchestration_error)
        if extra_lines:
            detail = detail + ("\n" if detail else "") + "\n".join(extra_lines)
        state["_set_flash"](request, flash_level, message, detail)
        return RedirectResponse(state["_sources_redirect"](source_type), status_code=303)

    @app.post("/sources/{file_id}/activate")
    async def source_activate(
        request: Request,
        background_tasks: BackgroundTasks,
        file_id: int,
        source_type: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
        config=Depends(state["get_config"]),
    ):
        row = state["get_source_file"](conn, file_id)
        if not row:
            raise HTTPException(404, "Source file not found")
        state["set_source_file_active"](conn, file_id, True)
        impact_result = state["_apply_source_file_state_change_impacts"](
            conn,
            row,
            is_active=True,
            requested_by=state["_get_actor_username"](request),
            background_tasks=background_tasks,
            config=config,
        )
        detail = "\n".join(state["_format_impact_result_lines"](impact_result))
        state["_set_flash"](request, "success", f"Soubor '{row['original_name']}' je teď aktivní.", detail)
        return RedirectResponse(state["_sources_redirect"](source_type or row["source_type"]), status_code=303)

    @app.post("/sources/{file_id}/deactivate")
    async def source_deactivate(
        request: Request,
        background_tasks: BackgroundTasks,
        file_id: int,
        source_type: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
        config=Depends(state["get_config"]),
    ):
        row = state["get_source_file"](conn, file_id)
        if not row:
            raise HTTPException(404, "Source file not found")
        state["set_source_file_active"](conn, file_id, False)
        impact_result = state["_apply_source_file_state_change_impacts"](
            conn,
            row,
            is_active=False,
            requested_by=state["_get_actor_username"](request),
            background_tasks=background_tasks,
            config=config,
        )
        detail = "\n".join(state["_format_impact_result_lines"](impact_result))
        state["_set_flash"](request, "success", f"Soubor '{row['original_name']}' je teď neaktivní.", detail)
        return RedirectResponse(state["_sources_redirect"](source_type or row["source_type"]), status_code=303)

    @app.get("/sources/{file_id}/download")
    async def source_download(
        file_id: int,
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
    ):
        row = state["get_source_file"](conn, file_id, include_content=True)
        if not row:
            raise HTTPException(404, "Source file not found")
        content = row.get("content") or b""
        if isinstance(content, memoryview):
            content = content.tobytes()
        filename = row.get("original_name") or f"source_{file_id}"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        return Response(content=bytes(content), media_type="application/octet-stream", headers=headers)

    state.update(
        {
            "sources_page": sources_page,
            "sources_import": sources_import,
            "source_activate": source_activate,
            "source_deactivate": source_deactivate,
            "source_download": source_download,
        }
    )
