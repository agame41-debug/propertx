import pytest

from report.db import get_connection, save_report_rows, log_report_generated
from report.db_admin import upsert_report_object
from report.web_support import _build_dashboard_maps


def _setup_object(conn, slug, client_type):
    upsert_report_object(conn, {
        "slug": slug,
        "display_name": slug,
        "listing_nickname": slug,
        "client_type": client_type,
        "rentero_commission": 0.15,
        "vat_rate": 0.21,
        "active": True,
    })


def _setup_month(conn, slug, year, month):
    rows = [{
        "confirmation_code": f"{slug}-1",
        "payout_czk": 10000.0,
        "cena_ubytovani_czk": 8000.0,
        "verification_status": "MATCHED",
    }]
    save_report_rows(conn, slug, year, month, rows)
    log_report_generated(conn, slug, year, month, f"{slug}.xlsx", rows)


def test_build_dashboard_maps_zeros_fee_only_for_rentero_client_type():
    conn = get_connection(":memory:")
    try:
        _setup_object(conn, "Own", "rentero")
        _setup_object(conn, "Klient", "klient")
        _setup_object(conn, "ZKlient", "z_klient")
        for slug in ("Own", "Klient", "ZKlient"):
            _setup_month(conn, slug, 2026, 4)

        properties = [
            {"slug": "Own", "listing_nickname": "Own", "display_name": "Own"},
            {"slug": "Klient", "listing_nickname": "Klient", "display_name": "Klient"},
            {"slug": "ZKlient", "listing_nickname": "ZKlient", "display_name": "ZKlient"},
        ]
        history_map, _state, _data, _notif = _build_dashboard_maps(
            conn, properties, [(2026, 4)]
        )

        own = history_map["Own"][(2026, 4)]["rentero_fee_sum_czk"]
        klient = history_map["Klient"][(2026, 4)]["rentero_fee_sum_czk"]
        zklient = history_map["ZKlient"][(2026, 4)]["rentero_fee_sum_czk"]

        # rentero-owned object: no client → no fee
        assert own == 0
        # klient: cena_ubytovani * commission * (1 + vat) = 8000 * 0.15 * 1.21
        assert klient == pytest.approx(1452.0)
        # z_klient: 3 % of payout = 10000 * 0.03
        assert zklient == pytest.approx(300.0)
    finally:
        conn.close()
