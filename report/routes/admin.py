"""Admin panel routes — user management (admin-only)."""

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def register(app, state) -> None:
    require_admin = state["require_admin"]
    require_csrf = state["require_csrf"]
    get_db = state["get_db"]
    get_config = state["get_config"]

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users_page(
        request: Request,
        _=Depends(require_admin),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        users = state["list_users"](conn)
        properties = state["_get_active_properties"](config)
        for u in users:
            u["assigned_properties"] = state["get_user_property_slugs"](conn, u["id"])
        return state["templates"].TemplateResponse(
            request,
            "admin_users.html",
            {
                "users": users,
                "properties": properties,
                "flash": state["_pop_flash"](request),
            },
        )

    @app.post("/admin/users/create")
    async def admin_create_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        role: str = Form("client"),
        display_name: str = Form(""),
        _=Depends(require_admin),
        _csrf=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        try:
            state["create_user"](
                conn, username=username, password=password,
                role=role, display_name=display_name,
            )
            state["_set_flash"](request, "success", f"Uživatel {username} vytvořen.")
        except Exception as e:
            state["_set_flash"](request, "error", f"Chyba: {e}")
        return RedirectResponse("/admin/users", status_code=302)

    @app.post("/admin/users/{user_id}/edit")
    async def admin_edit_user(
        request: Request,
        user_id: int,
        role: str = Form(...),
        display_name: str = Form(""),
        is_active: str = Form("1"),
        _=Depends(require_admin),
        _csrf=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        state["update_user_record"](
            conn, user_id,
            role=role, display_name=display_name,
            is_active=(is_active == "1"),
        )
        state["_set_flash"](request, "success", "Uživatel upraven.")
        return RedirectResponse("/admin/users", status_code=302)

    @app.post("/admin/users/{user_id}/password")
    async def admin_change_password(
        request: Request,
        user_id: int,
        new_password: str = Form(...),
        _=Depends(require_admin),
        _csrf=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        state["change_password"](conn, user_id, new_password)
        state["_set_flash"](request, "success", "Heslo změněno.")
        return RedirectResponse("/admin/users", status_code=302)

    @app.post("/admin/users/{user_id}/delete")
    async def admin_delete_user(
        request: Request,
        user_id: int,
        _=Depends(require_admin),
        _csrf=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        user = state["get_user_by_id"](conn, user_id)
        if user and user["role"] == "admin":
            admin_count = len([u for u in state["list_users"](conn) if u["role"] == "admin"])
            if admin_count <= 1:
                state["_set_flash"](request, "error", "Nelze smazat posledního admina.")
                return RedirectResponse("/admin/users", status_code=302)
        state["delete_user"](conn, user_id)
        state["_set_flash"](request, "success", "Uživatel smazán.")
        return RedirectResponse("/admin/users", status_code=302)

    @app.post("/admin/users/{user_id}/properties")
    async def admin_set_user_properties(
        request: Request,
        user_id: int,
        _=Depends(require_admin),
        _csrf=Depends(require_csrf),
        conn=Depends(get_db),
    ):
        form = await request.form()
        slugs = form.getlist("property_slugs")
        state["set_user_properties"](conn, user_id, slugs)
        state["_set_flash"](request, "success", "Objekty přiřazeny.")
        return RedirectResponse("/admin/users", status_code=302)

    state.update(
        {
            "admin_users_page": admin_users_page,
            "admin_create_user": admin_create_user,
            "admin_edit_user": admin_edit_user,
            "admin_change_password": admin_change_password,
            "admin_delete_user": admin_delete_user,
            "admin_set_user_properties": admin_set_user_properties,
        }
    )
