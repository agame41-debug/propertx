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


from report.db_object_profiles import (
    set_profile_from_month_onward, set_profile_this_month_only,
    update_profile_segment,
)


def _seed_open(conn, slug="x", **fields):
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES (?,?,?)", (slug, "t", "t"))
    insert_segment(conn, slug, None, None, {"client_type": "klient", "owner_name": "Old", **fields})


def test_from_month_onward_trims_prior_and_carries_forward():
    conn = get_connection(":memory:")
    _seed_open(conn, city_tax_rate=50)
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "New"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    may = get_object_profile(conn, "x", 2026, 5)
    assert may["owner_name"] == "New" and may["city_tax_rate"] == 50
    assert get_object_profile(conn, "x", 2027, 1)["owner_name"] == "New"
    segs = list_object_profile_segments(conn, "x")
    assert segs[0]["valid_to_ym"] == "2026-04"
    assert segs[1]["valid_from_ym"] == "2026-05" and segs[1]["valid_to_ym"] is None
    conn.close()


def test_from_month_onward_preserves_future_segment():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_from_month_onward(conn, "x", 2026, 8, {"owner_name": "Future"})
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "Mid"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["owner_name"] == "Mid"
    assert get_object_profile(conn, "x", 2026, 7)["owner_name"] == "Mid"
    assert get_object_profile(conn, "x", 2026, 8)["owner_name"] == "Future"
    conn.close()


def test_this_month_only_splits_into_three():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_this_month_only(conn, "x", 2026, 5, {"owner_name": "JustMay"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["owner_name"] == "JustMay"
    assert get_object_profile(conn, "x", 2026, 6)["owner_name"] == "Old"
    conn.close()


def test_no_overlap_invariant_holds():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "A"})
    set_profile_this_month_only(conn, "x", 2026, 3, {"owner_name": "B"})
    for mth in range(1, 13):
        assert get_object_profile(conn, "x", 2026, mth) is not None
    conn.close()


from report.db_object_profiles import backfill_object_profiles


def test_backfill_collapses_legacy_into_open_segment():
    conn = get_connection(":memory:")
    conn.execute(
        """INSERT INTO report_objects (slug, display_name, client_type, city_tax_rate,
              vat_rate, rentero_commission, balicky_per_person, active, created_at, updated_at)
           VALUES ('a','A','z_klient',50,0.12,0.03,249,1,'t','t')"""
    )
    conn.execute(
        """INSERT INTO clients (property_slug, name, ico, platce_dph, adresa, updated_at)
           VALUES ('a','Owner s.r.o.','123',1,'Praha','t')"""
    )
    conn.commit()
    # Wipe the segment auto-created by migrations to test the backfill directly
    conn.execute("DELETE FROM report_object_profiles WHERE slug='a'")
    n = backfill_object_profiles(conn)
    assert n == 1
    seg = get_object_profile(conn, "a", 2026, 5)
    assert seg["client_type"] == "z_klient"
    assert seg["owner_name"] == "Owner s.r.o."
    assert seg["ico"] == "123" and seg["platce_dph"] == 1
    assert seg["valid_from_ym"] is None and seg["valid_to_ym"] is None
    assert backfill_object_profiles(conn) == 0
    conn.close()


def test_report_object_profiles_table_exists():
    conn = get_connection(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(report_object_profiles)")}
    assert {"id", "slug", "valid_from_ym", "valid_to_ym", "owner_name",
            "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
            "rentero_commission", "stredisko", "active", "source"} <= cols
    conn.close()
