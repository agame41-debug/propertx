"""Route-level smoke tests for the four reservation mutation endpoints
activated by the property-page redesign Phase 2 + 3:

  POST /property/{slug}/{year}/{month}/reservation/{code}/exclude
  POST /property/{slug}/{year}/{month}/reservation/{code}/reinstate
  POST /property/{slug}/{year}/{month}/reservation/{code}/move
  POST /property/{slug}/{year}/{month}/reservation/{code}/move-revert

Each test calls the route function directly (route is exposed on
`report.web` after `register_route_modules`) with monkey-patched
state-dict deps so we don't have to spin up a real DB or auth flow.
The goal here is *plumbing* — confirming the route reads the right
form fields, calls the right helper, and returns 303 with a flash —
not domain logic, which is covered by `tests/test_controls.py`.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("RENTERO_ALLOW_INSECURE_DEFAULTS", "1")

import report.web as web_module
from report.db import get_connection


def _admin_request() -> SimpleNamespace:
    return SimpleNamespace(
        session={},
        state=SimpleNamespace(user={"id": 1, "username": "admin", "role": "admin"}),
        headers={},
    )


# ────────────────────────────────────────────────────────────────────────
# /exclude
# ────────────────────────────────────────────────────────────────────────

def test_reservation_exclude_creates_exclusion_and_redirects(monkeypatch):
    seen = {}

    def fake_create(conn, payload):
        seen["payload"] = payload

    monkeypatch.setattr(web_module,"_ensure_month_open", lambda *a, **kw: None)
    monkeypatch.setattr(web_module,"create_reservation_exclusion", fake_create)
    monkeypatch.setattr(web_module,"_get_actor_username", lambda req: "admin")
    monkeypatch.setattr(
        web_module, "generate_report_in_process",
        lambda *a, **kw: {"rows_count": 0},
    )
    monkeypatch.setattr(web_module,"_set_flash", lambda req, lvl, msg: req.session.update({"_flash": {"level": lvl, "message": msg}}))

    request = _admin_request()
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.reservation_exclude(
                request=request,
                slug="28_Pluku_58",
                year=2026,
                month=4,
                code="HMA-1",
                reason="Vyloučeno přes UI",
                conn=conn,
                config={},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 303
    assert response.headers["location"] == "/property/28_Pluku_58/2026/4"
    assert seen["payload"]["confirmation_code"] == "HMA-1"
    assert seen["payload"]["reason"] == "Vyloučeno přes UI"
    assert seen["payload"]["actor"] == "admin"
    assert request.session["_flash"]["level"] == "success"


# ────────────────────────────────────────────────────────────────────────
# /reinstate
# ────────────────────────────────────────────────────────────────────────

def test_reservation_reinstate_calls_helper_and_redirects(monkeypatch):
    seen = {}

    def fake_reinstate(conn, slug, code, *, actor):
        seen["args"] = (slug, code, actor)

    monkeypatch.setattr(web_module,"_ensure_month_open", lambda *a, **kw: None)
    monkeypatch.setattr(web_module,"reinstate_reservation", fake_reinstate)
    monkeypatch.setattr(web_module,"_get_actor_username", lambda req: "admin")
    monkeypatch.setattr(
        web_module, "generate_report_in_process",
        lambda *a, **kw: {"rows_count": 0},
    )
    monkeypatch.setattr(web_module,"_set_flash", lambda req, lvl, msg: req.session.update({"_flash": {"level": lvl, "message": msg}}))

    request = _admin_request()
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.reservation_reinstate(
                request=request,
                slug="28_Pluku_58",
                year=2026,
                month=4,
                code="HMA-1",
                conn=conn,
                config={},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 303
    assert response.headers["location"] == "/property/28_Pluku_58/2026/4"
    assert seen["args"] == ("28_Pluku_58", "HMA-1", "admin")
    assert request.session["_flash"]["level"] == "success"


# ────────────────────────────────────────────────────────────────────────
# /move
# ────────────────────────────────────────────────────────────────────────

def test_reservation_move_creates_assignment_and_regens_both_months(monkeypatch):
    seen = {"assignments": [], "regens": []}

    def fake_create_assignment(conn, payload):
        seen["assignments"].append(payload)

    def fake_regen(conn, slug, year, month, config):
        seen["regens"].append((slug, year, month))
        return {"rows_count": 0}

    monkeypatch.setattr(web_module,"_ensure_month_open", lambda *a, **kw: None)
    monkeypatch.setattr(
        web_module, "get_report_rows",
        lambda conn, slug, year, month: [
            {"confirmation_code": "HMA-1", "is_payout_adjustment": False, "batch_ref": ""}
        ],
    )
    monkeypatch.setattr(web_module,"create_reservation_month_assignment", fake_create_assignment)
    monkeypatch.setattr(web_module,"_get_actor_username", lambda req: "admin")
    monkeypatch.setattr(web_module,"generate_report_in_process", fake_regen)
    monkeypatch.setattr(web_module,"mark_report_month_stale", lambda *a, **kw: None)
    monkeypatch.setattr(web_module,"_set_flash", lambda req, lvl, msg: req.session.update({"_flash": {"level": lvl, "message": msg}}))

    request = _admin_request()
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.reservation_move(
                request=request,
                slug="28_Pluku_58",
                year=2026,
                month=4,
                code="HMA-1",
                target_year=2026,
                target_month=5,
                reason="Přesun do 05/2026 přes UI",
                conn=conn,
                config={},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 303
    assert response.headers["location"] == "/property/28_Pluku_58/2026/4"
    a = seen["assignments"][0]
    assert (a["original_year"], a["original_month"]) == (2026, 4)
    assert (a["target_year"], a["target_month"]) == (2026, 5)
    assert a["confirmation_code"] == "HMA-1"
    # Both months regenerated, in chronological order so MOVED_IN sees the source.
    assert seen["regens"] == [("28_Pluku_58", 2026, 4), ("28_Pluku_58", 2026, 5)]


def test_reservation_move_rejects_same_month():
    request = _admin_request()
    conn = get_connection(":memory:")
    try:
        try:
            asyncio.run(
                web_module.reservation_move(
                    request=request,
                    slug="28_Pluku_58",
                    year=2026,
                    month=4,
                    code="HMA-1",
                    target_year=2026,
                    target_month=4,
                    reason="",
                    conn=conn,
                    config={},
                )
            )
        except Exception as exc:
            # FastAPI HTTPException(400, ...)
            assert getattr(exc, "status_code", None) == 400
        else:
            raise AssertionError("Expected HTTPException for same-month move")
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────
# /move-revert
# ────────────────────────────────────────────────────────────────────────

def test_reservation_move_revert_calls_helper_and_regens_both_months(monkeypatch):
    seen = {"reverts": [], "regens": []}

    def fake_revert(conn, slug, code, *, original_year, original_month, actor):
        seen["reverts"].append((slug, code, original_year, original_month, actor))

    def fake_regen(conn, slug, year, month, config):
        seen["regens"].append((slug, year, month))
        return {"rows_count": 0}

    monkeypatch.setattr(web_module,"_ensure_month_open", lambda *a, **kw: None)
    monkeypatch.setattr(
        web_module, "get_assignment_for_code",
        lambda conn, slug, code, *, original_year, original_month: {
            "original_year": original_year,
            "original_month": original_month,
            "target_year": 2026,
            "target_month": 5,
        },
    )
    monkeypatch.setattr(web_module,"revert_reservation_month_assignment", fake_revert)
    monkeypatch.setattr(web_module,"_get_actor_username", lambda req: "admin")
    monkeypatch.setattr(web_module,"generate_report_in_process", fake_regen)
    monkeypatch.setattr(web_module,"mark_report_month_stale", lambda *a, **kw: None)
    monkeypatch.setattr(web_module,"_set_flash", lambda req, lvl, msg: req.session.update({"_flash": {"level": lvl, "message": msg}}))

    request = _admin_request()
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.reservation_move_revert(
                request=request,
                slug="28_Pluku_58",
                year=2026,
                month=4,
                code="HMA-1",
                conn=conn,
                config={},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 303
    assert response.headers["location"] == "/property/28_Pluku_58/2026/4"
    assert seen["reverts"] == [("28_Pluku_58", "HMA-1", 2026, 4, "admin")]
    # Source + target both regenerated.
    assert sorted(seen["regens"]) == sorted([
        ("28_Pluku_58", 2026, 4),
        ("28_Pluku_58", 2026, 5),
    ])
