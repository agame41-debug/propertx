from __future__ import annotations

import json

from report.config import (
    get_airbnb_listing_names,
    get_all_properties,
    get_all_slugs,
    get_booking_config,
    get_hostify_listing_names,
    load_runtime_config,
    sync_property_to_db,
    sync_json_config_to_db,
)
from report.db import (
    get_connection,
    get_report_object_aliases,
    set_report_object_aliases,
    upsert_report_object,
)


BASE_CONFIG = {
    "properties": {
        "28_Pluku_58": {
            "listing_id": 184988,
            "listing_nickname": "28. Pluku 58",
            "display_name": "28. Pluku 58",
            "balicky_per_person": 249,
            "city_tax_rate": 50,
            "vat_rate": 0.21,
            "rentero_commission": 0.15,
            "channels": {
                "airbnb": {
                    "listing_names": ["Modern APT City Hideaway"],
                },
                "booking": {
                    "listing_nickname": "28. Pluku 58 - Bcom",
                    "property_id": "12860254",
                },
            },
        }
    }
}


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / "properties.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def test_load_runtime_config_falls_back_to_json_when_db_is_empty(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        loaded = load_runtime_config(path, db_conn=conn)
        assert loaded == BASE_CONFIG
    finally:
        conn.close()


def test_load_runtime_config_prefers_db_backed_object_settings(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        upsert_report_object(
            conn,
            {
                "slug": "28_Pluku_58",
                "display_name": "DB Display Name",
                "hostify_listing_id": 184988,
                "listing_nickname": "28. Pluku 58",
                "balicky_per_person": 249,
                "city_tax_rate": 50,
                "vat_rate": 0.21,
                "rentero_commission": 0.22,
                "active": True,
            },
        )

        loaded = load_runtime_config(path, db_conn=conn)
        prop = loaded["properties"]["28_Pluku_58"]

        assert prop["display_name"] == "DB Display Name"
        assert prop["rentero_commission"] == 0.22
        assert prop["channels"]["booking"]["property_id"] == "12860254"
        assert prop["channels"]["airbnb"]["listing_names"] == ["Modern APT City Hideaway"]
    finally:
        conn.close()


def test_load_runtime_config_can_run_db_only_without_json_file():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        assert loaded["properties"]["28_Pluku_58"]["display_name"] == "28. Pluku 58"
    finally:
        conn.close()


def test_alias_history_is_preserved_and_runtime_uses_active_alias(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        set_report_object_aliases(
            conn,
            "28_Pluku_58",
            "booking",
            "property_id",
            ["99999999"],
        )

        aliases = get_report_object_aliases(
            conn,
            "28_Pluku_58",
            channel="booking",
            alias_type="property_id",
            include_inactive=True,
        )
        assert [row["alias_value"] for row in aliases] == ["12860254", "99999999"]
        assert aliases[0]["is_active"] == 0
        assert aliases[0]["valid_to"] is not None
        assert aliases[1]["is_active"] == 1

        loaded = load_runtime_config(path, db_conn=conn)
        booking = loaded["properties"]["28_Pluku_58"]["channels"]["booking"]
        assert booking["property_id"] == "99999999"
    finally:
        conn.close()


def test_month_context_uses_aliases_valid_for_that_month(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        set_report_object_aliases(
            conn,
            "28_Pluku_58",
            "hostify",
            "listing_nickname",
            ["28. Pluku 58 Renamed"],
            valid_from="2026-06-01",
        )
        set_report_object_aliases(
            conn,
            "28_Pluku_58",
            "booking",
            "property_id",
            ["99999999"],
            valid_from="2026-06-01",
        )
        set_report_object_aliases(
            conn,
            "28_Pluku_58",
            "airbnb",
            "listing_name",
            ["Modern APT New Brand"],
            valid_from="2026-06-01",
        )

        loaded = load_runtime_config(path, db_conn=conn)
        prop = loaded["properties"]["28_Pluku_58"]

        assert get_hostify_listing_names(prop, year=2026, month=3) == ["28. Pluku 58"]
        assert get_hostify_listing_names(prop, year=2026, month=6) == ["28. Pluku 58 Renamed"]
        assert get_booking_config(prop, year=2026, month=3)["property_id"] == "12860254"
        assert get_booking_config(prop, year=2026, month=6)["property_id"] == "99999999"
        assert get_airbnb_listing_names(prop, year=2026, month=3) == ["Modern APT City Hideaway"]
        assert get_airbnb_listing_names(prop, year=2026, month=6) == ["Modern APT New Brand"]
    finally:
        conn.close()


def test_get_hostify_listing_names_includes_extra_child_aliases_from_channel_config(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        loaded = load_runtime_config(path, db_conn=conn)
        prop = loaded["properties"]["28_Pluku_58"]
        updated_prop = json.loads(json.dumps(prop))
        updated_prop.setdefault("channels", {}).setdefault("hostify", {})["listing_names"] = [
            "28. Pluku 58 - Marriott",
            "28. Pluku 58 - Vrbo",
        ]

        sync_property_to_db(
            conn,
            "28_Pluku_58",
            updated_prop,
            replace_aliases=False,
            alias_valid_from="0001-01-01",
        )

        loaded = load_runtime_config(path, db_conn=conn)
        names = get_hostify_listing_names(loaded["properties"]["28_Pluku_58"])

        assert names == [
            "28. Pluku 58",
            "28. Pluku 58 - Marriott",
            "28. Pluku 58 - Vrbo",
        ]
    finally:
        conn.close()


def test_sync_property_to_db_respects_alias_effective_from(tmp_path):
    path = _write_config(tmp_path, BASE_CONFIG)
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)
        loaded = load_runtime_config(path, db_conn=conn)
        prop = loaded["properties"]["28_Pluku_58"]
        updated_prop = json.loads(json.dumps(prop))
        updated_prop["channels"]["booking"]["property_id"] = "77777777"

        sync_property_to_db(
            conn,
            "28_Pluku_58",
            updated_prop,
            replace_aliases=True,
            alias_valid_from="2026-08-01",
        )

        aliases = get_report_object_aliases(
            conn,
            "28_Pluku_58",
            channel="booking",
            alias_type="property_id",
            include_inactive=True,
        )
        assert aliases[0]["alias_value"] == "12860254"
        assert aliases[0]["valid_to"] == "2026-07-31"
        assert aliases[1]["alias_value"] == "77777777"
        assert aliases[1]["valid_from"] == "2026-08-01"
    finally:
        conn.close()


def test_active_only_runtime_filters_inactive_objects():
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

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)

        assert set(get_all_slugs(loaded)) == {"28_Pluku_58", "Draft_Property"}
        assert get_all_slugs(loaded, active_only=True) == ["28_Pluku_58"]
        assert {prop["slug"] for prop in get_all_properties(loaded, active_only=True)} == {"28_Pluku_58"}
    finally:
        conn.close()
