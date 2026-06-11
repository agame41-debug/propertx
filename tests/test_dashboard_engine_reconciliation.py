"""Reconciliation: dashboard SQL aggregates vs the engine/summary path.

_build_dashboard_maps re-implements the money math of report/summary.py in
SQL (client payout, rentero fee incl. DPH, z_klient 3% rule, is_excluded
filtering). Any rule change on either side that isn't mirrored silently
skews the dashboard KPIs against the property page. This test generates
real months through the engine for all three client_types and asserts both
paths agree.
"""
from __future__ import annotations

import pytest

from report.config import (
    get_all_properties,
    resolve_property_config,
    sync_json_config_to_db,
)
from report.db import get_connection, get_report_rows, save_hostify_reservations
from report.db_controls import create_reservation_exclusion
from report.db_object_profiles import backfill_object_profiles
from report.engine import generate_report_in_process
from report.loader import normalize_reservations
from report.summary import build_report_summary
from report.web_support import _build_dashboard_maps

YEAR, MONTH = 2026, 3

_SLUGS = {
    "rec_rentero": {"client_type": "rentero", "code": "HMRECR111", "nickname": "Rec Rentero"},
    "rec_klient": {"client_type": "klient", "code": "HMRECK222", "nickname": "Rec Klient"},
    "rec_zklient": {"client_type": "z_klient", "code": "HMRECZ333", "nickname": "Rec ZKlient"},
}


def _make_config() -> dict:
    props = {}
    for i, (slug, spec) in enumerate(_SLUGS.items()):
        props[slug] = {
            "listing_id": 9000 + i,
            "listing_nickname": spec["nickname"],
            "display_name": slug,
            "active": True,
            "client_type": spec["client_type"],
            "rentero_commission": 0.15,
            "balicky_per_person": 0,
            "city_tax_rate": 45.0,
            "vat_rate": 0.21,
            "channels": {
                "hostify": {"listing_names": []},
                "airbnb": {"listing_names": [spec["nickname"]]},
                "booking": {"listing_nickname": "", "property_id": ""},
            },
        }
    return {"properties": props}


def _seed_reservation(conn, *, code: str, nickname: str, payout_eur: float) -> None:
    raw = {
        "channel_reservation_id": code,
        "guest_name": f"Guest {code}",
        "listing_nickname": nickname,
        "checkIn": "2026-03-10",
        "checkOut": "2026-03-12",
        "status": "confirmed",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "cleaning_fee": 10.0,
        "city_tax": 0.0,
        "channel_commission": 15.0,
        "payout_price": payout_eur,
        "confirmedAt": "2026-01-15T10:00:00",
    }
    normalized = normalize_reservations([raw])
    for n in normalized:
        n["checkIn"] = n["check_in"]
        n["checkOut"] = n["check_out"]
    save_hostify_reservations(conn, normalized)


def _generate_all(conn, config) -> None:
    for slug in _SLUGS:
        result = generate_report_in_process(conn, slug, YEAR, MONTH, config)
        assert result.get("rows_count", 0) >= 1, f"no rows generated for {slug}"


def _dashboard_agg(conn, config) -> dict:
    props = get_all_properties(config)
    history_map, _, _, _ = _build_dashboard_maps(conn, props, [(YEAR, MONTH)])
    return history_map


def _summary_for(conn, slug, config) -> dict:
    prop = resolve_property_config(conn, slug, YEAR, MONTH, config)
    rows = get_report_rows(conn, slug, YEAR, MONTH)
    return build_report_summary(rows, prop)


@pytest.fixture
def setup_conn():
    conn = get_connection(":memory:")
    config = _make_config()
    sync_json_config_to_db(conn, config)
    # Profile segments are what the dashboard SQL joins on for
    # client_type/rates — without them every slug falls back to 'rentero'.
    backfill_object_profiles(conn)
    for slug, spec in _SLUGS.items():
        _seed_reservation(
            conn, code=spec["code"], nickname=spec["nickname"], payout_eur=200.0,
        )
    _generate_all(conn, config)
    yield conn, config
    conn.close()


