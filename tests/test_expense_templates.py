from report.db import get_connection
from report.db_admin import get_expenses
from report.db_expense_templates import (
    create_expense_template, list_expense_templates, update_expense_template,
    delete_expense_template, materialize_templates_for_month, add_template_skip,
    upsert_tsv_template,
)


def _seed_object(conn, slug="x"):
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES (?,?,?)", (slug, "t", "t"))
    conn.commit()


def _mk(conn, slug="x", **over):
    data = {
        "property_slug": slug, "category_id": None, "description": "Internet",
        "amount_czk": 1000.0, "amount_net_czk": 826.45, "amount_dph_czk": 173.55,
        "vat_rate": 0.21, "start_ym": "2026-05", "end_ym": None, "source": "ui",
    }
    data.update(over)
    return create_expense_template(conn, data)


def test_schema_has_template_tables_and_column():
    conn = get_connection(":memory:")
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(expense_templates)")}
    assert {"property_slug", "description", "start_ym", "end_ym", "vat_rate", "source", "active"} <= tcols
    skips = {r["name"] for r in conn.execute("PRAGMA table_info(expense_template_skips)")}
    assert {"template_id", "year", "month"} <= skips
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(expenses)")}
    assert "template_id" in ecols
    conn.close()


def test_template_crud():
    conn = get_connection(":memory:")
    _seed_object(conn)
    tid = _mk(conn)
    rows = list_expense_templates(conn, "x")
    assert len(rows) == 1 and rows[0]["description"] == "Internet"
    update_expense_template(conn, tid, {"amount_czk": 1210.0, "end_ym": "2026-12"})
    assert list_expense_templates(conn, "x")[0]["end_ym"] == "2026-12"
    delete_expense_template(conn, tid)
    assert list_expense_templates(conn, "x") == []
    conn.close()


def test_materialize_creates_once_and_respects_period():
    conn = get_connection(":memory:")
    _seed_object(conn)
    _mk(conn, start_ym="2026-05", end_ym="2026-07")
    # Out of period → nothing
    assert materialize_templates_for_month(conn, "x", 2026, 4) == 0
    assert get_expenses(conn, "x", 2026, 4) == []
    # In period → one row
    assert materialize_templates_for_month(conn, "x", 2026, 5) == 1
    rows = get_expenses(conn, "x", 2026, 5)
    assert len(rows) == 1 and rows[0]["description"] == "Internet"
    # Idempotent — second call creates nothing
    assert materialize_templates_for_month(conn, "x", 2026, 5) == 0
    assert len(get_expenses(conn, "x", 2026, 5)) == 1
    # Past end → nothing
    assert materialize_templates_for_month(conn, "x", 2026, 8) == 0
    conn.close()


def test_materialize_respects_tombstone():
    conn = get_connection(":memory:")
    _seed_object(conn)
    tid = _mk(conn)
    materialize_templates_for_month(conn, "x", 2026, 5)
    # Operator deletes the generated row → tombstone; re-materialize must not recreate.
    add_template_skip(conn, tid, 2026, 5)
    conn.execute("DELETE FROM expenses WHERE template_id=? AND year=2026 AND month=5", (tid,))
    conn.commit()
    assert materialize_templates_for_month(conn, "x", 2026, 5) == 0
    assert get_expenses(conn, "x", 2026, 5) == []
    conn.close()


def test_materialize_skips_locked_month():
    conn = get_connection(":memory:")
    _seed_object(conn)
    _mk(conn)
    conn.execute(
        """INSERT INTO report_month_state (slug, year, month, status)
           VALUES ('x', 2026, 5, 'LOCKED')"""
    )
    conn.commit()
    assert materialize_templates_for_month(conn, "x", 2026, 5) == 0
    conn.close()


def test_engine_triggers_materialization(monkeypatch):
    import pytest
    import report.engine as engine
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    conn.commit()
    seen = {}

    def spy(c, slug, y, m):
        seen["args"] = (slug, y, m)
        return 0

    monkeypatch.setattr("report.engine.materialize_templates_for_month", spy, raising=True)
    config = {"properties": {"x": {"channels": {}, "client_type": "rentero"}}}
    try:
        engine.generate_report_in_process(conn, "x", 2026, 5, config)
    except Exception:
        pass  # downstream generation steps are irrelevant to this test
    assert seen["args"] == ("x", 2026, 5)
    conn.close()


def test_delete_template_preserves_past_rows_and_stops_future():
    """Deleting a template must leave already-materialized expense rows intact
    (so historical reports don't change) while preventing any future month from
    re-materializing it."""
    conn = get_connection(":memory:")
    _seed_object(conn)
    tid = _mk(conn, start_ym="2026-05", end_ym=None)
    assert materialize_templates_for_month(conn, "x", 2026, 5) == 1
    assert len(get_expenses(conn, "x", 2026, 5)) == 1
    delete_expense_template(conn, tid)
    # Past row survives the delete...
    assert len(get_expenses(conn, "x", 2026, 5)) == 1
    # ...and future months no longer materialize anything.
    assert materialize_templates_for_month(conn, "x", 2026, 6) == 0
    assert get_expenses(conn, "x", 2026, 6) == []
    conn.close()


def test_upsert_tsv_template_is_idempotent_by_source():
    conn = get_connection(":memory:")
    _seed_object(conn)
    upsert_tsv_template(conn, "x", "tsv:internet", {
        "description": "Internet", "amount_net_czk": 826.45, "amount_dph_czk": 173.55,
        "amount_czk": 1000.0, "vat_rate": 0.21, "start_ym": "2026-05",
    })
    upsert_tsv_template(conn, "x", "tsv:internet", {
        "description": "Internet", "amount_net_czk": 900.0, "amount_dph_czk": 189.0,
        "amount_czk": 1089.0, "vat_rate": 0.21, "start_ym": "2026-05",
    })
    rows = [t for t in list_expense_templates(conn, "x") if t["source"] == "tsv:internet"]
    assert len(rows) == 1 and rows[0]["amount_net_czk"] == 900.0
    conn.close()


def test_upsert_tsv_template_preserves_original_start_ym():
    """Re-importing the TSV for a later month must keep the template's original
    start_ym so the intermediate months still materialize the recurring expense."""
    conn = get_connection(":memory:")
    _seed_object(conn)
    upsert_tsv_template(conn, "x", "tsv:internet", {
        "description": "Internet", "amount_net_czk": 800.0, "amount_dph_czk": 168.0,
        "amount_czk": 968.0, "vat_rate": 0.21, "start_ym": "2026-05",
    })
    upsert_tsv_template(conn, "x", "tsv:internet", {
        "description": "Internet", "amount_net_czk": 800.0, "amount_dph_czk": 168.0,
        "amount_czk": 968.0, "vat_rate": 0.21, "start_ym": "2026-08",  # later month
    })
    t = [t for t in list_expense_templates(conn, "x") if t["source"] == "tsv:internet"][0]
    assert t["start_ym"] == "2026-05"  # original preserved, not moved to 2026-08
    conn.close()
