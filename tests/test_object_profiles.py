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


def test_no_overlap_invariant_after_random_op_sequences():
    """Integrity: after arbitrary onward/this-month-only sequences from a full-
    coverage seed, every month must resolve to EXACTLY one covering segment (the
    dashboard JOIN double-counts revenue on overlap, with no error)."""
    import random
    conn = get_connection(":memory:")
    _seed_open(conn)  # [NULL, NULL] full coverage
    rnd = random.Random(20260525)
    for _ in range(60):
        mth = rnd.randint(1, 12)
        if rnd.random() < 0.5:
            set_profile_from_month_onward(conn, "x", 2026, mth, {"owner_name": f"o{mth}"})
        else:
            set_profile_this_month_only(conn, "x", 2026, mth, {"owner_name": f"m{mth}"})
    for mth in range(1, 13):
        m = ym(2026, mth)
        c = conn.execute(
            """SELECT COUNT(*) AS c FROM report_object_profiles
               WHERE slug='x'
                 AND (valid_from_ym IS NULL OR valid_from_ym <= ?)
                 AND (valid_to_ym IS NULL OR valid_to_ym >= ?)""",
            (m, m),
        ).fetchone()["c"]
        assert c == 1, f"month {m} resolved to {c} segments (expected exactly 1)"
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


def test_client_save_payload_writes_segment_from_month_onward():
    """The save handler's profile write path: 'from month onward' creates a segment
    starting at the anchor month and leaves earlier months on the old owner."""
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, None, {"owner_name": "Old", "client_type": "klient"})
    set_profile_from_month_onward(conn, "x", 2026, 5,
                                  {"owner_name": "New s.r.o.", "ico": "999", "client_type": "z_klient"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["client_type"] == "z_klient"
    assert get_object_profile(conn, "x", 2026, 5)["ico"] == "999"
    conn.close()


def test_new_segment_defaults_balicky_249_commission_015():
    """New objects default to 15% commission and 249 Kč/person bundle."""
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('n','t','t')")
    insert_segment(conn, "n", None, None, {"owner_name": "X"})  # bundle/commission omitted
    seg = get_object_profile(conn, "n", 2026, 5)
    assert seg["balicky_per_person"] == 249
    assert seg["rentero_commission"] == 0.15
    conn.close()


def test_report_object_profiles_table_exists():
    conn = get_connection(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(report_object_profiles)")}
    assert {"id", "slug", "valid_from_ym", "valid_to_ym", "owner_name",
            "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
            "rentero_commission", "stredisko", "active", "source"} <= cols
    conn.close()


def test_backfill_tolerates_null_rate_legacy_rows():
    """report_objects rate columns are nullable; the profile columns are NOT NULL.
    A legacy row with NULL rates must backfill to defaults, not crash on the
    constraint (regression for the present-but-None merge in insert_segment)."""
    conn = get_connection(":memory:")
    conn.execute(
        """INSERT INTO report_objects
              (slug, display_name, city_tax_rate, vat_rate, rentero_commission,
               balicky_per_person, active, created_at, updated_at)
           VALUES ('nul','Nul',NULL,NULL,NULL,NULL,1,'t','t')"""
    )
    conn.commit()
    conn.execute("DELETE FROM report_object_profiles WHERE slug='nul'")
    assert backfill_object_profiles(conn) == 1  # would raise IntegrityError before the fix
    seg = get_object_profile(conn, "nul", 2026, 5)
    assert seg["rentero_commission"] == 0.15
    assert seg["vat_rate"] == 0.21
    assert seg["balicky_per_person"] == 249
    assert seg["city_tax_rate"] == 0
    assert seg["client_type"] == "rentero"
    conn.close()


def test_this_month_only_splits_bounded_segment_into_three():
    """this-month-only on a CLOSED [from,to] segment yields three parts; the right
    part carries the ORIGINAL values (covers the orig_to > m branch that the
    open-segment split test never exercises)."""
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", "2026-03", "2026-08", {"client_type": "klient", "owner_name": "Base"})
    set_profile_this_month_only(conn, "x", 2026, 5, {"owner_name": "JustMay"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Base"
    assert get_object_profile(conn, "x", 2026, 5)["owner_name"] == "JustMay"
    assert get_object_profile(conn, "x", 2026, 6)["owner_name"] == "Base"   # right part = original
    assert get_object_profile(conn, "x", 2026, 8)["owner_name"] == "Base"
    assert get_object_profile(conn, "x", 2026, 9) is None                   # outside original range
    bounds = sorted((s["valid_from_ym"], s["valid_to_ym"])
                    for s in list_object_profile_segments(conn, "x"))
    assert bounds == [("2026-03", "2026-04"), ("2026-05", "2026-05"), ("2026-06", "2026-08")]
    conn.close()
