"""
report/summary.py — canonical financial summary shared by web and Excel.
"""

from __future__ import annotations


# Czech reduced VAT rate for accommodation services (ubytovací služby).
# Rentero-owned objects are the supplier and owe this on the full guest
# consideration. Fixed by law; not a per-object config value.
ACCOMMODATION_VAT_RATE = 0.12


def _r(value) -> float:
    return round(float(value or 0), 2)


def build_report_summary(
    rows: list[dict],
    property_config: dict,
    *,
    expenses: list[dict] | None = None,
    transferred_rows: list[dict] | None = None,
) -> dict:
    """
    Build the canonical month summary for a property.

    This summary is the shared source of truth for web and Excel views.
    """
    expenses = expenses or []
    transferred_rows = transferred_rows or []
    rows = [r for r in rows if not r.get("is_excluded")]

    # Dedup by confirmation_code (L1 integrity defense). Empty codes are
    # legitimately repeatable (synthetic rows); only non-empty repeats are
    # treated as duplicates.
    seen_codes: set[str] = set()
    integrity_warnings: list[str] = []
    deduped_rows: list[dict] = []
    for r in rows:
        code = r.get("confirmation_code") or ""
        if code:
            if code in seen_codes:
                if code not in integrity_warnings:
                    integrity_warnings.append(code)
                continue  # skip duplicate
            seen_codes.add(code)
        deduped_rows.append(r)
    rows = deduped_rows

    client_type = property_config.get("client_type", "rentero")
    rentero_commission_rate = float(property_config.get("rentero_commission", 0.15))
    vat_rate = float(property_config.get("vat_rate", 0.21))

    gross_payout_czk = _r(sum(float(r.get("payout_czk") or 0) for r in rows))
    accommodation_income_czk = _r(
        sum(float(r.get("cena_ubytovani_czk") or 0) for r in rows)
    )
    city_tax_czk = _r(sum(float(r.get("city_tax_czk") or 0) for r in rows))
    platform_commission_czk = _r(sum(float(r.get("provize_czk") or 0) for r in rows))
    platform_commission_vat_czk = _r(sum(float(r.get("dph_provize_czk") or 0) for r in rows))
    room_prep_czk = _r(sum(float(r.get("priprava_pokoje_czk") or 0) for r in rows))
    vat_room_prep_czk = _r(
        sum(float(r.get("dph_uklid_balicky_czk") or 0) for r in rows)
    )

    if client_type == "z_klient":
        # Z Klient: odměna = 3 % of gross payout. Výplata klientovi is NET of
        # the odměna: (cena_ubytování + city_tax) − 3 % payout — canonical
        # rule confirmed 2026-06-10; the dashboard SQL mirrors it and
        # tests/test_dashboard_engine_reconciliation.py keeps both in sync.
        rentero_commission_rate = 0.03
        rentero_fee_czk = _r(gross_payout_czk * rentero_commission_rate)
        vat_rentero_fee_czk = 0.0
        client_gross_income_czk = _r(accommodation_income_czk + city_tax_czk)
    elif client_type == "rentero":
        # Rentero-owned object: no external client and no commission to charge
        # itself → no management fee. The meaningful KPI here is zisk_czk
        # (computed below). Mirrors the dashboard rule: zero only for
        # client_type='rentero'; klient/z_klient keep their fee.
        rentero_fee_czk = 0.0
        vat_rentero_fee_czk = 0.0
        client_gross_income_czk = accommodation_income_czk
    else:
        rentero_fee_czk = _r(accommodation_income_czk * rentero_commission_rate)
        vat_rentero_fee_czk = _r(rentero_fee_czk * vat_rate)
        client_gross_income_czk = accommodation_income_czk

    rentero_room_prep_with_vat_czk = _r(room_prep_czk + vat_room_prep_czk)
    client_payout_before_expenses_czk = _r(
        client_gross_income_czk - rentero_fee_czk - vat_rentero_fee_czk
    )
    expenses_total_czk = _r(
        sum(float(expense.get("amount_czk") or 0) for expense in expenses)
    )
    client_payout_after_expenses_czk = _r(
        client_payout_before_expenses_czk - expenses_total_czk
    )
    dph_prefakturace_klient_czk = _r(vat_room_prep_czk + vat_rentero_fee_czk)

    bank_confirmed_czk = _r(
        sum(float(r.get("payout_czk") or 0) for r in rows if r.get("bank_status") == "DORAZILO")
    )
    bank_pending_czk = _r(
        sum(
            float(r.get("czk_booked") or r.get("payout_czk") or 0)
            for r in rows
            if r.get("bank_status") == "CHYBÍ"
        )
    )
    bank_transferred_czk = _r(
        sum(float(row.get("bank_amount_czk") or 0) for row in transferred_rows)
    )
    bank_received_this_month_czk = _r(bank_confirmed_czk + bank_transferred_czk)

    result = {
        "gross_payout_czk": gross_payout_czk,
        "accommodation_income_czk": accommodation_income_czk,
        "platform_commission_czk": platform_commission_czk,
        "platform_commission_vat_czk": platform_commission_vat_czk,
        "room_prep_czk": room_prep_czk,
        "vat_room_prep_czk": vat_room_prep_czk,
        "rentero_commission_rate": rentero_commission_rate,
        "rentero_fee_czk": rentero_fee_czk,
        "vat_rate": vat_rate,
        "vat_rentero_fee_czk": vat_rentero_fee_czk,
        "rentero_room_prep_with_vat_czk": rentero_room_prep_with_vat_czk,
        "client_gross_income_czk": client_gross_income_czk,
        "client_payout_before_expenses_czk": client_payout_before_expenses_czk,
        "expenses_total_czk": expenses_total_czk,
        "client_payout_after_expenses_czk": client_payout_after_expenses_czk,
        "dph_prefakturace_klient_czk": dph_prefakturace_klient_czk,
        "bank_confirmed_czk": bank_confirmed_czk,
        "bank_pending_czk": bank_pending_czk,
        "bank_transferred_czk": bank_transferred_czk,
        "bank_received_this_month_czk": bank_received_this_month_czk,
        "integrity_warnings": integrity_warnings,
    }

    # vat_input: sum of DPH from expenses that have a VAT rate set.
    # Legacy expenses with NULL vat_rate are excluded from the aggregate so we
    # don't lie about the deduction. Computed before output VAT so the
    # klient/z_klient branch can fold recharged-expense VAT into the output.
    rated_expenses = [
        e for e in expenses
        if (e.get("vat_rate") is not None) and (float(e.get("vat_rate") or 0) > 0)
    ]
    result["vat_input_czk"] = _r(sum(float(e.get("amount_dph_czk") or 0) for e in rated_expenses))
    result["vat_input_count"] = len(rated_expenses)

    # ── Output VAT (DPH na výstupu) ──────────────────────────────────────
    #   rentero           → 12% reduced-rate accommodation VAT on the full
    #                       guest consideration (payout + commission − city
    #                       tax). The commission is already inside this base.
    #   klient / z_klient → Rentero recharges costs to the client with output
    #                       VAT and charges its odměna with VAT. Output VAT =
    #                       prefakturace (fee + room prep) + commission VAT (net
    #                       of the Airbnb/Booking reverse charge) +
    #                       recharged-expense VAT. The recharged expenses also
    #                       sit in vat_input, so they net out in the balance.
    if client_type == "rentero":
        accommodation_gross_czk = _r(
            gross_payout_czk + platform_commission_czk - city_tax_czk
        )
        accommodation_vat_czk = _r(
            accommodation_gross_czk
            * ACCOMMODATION_VAT_RATE
            / (1 + ACCOMMODATION_VAT_RATE)
        )
        result["accommodation_gross_czk"] = accommodation_gross_czk
        result["accommodation_vat_czk"] = accommodation_vat_czk
        result["vat_output_czk"] = accommodation_vat_czk
    else:
        result["vat_output_czk"] = _r(
            result["dph_prefakturace_klient_czk"]
            + platform_commission_vat_czk
            + result["vat_input_czk"]
        )

    result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])

    # Net total for the expense-table footer (same exclusion rule as above).
    # Falls back to amount_czk for legacy rows so the footer still adds up.
    result["expenses_net_total_czk"] = _r(sum(
        float(e.get("amount_net_czk") if e.get("amount_net_czk") is not None else (e.get("amount_czk") or 0))
        for e in expenses
    ))

    # Rentero's payout model on klient/z_klient objects:
    #   odměna  = the commission itself (NET — without its DPH portion). The
    #             DPH is separately visible as vat_rentero_fee_czk.
    #   výplata = odměna + DPH on commission + expenses. The client
    #             reimburses property expenses through Rentero, so the cash
    #             flowing to Rentero's account on this object is the full
    #             commission (with DPH) plus the pass-through expenses.
    result["rentero_odmena_czk"] = _r(result["rentero_fee_czk"])
    result["rentero_vyplata_czk"] = _r(
        result["rentero_odmena_czk"]
        + result["vat_rentero_fee_czk"]
        + result["expenses_total_czk"]
    )

    # Zisk — Rentero's residual margin. Only meaningful when the property is
    # Rentero-owned; for klient/z_klient the equivalent KPI is
    # client_payout_after_expenses_czk (which is already in the dict).
    if client_type == "rentero":
        result["zisk_czk"] = _r(
            result["gross_payout_czk"]
            - result["expenses_total_czk"]
            - result["vat_balance_czk"]
        )
    else:
        result["zisk_czk"] = None

    # Illustrative "if this were a client object" model for Rentero-owned
    # objects: what a client would be paid and what Rentero would earn.
    # Display-only; does not affect any real figure (fee stays 0).
    if client_type == "rentero":
        model_fee = _r(accommodation_income_czk * rentero_commission_rate)
        model_vat = _r(model_fee * vat_rate)
        result["model_client"] = {
            "rentero_commission_rate": rentero_commission_rate,
            "rentero_fee_czk": model_fee,
            "vat_rentero_fee_czk": model_vat,
            "rentero_odmena_total_czk": _r(model_fee + model_vat),
            "client_payout_before_expenses_czk": _r(
                accommodation_income_czk - model_fee - model_vat
            ),
            "balicky_per_person": float(property_config.get("balicky_per_person", 0) or 0),
        }

    return result
