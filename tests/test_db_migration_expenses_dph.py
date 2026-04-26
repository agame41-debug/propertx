import sqlite3
from report.db import get_connection


def test_expenses_table_has_dph_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    assert "amount_net_czk" in cols
    assert "amount_dph_czk" in cols
    assert "vat_rate" in cols


def test_legacy_expenses_table_gets_columns_added(tmp_path):
    """Simulate an old DB that has expenses without the new columns."""
    db_path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(db_path)
    raw.execute("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_slug TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            date TEXT DEFAULT '',
            category_id INTEGER,
            description TEXT NOT NULL,
            amount_czk REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    raw.execute(
        "INSERT INTO expenses (property_slug, year, month, description, amount_czk, created_at) "
        "VALUES ('legacy-slug', 2026, 1, 'Old expense', 1000.0, '2026-01-01T00:00:00')"
    )
    raw.commit()
    raw.close()

    conn = get_connection(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    assert {"amount_net_czk", "amount_dph_czk", "vat_rate"}.issubset(cols)

    # Legacy row must remain accessible with NULL in new columns
    row = conn.execute("SELECT * FROM expenses WHERE property_slug='legacy-slug'").fetchone()
    assert row["amount_czk"] == 1000.0
    assert row["amount_net_czk"] is None
    assert row["amount_dph_czk"] is None
    assert row["vat_rate"] is None
