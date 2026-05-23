from report.summary import build_report_summary


PROP = {
    "rentero_commission": 0.15,
    "vat_rate": 0.21,
}

# Fee math is only charged on client-owned objects (klient/z_klient); the
# fee-calculation test below uses this explicit client config so it keeps
# exercising the commission formula now that rentero-owned objects charge 0.
KLIENT_PROP = {
    "client_type": "klient",
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
        KLIENT_PROP,
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


def test_build_report_summary_dedupes_duplicate_codes_and_reports_warnings():
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "BBB", "payout_czk": 50.0, "cena_ubytovani_czk": 40.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)

    # Sums must use deduplicated rows: 100 (AAA once) + 50 (BBB) = 150
    assert summary["gross_payout_czk"] == 150.0
    assert summary["accommodation_income_czk"] == 120.0
    assert summary["integrity_warnings"] == ["AAA"]


def test_build_report_summary_no_warnings_when_unique():
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "AAA", "payout_czk": 100.0, "cena_ubytovani_czk": 80.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["integrity_warnings"] == []


def test_build_report_summary_empty_codes_dont_warn():
    """Multiple rows with empty confirmation_code are legitimate (synthetic
    rows). They should not trigger integrity warnings, but should NOT be
    deduped (each is a distinct synthetic record)."""
    from report.summary import build_report_summary

    rows = [
        {"confirmation_code": "", "payout_czk": 10.0, "cena_ubytovani_czk": 8.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
        {"confirmation_code": "", "payout_czk": 5.0, "cena_ubytovani_czk": 4.0,
         "city_tax_czk": 0, "priprava_pokoje_czk": 0, "dph_uklid_balicky_czk": 0,
         "is_excluded": False},
    ]
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    summary = build_report_summary(rows, prop)
    assert summary["integrity_warnings"] == []
    assert summary["gross_payout_czk"] == 15.0  # both summed, neither deduped


# ── Rentero-owned objects charge no management fee ──────────────────────
# A Rentero-owned property (client_type='rentero') has no external client and
# no commission to charge itself, so its fee must be 0. Mirrors the dashboard
# rule: zero only for client_type='rentero'; klient/z_klient keep their fee.

def _fee_rows():
    return [{"payout_czk": 10000.0, "cena_ubytovani_czk": 8000.0,
             "city_tax_czk": 200.0, "priprava_pokoje_czk": 0,
             "dph_uklid_balicky_czk": 0}]


def test_rentero_owned_object_charges_no_fee():
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_fee_rows(), prop)
    assert s["rentero_fee_czk"] == 0.0
    assert s["vat_rentero_fee_czk"] == 0.0
    # derived fields follow
    assert s["rentero_odmena_czk"] == 0.0
    assert s["rentero_vyplata_czk"] == 0.0  # 0 fee + 0 dph + 0 expenses
    # client payout is just the accommodation income (no fee deducted)
    assert s["client_payout_before_expenses_czk"] == 8000.0


def test_klient_object_still_charges_commission_fee():
    prop = {"client_type": "klient", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_fee_rows(), prop)
    assert s["rentero_fee_czk"] == 1200.0          # 8000 * 0.15
    assert s["vat_rentero_fee_czk"] == 252.0        # 1200 * 0.21


def test_zklient_object_still_charges_three_percent():
    prop = {"client_type": "z_klient", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_fee_rows(), prop)
    assert s["rentero_fee_czk"] == 300.0            # 10000 * 0.03
    assert s["vat_rentero_fee_czk"] == 0.0


# ── Illustrative "if it were a client object" model on Rentero-owned ────
# Rentero-owned objects charge no real fee, but the property page shows a
# model of what a client would be paid / what Rentero would earn.

def test_rentero_owned_object_has_model_client():
    prop = {"client_type": "rentero", "rentero_commission": 0.15,
            "vat_rate": 0.21, "balicky_per_person": 199}
    s = build_report_summary(_fee_rows(), prop)
    assert s["rentero_fee_czk"] == 0.0            # real fee stays zero
    m = s["model_client"]
    assert m["rentero_fee_czk"] == 1200.0          # 8000 * 0.15
    assert m["vat_rentero_fee_czk"] == 252.0        # 1200 * 0.21
    assert m["rentero_odmena_total_czk"] == 1452.0  # 1200 + 252
    assert m["client_payout_before_expenses_czk"] == 6548.0  # 8000 - 1200 - 252
    assert m["rentero_commission_rate"] == 0.15
    assert m["balicky_per_person"] == 199           # per-person package rate


