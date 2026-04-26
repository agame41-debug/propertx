from report.db import get_connection
from report.db_admin import add_expense, update_expense, get_expense, get_expense_categories


def _setup(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    # property_slug in expenses is plain TEXT (no FK) — no report_objects row needed.
    # _assert_report_month_mutable only checks report_month_state (not LOCKED = OK).
    return conn


def test_add_expense_persists_dph_columns(tmp_path):
    conn = _setup(tmp_path)
    cats = get_expense_categories(conn)
    cat_id = cats[0]["id"]

    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Test",
        "amount_czk": 121.0, "amount_net_czk": 100.0, "amount_dph_czk": 21.0, "vat_rate": 0.21,
    })

    row = get_expense(conn, expense_id)
    assert row["amount_czk"] == 121.0
    assert row["amount_net_czk"] == 100.0
    assert row["amount_dph_czk"] == 21.0
    assert row["vat_rate"] == 0.21


def test_update_expense_overwrites_dph_columns(tmp_path):
    conn = _setup(tmp_path)
    cat_id = get_expense_categories(conn)[0]["id"]
    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Test",
        "amount_czk": 121.0, "amount_net_czk": 100.0, "amount_dph_czk": 21.0, "vat_rate": 0.21,
    })
    update_expense(conn, expense_id, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-16", "category_id": cat_id, "description": "Updated",
        "amount_czk": 1120.0, "amount_net_czk": 1000.0, "amount_dph_czk": 120.0, "vat_rate": 0.12,
    })
    row = get_expense(conn, expense_id)
    assert row["vat_rate"] == 0.12
    assert row["amount_net_czk"] == 1000.0


def test_legacy_add_without_dph_fields_keeps_nulls(tmp_path):
    """Backward-compat: callers that only pass amount_czk still work; new fields are NULL."""
    conn = _setup(tmp_path)
    cat_id = get_expense_categories(conn)[0]["id"]
    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Legacy",
        "amount_czk": 500.0,
        # No DPH fields supplied
    })
    row = get_expense(conn, expense_id)
    assert row["amount_czk"] == 500.0
    assert row["amount_net_czk"] is None
    assert row["amount_dph_czk"] is None
    assert row["vat_rate"] is None
