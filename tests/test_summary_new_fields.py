from report.summary import build_report_summary


def _rentero_config() -> dict:
    return {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}


def _klient_config() -> dict:
    return {"client_type": "klient", "rentero_commission": 0.15, "vat_rate": 0.21}


def _row(payout=10000, accommodation=8000, dph_uklid=210):
    return {
        "payout_czk": payout,
        "cena_ubytovani_czk": accommodation,
        "city_tax_czk": 200,
        "priprava_pokoje_czk": 1000,
        "dph_uklid_balicky_czk": dph_uklid,
        "bank_status": "DORAZILO",
    }


def _expense(gross=1000, dph=210, rate=0.21):
    return {
        "amount_czk": gross,
        "amount_dph_czk": dph,
        "amount_net_czk": gross - dph,
        "vat_rate": rate,
        "category_name": "Energie",
    }


def test_vat_output_for_klient_is_prefakturace_plus_commission_plus_input():
    # klient/z_klient: output VAT = prefakturace + commission VAT + recharged
    # expense VAT (no longer a plain alias of dph_prefakturace_klient_czk).
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    expected = round(
        s["dph_prefakturace_klient_czk"]
        + s["platform_commission_vat_czk"]
        + s["vat_input_czk"],
        2,
    )
    assert s["vat_output_czk"] == expected


def test_vat_output_is_accommodation_vat_for_rentero():
    # rentero: output VAT is the 12% accommodation VAT, no longer the
    # prefakturace alias. _row() has no provize_czk, so gross = 10000 − 200.
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["accommodation_vat_czk"] == 1050.0          # 9800 × 0.12/1.12
    assert s["vat_output_czk"] == s["accommodation_vat_czk"]
    assert s["vat_output_czk"] != s["dph_prefakturace_klient_czk"]


def test_vat_input_sums_only_rated_expenses():
    expenses = [
        _expense(gross=1210, dph=210, rate=0.21),
        _expense(gross=560, dph=60, rate=0.12),
        _expense(gross=300, dph=0, rate=0.0),  # zero-rate excluded
        {"amount_czk": 100, "amount_dph_czk": None, "amount_net_czk": None, "vat_rate": None, "category_name": "Legacy"},  # NULL excluded
    ]
    s = build_report_summary([_row()], _rentero_config(), expenses=expenses)
    assert s["vat_input_czk"] == 270.0
    assert s["vat_input_count"] == 2  # only the two with rate > 0


def test_vat_balance_positive_means_owed():
    s = build_report_summary([_row(dph_uklid=500)], _rentero_config(), expenses=[_expense(gross=121, dph=21)])
    # vat_output = vat_rentero_fee + vat_room_prep_total; vat_input = 21
    assert s["vat_balance_czk"] == round(s["vat_output_czk"] - s["vat_input_czk"], 2)


def test_zisk_present_for_rentero():
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["zisk_czk"] is not None
    expected = round(s["gross_payout_czk"] - s["expenses_total_czk"] - s["vat_balance_czk"], 2)
    assert s["zisk_czk"] == expected


def test_zisk_none_for_klient():
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    assert s["zisk_czk"] is None


def test_expenses_net_total_uses_amount_net_czk_when_present():
    expenses = [
        _expense(gross=121, dph=21),  # net=100
        {"amount_czk": 500, "amount_net_czk": None, "vat_rate": None, "category_name": "Legacy"},  # falls back to gross
    ]
    s = build_report_summary([_row()], _rentero_config(), expenses=expenses)
    assert s["expenses_net_total_czk"] == 600.0  # 100 + 500


def test_rentero_odmena_is_net_commission():
    # odměna = the commission fee itself (NET); DPH on it is tracked
    # separately as vat_rentero_fee_czk.
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    assert s["rentero_odmena_czk"] == s["rentero_fee_czk"]


def test_rentero_vyplata_is_odmena_plus_dph_plus_expenses():
    expenses = [_expense(gross=1210, dph=210, rate=0.21), _expense(gross=500, dph=0, rate=0.0)]
    s = build_report_summary([_row()], _klient_config(), expenses=expenses)
    expected = round(
        s["rentero_odmena_czk"]
        + s["vat_rentero_fee_czk"]
        + s["expenses_total_czk"],
        2,
    )
    assert s["rentero_vyplata_czk"] == expected


def test_rentero_vyplata_equals_odmena_plus_dph_when_no_expenses():
    s = build_report_summary([_row()], _klient_config(), expenses=[])
    assert s["expenses_total_czk"] == 0.0
    expected = round(s["rentero_odmena_czk"] + s["vat_rentero_fee_czk"], 2)
    assert s["rentero_vyplata_czk"] == expected


def test_rentero_odmena_vyplata_present_for_rentero_owned_too():
    # On rentero-owned objects the fields are still defined (the template
    # uses different KPI rendering, but downstream consumers should not
    # have to special-case the dict shape).
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert "rentero_odmena_czk" in s
    assert "rentero_vyplata_czk" in s
