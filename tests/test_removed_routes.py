"""Routes deleted in the engine-unification refactor must NOT be
re-added accidentally — the Excel pipeline is gone, the preview path
was already a stub."""

from __future__ import annotations


def test_property_download_route_no_longer_registered():
    from report.web import app

    paths_methods = {
        (route.path, frozenset(route.methods))
        for route in app.routes
        if hasattr(route, "methods")
    }

    assert ("/property/{slug}/{year}/{month}/download", frozenset({"GET"})) not in paths_methods


def test_property_preview_route_no_longer_registered():
    from report.web import app

    paths_methods = {
        (route.path, frozenset(route.methods))
        for route in app.routes
        if hasattr(route, "methods")
    }

    assert ("/property/{slug}/{year}/{month}/preview", frozenset({"GET"})) not in paths_methods
