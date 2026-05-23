"""Preventive regen on object-config / alias changes.

Root cause this guards against: when a Hostify listing is renamed, the
object's old report rows silently break (CSV rows fall to CHYBÍ_V_HOSTIFY,
Hostify reservations become orphans).  Adding/correcting the alias via the
Clients page used to NOT mark the affected months stale nor regenerate
them — so reports stayed broken until the next nightly Hostify sync.

These tests lock in:
  1. `_apply_object_config_change_impacts` marks OPEN generated months STALE
     and auto-starts regen, while LOCKED months are only notified (not
     rewritten) and never-generated EMPTY months are skipped.
  2. `client_save` triggers those impacts when the object's aliases change.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("RENTERO_ALLOW_INSECURE_DEFAULTS", "1")

import threading

import report.web as web_module
from report.db import (
    MONTH_DATA_STATE_GENERATED,
    MONTH_DATA_STATE_STALE,
    MONTH_STATUS_LOCKED,
    get_connection,
    get_report_month_state,
    list_report_month_notifications,
    set_report_month_locked,
    touch_report_month_generation,
)


def test_apply_object_config_change_impacts_stales_open_and_notifies_locked(monkeypatch):
    conn = get_connection(":memory:")
    try:
        # OPEN + generated → should be marked STALE and auto-started.
        touch_report_month_generation(conn, "Obj_A", 2026, 4)
        # LOCKED + generated → notify only, never rewritten.
        touch_report_month_generation(conn, "Obj_A", 2026, 3)
        set_report_month_locked(conn, "Obj_A", 2026, 3, locked=True, actor="admin")
        # EMPTY (never generated) → skipped.
        get_report_month_state(conn, "Obj_A", 2026, 5, create=True)
        # Unrelated object must not be touched.
        touch_report_month_generation(conn, "Obj_B", 2026, 4)

        # Suppress the background regen thread so the test stays in-process.
        monkeypatch.setattr(
            threading, "Thread", lambda *a, **kw: SimpleNamespace(start=lambda: None)
        )
        monkeypatch.setattr(web_module, "_db_path_for_connection", lambda _conn: ":memory:")

        result = web_module._apply_object_config_change_impacts(conn, "Obj_A", config={})

        assert ("Obj_A", 2026, 4) in result["auto_started"]
        assert ("Obj_A", 2026, 3) in result["locked_notified"]
        assert ("Obj_A", 2026, 3) not in result["auto_started"]
        assert all(slug == "Obj_A" for slug, _y, _m in result["auto_started"])

        open_state = get_report_month_state(conn, "Obj_A", 2026, 4)
        assert open_state["data_state"] == MONTH_DATA_STATE_STALE
        assert open_state["has_new_data_since_generation"] == 1

        locked_state = get_report_month_state(conn, "Obj_A", 2026, 3)
        assert locked_state["status"] == MONTH_STATUS_LOCKED
        # Locked month is NOT degraded to stale.
        assert locked_state["data_state"] == MONTH_DATA_STATE_GENERATED

        # EMPTY month neither staled nor notified.
        assert ("Obj_A", 2026, 5) not in result["auto_started"]
        assert ("Obj_A", 2026, 5) not in result["locked_notified"]

        # Unrelated object untouched.
        assert get_report_month_state(conn, "Obj_B", 2026, 4)["data_state"] == MONTH_DATA_STATE_GENERATED

        notes = list_report_month_notifications(conn, slug="Obj_A", year=2026, month=3)
        assert len(notes) == 1
        assert "uzamčený měsíc" in notes[0]["message"]
    finally:
        conn.close()


def test_client_save_triggers_impacts_on_alias_change(monkeypatch):
    calls: dict = {}
    prop = {
        "slug": "Obj_A",
        "display_name": "A",
        "listing_nickname": "A",
        "listing_id": 1,
        "channels": {},
        "client_type": "rentero",
        "vat_rate": 0.21,
        "rentero_commission": 0.15,
        "balicky_per_person": 0,
        "city_tax_rate": 50,
        "active": True,
    }

    monkeypatch.setattr(web_module, "get_all_properties", lambda config: [prop])
    monkeypatch.setattr(web_module, "save_client", lambda conn, data: calls.__setitem__("client", data))
    monkeypatch.setattr(web_module, "sync_property_to_db", lambda *a, **kw: calls.__setitem__("synced", True))

    # First call (before sync) → no alias; second call (after sync) → one alias.
    alias_seq = [
        [],
        [{"channel": "hostify", "alias_type": "listing_nickname",
          "alias_value": "A renamed", "is_active": 1}],
    ]
    monkeypatch.setattr(
        web_module, "get_report_object_aliases",
        lambda conn, slug, **kw: alias_seq.pop(0) if alias_seq else [],
    )
    monkeypatch.setattr(
        web_module, "_apply_object_config_change_impacts",
        lambda conn, slug, **kw: (calls.__setitem__("impact_slug", slug)
                                  or {"auto_started": [("Obj_A", 2026, 4)], "locked_notified": []}),
    )
    monkeypatch.setattr(
        web_module, "_set_flash",
        lambda req, level, message, detail=None: req.session.update(
            {"_flash": {"level": level, "message": message, "detail": detail}}
        ),
    )

    request = SimpleNamespace(
        session={},
        state=SimpleNamespace(user={"id": 1, "username": "admin", "role": "admin"}),
        headers={},
    )
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(
            web_module.client_save(
                request=request, slug="Obj_A",
                name="", ico="", dic="", platce_dph="", adresa="", address="",
                bank_account="", email="", phone="", notes="",
                display_name="A", listing_id="1", listing_nickname="A",
                balicky_per_person="0", city_tax_rate="50", vat_rate="0.21",
                hostify_listing_names="A renamed", airbnb_listing_names="",
                booking_listing_nickname="", booking_property_id="",
                active="on", config_effective_from="",
                rentero_commission="15", client_type="rentero",
                conn=conn, config={},
            )
        )
    finally:
        conn.close()

    assert response.status_code == 303
    assert calls.get("synced") is True
    # Alias set changed → preventive impacts MUST fire for this slug.
    assert calls.get("impact_slug") == "Obj_A"


def test_inventory_sync_triggers_impacts_for_changed_objects(monkeypatch):
    impacted: list = []
    monkeypatch.setattr(web_module, "sync_hostify_inventory", lambda conn: {"created": 0, "updated": 1})
    monkeypatch.setattr(web_module, "_format_inventory_sync_summary", lambda s: ("Sync OK", None))

    # Before sync: Obj_A has no alias. After sync (Hostify rename detected):
    # Obj_A gains a renamed nickname → must be regenerated.
    alias_seq = [
        [],
        [{"report_object_slug": "Obj_A", "channel": "hostify",
          "alias_type": "listing_nickname", "alias_value": "A renamed", "is_active": 1}],
    ]
    monkeypatch.setattr(
        web_module, "get_report_object_aliases",
        lambda conn, *a, **kw: alias_seq.pop(0) if alias_seq else [],
    )
    monkeypatch.setattr(
        web_module, "_apply_object_config_change_impacts",
        lambda conn, slug, **kw: (impacted.append(slug)
                                  or {"auto_started": [("Obj_A", 2026, 4)], "locked_notified": []}),
    )
    monkeypatch.setattr(
        web_module, "_set_flash",
        lambda req, level, message, detail=None: req.session.update(
            {"_flash": {"level": level, "message": message, "detail": detail}}
        ),
    )

    request = SimpleNamespace(
        session={},
        state=SimpleNamespace(user={"id": 1, "username": "admin", "role": "admin"}),
        headers={},
    )
    conn = get_connection(":memory:")
    try:
        response = asyncio.run(web_module.inventory_sync(request=request, conn=conn, config={}))
    finally:
        conn.close()

    assert response.status_code == 303
    assert impacted == ["Obj_A"]
    assert "Auto-regenerace: 1" in (request.session["_flash"]["detail"] or "")
