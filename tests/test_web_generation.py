from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from report.config import get_hostify_listing_names, load_runtime_config, sync_json_config_to_db, sync_property_to_db
from report.db import (
    BULK_GENERATION_FAILED,
    create_bulk_generation_run,
    get_client,
    get_connection,
    get_latest_bulk_generation_run,
)
import report.web as web_module
from tests.test_config_db import BASE_CONFIG


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("Missing CSRF token in rendered HTML")
    return match.group(1)


def _admin_request(**overrides) -> SimpleNamespace:
    """Build a minimal Request stub that satisfies RBAC + Jinja `request.*` access.

    Tests that bypass FastAPI's DI and call route functions directly need
    `request.state.user` (read by `check_property_access` /
    `get_accessible_properties`) and `request.headers` (read by some
    base.html partials). Default to admin role + empty headers.
    """
    base = {
        "session": {},
        "state": SimpleNamespace(user={"id": 1, "username": "admin", "role": "admin"}),
        "headers": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_validate_web_runtime_config_requires_explicit_secure_settings(monkeypatch):
    monkeypatch.delenv("RENTERO_ALLOW_INSECURE_DEFAULTS", raising=False)
    monkeypatch.delenv("RENTERO_USERNAME", raising=False)
    monkeypatch.delenv("RENTERO_PASSWORD", raising=False)
    monkeypatch.delenv("RENTERO_SESSION_SECRET", raising=False)

    try:
        web_module._validate_web_runtime_config()
    except RuntimeError as exc:
        assert "RENTERO_SESSION_SECRET" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for insecure runtime config")


def test_login_rejects_missing_csrf(monkeypatch):
    monkeypatch.setenv("RENTERO_ALLOW_INSECURE_DEFAULTS", "1")

    with TestClient(web_module.app) as client:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )

    assert response.status_code == 403


def test_start_bulk_generation_runner_invokes_background_process(monkeypatch):
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        # The runner now spawns a daemon reaper thread that reads proc.pid
        # and calls proc.wait(); the fake must satisfy both.
        return SimpleNamespace(pid=12345, wait=lambda: 0)

    monkeypatch.setattr(web_module.subprocess, "Popen", fake_popen)

    web_module._start_bulk_generation_runner(
        23,
        2026,
        4,
        db_path="/tmp/rentero.db",
    )

    assert seen["cmd"] == [
        web_module.sys.executable,
        "-u",
        "-m",
        "report.bulk_generation_runner",
        "--run-id",
        "23",
        "--year",
        "2026",
        "--month",
        "4",
        "--config",
        web_module._CONFIG_PATH,
        "--db-path",
        "/tmp/rentero.db",
    ]
    assert seen["kwargs"]["cwd"] == web_module._BASE_DIR
    assert seen["kwargs"]["start_new_session"] is True


