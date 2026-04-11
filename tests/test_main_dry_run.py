from __future__ import annotations

from argparse import Namespace

import report.main as main_module


def test_dry_run_is_read_only_for_report_artifacts(monkeypatch):
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
        dry_run=True,
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
    monkeypatch.setattr(
        main_module,
        "get_active_source_files",
        lambda _conn, source_type: [{"id": 1, "original_name": f"{source_type}.csv", "content": b""}],
    )

    monkeypatch.setattr(main_module, "load_airbnb_csv", lambda _: {})
    monkeypatch.setattr(main_module, "load_booking_csv", lambda _: {})
    monkeypatch.setattr(
        main_module,
        "build_airbnb_payout_data",
        lambda _: {"reservation_map": {}, "batches": [{"batch_ref": "G-TEST"}], "items": [{"batch_ref": "G-TEST", "item_index": 1}]},
    )
    monkeypatch.setattr(
        main_module,
        "build_booking_payout_data",
        lambda _: {"reservation_map": {}, "batches": [{"batch_ref": "BATCH-TEST"}], "items": [{"batch_ref": "BATCH-TEST", "item_index": 1}]},
    )
    monkeypatch.setattr(main_module, "load_bank_csv", lambda _: [{"datum": None, "amount_czk": 1000.0}])
    monkeypatch.setattr(main_module, "filter_bank_by_cutoff", lambda rows, _: rows)
    monkeypatch.setattr(main_module, "build_bank_index", lambda _: ({}, []))
    monkeypatch.setattr(main_module, "load_booking_bank_transactions", lambda _: {"12860254": []})

    preload_calls: list[bool] = []
    rate_calls: list[bool] = []
    monkeypatch.setattr(
        main_module,
        "preload_rates_for_month",
        lambda year, month, *, persist=True: preload_calls.append(persist) or {},
    )
    monkeypatch.setattr(main_module, "fetch_raw_reservations_for_period", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "filter_for_property_month",
        lambda *args, **kwargs: [{"confirmed_at": "2026-03-01", "check_in": "2026-03-01", "source": "Other", "confirmation_code": "ABC"}],
    )
    monkeypatch.setattr(main_module, "get_hostify_reservations_by_codes", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        main_module,
        "build_verification_index",
        lambda *args, **kwargs: ([{"confirmed_at": "2026-03-01", "check_in": "2026-03-01", "source": "Other", "confirmation_code": "ABC"}], []),
    )
    monkeypatch.setattr(
        main_module,
        "get_rate_for_reservation",
        lambda confirmed_at, *, persist=True: rate_calls.append(persist) or {"rate": 25.0, "valid_for": "2026-03-01"},
    )
    monkeypatch.setattr(main_module, "calculate_all_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "enrich_rows_with_bank", lambda rows, *args, **kwargs: (rows, []))
    monkeypatch.setattr(main_module, "enrich_booking_rows_with_bank", lambda rows, *args, **kwargs: (rows, []))
    monkeypatch.setattr(main_module, "calculate_totals_with_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "get_expenses", lambda *args, **kwargs: [])

    writes: list[str] = []
    for name in (
        "save_payout_batches",
        "save_payout_batch_items",
        "save_bank_transactions",
        "save_hostify_reservations",
        "save_payout_batch_bank_matches",
        "save_pending_payments",
        "save_report_rows",
        "log_report_generated",
        "resolve_pending_payment",
        "write_property_report",
    ):
        monkeypatch.setattr(main_module, name, lambda *args, _name=name, **kwargs: writes.append(_name))

    main_module.main()

    assert writes == []
    assert preload_calls == [False]
    assert rate_calls == [False]
