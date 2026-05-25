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
    # get_reservation_month_assignments returns a flat list of assignment dicts.
    a = next((x for x in assignments if x["confirmation_code"] == "HMA001"), None)
    assert a is not None
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
    assert all(a["confirmation_code"] != "HMA002" for a in assignments)


def test_get_codes_assigned_to_month(conn):
    from report.db_controls import create_reservation_month_assignment, get_codes_assigned_to_month
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": "HMA003",
        "target_year": 2026, "target_month": 4,
        "original_year": 2026, "original_month": 3,
        "reason": "test", "actor": "admin",
    })
    codes = get_codes_assigned_to_month(conn, "obj1", 2026, 4)
    # get_codes_assigned_to_month returns a list of assignment dicts.
    assert any(c["confirmation_code"] == "HMA003" for c in codes)
    codes_other = get_codes_assigned_to_month(conn, "obj1", 2026, 3)
    assert all(c["confirmation_code"] != "HMA003" for c in codes_other)


# ── Move supersede (one active main move per reservation) ──────────────────
#
# Prod bug 2026-05 (codes 5905484643 / HM45TR2QRN): a reservation moved
# month A→B and later "moved back" B→A accumulated TWO active assignments
# (the move-back was recorded as a fresh forward move with a different
# original month, dodging the UNIQUE(slug,code,original_y,original_m) key).
# The engine pulls a code into ANY month that is an active move target, so it
# materialized in BOTH months identically. A main reservation must have at
# most ONE active move.

def _move(conn, code, *, target_month, original_month, is_adjustment=False, batch_ref=""):
    from report.db_controls import create_reservation_month_assignment
    create_reservation_month_assignment(conn, {
        "slug": "obj1", "confirmation_code": code,
        "target_year": 2026, "target_month": target_month,
        "original_year": 2026, "original_month": original_month,
        "reason": "", "actor": "admin",
        "is_adjustment": is_adjustment, "batch_ref": batch_ref,
    })


def _targets(conn, month):
    from report.db_controls import get_codes_assigned_to_month
    return [a["confirmation_code"] for a in get_codes_assigned_to_month(conn, "obj1", 2026, month)]


def test_main_move_back_to_natural_clears_assignment(conn):
    from report.db_controls import get_all_assignments_for_code
    # March(3) → April(4), then "move back" from April page → March(3).
    _move(conn, "C1", target_month=4, original_month=3)
    _move(conn, "C1", target_month=3, original_month=4)
    # Net effect = no move: ZERO active assignments (not two contradictory ones).
    active = get_all_assignments_for_code(conn, "obj1", "C1")
    assert active == [], active
    # Not a move-target of EITHER month → engine keeps it only in natural March.
    assert "C1" not in _targets(conn, 4)
    assert "C1" not in _targets(conn, 3)


def test_chained_move_keeps_single_active_anchored_to_natural(conn):
    from report.db_controls import get_all_assignments_for_code
    # March(3) → April(4), then from April page → May(5).
    _move(conn, "C2", target_month=4, original_month=3)
    _move(conn, "C2", target_month=5, original_month=4)
    active = get_all_assignments_for_code(conn, "obj1", "C2")
    assert len(active) == 1, active
    a = active[0]
    assert (a["target_year"], a["target_month"]) == (2026, 5)
    # original must stay anchored to the NATURAL month (3) so the engine's
    # move-OUT suppression removes it from March, not from the stale April page.
    assert (a["original_year"], a["original_month"]) == (2026, 3)
    assert "C2" in _targets(conn, 5)
    assert "C2" not in _targets(conn, 4)


def test_main_move_does_not_supersede_adjustment_of_same_code(conn):
    from report.db_controls import get_all_assignments_for_code
    # An adjustment (synthetic) row lives independently of the main reservation.
    _move(conn, "C3", target_month=4, original_month=3, is_adjustment=True, batch_ref="B1")
    # A main reservation move for the SAME code must not revert the adjustment.
    _move(conn, "C3", target_month=4, original_month=2, is_adjustment=False)
    active = get_all_assignments_for_code(conn, "obj1", "C3")
    assert sorted(a["is_adjustment"] for a in active) == [0, 1], active


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
