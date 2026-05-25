from report.db import get_connection
from report.db_object_profiles import insert_segment, get_object_profile, list_object_profile_segments
from report.objekty_import import (
    parse_objekty_tsv, objekty_delta_summary, apply_objekty_import,
    CATEGORY_TO_CLIENT_TYPE,
)


HEADER = ("category\tstredisko\tcanonical_name\towner_name\tico\tplatce_dph\tdic\t"
          "aliases\tulice\tmisto\tpsc\tstat\tkod_statu\tbanka\tucet\tkod_banky\t"
          "internet\tost_sluzby\tost_sluzby_popis\tost_sluzby2\tost_sluzby2_popis")


def _tsv(*rows):
    lines = ["# comment line", HEADER, *rows]
    return ("\n".join(lines)).encode("utf-8")


def _seed(conn, slug, display_name, **prof):
    conn.execute(
        "INSERT INTO report_objects (slug, display_name, created_at, updated_at) VALUES (?,?,?,?)",
        (slug, display_name, "t", "t"),
    )
    insert_segment(conn, slug, None, None, {"owner_name": "OldOwner", "client_type": "rentero",
                                            "city_tax_rate": 50, **prof})


def test_parse_skips_comments_and_maps_category():
    content = _tsv(
        "standard\tFRAN_50\tFrancouzská 50\tBuild with us\t22383115\t0\t\tFrancouzska 50\t\t\t\t\t\t\t\t\t739.26\t826.45\tVýměna\t0\t",
        "todo\tLEGE\tLegerova\t\t\t0\t0\tLegerova\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    rows = parse_objekty_tsv(content)
    assert len(rows) == 2
    assert rows[0]["category"] == "standard"
    assert rows[0]["canonical_name"] == "Francouzská 50"
    assert rows[0]["owner_name"] == "Build with us"
    assert rows[0]["internet"] == "739.26"
    assert rows[1]["category"] == "todo"
    assert CATEGORY_TO_CLIENT_TYPE["zrezim"] == "z_klient"


def test_apply_writes_segment_from_effective_month_and_skips_todo():
    conn = get_connection(":memory:")
    _seed(conn, "Francouzska_50", "Francouzská 50")
    content = _tsv(
        "standard\tFRAN_50\tFrancouzská 50\tBuild with us\t22383115\t1\tCZ22383115\tFrancouzska 50\tPreslova 19\tPraha\t15000\t\t\t\t\t\t739.26\t0\t\t0\t",
        "todo\tLEGE\tLegerova\t\t\t0\t0\tLegerova\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    summary = apply_objekty_import(conn, content, "2026-05")
    assert "Francouzska_50" in summary["updated_slugs"]
    # todo never imported (no object exists anyway, but also must not be in updated)
    assert all("Leg" not in s for s in summary["updated_slugs"])
    apr = get_object_profile(conn, "Francouzska_50", 2026, 4)
    may = get_object_profile(conn, "Francouzska_50", 2026, 5)
    assert apr["owner_name"] == "OldOwner"               # past unchanged
    assert may["owner_name"] == "Build with us"           # from May
    assert may["client_type"] == "klient"                 # standard -> klient
    assert may["city_tax_rate"] == 50                     # rate carried forward (not in TSV)
    assert may["ico"] == "22383115" and may["platce_dph"] == 1


def test_unmatched_rows_reported_not_applied():
    conn = get_connection(":memory:")
    _seed(conn, "Francouzska_50", "Francouzská 50")
    content = _tsv(
        "standard\tNOPE\tNeznámý objekt\tX\t1\t0\t\tNeznamy\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    summary = apply_objekty_import(conn, content, "2026-05")
    assert "Neznámý objekt" in summary["unmatched"]
    assert summary["updated_slugs"] == []


def test_reimport_same_data_is_idempotent_no_new_segment():
    conn = get_connection(":memory:")
    _seed(conn, "Francouzska_50", "Francouzská 50")
    content = _tsv(
        "standard\tFRAN_50\tFrancouzská 50\tBuild with us\t1\t0\t\tFrancouzska 50\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    apply_objekty_import(conn, content, "2026-05")
    segs_after_first = len(list_object_profile_segments(conn, "Francouzska_50"))
    apply_objekty_import(conn, content, "2026-05")  # same file, same month
    segs_after_second = len(list_object_profile_segments(conn, "Francouzska_50"))
    assert segs_after_first == segs_after_second  # no duplicate segment


def test_import_uploaded_source_objekty_applies_and_reimports_for_new_month():
    from report.source_registry import import_uploaded_source, SOURCE_TYPES
    assert "objekty" in SOURCE_TYPES
    conn = get_connection(":memory:")
    _seed(conn, "Francouzska_50", "Francouzská 50")
    content = _tsv(
        "standard\tFRAN_50\tFrancouzská 50\tBuild with us\t1\t0\t\tFrancouzska 50\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    s1 = import_uploaded_source(conn, "objekty", "Objekty.tsv", content,
                               imported_by="t", effective_ym="2026-05")
    assert s1["updated_count"] == 1
    assert get_object_profile(conn, "Francouzska_50", 2026, 5)["owner_name"] == "Build with us"
    # Same bytes, new month → not blocked by SHA dedup; applies again forward.
    s2 = import_uploaded_source(conn, "objekty", "Objekty.tsv", content,
                               imported_by="t", effective_ym="2026-07")
    assert s2["is_duplicate"] is False
    # April still old owner, May+ new owner (unchanged), July+ still new owner.
    assert get_object_profile(conn, "Francouzska_50", 2026, 4)["owner_name"] == "OldOwner"
    assert get_object_profile(conn, "Francouzska_50", 2026, 8)["owner_name"] == "Build with us"
    conn.close()


def test_delta_summary_counts_without_writing():
    conn = get_connection(":memory:")
    _seed(conn, "Francouzska_50", "Francouzská 50")
    content = _tsv(
        "standard\tFRAN_50\tFrancouzská 50\tBuild with us\t1\t0\t\tFrancouzska 50\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
        "standard\tNOPE\tNeznámý\tX\t1\t0\t\tNeznamy\t\t\t\t\t\t\t\t\t0\t0\t\t0\t",
    )
    delta = objekty_delta_summary(conn, content, "2026-05")
    assert delta["updated_count"] == 1
    assert delta["unmatched_count"] == 1
    # delta must not write anything
    assert len(list_object_profile_segments(conn, "Francouzska_50")) == 1
    conn.close()