def test_dashboard_sums_match_engine_summary_for_all_client_types(setup_conn):
    conn, config = setup_conn
    history_map = _dashboard_agg(conn, config)

    for slug, spec in _SLUGS.items():
        agg = history_map[slug].get((YEAR, MONTH))
        assert agg, f"dashboard has no aggregate for {slug}"
        summary = _summary_for(conn, slug, config)

        assert agg["payout_sum_czk"] == pytest.approx(
            summary["gross_payout_czk"], abs=0.05
        ), f"{slug}: payout mismatch"
        assert agg["cena_ubytovani_sum_czk"] == pytest.approx(
            summary["accommodation_income_czk"], abs=0.05
        ), f"{slug}: cena ubytování mismatch"
        assert agg["provize_sum_czk"] == pytest.approx(
            summary["platform_commission_czk"], abs=0.05
        ), f"{slug}: provize mismatch"

        # Dashboard fee = odměna INCLUDING its DPH (rentero: 0).
        assert agg["rentero_fee_sum_czk"] == pytest.approx(
            summary["rentero_fee_czk"] + summary["vat_rentero_fee_czk"], abs=0.05
        ), f"{slug}: rentero fee mismatch"

        if spec["client_type"] == "rentero":
            assert agg["model_rentero_fee_sum_czk"] == pytest.approx(
                summary["model_client"]["rentero_odmena_total_czk"], abs=0.05
            ), f"{slug}: model fee mismatch"
            # client_payout for rentero objects is excluded from the KPI in JS.
        else:
            assert agg["model_rentero_fee_sum_czk"] == 0
            # Canonical rule (decided 2026-06-10): výplata klientovi is net of
            # the odměna — for z_klient: cena + city_tax − 3 % payout.
            assert agg["client_payout_sum_czk"] == pytest.approx(
                summary["client_payout_before_expenses_czk"], abs=0.05
            ), f"{slug}: client payout mismatch"


def test_dashboard_sums_skip_excluded_rows_like_summary_does(setup_conn):
    conn, config = setup_conn
    slug, spec = "rec_klient", _SLUGS["rec_klient"]

    create_reservation_exclusion(conn, {
        "slug": slug,
        "confirmation_code": spec["code"],
        "reason": "test exclusion",
        "actor": "test",
    })
    generate_report_in_process(conn, slug, YEAR, MONTH, config)

    rows = get_report_rows(conn, slug, YEAR, MONTH)
    assert any(r.get("is_excluded") for r in rows), "exclusion did not stick"

    summary = _summary_for(conn, slug, config)
    agg = _dashboard_agg(conn, config)[slug][(YEAR, MONTH)]

    # summary drops excluded rows entirely — the dashboard must agree.
    assert summary["gross_payout_czk"] == 0
    assert agg["payout_sum_czk"] == pytest.approx(0, abs=0.01)
    assert agg["cena_ubytovani_sum_czk"] == pytest.approx(0, abs=0.01)
    assert agg["client_payout_sum_czk"] == pytest.approx(0, abs=0.01)
    assert agg["rentero_fee_sum_czk"] == pytest.approx(0, abs=0.01)
    # Excluded rows must not light up problem badges either (the property
    # page maps them to EXCLUDED, not to their verification status).
    assert (agg["chybi_csv"] or 0) == 0
    assert (agg["rozdil"] or 0) == 0


def test_dashboard_status_counts_match_effective_statuses(setup_conn):
    conn, config = setup_conn
    history_map = _dashboard_agg(conn, config)

    for slug in _SLUGS:
        agg = history_map[slug][(YEAR, MONTH)]
        rows = [r for r in get_report_rows(conn, slug, YEAR, MONTH) if not r.get("is_excluded")]
        chybi_csv = sum(1 for r in rows if r.get("verification_status") == "CHYBÍ_V_CSV")
        assert (agg["chybi_csv"] or 0) == chybi_csv, f"{slug}: chybi_csv count mismatch"
