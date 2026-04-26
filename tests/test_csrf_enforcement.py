"""Regression tests: state-changing JSON API routes must depend on require_csrf.

Without this guard, a logged-in admin/manager browsing a hostile site can be
made to silently mutate středisko entries or wipe the log buffer.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from report.routes import logs as logs_routes
from report.routes import reconciliation as reconciliation_routes


def _route_dependency_names(app: FastAPI, path: str, method: str) -> list[str]:
    for route in app.routes:
        if (
            getattr(route, "path", None) == path
            and method in getattr(route, "methods", set())
        ):
            return [d.call.__name__ for d in route.dependant.dependencies]
    raise AssertionError(f"route not found: {method} {path}")


def _make_state_with_csrf_marker():
    def _csrf_marker(*_args, **_kwargs):  # pragma: no cover
        return None

    _csrf_marker.__name__ = "require_csrf_marker"

    def noop(*_args, **_kwargs):  # pragma: no cover
        return None

    return {
        "require_auth": noop,
        "require_admin": noop,
        "require_admin_or_manager": noop,
        "require_write_access": noop,
        "require_csrf": _csrf_marker,
        "templates": SimpleNamespace(TemplateResponse=lambda *a, **k: None),
        "get_db": noop,
        "_load_reconciliation_view": lambda *a, **k: {},
    }


def test_logs_clear_route_depends_on_require_csrf():
    app = FastAPI()
    state = _make_state_with_csrf_marker()
    logs_routes.register(app, state)

    deps = _route_dependency_names(app, "/api/logs/clear", "POST")
    assert "require_csrf_marker" in deps, (
        f"POST /api/logs/clear lost its CSRF guard. Dependencies: {deps}"
    )


def test_stredisko_upsert_route_depends_on_require_csrf():
    app = FastAPI()
    state = _make_state_with_csrf_marker()
    reconciliation_routes.register(app, state)

    deps = _route_dependency_names(app, "/api/stredisko", "POST")
    assert "require_csrf_marker" in deps, (
        f"POST /api/stredisko lost its CSRF guard. Dependencies: {deps}"
    )


def test_stredisko_delete_route_depends_on_require_csrf():
    app = FastAPI()
    state = _make_state_with_csrf_marker()
    reconciliation_routes.register(app, state)

    deps = _route_dependency_names(app, "/api/stredisko/{zkratka:path}", "DELETE")
    assert "require_csrf_marker" in deps, (
        f"DELETE /api/stredisko lost its CSRF guard. Dependencies: {deps}"
    )


def test_stredisko_list_route_does_not_require_csrf():
    """Read-only GETs must not require CSRF (they have no body to forge)."""
    app = FastAPI()
    state = _make_state_with_csrf_marker()
    reconciliation_routes.register(app, state)

    deps = _route_dependency_names(app, "/api/stredisko", "GET")
    assert "require_csrf_marker" not in deps
