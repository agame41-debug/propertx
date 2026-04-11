from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse


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
    ):
        expected_username, expected_password = state["_get_auth_credentials"]()
        if username == expected_username and password == expected_password:
            request.session["authenticated"] = True
            request.session["username"] = username
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
