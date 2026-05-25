import json

from report.db import get_connection
from report.db_object_profiles import insert_segment
from report.web_support import _build_dashboard_maps


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
