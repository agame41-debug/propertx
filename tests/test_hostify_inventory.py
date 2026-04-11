from __future__ import annotations

from report.config import get_all_properties, load_runtime_config, sync_json_config_to_db
from report.db import get_connection, list_report_objects
from report.hostify_inventory import sync_hostify_inventory

from tests.test_config_db import BASE_CONFIG


def test_sync_hostify_inventory_creates_new_draft_objects_and_preserves_active_existing():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)

        def list_fetcher(service_pms: int) -> list[dict]:
            assert service_pms == 1
            return [
                {"id": 184988, "nickname": "28. Pluku 58", "name": "28. Pluku 58"},
                {"id": 999001, "nickname": "Mala Stepanska 12", "name": "Mala Stepanska 12"},
            ]

        def children_fetcher(listing_id: int) -> list[dict]:
            if listing_id == 184988:
                return [
                    {
                        "id": 184991,
                        "nickname": "28. Pluku 58 - Bcom",
                        "name": "",
                        "channel_listing_id": "12860254",
                        "fs_integration_type": 22,
                    },
                    {
                        "id": 206426,
                        "nickname": "28. Pluku 58",
                        "name": "Modern APT City Hideaway",
                        "channel_listing_id": "1253486536317520918",
                        "fs_integration_type": 1,
                    },
                    {
                        "id": 206427,
                        "nickname": "28. Pluku 58 - Marriott",
                        "name": "",
                        "channel_listing_id": "marriott-184988",
                        "fs_integration_type": 77,
                    },
                ]
            if listing_id == 999001:
                return [
                    {
                        "id": 999011,
                        "nickname": "Mala Stepanska 12 - Bcom",
                        "name": "",
                        "channel_listing_id": "99887766",
                        "fs_integration_type": 22,
                    },
                    {
                        "id": 999012,
                        "nickname": "Mala Stepanska 12",
                        "name": "Quiet Loft Prague Center",
                        "channel_listing_id": "airbnb-999012",
                        "fs_integration_type": 1,
                    },
                ]
            return []

        summary = sync_hostify_inventory(
            conn,
            list_fetcher=list_fetcher,
            children_fetcher=children_fetcher,
        )

        assert summary["parents_total"] == 2
        assert summary["children_total"] == 5
        assert summary["created"] == 1
        assert summary["updated"] == 1
        assert summary["draft_created"] == 1
        assert summary["active_preserved"] == 1
        assert summary["activated_new"] == 0

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)
        all_props = {prop["slug"]: prop for prop in get_all_properties(loaded)}
        active_props = {prop["slug"] for prop in get_all_properties(loaded, active_only=True)}

        assert "28_Pluku_58" in all_props
        assert "Mala_Stepanska_12" in all_props
        assert active_props == {"28_Pluku_58"}

        new_prop = all_props["Mala_Stepanska_12"]
        assert new_prop["listing_id"] == 999001
        assert new_prop["active"] is False
        assert new_prop["channels"]["booking"]["property_id"] == "99887766"
        assert new_prop["channels"]["booking"]["listing_nickname"] == "Mala Stepanska 12 - Bcom"
        assert all_props["28_Pluku_58"]["channels"]["hostify"]["listing_names"] == [
            "28. Pluku 58 - Bcom",
            "28. Pluku 58 - Marriott",
        ]
        assert new_prop["channels"]["airbnb"]["listing_names"] == [
            "Mala Stepanska 12",
            "Quiet Loft Prague Center",
        ]
        assert new_prop["channels"]["hostify"]["listing_names"] == [
            "Mala Stepanska 12 - Bcom",
        ]

        assert [row["slug"] for row in list_report_objects(conn, active_only=True)] == ["28_Pluku_58"]
    finally:
        conn.close()


def test_sync_hostify_inventory_dry_run_does_not_persist_changes():
    conn = get_connection(":memory:")
    try:
        sync_json_config_to_db(conn, BASE_CONFIG)

        summary = sync_hostify_inventory(
            conn,
            list_fetcher=lambda service_pms: [
                {"id": 999001, "nickname": "Mala Stepanska 12", "name": "Mala Stepanska 12"}
            ],
            children_fetcher=lambda listing_id: [],
            dry_run=True,
        )

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)

        assert summary["created"] == 1
        assert "Mala_Stepanska_12" not in loaded["properties"]
        assert [row["slug"] for row in list_report_objects(conn)] == ["28_Pluku_58"]
    finally:
        conn.close()


def test_sync_hostify_inventory_preserves_active_default_for_json_backed_existing_properties():
    conn = get_connection(":memory:")
    try:
        summary = sync_hostify_inventory(
            conn,
            existing_properties=[
                {
                    "slug": "28_Pluku_58",
                    "display_name": "28. Pluku 58",
                    "listing_id": 184988,
                    "listing_nickname": "28. Pluku 58",
                    "channels": {},
                }
            ],
            list_fetcher=lambda service_pms: [
                {"id": 184988, "nickname": "28. Pluku 58", "name": "28. Pluku 58"}
            ],
            children_fetcher=lambda listing_id: [],
        )

        loaded = load_runtime_config("/tmp/does-not-exist-properties.json", db_conn=conn)

        assert summary["updated"] == 1
        assert summary["active_preserved"] == 1
        assert loaded["properties"]["28_Pluku_58"]["active"] is True
    finally:
        conn.close()
