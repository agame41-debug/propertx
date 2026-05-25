from report.db import get_connection
from report.db_object_profiles import insert_segment
from report.config import resolve_property_config


def test_resolve_overlays_month_profile():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "rentero_commission": 0.15})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient", "rentero_commission": 0.03})
    base = {"properties": {"x": {"channels": {"airbnb": {"listing_names": ["L"]}}, "client_type": "rentero"}}}
    apr = resolve_property_config(conn, "x", 2026, 4, base)
    may = resolve_property_config(conn, "x", 2026, 5, base)
    assert apr["client_type"] == "klient"
    assert may["client_type"] == "z_klient" and may["rentero_commission"] == 0.03
    assert may["channels"]["airbnb"]["listing_names"] == ["L"]
    assert may["slug"] == "x"
    conn.close()
