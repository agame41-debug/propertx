import pytest

from report.db import get_connection, save_report_rows, log_report_generated
from report.db_admin import upsert_report_object
from report.web_support import _build_dashboard_maps, _build_dashboard_view_model


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


def test_build_dashboard_maps_model_fee_only_for_rentero_client_type():
    """Modelová odměna ("if this were a client") is computed ONLY for
    Rentero-owned objects (client_type='rentero'); for klient/z_klient it is 0
    so the KPI "with model" total = real fees + model fees with no double-count.
    """
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

        own = history_map["Own"][(2026, 4)]["model_rentero_fee_sum_czk"]
        klient = history_map["Klient"][(2026, 4)]["model_rentero_fee_sum_czk"]
        zklient = history_map["ZKlient"][(2026, 4)]["model_rentero_fee_sum_czk"]

        # rentero-owned: model = cena_ubytovani * commission * (1 + vat)
        #              = 8000 * 0.15 * 1.21
        assert own == pytest.approx(1452.0)
        # client objects already have a REAL fee → no model (avoids double-count)
        assert klient == 0
        assert zklient == 0
    finally:
        conn.close()


def test_dashboard_view_model_threads_model_fee_and_total():
    """The view model exposes model_rentero_fee_sum_czk on each current cell
    and aggregates total_model_rentero_fee_czk across Rentero-owned objects.
    """
    conn = get_connection(":memory:")
    try:
        _setup_object(conn, "Own", "rentero")
        _setup_object(conn, "Klient", "klient")
        for slug in ("Own", "Klient"):
            _setup_month(conn, slug, 2026, 4)

        properties = [
            {"slug": "Own", "listing_nickname": "Own", "display_name": "Own"},
            {"slug": "Klient", "listing_nickname": "Klient", "display_name": "Klient"},
        ]
        history_map, state_map, data_map, notif_map = _build_dashboard_maps(
            conn, properties, [(2026, 4)]
        )
        summary, _months, rows = _build_dashboard_view_model(
            properties, [(2026, 4)], history_map, state_map, data_map, notif_map
        )

        by_slug = {r["slug"]: r for r in rows}
        own_cell = by_slug["Own"]["cells"][-1]
        klient_cell = by_slug["Klient"]["cells"][-1]
        assert own_cell["model_rentero_fee_sum_czk"] == pytest.approx(1452.0)
        assert klient_cell["model_rentero_fee_sum_czk"] == 0

        # Portfolio model total = only Rentero-owned objects contribute.
        assert summary["total_model_rentero_fee_czk"] == pytest.approx(1452.0)
    finally:
        conn.close()
