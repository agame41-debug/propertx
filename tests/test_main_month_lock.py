from __future__ import annotations

from argparse import Namespace

import pytest

import report.main as main_module


def test_generation_skips_locked_month(monkeypatch):
    args = Namespace(
        year=2026,
        month=3,
        properties=None,
        airbnb_csvs=None,
        booking_csvs=None,
        bank_csvs=None,
        output_dir="output/reports",
        config=None,
        overwrite=False,
        dry_run=False,
        verbose=False,
        legacy_autodiscover=False,
        cutoff_day=7,
    )
    monkeypatch.setattr(main_module.argparse.ArgumentParser, "parse_args", lambda self: args)

    prop = {"slug": "28_Pluku_58", "listing_nickname": "28. Pluku 58"}
    monkeypatch.setattr(main_module, "load_runtime_config", lambda *args, **kwargs: {"properties": {prop["slug"]: {}}})
    monkeypatch.setattr(main_module, "get_all_properties", lambda _: [prop])
    monkeypatch.setattr(main_module, "get_booking_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "get_hostify_listing_names", lambda *args, **kwargs: [prop["listing_nickname"]])
    monkeypatch.setattr(main_module, "get_connection", lambda: object())
    monkeypatch.setattr(main_module, "get_active_source_files", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "load_airbnb_csv", lambda _: {})
    monkeypatch.setattr(main_module, "load_booking_csv", lambda _: {})
    monkeypatch.setattr(main_module, "build_airbnb_payout_data", lambda _: {"reservation_map": {}, "batches": [], "items": []})
    monkeypatch.setattr(main_module, "build_booking_payout_data", lambda _: {"reservation_map": {}, "batches": [], "items": []})
    monkeypatch.setattr(main_module, "preload_rates_for_month", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "fetch_raw_reservations_for_period", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "get_report_month_state", lambda *args, **kwargs: {"status": "LOCKED"})

    writes: list[str] = []
    for name in (
        "save_payout_batches",
        "save_payout_batch_items",
        "save_bank_transactions",
        "save_hostify_reservations",
        "save_report_rows",
        "log_report_generated",
        "touch_report_month_generation",
        "write_property_report",
    ):
        monkeypatch.setattr(main_module, name, lambda *args, _name=name, **kwargs: writes.append(_name))

    with pytest.raises(SystemExit):
        main_module.main()

    assert "write_property_report" not in writes
    assert "touch_report_month_generation" not in writes
