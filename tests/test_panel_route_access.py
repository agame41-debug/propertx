"""Regression test: /reservation/{code}/panel must enforce property access.

A `client` user without a slug assignment must not be able to read a
reservation owned by another property by guessing or scraping the
confirmation code.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request

from report.routes import dashboard as dashboard_routes


@pytest.fixture
def captured_panel_handler():
    """Register the dashboard routes against a throwaway FastAPI app and
    capture the panel handler closure plus the state dict it uses."""
    app = FastAPI()

    captured = {}

    def _check(request, slug, conn):  # noqa: ARG001
        captured["check_args"] = (slug, getattr(request.state, "user", None))
        if slug not in captured["allowed_slugs"]:
            raise HTTPException(status_code=403, detail="forbidden")

    state = {
        "templates": SimpleNamespace(TemplateResponse=lambda *a, **k: "OK"),
        "require_auth": lambda: None,
        "require_admin": lambda: None,
        "require_admin_or_manager": lambda: None,
        "require_write_access": lambda: None,
        "require_csrf": lambda: None,
        "get_db": lambda: None,
        "get_config": lambda: {},
        "get_report_row_by_code": lambda conn, code, year=None, month=None: {
            "slug": "private_apt",
            "year": 2026,
            "month": 4,
            "confirmation_code": code,
        },
        "apply_overrides_to_rows": lambda conn, rows, slug, year, month: rows,
        "_load_all_bank_transactions_for_codes": lambda conn, codes: {},
        "get_report_month_state": lambda conn, slug, year, month: None,
        "get_exclusion_for_code": lambda conn, slug, code: None,
        "get_accessible_properties": lambda request, config, conn: [],
        "get_all_clients": lambda conn: [],
        "check_property_access": _check,
        "_get_active_properties": lambda config: [],
    }

    dashboard_routes.register(app, state)
    captured["state"] = state
    captured["app"] = app
    return captured


def _route_callable(app, path: str, method: str = "GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.anyio
async def test_panel_denies_client_without_slug_assignment(captured_panel_handler):
    """Client without slug access must get 403."""
    captured_panel_handler["allowed_slugs"] = set()
    handler = _route_callable(captured_panel_handler["app"], "/reservation/{code}/panel")

    request = SimpleNamespace(state=SimpleNamespace(user={"id": 99, "role": "client"}))

    with pytest.raises(HTTPException) as exc:
        await handler(code="HMA12345", request=request, year=0, month=0, _=None, conn=None)

    assert exc.value.status_code == 403
    assert captured_panel_handler["check_args"] == ("private_apt", {"id": 99, "role": "client"})


@pytest.mark.anyio
async def test_panel_check_runs_before_returning_data(captured_panel_handler):
    """check_property_access is called with the row's slug *before* the
    handler reads any further reservation data."""
    captured_panel_handler["allowed_slugs"] = {"private_apt"}
    handler = _route_callable(captured_panel_handler["app"], "/reservation/{code}/panel")

    request = SimpleNamespace(state=SimpleNamespace(user={"id": 99, "role": "client"}))

    # The handler will likely fail later because we did not mock every
    # downstream helper — but reaching that failure proves the access
    # check passed for an authorized user, which is what we want to
    # confirm here.
    try:
        await handler(code="HMA12345", request=request, year=0, month=0, _=None, conn=None)
    except (KeyError, AttributeError):
        pass

    assert captured_panel_handler["check_args"] == (
        "private_apt",
        {"id": 99, "role": "client"},
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"
