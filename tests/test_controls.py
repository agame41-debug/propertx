"""Tests for db_controls — reservation move/exclude/reinstate."""
from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


# ── Month assignments ──────────────────────────────────────────────────────

def test_create_and_get_month_assignment(conn):
    from report.db_controls import create_reservation_month_assignment, get_reservation_month_assignments
    create_reservation_month_assignment(conn, {
        "slug": "obj1",
        "confirmation_code": "HMA001",
        "target_year": 2026,
        "target_month": 4,
        "original_year": 2026,
        "original_month": 3,
        "reason": "Guest requested change",
        "actor": "admin",
    })
    assignments = get_reservation_month_assignments(conn, "obj1")
    assert "HMA001" in assignments
    a = assignments["HMA001"]
    assert a["target_year"] == 2026
    assert a["target_month"] == 4
    assert a["original_year"] == 2026
    assert a["original_month"] == 3


def test_revert_month_assignment(conn):
    from report.db_controls import (
        create_reservation_month_assignment,
        revert_reservation_month_assignment,
        get_reservation_month_assignments,
    )
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": "HMA002",
        "target_year": 2026, "target_month": 4,
        "original_year": 2026, "original_month": 3,
        "reason": "test", "actor": "admin",
    })
    revert_reservation_month_assignment(conn, "obj1", "HMA002", actor="admin")
    assignments = get_reservation_month_assignments(conn, "obj1")
    assert "HMA002" not in assignments


def test_get_codes_assigned_to_month(conn):
    from report.db_controls import create_reservation_month_assignment, get_codes_assigned_to_month
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": "HMA003",
        "target_year": 2026, "target_month": 4,
        "original_year": 2026, "original_month": 3,
        "reason": "test", "actor": "admin",
    })
    codes = get_codes_assigned_to_month(conn, "obj1", 2026, 4)
    assert "HMA003" in codes
    codes_other = get_codes_assigned_to_month(conn, "obj1", 2026, 3)
    assert "HMA003" not in codes_other


# ── Exclusions ─────────────────────────────────────────────────────────────

def test_create_and_get_exclusion(conn):
    from report.db_controls import create_reservation_exclusion, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1",
        "confirmation_code": "HMA010",
        "reason": "duplicate",
        "actor": "admin",
    })
    exclusions = get_active_exclusions(conn, "obj1")
    assert "HMA010" in exclusions


def test_reinstate_removes_from_active_exclusions(conn):
    from report.db_controls import create_reservation_exclusion, reinstate_reservation, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1", "confirmation_code": "HMA011",
        "reason": "test", "actor": "admin",
    })
    reinstate_reservation(conn, "obj1", "HMA011", actor="admin")
    exclusions = get_active_exclusions(conn, "obj1")
    assert "HMA011" not in exclusions


def test_exclusion_is_slug_scoped(conn):
    from report.db_controls import create_reservation_exclusion, get_active_exclusions
    create_reservation_exclusion(conn, {
        "slug": "obj1", "confirmation_code": "HMA020",
        "reason": "test", "actor": "admin",
    })
    exclusions_obj2 = get_active_exclusions(conn, "obj2")
    assert "HMA020" not in exclusions_obj2


# ── Summary and breakdown exclusion ───────────────────────────────────────

def test_summary_skips_excluded_rows():
    from report.summary import build_report_summary
    rows = [
        {"payout_czk": 5000.0, "cena_ubytovani_czk": 4000.0, "priprava_pokoje_czk": 500.0,
         "dph_uklid_balicky_czk": 100.0, "bank_status": "DORAZILO", "is_excluded": False},
        {"payout_czk": 3000.0, "cena_ubytovani_czk": 2500.0, "priprava_pokoje_czk": 300.0,
         "dph_uklid_balicky_czk": 60.0, "bank_status": "DORAZILO", "is_excluded": True},
    ]
    prop = {"rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["gross_payout_czk"] == 5000.0, "excluded row must not be counted"
    assert summary["accommodation_income_czk"] == 4000.0


def test_breakdown_skips_excluded_rows():
    from report.web_support import _compute_row_breakdown
    rows = [
        {"source": "airbnb", "payout_czk": 5000.0, "cena_ubytovani_czk": 4000.0,
         "provize_czk": 0, "dph_provize_czk": 0, "city_tax_czk": 0, "uklid_czk": 0,
         "balicky_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"source": "airbnb", "payout_czk": 3000.0, "cena_ubytovani_czk": 2500.0,
         "provize_czk": 0, "dph_provize_czk": 0, "city_tax_czk": 0, "uklid_czk": 0,
         "balicky_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": True},
    ]
    breakdown = _compute_row_breakdown(rows)
    assert breakdown["airbnb"]["payout_czk"] == 5000.0
    assert breakdown["airbnb"]["count"] == 1
