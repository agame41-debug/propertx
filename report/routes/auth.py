from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def _client_ip(request: Request) -> str:
    """Return the original client IP, honoring nginx's X-Real-IP / X-Forwarded-For."""
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        # X-Forwarded-For can be a comma-separated list — take the first hop
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def register(app, state) -> None:
    require_csrf = state["require_csrf"]

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse("/", status_code=302)
        return state["templates"].TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        _csrf=Depends(require_csrf),
        conn=Depends(state["get_db"]),
    ):
        user = state["authenticate_user"](conn, username, password, ip=_client_ip(request))
        if user:
            # Rotate the session on privilege change: the pre-login CSRF token
            # and any anonymous-session keys must not survive authentication.
            request.session.clear()
            request.session["authenticated"] = True
            request.session["username"] = user["username"]
            request.session["user_id"] = user["id"]
            request.session["user_role"] = user["role"]
            return RedirectResponse("/", status_code=302)
        return state["templates"].TemplateResponse(
            request, "login.html", {"error": "Nesprávné přihlašovací údaje"}
        )

    @app.post("/logout")
    async def logout(request: Request, _csrf=Depends(require_csrf)):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    state.update(
        {
            "login_page": login_page,
            "login_submit": login_submit,
            "logout": logout,
        }
    )
