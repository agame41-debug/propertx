"""When ADJ row synthesis can't find payout_eur OR airbnb_rate, the
fallback CZK/EUR ratio must be logged so operators see the inferred
data instead of trusting silently-fabricated EUR amounts."""

from __future__ import annotations

import logging

from report.engine import _build_adjustment_reservation


def test_uses_explicit_payout_eur_when_present_no_warning(caplog):
    past = {"confirmation_code": "HMA1", "source": "airbnb"}
    batch = {"payout_eur": 50.0, "payout_czk": 1250.0, "airbnb_rate": 25.0}

    with caplog.at_level(logging.WARNING, logger="report.engine"):
        row = _build_adjustment_reservation(past, batch)

    assert row["effective_payout_eur"] == 50.0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_derives_eur_from_rate_when_eur_missing_no_warning(caplog):
    past = {"confirmation_code": "HMA1", "source": "airbnb"}
    batch = {"payout_czk": 2500.0, "airbnb_rate": 25.0}

    with caplog.at_level(logging.WARNING, logger="report.engine"):
        row = _build_adjustment_reservation(past, batch)

    # 2500 / max(25, 1) = 100.0
    assert row["effective_payout_eur"] == 100.0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_warns_when_fallback_25_used_for_synthesis(caplog):
    past = {"confirmation_code": "HMA42", "source": "airbnb"}
    batch = {"payout_czk": 2500.0}  # no payout_eur, no airbnb_rate

    with caplog.at_level(logging.WARNING, logger="report.engine"):
        row = _build_adjustment_reservation(past, batch)

    # 2500 / 25 = 100.0
    assert row["effective_payout_eur"] == 100.0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("HMA42" in r.getMessage() and "25.0 CZK/EUR" in r.getMessage() for r in warnings)


def test_zero_payout_czk_yields_zero_eur_no_warning(caplog):
    """A zero-amount adjustment is harmless — don't spam warnings."""
    past = {"confirmation_code": "HMA1", "source": "airbnb"}
    batch = {}

    with caplog.at_level(logging.WARNING, logger="report.engine"):
        row = _build_adjustment_reservation(past, batch)

    assert row["effective_payout_eur"] == 0.0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
