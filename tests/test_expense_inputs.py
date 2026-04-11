from fastapi import HTTPException

from report.routes.property_routes import _resolve_expense_amount_czk


def test_resolve_expense_amount_prefers_explicit_total():
    amount = _resolve_expense_amount_czk(
        amount_czk_raw="1210",
        amount_net_czk_raw="1000",
        vat_rate_raw="21",
    )

    assert amount == 1210.0


def test_resolve_expense_amount_computes_total_from_net_and_vat():
    amount = _resolve_expense_amount_czk(
        amount_czk_raw="",
        amount_net_czk_raw="1000",
        vat_rate_raw="21",
    )

    assert amount == 1210.0


def test_resolve_expense_amount_rejects_missing_values():
    try:
        _resolve_expense_amount_czk(
            amount_czk_raw="",
            amount_net_czk_raw="",
            vat_rate_raw="",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "Částku celkem" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for missing expense amount input")