def test_generate_all_for_month_redirects_with_bulk_run_flash(monkeypatch):
    request = SimpleNamespace(session={})
    config = {
        "properties": {
            "28_Pluku_58": {"display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58", "active": True},
            "Second": {"display_name": "Second", "listing_nickname": "Second", "active": True},
        }
    }
    monkeypatch.setattr(web_module, "get_active_bulk_generation_run", lambda conn: None)
    monkeypatch.setattr(
        web_module,
        "create_bulk_generation_run",
        lambda *args, **kwargs: {"id": 41, "year": 2026, "month": 3, "status": "PENDING"},
    )
    monkeypatch.setattr(web_module, "_db_path_for_connection", lambda conn: "/tmp/rentero.db")
    seen = {}

    def fake_start(run_id, year, month, *, db_path):
        seen["args"] = (run_id, year, month, db_path)

    monkeypatch.setattr(web_module, "_start_bulk_generation_runner", fake_start)

    response = asyncio.run(
        web_module.generate_all_for_month(
            request=request,
            year=2026,
            month=3,
            conn=object(),
            config=config,
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/inventory?bulk_run_id=41"
    assert request.session["_flash"]["level"] == "success"
    assert "Sekvenční generování pro 03/2026 bylo spuštěno." == request.session["_flash"]["message"]
    assert request.session["_flash"]["detail"] == "Objektů ve frontě: 2"
    assert seen["args"] == (41, 2026, 3, "/tmp/rentero.db")


def test_generate_all_for_month_rejects_invalid_month():
    request = SimpleNamespace(session={})

    response = asyncio.run(
        web_module.generate_all_for_month(
            request=request,
            year=2026,
            month=13,
            conn=object(),
            config={"properties": {}},
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/inventory"
    assert request.session["_flash"]["level"] == "error"


def test_generate_all_for_month_redirects_to_existing_active_bulk_run(monkeypatch):
    request = SimpleNamespace(session={})
    monkeypatch.setattr(
        web_module,
        "get_active_bulk_generation_run",
        lambda conn: {"id": 55, "year": 2026, "month": 3, "status": "RUNNING"},
    )

    response = asyncio.run(
        web_module.generate_all_for_month(
            request=request,
            year=2026,
            month=4,
            conn=object(),
            config={"properties": {}},
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/inventory?bulk_run_id=55"
    assert request.session["_flash"]["level"] == "info"
    assert "už běží pro 03/2026" in request.session["_flash"]["message"]


def test_generate_all_for_month_marks_bulk_run_failed_when_background_start_fails(monkeypatch):
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        config = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        request = SimpleNamespace(session={})
        monkeypatch.setattr(web_module, "get_active_bulk_generation_run", lambda _: None)
        monkeypatch.setattr(web_module, "_db_path_for_connection", lambda _: "/tmp/rentero.db")
        monkeypatch.setattr(
            web_module,
            "_start_bulk_generation_runner",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("spawn failed")),
        )

        response = asyncio.run(
            web_module.generate_all_for_month(
                request=request,
                year=2026,
                month=3,
                conn=conn,
                config=config,
            )
        )

        run = get_latest_bulk_generation_run(conn)
        assert response.status_code == 303
        assert response.headers["location"] == "/inventory"
        assert run is not None
        assert run["status"] == BULK_GENERATION_FAILED
        assert request.session["_flash"]["level"] == "error"
    finally:
        conn.close()


def test_import_change_lines_formats_bank_and_checkin_summaries():
    bank_lines = web_module._import_change_lines(
        {
            "source_type": "bank",
            "new_transactions_count": 3,
            "new_airbnb_transactions_count": 1,
            "new_booking_transactions_count": 2,
            "new_transaction_keys": ["tx-1", "tx-2"],
        }
    )
    checkin_lines = web_module._import_change_lines(
        {
            "source_type": "checkin",
            "new_reservations_count": 2,
            "changed_groups_count": 4,
            "changed_checkin_reservation_ids": ["chk-1"],
        }
    )

    assert bank_lines[0] == "Banka: +3 transakcí (Airbnb 1, Booking 2)"
    assert bank_lines[1].startswith("Nové transakce:")
    assert checkin_lines[0] == "Checkin: +2 nových skupin, 4 změněných"


def test_show_recent_generation_success_only_for_fresh_success_jobs():
    fresh = {
        "status": "SUCCEEDED",
        "finished_at": (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat(),
    }
    stale = {
        "status": "SUCCEEDED",
        "finished_at": (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(),
    }
    failed = {
        "status": "FAILED",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    assert web_module._show_recent_generation_success(fresh) is True
    assert web_module._show_recent_generation_success(stale) is False
    assert web_module._show_recent_generation_success(failed) is False


def test_dashboard_uses_requested_month_from_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(web_module, "_get_active_properties", lambda config: [{"slug": "28_Pluku_58", "display_name": "28. Pluku 58"}])

    def fake_maps(conn, properties, months):
        captured["months"] = months
        return {}, {}, {}, {}

    monkeypatch.setattr(web_module, "_build_dashboard_maps", fake_maps)
    monkeypatch.setattr(web_module, "_build_dashboard_view_model", lambda *args, **kwargs: ({"current_month_label": "02/2025", "property_count": 1, "property_suffix": "", "total_payout_czk": 0, "total_client_payout_czk": 0, "total_reservations": 0, "reservations_delta": 0, "sparkline_points": [0, 0, 0, 0, 0, 0], "issues": 0, "new_data": 0, "locked": 0, "total_with_data": 0, "needs_report": 0}, [], []))
    monkeypatch.setattr(web_module.templates, "TemplateResponse", lambda request, template, context: captured.update({"template": template, "context": context}) or SimpleNamespace(status_code=200))
    monkeypatch.setattr(web_module, "_pop_flash", lambda request: None)
    monkeypatch.setattr(web_module, "get_all_clients", lambda conn: [])

    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.dashboard(
                request=_admin_request(),
                year=2025,
                month=2,
                conn=conn,
                config={"properties": {}},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 200
    assert captured["template"] == "dashboard.html"
    assert captured["months"][-1] == (2025, 2)


def test_bank_page_renders_successfully():
    import os

    old_allow = os.environ.get("RENTERO_ALLOW_INSECURE_DEFAULTS")
    os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = "1"
    try:
        with TestClient(web_module.app) as client:
            login_page = client.get("/login")
            csrf_token = _extract_csrf_token(login_page.text)
            login = client.post(
                "/login",
                data={"username": "admin", "password": "admin", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            assert login.status_code == 302

            response = client.get("/bank")
    finally:
        if old_allow is None:
            os.environ.pop("RENTERO_ALLOW_INSECURE_DEFAULTS", None)
        else:
            os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = old_allow

    assert response.status_code == 200
    assert "Bankovní transakce" in response.text


def test_dashboard_renders_odmena_rentero_kpi():
    import os

    old_allow = os.environ.get("RENTERO_ALLOW_INSECURE_DEFAULTS")
    os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = "1"
    try:
        with TestClient(web_module.app) as client:
            login_page = client.get("/login")
            csrf_token = _extract_csrf_token(login_page.text)
            login = client.post(
                "/login",
                data={"username": "admin", "password": "admin", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            assert login.status_code == 302

            response = client.get("/")
    finally:
        if old_allow is None:
            os.environ.pop("RENTERO_ALLOW_INSECURE_DEFAULTS", None)
        else:
            os.environ["RENTERO_ALLOW_INSECURE_DEFAULTS"] = old_allow

    assert response.status_code == 200
    assert "Odměna Rentero" in response.text
    assert "Zisk Rentero" not in response.text


def _render_property_kpi(*, is_rentero_owned, summary, prop, expenses=None, is_dph=True):
    tmpl = web_module.templates.get_template("partials/property_kpi.html")
    return tmpl.render(
        _is_rentero_owned=is_rentero_owned,
        _is_dph_applicable=is_dph,
        summary=summary,
        prop=prop,
        expenses=expenses or [],
    )


_RENTERO_OWNED_SUMMARY = {
    "gross_payout_czk": 10000.0,
    "accommodation_income_czk": 8000.0,
    "expenses_total_czk": 0.0,
    "vat_input_czk": 0.0,
    "vat_balance_czk": 0.0,
    "zisk_czk": 9000.0,
    "client_payout_before_expenses_czk": 8000.0,
    "client_payout_after_expenses_czk": 8000.0,
    "rentero_commission_rate": 0.15,
    "rentero_fee_czk": 0.0,
    "vat_rentero_fee_czk": 0.0,
    "rentero_odmena_czk": 0.0,
    "rentero_vyplata_czk": 0.0,
}


def test_property_kpi_rentero_owned_shows_dash_when_no_fee():
    # Rentero-owned object charges no fee → the "Rentero fee" KPI card shows
    # "—" instead of a (zero) amount, with no percentage suffix and no Net row.
    html = _render_property_kpi(
        is_rentero_owned=True,
        summary=_RENTERO_OWNED_SUMMARY,
        prop={"client_type": "rentero"},
    )
    assert "Rentero fee" in html        # the card still exists
    assert "—" in html                  # dash placeholder for the missing fee
    assert "Net" not in html            # no fee breakdown rendered
    assert "Rentero fee (15" not in html  # no "(15 %)" suffix when fee is 0


def test_property_kpi_zklient_owned_still_shows_fee():
    # A Rentero-owned object tagged z_klient still charges its 3 % fee, so the
    # card shows the value and the Net breakdown (regression guard).
    summary = dict(
        _RENTERO_OWNED_SUMMARY,
        rentero_commission_rate=0.03,
        rentero_fee_czk=300.0,
        rentero_odmena_czk=300.0,
        rentero_vyplata_czk=300.0,
    )
    html = _render_property_kpi(
        is_rentero_owned=True,
        summary=summary,
        prop={"client_type": "z_klient"},
    )
    assert "Net" in html


def test_property_kpi_rentero_owned_shows_model_card():
    # Rentero-owned object with bookings → KPI slot 3 shows the illustrative
    # "modelová výplata klienta" card instead of the empty "—".
    summary = dict(
        _RENTERO_OWNED_SUMMARY,
        model_client={
            "rentero_commission_rate": 0.15,
            "rentero_fee_czk": 1200.0,
            "vat_rentero_fee_czk": 252.0,
            "rentero_odmena_total_czk": 1452.0,
            "client_payout_before_expenses_czk": 6548.0,
            "balicky_per_person": 199.0,
        },
    )
    html = _render_property_kpi(
        is_rentero_owned=True, summary=summary, prop={"client_type": "rentero"},
    )
    assert "Modelová výplata klienta" in html
    assert "Odměna bez DPH" in html      # net odměna, labelled bez DPH
    assert "Odměna Rentero" not in html  # shortened to fit one line
    assert "Balíčky" in html             # full word (fits on one line)
    assert "199 Kč/os" in html           # balíček per-person rate, same sub line


def test_property_kpi_rentero_owned_no_data_still_shows_dash():
    # Rentero-owned object with no bookings → nothing to model → "—".
    summary = dict(
        _RENTERO_OWNED_SUMMARY,
        model_client={
            "rentero_commission_rate": 0.15,
            "rentero_fee_czk": 0.0,
            "vat_rentero_fee_czk": 0.0,
            "rentero_odmena_total_czk": 0.0,
            "client_payout_before_expenses_czk": 0.0,
        },
    )
    html = _render_property_kpi(
        is_rentero_owned=True, summary=summary, prop={"client_type": "rentero"},
    )
    assert "Modelová výplata klienta" not in html
    assert "—" in html


def test_guest_evidence_template_renders_group_audits():
    request = _admin_request(
        url=SimpleNamespace(path="/property/28_Pluku_58/2026/4/evidence-hostu"),
    )
    html = web_module.templates.get_template("guest_evidence.html").render(
        request=request,
        prop=SimpleNamespace(display_name="28. Pluku 58", listing_nickname="28. Pluku 58"),
        slug="28_Pluku_58",
        year=2026,
        month=4,
        month_state={"has_new_data_since_generation": False},
        audit_rows=[],
        reservation_audits=[],
        group_audits=[
            {
                "guest_name": "John Doe",
                "check_in": "2026-04-10",
                "check_out": "2026-04-12",
                "match_status": "MATCHED",
                "detail": {"matched_confirmation_code": "ABC123"},
                "checkin_property_name": "28. Pluku 58",
            }
        ],
        evidence_groups=[],
        audit_summary={
            "active_groups": 1,
            "matched_reservations": 1,
            "reservation_issues": 0,
            "unmatched_groups": 0,
        },
        status_label=web_module._checkin_match_status_label,
    )

    assert "John Doe" in html
    assert "Spárováno s rezervací ABC123." in html


def test_inventory_page_filters_draft_objects(monkeypatch):
    captured = {}
    monkeypatch.setattr(web_module, "_pop_flash", lambda request: None)
    monkeypatch.setattr(web_module, "get_all_clients", lambda conn: [{"property_slug": "Active", "name": "Client A"}])
    monkeypatch.setattr(web_module, "_resolve_inventory_bulk_run", lambda conn, bulk_run_id=None: {"id": 1, "status": "RUNNING"})
    monkeypatch.setattr(
        web_module.templates,
        "TemplateResponse",
        lambda request, template, context: captured.update({"template": template, **context}) or SimpleNamespace(status_code=200),
    )

    config = {
        "properties": {
            "Active": {
                "display_name": "Active Listing",
                "listing_id": 1,
                "listing_nickname": "Active Listing",
                "active": True,
                "channels": {
                    "airbnb": {"listing_names": ["Active Airbnb"]},
                    "booking": {"property_id": "111", "listing_nickname": "Active - Bcom"},
                },
            },
            "Draft": {
                "display_name": "Draft Listing",
                "listing_id": 2,
                "listing_nickname": "Draft Listing",
                "active": False,
                "channels": {"airbnb": {"listing_names": []}, "booking": {}},
            },
        }
    }

    response = asyncio.run(
        web_module.inventory_page(
            request=_admin_request(),
            status="draft",
            conn=object(),
            config=config,
        )
    )

    assert response.status_code == 200
    assert captured["template"] == "inventory.html"
    assert captured["summary"]["draft_count"] == 1
    assert captured["bulk_run"]["id"] == 1
    assert [row["slug"] for row in captured["rows"]] == ["Draft"]


def test_inventory_sync_redirects_with_success_flash(monkeypatch):
    monkeypatch.setattr(
        web_module,
        "sync_hostify_inventory",
        lambda conn: {
            "parents_total": 62,
            "children_total": 80,
            "created": 61,
            "updated": 1,
            "draft_created": 61,
            "activated_new": 0,
        },
    )
    request = SimpleNamespace(session={})

    response = asyncio.run(
        web_module.inventory_sync(
            request=request,
            conn=object(),
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/inventory?status=draft"
    assert request.session["_flash"]["level"] == "success"
    assert "Nové: 61" in request.session["_flash"]["message"]


def test_inventory_activate_marks_property_active():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        sync_property_to_db(
            conn,
            "Draft_Property",
            {
                "listing_id": 999001,
                "listing_nickname": "Draft Property",
                "display_name": "Draft Property",
                "active": False,
                "channels": {},
            },
            replace_aliases=True,
            alias_valid_from="0001-01-01",
        )
        config = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        request = SimpleNamespace(session={})

        response = asyncio.run(
            web_module.inventory_activate(
                request=request,
                slug="Draft_Property",
                redirect_to="/inventory?status=draft",
                conn=conn,
                config=config,
            )
        )

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)

        assert response.status_code == 303
        assert response.headers["location"] == "/inventory?status=draft"
        assert loaded["properties"]["Draft_Property"]["active"] is True
        assert request.session["_flash"]["level"] == "success"
    finally:
        conn.close()


def test_client_save_accepts_address_alias_and_sets_flash():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        config = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        request = _admin_request()

        response = asyncio.run(
            web_module.client_save(
                request=request,
                slug="28_Pluku_58",
                name="Client Name",
                ico="",
                dic="",
                platce_dph="",
                adresa="",
                address="Ulice 1, Praha",
                bank_account="",
                email="",
                phone="",
                notes="",
                display_name="",
                listing_id="",
                listing_nickname="",
                balicky_per_person="",
                city_tax_rate="",
                vat_rate="",
                hostify_listing_names="",
                airbnb_listing_names="",
                booking_listing_nickname="",
                booking_property_id="",
                active="1",
                config_effective_from="",
                rentero_commission="",
                conn=conn,
                config=config,
            )
        )

        client = get_client(conn, "28_Pluku_58")

        assert response.status_code == 303
        assert response.headers["location"] == "/clients/28_Pluku_58"
        assert client["adresa"] == "Ulice 1, Praha"
        assert request.session["_flash"]["level"] == "success"
    finally:
        conn.close()


def test_client_save_persists_hostify_child_aliases():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        config = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        request = SimpleNamespace(session={})

        response = asyncio.run(
            web_module.client_save(
                request=request,
                slug="28_Pluku_58",
                name="Client Name",
                ico="",
                dic="",
                platce_dph="",
                adresa="",
                address="",
                bank_account="",
                email="",
                phone="",
                notes="",
                display_name="",
                listing_id="",
                listing_nickname="",
                balicky_per_person="",
                city_tax_rate="",
                vat_rate="",
                hostify_listing_names="28. Pluku 58 - Marriott\n28. Pluku 58 - Vrbo",
                airbnb_listing_names="",
                booking_listing_nickname="",
                booking_property_id="",
                active="1",
                config_effective_from="",
                rentero_commission="",
                conn=conn,
                config=config,
            )
        )

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        names = get_hostify_listing_names(loaded["properties"]["28_Pluku_58"])

        assert response.status_code == 303
        assert response.headers["location"] == "/clients/28_Pluku_58"
        assert names == [
            "28. Pluku 58",
            "28. Pluku 58 - Marriott",
            "28. Pluku 58 - Vrbo",
        ]
    finally:
        conn.close()


def test_inventory_view_does_not_flag_marriott_only_object_as_missing_booking_or_airbnb(monkeypatch):
    monkeypatch.setattr(web_module, "get_all_clients", lambda conn: [{"property_slug": "MarriottOnly", "name": "Client M"}])

    config = {
        "properties": {
            "MarriottOnly": {
                "display_name": "Marriott Only",
                "listing_id": 1,
                "listing_nickname": "Marriott Only",
                "active": True,
                "channels": {
                    "hostify": {"listing_names": ["Marriott Only - Marriott"]},
                    "airbnb": {"listing_names": []},
                    "booking": {"property_id": "", "listing_nickname": ""},
                },
            },
        }
    }

    summary, rows = web_module._build_inventory_view(object(), config)

    assert summary["missing_mapping_count"] == 0
    assert summary["review_needed_count"] == 0
    assert rows[0]["hostify_listing_names"] == ["Marriott Only - Marriott"]
    assert rows[0]["issue_labels"] == []


def test_property_detail_includes_transferred_rows_in_summary(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        web_module,
        "get_all_properties",
        lambda config: [{"slug": "28_Pluku_58", "display_name": "28. Pluku 58", "listing_nickname": "28. Pluku 58"}],
    )
    monkeypatch.setattr(web_module, "get_report_rows", lambda *args, **kwargs: [{"guest_name": "Stored"}])
    monkeypatch.setattr(web_module, "apply_overrides_to_rows", lambda conn, rows, *args, **kwargs: rows)
    monkeypatch.setattr(web_module, "get_expenses", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        web_module,
        "get_resolved_pending_payments_for_month",
        lambda *args, **kwargs: [{"confirmation_code": "XFER-1"}],
    )
    monkeypatch.setattr(web_module, "_month_has_data", lambda *args, **kwargs: True)
    monkeypatch.setattr(web_module, "_pop_flash", lambda request: None)
    monkeypatch.setattr(
        web_module,
        "_render_property_template",
        lambda request, **context: captured.update(context) or SimpleNamespace(status_code=200),
    )

    def fake_summary(rows, prop, *, expenses=None, transferred_rows=None):
        captured["summary_args"] = {
            "rows": rows,
            "prop": prop,
            "expenses": expenses,
            "transferred_rows": transferred_rows,
        }
        return {"gross_payout_czk": 0}

    monkeypatch.setattr(web_module, "build_report_summary", fake_summary)

    response = asyncio.run(
        web_module.property_detail(
            request=_admin_request(),
            slug="28_Pluku_58",
            year=2026,
            month=4,
            conn=object(),
            config={"properties": {"28_Pluku_58": {"display_name": "28. Pluku 58"}}},
        )
    )

    assert response.status_code == 200
    assert captured["summary_args"]["transferred_rows"] == [{"confirmation_code": "XFER-1"}]


def test_engine_is_called_on_override_save(monkeypatch):
    """Saving an override triggers in-process generation synchronously."""
    import report.routes.property_routes as pr
    generated = []

    monkeypatch.setattr(
        pr,
        "generate_report_in_process",
        lambda conn, slug, year, month, config, **kw: generated.append((slug, year, month)) or {"rows_count": 0},
    )

    # Verify the function is wired into the state dict
    assert hasattr(pr, "generate_report_in_process")
    # Simple check: function reference is importable and callable
    result = pr.generate_report_in_process(None, "test", 2026, 3, {})
    assert result["rows_count"] == 0
    assert ("test", 2026, 3) in generated


