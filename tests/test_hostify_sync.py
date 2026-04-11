from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from report.hostify_sync import compute_sync_months, HostifySyncTask


def test_compute_sync_months_returns_five_months():
    months = compute_sync_months(reference_date=date(2026, 4, 6))
    assert len(months) == 5


def test_compute_sync_months_correct_range():
    months = compute_sync_months(reference_date=date(2026, 4, 6))
    # prev=March, current=April, +1=May, +2=June, +3=July
    assert months[0] == (2026, 3)
    assert months[1] == (2026, 4)
    assert months[2] == (2026, 5)
    assert months[3] == (2026, 6)
    assert months[4] == (2026, 7)


def test_compute_sync_months_handles_january():
    months = compute_sync_months(reference_date=date(2026, 1, 15))
    assert months[0] == (2025, 12)   # previous
    assert months[1] == (2026, 1)    # current


def test_hostify_sync_task_calls_fetch_for_each_month(monkeypatch):
    fetched = []

    def fake_fetch(year, month, *, db_conn=None):
        fetched.append((year, month))
        return []

    monkeypatch.setattr("report.hostify_sync.fetch_raw_reservations_for_period", fake_fetch)
    monkeypatch.setattr("report.hostify_sync.save_hostify_reservations", lambda *a: None)
    monkeypatch.setattr("report.hostify_sync.normalize_reservations", lambda x: x)
    monkeypatch.setattr("report.hostify_sync.generate_report_in_process", lambda *a, **kw: {"rows_count": 0})
    monkeypatch.setattr("report.hostify_sync.get_report_month_state", lambda *a: {"status": "OPEN", "last_generated_at": "2026-04-01"})

    from report.db import get_connection
    conn = get_connection(":memory:")
    try:
        task = HostifySyncTask(db_path=":memory:", config={}, config_path=None)
        task._sync_once(conn=conn, reference_date=date(2026, 4, 6))
    finally:
        conn.close()

    assert len(fetched) == 5
    assert (2026, 3) in fetched
    assert (2026, 7) in fetched


def test_hostify_sync_task_skips_locked_months(monkeypatch):
    regenerated = []

    monkeypatch.setattr("report.hostify_sync.fetch_raw_reservations_for_period", lambda *a, **kw: [])
    monkeypatch.setattr("report.hostify_sync.save_hostify_reservations", lambda *a: None)
    monkeypatch.setattr("report.hostify_sync.normalize_reservations", lambda x: x)

    def fake_state(conn, slug, year, month):
        return {"status": "LOCKED", "last_generated_at": "2026-04-01"}

    monkeypatch.setattr("report.hostify_sync.get_report_month_state", fake_state)

    def fake_generate(*a, **kw):
        regenerated.append(a)
        return {"rows_count": 0}

    monkeypatch.setattr("report.hostify_sync.generate_report_in_process", fake_generate)

    from report.db import get_connection
    from report.config import sync_json_config_to_db, load_runtime_config
    conn = get_connection(":memory:")
    config = {
        "properties": {
            "test": {
                "listing_id": 1, "listing_nickname": "Test", "display_name": "Test",
                "active": True, "channels": {"airbnb": {"listing_names": []}, "booking": {}},
            }
        }
    }
    sync_json_config_to_db(conn, config)
    loaded_config = load_runtime_config(None, db_conn=conn)

    task = HostifySyncTask(db_path=":memory:", config=loaded_config, config_path=None)
    task._sync_once(conn=conn, reference_date=date(2026, 4, 6))
    conn.close()

    assert len(regenerated) == 0
