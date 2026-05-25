import pytest

from report.db import get_connection
from report.db_object_profiles import insert_segment
from report.config import resolve_property_config
import report.engine as engine


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


def test_engine_resolves_month_profile(monkeypatch):
    """Engine must overlay the month segment so client_type/rates match the month."""
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient"})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient"})
    config = {"properties": {"x": {"channels": {}, "client_type": "rentero"}}}

    captured = {}

    def spy(conn_, slug_, y_, m_, cfg_):
        p = resolve_property_config(conn_, slug_, y_, m_, cfg_)
        captured[(y_, m_)] = p["client_type"]
        raise RuntimeError("stop-after-resolve")

    monkeypatch.setattr("report.engine.resolve_property_config", spy, raising=True)
    for mth in (4, 5):
        with pytest.raises(RuntimeError):
            engine.generate_report_in_process(conn, "x", 2026, mth, config)
    assert captured[(2026, 4)] == "klient"
    assert captured[(2026, 5)] == "z_klient"
    conn.close()
