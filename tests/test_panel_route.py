"""Tests for get_report_row_by_code and /reservation/{code}/panel endpoint."""
from __future__ import annotations

import json
import sqlite3

import pytest

from report.db import _SCHEMA, get_report_row_by_code, save_report_rows


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


def test_get_report_row_by_code_finds_row(conn):
    row_data = {
        "confirmation_code": "HMA12345",
        "guest_name": "Jan Novák",
        "payout_czk": 5000.0,
    }
    save_report_rows(conn, "obj1", 2026, 3, [row_data])
    result = get_report_row_by_code(conn, "HMA12345")
    assert result is not None
    assert result["confirmation_code"] == "HMA12345"
    assert result["slug"] == "obj1"
    assert result["year"] == 2026
    assert result["month"] == 3


def test_get_report_row_by_code_returns_none_for_missing(conn):
    result = get_report_row_by_code(conn, "NOTEXISTS")
    assert result is None


def test_get_report_row_by_code_returns_most_recent_month(conn):
    old_data = {"confirmation_code": "HMA99", "guest_name": "Old"}
    new_data = {"confirmation_code": "HMA99", "guest_name": "New"}
    save_report_rows(conn, "obj1", 2025, 12, [old_data])
    save_report_rows(conn, "obj1", 2026, 1, [new_data])
    result = get_report_row_by_code(conn, "HMA99")
    assert result["year"] == 2026
    assert result["month"] == 1
