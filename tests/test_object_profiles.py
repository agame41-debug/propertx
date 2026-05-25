from report.db import get_connection
from report.db_object_profiles import (
    ym, prev_ym, next_ym, insert_segment, get_object_profile,
    list_object_profile_segments,
)


def test_ym_helpers():
    assert ym(2026, 5) == "2026-05"
    assert prev_ym("2026-01") == "2025-12"
    assert next_ym("2026-12") == "2027-01"


def test_resolution_picks_segment_covering_month():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "owner_name": "Old"})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient", "owner_name": "New"})
    assert get_object_profile(conn, "x", 2026, 3)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 4)["client_type"] == "klient"
    assert get_object_profile(conn, "x", 2026, 5)["client_type"] == "z_klient"
    assert get_object_profile(conn, "x", 2026, 9)["owner_name"] == "New"
    segs = list_object_profile_segments(conn, "x")
    assert [s["valid_from_ym"] for s in segs] == [None, "2026-05"]
    conn.close()


def test_report_object_profiles_table_exists():
    conn = get_connection(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(report_object_profiles)")}
    assert {"id", "slug", "valid_from_ym", "valid_to_ym", "owner_name",
            "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
            "rentero_commission", "stredisko", "active", "source"} <= cols
    conn.close()
