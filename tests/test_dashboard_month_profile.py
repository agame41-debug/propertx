import json

from report.db import get_connection
from report.db_object_profiles import insert_segment, set_profile_from_month_onward
from report.web_support import (
    _build_dashboard_maps, _resolve_dashboard_profile_overlay, _is_rentero_side,
)


def test_is_rentero_side_classification():
    # Rentero-owned: always Rentero-side, regardless of owner.
    assert _is_rentero_side("rentero", "") is True
    assert _is_rentero_side("rentero", None) is True
    assert _is_rentero_side(None, "") is True  # default type = rentero
    # klient/z_klient with NO owner → client side (the Kenji empty-owner bug).
    assert _is_rentero_side("klient", "") is False
    assert _is_rentero_side("klient", None) is False
    assert _is_rentero_side("z_klient", "  ") is False
    # klient/z_klient with a real (non-Rentero) owner → client side.
    assert _is_rentero_side("klient", "BND Pyramid Group s.r.o.") is False
    # z_klient owned by a Rentero entity → still Rentero-side.
    assert _is_rentero_side("z_klient", "Rentero Investments") is True
    assert _is_rentero_side("klient", "Rentero Home A") is True


def _add_row(conn, slug, y, m, payout, cena):
    data = json.dumps({"payout_czk": payout, "cena_ubytovani_czk": cena,
                       "provize_czk": 0, "verification_status": "MATCHED"})
    conn.execute(
        """INSERT INTO report_rows (slug, year, month, confirmation_code, data, generated_at)
           VALUES (?,?,?,?,?,'t')""",
        (slug, y, m, f"C{y}{m}", data),
    )
    conn.execute(
        """INSERT INTO report_history (slug, year, month, file_path, rows_count, generated_at)
           VALUES (?,?,?,'',1,'t')""", (slug, y, m))
    conn.commit()


def test_dashboard_uses_month_profile_for_fee():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    # klient in April (15% fee on cena, vat 0), z_klient in May (3% of payout)
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "rentero_commission": 0.15, "vat_rate": 0.0})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient"})
    _add_row(conn, "x", 2026, 4, payout=1000, cena=800)
    _add_row(conn, "x", 2026, 5, payout=1000, cena=800)
    props = [{"slug": "x"}]
    history_map, *_rest = _build_dashboard_maps(conn, props, [(2026, 4), (2026, 5)])
    # April: klient fee = cena * 0.15 * (1+0) = 120
    assert round(history_map["x"][(2026, 4)]["rentero_fee_sum_czk"]) == 120
    # May: z_klient fee = payout * 0.03 = 30
    assert round(history_map["x"][(2026, 5)]["rentero_fee_sum_czk"]) == 30
    conn.close()


def test_overlay_month_resolves_displayed_type_and_owner():
    """Regression: the dashboard's displayed client_type/owner must follow the
    month-versioned profile, not the stale base report_objects.client_type. An
    Objekty import that flips an object rentero->klient from April onward must
    show as 'klient' for April+ and stay 'rentero' for March."""
    conn = get_connection(":memory:")
    # Base object stays 'rentero' (Objekty import never touches report_objects).
    conn.execute(
        "INSERT INTO report_objects (slug, display_name, client_type, created_at, updated_at) "
        "VALUES ('Opletalova_45_Leva','Opletalova 45 - levá','rentero','t','t')"
    )
    insert_segment(conn, "Opletalova_45_Leva", None, None,
                   {"client_type": "rentero", "owner_name": ""})
    # Import writes a klient segment from 2026-04 onward.
    set_profile_from_month_onward(conn, "Opletalova_45_Leva", 2026, 4,
                                  {"client_type": "klient",
                                   "owner_name": "D-Corp Property A s.r.o."}, source="tsv")
    slugs = ["Opletalova_45_Leva"]

    march = _resolve_dashboard_profile_overlay(conn, slugs, 2026, 3)
    assert march["Opletalova_45_Leva"]["client_type"] == "rentero"

    for mth in (4, 5):
        ov = _resolve_dashboard_profile_overlay(conn, slugs, 2026, mth)
        assert ov["Opletalova_45_Leva"]["client_type"] == "klient"
        assert ov["Opletalova_45_Leva"]["owner_name"] == "D-Corp Property A s.r.o."
    conn.close()


def test_overlay_exposes_month_resolved_active():
    """The dashboard overlay returns the segment's `active` for the covering month,
    so the route can hide an object that the profile deactivates from month M onward
    (even while base report_objects.active stays 1)."""
    conn = get_connection(":memory:")
    conn.execute(
        "INSERT INTO report_objects (slug, display_name, client_type, active, created_at, updated_at) "
        "VALUES ('x','X','rentero',1,'t','t')"
    )
    insert_segment(conn, "x", None, None, {"client_type": "rentero", "active": 1})
    set_profile_from_month_onward(conn, "x", 2026, 6, {"active": 0})  # deactivate from June
    assert _resolve_dashboard_profile_overlay(conn, ["x"], 2026, 5)["x"]["active"] == 1
    assert _resolve_dashboard_profile_overlay(conn, ["x"], 2026, 6)["x"]["active"] == 0
    assert _resolve_dashboard_profile_overlay(conn, ["x"], 2026, 7)["x"]["active"] == 0
    conn.close()
