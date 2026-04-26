from datetime import date

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse


def register(app, state) -> None:
    require_auth = state["require_auth"]
    require_admin_or_manager = state["require_admin_or_manager"]
    require_csrf = state["require_csrf"]
    get_db = state["get_db"]

    @app.get("/reconciliation", response_class=HTMLResponse)
    async def reconciliation_page(
        request: Request,
        year: int = 0,
        month: int = 0,
        channel: str = "",
        status: str = "",
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
    ):
        today = date.today()
        y = year or today.year
        m = month or today.month

        view = state["_load_reconciliation_view"](
            conn,
            year=y,
            month=m,
            channel_filter=channel,
            status_filter=status,
        )

        prev_m, prev_y = (m - 1 or 12), (y if m > 1 else y - 1)
        next_m, next_y = (m % 12 + 1), (y if m < 12 else y + 1)

        query_suffix = []
        if channel:
            query_suffix.append(f"channel={channel}")
        if status:
            query_suffix.append(f"status={status}")
        extra_query = "&" + "&".join(query_suffix) if query_suffix else ""

        return state["templates"].TemplateResponse(
            request,
            "reconciliation.html",
            {
                "rows": view["rows"],
                "matched": view["matched"],
                "partial": view["partial"],
                "unmatched": view["unmatched"],
                "no_source": view["no_source"],
                "total_pairs": view["total_pairs"],
                "total_diff": view["total_diff"],
                "year": y,
                "month": m,
                "selected_channel": channel,
                "selected_status": status,
                "extra_query": extra_query,
                "prev_y": prev_y,
                "prev_m": prev_m,
                "next_y": next_y,
                "next_m": next_m,
            },
        )

    # ── Středisko settings API ────────────────────────────────────────────────

    @app.get("/api/stredisko")
    async def api_stredisko_list(
        _=Depends(require_admin_or_manager),
        conn=Depends(get_db),
    ):
        from report.db import list_stredisko_entries
        entries = list_stredisko_entries(conn)
        return JSONResponse(entries)

    @app.post("/api/stredisko")
    async def api_stredisko_upsert(
        request: Request,
        _=Depends(require_admin_or_manager),
        __=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        from report.db import upsert_stredisko_entry
        body = await request.json()
        zkratka = str(body.get("zkratka", "")).strip()
        popis = str(body.get("popis", "")).strip()
        if not zkratka or not popis:
            return JSONResponse({"error": "zkratka a popis jsou povinné"}, status_code=400)
        upsert_stredisko_entry(conn, zkratka, popis)
        return JSONResponse({"ok": True})

    @app.delete("/api/stredisko/{zkratka:path}")
    async def api_stredisko_delete(
        zkratka: str,
        _=Depends(require_admin_or_manager),
        __=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        from report.db import delete_stredisko_entry
        delete_stredisko_entry(conn, zkratka.strip())
        return JSONResponse({"ok": True})
