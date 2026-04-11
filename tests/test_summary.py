from report.summary import build_report_summary


PROP = {
    "rentero_commission": 0.15,
    "vat_rate": 0.21,
}


def test_build_report_summary_includes_fees_expenses_and_bank_totals():
    rows = [
        {
            "payout_czk": 2550.0,
            "cena_ubytovani_czk": 976.77,
            "priprava_pokoje_czk": 1008.0,
            "dph_uklid_balicky_czk": 211.68,
            "bank_status": "DORAZILO",
            "czk_booked": None,
        },
        {
            "payout_czk": 2016.0,
            "cena_ubytovani_czk": 800.0,
            "priprava_pokoje_czk": 400.0,
            "dph_uklid_balicky_czk": 84.0,
            "bank_status": "CHYBÍ",
            "czk_booked": 2016.0,
        },
    ]
    expenses = [
        {"amount_czk": 1000.0},
        {"amount_czk": 250.0},
    ]
    transferred_rows = [
        {"bank_amount_czk": 500.0},
    ]

    summary = build_report_summary(
        rows,
        PROP,
        expenses=expenses,
        transferred_rows=transferred_rows,
    )

    assert summary["gross_payout_czk"] == 4566.0
    assert summary["accommodation_income_czk"] == 1776.77
    assert summary["rentero_fee_czk"] == 266.52
    assert summary["vat_rentero_fee_czk"] == 55.97
    assert summary["expenses_total_czk"] == 1250.0
    assert summary["client_payout_before_expenses_czk"] == 1454.28
    assert summary["client_payout_after_expenses_czk"] == 204.28
    assert summary["bank_confirmed_czk"] == 2550.0
    assert summary["bank_pending_czk"] == 2016.0
    assert summary["bank_transferred_czk"] == 500.0
    assert summary["bank_received_this_month_czk"] == 3050.0


def test_build_report_summary_defaults_to_zero_without_optional_inputs():
    summary = build_report_summary([], PROP)

    assert summary["gross_payout_czk"] == 0.0
    assert summary["expenses_total_czk"] == 0.0
    assert summary["client_payout_after_expenses_czk"] == 0.0
    assert summary["bank_received_this_month_czk"] == 0.0