def test_klient_and_zklient_have_no_model_client():
    for ct in ("klient", "z_klient"):
        prop = {"client_type": ct, "rentero_commission": 0.15, "vat_rate": 0.21}
        s = build_report_summary(_fee_rows(), prop)
        assert "model_client" not in s


# ── 12% accommodation VAT on Rentero-owned objects ──────────────────────
# Rentero is the accommodation supplier on its own objects, so it owes the
# Czech 12% reduced-rate VAT on the full guest consideration
# (payout + platform commission − city tax), extracted from the VAT-inclusive
# gross. Replaces the prefakturace output-VAT for rentero objects only.

def _accommodation_rows():
    return [{"payout_czk": 10000.0, "provize_czk": 2000.0, "city_tax_czk": 200.0,
             "cena_ubytovani_czk": 8000.0, "priprava_pokoje_czk": 0,
             "dph_uklid_balicky_czk": 0}]


def test_rentero_accommodation_vat_is_output_vat():
    prop = {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}
    s = build_report_summary(_accommodation_rows(), prop)
    assert s["platform_commission_czk"] == 2000.0
    assert s["accommodation_gross_czk"] == 11800.0          # 10000 + 2000 − 200
    assert s["accommodation_vat_czk"] == 1264.29            # 11800 × 0.12/1.12
    assert s["vat_output_czk"] == 1264.29                   # replaces prefakturace
    assert s["vat_balance_czk"] == 1264.29                  # output − 0 input
    # zisk reflects the new (larger) vat_balance
    assert s["zisk_czk"] == round(10000.0 - 0.0 - 1264.29, 2)  # 8735.71


def test_klient_and_zklient_have_no_accommodation_vat():
    for ct in ("klient", "z_klient"):
        prop = {"client_type": ct, "rentero_commission": 0.15, "vat_rate": 0.21}
        s = build_report_summary(_accommodation_rows(), prop)
        assert "accommodation_vat_czk" not in s
        assert "accommodation_gross_czk" not in s


# ── Commission VAT in klient/z_klient output VAT ────────────────────────
# Rentero recharges the Airbnb/Booking commission to the client with 21%
# output VAT (the reverse charge on the platform invoice nets to zero), so the
# object's output VAT = prefakturace (fee + room prep) + commission VAT +
# recharged-expense VAT. Recharged expenses also sit in vat_input → net zero.

def _klient_provize_rows():
    return [{"payout_czk": 12000.0, "cena_ubytovani_czk": 10000.0,
             "provize_czk": 5000.0, "dph_provize_czk": 1050.0,
             "city_tax_czk": 0, "priprava_pokoje_czk": 0,
             "dph_uklid_balicky_czk": 100.0}]


def test_klient_output_vat_includes_commission():
    prop = {"client_type": "klient", "rentero_commission": 0.20, "vat_rate": 0.21}
    s = build_report_summary(_klient_provize_rows(), prop)
    assert s["platform_commission_vat_czk"] == 1050.0
    assert s["vat_rentero_fee_czk"] == 420.0            # 10000 × 0.20 × 0.21
    assert s["vat_room_prep_czk"] == 100.0
    assert s["dph_prefakturace_klient_czk"] == 520.0     # 420 + 100
    assert s["vat_output_czk"] == 1570.0                 # 520 + 1050 + 0 input
    assert s["vat_balance_czk"] == 1570.0


def test_klient_recharged_expense_nets_out_in_balance():
    prop = {"client_type": "klient", "rentero_commission": 0.20, "vat_rate": 0.21}
    expenses = [{"amount_czk": 363.0, "amount_dph_czk": 63.0,
                 "amount_net_czk": 300.0, "vat_rate": 0.21}]
    s = build_report_summary(_klient_provize_rows(), prop, expenses=expenses)
    assert s["vat_input_czk"] == 63.0
    assert s["vat_output_czk"] == 1633.0                 # 520 + 1050 + 63 recharged
    assert s["vat_balance_czk"] == 1570.0                # expense nets out
