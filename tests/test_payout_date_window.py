"""_payout_date_in_window must include valid in-range dates, exclude
out-of-range, treat empty as conservatively-included, and treat
unparseable as excluded with a warning."""

from __future__ import annotations

import logging
from datetime import date

import pytest

from report.engine import _payout_date_in_window


@pytest.fixture
def window():
    # Window: Apr 8 ≤ date ≤ May 7  (typical: prev cutoff = Apr 7, this cutoff = May 7)
    return {"after_prev_cutoff": date(2026, 4, 8), "cutoff_date": date(2026, 5, 7)}


def test_inside_window(window):
    assert _payout_date_in_window("2026-04-15", **window) is True


def test_at_lower_boundary(window):
    assert _payout_date_in_window("2026-04-08", **window) is True


def test_at_upper_boundary(window):
    assert _payout_date_in_window("2026-05-07", **window) is True


def test_before_window(window):
    assert _payout_date_in_window("2026-04-07", **window) is False


def test_after_window(window):
    assert _payout_date_in_window("2026-05-08", **window) is False


def test_empty_string_is_included_conservatively(window):
    """Legacy rows without payout_date metadata must not be silently dropped."""
    assert _payout_date_in_window("", **window) is True
    assert _payout_date_in_window(None, **window) is True


def test_unparseable_date_excluded_and_logged(window, caplog):
    with caplog.at_level(logging.WARNING, logger="report.engine"):
        result = _payout_date_in_window("garbage-not-a-date", **window)
    assert result is False
    assert any(
        "Unparseable payout_date" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_accepts_european_dot_format(window):
    """%d.%m.%Y is a recognized format."""
    assert _payout_date_in_window("15.04.2026", **window) is True


def test_accepts_us_slash_format(window):
    """%m/%d/%Y is a recognized format (Airbnb US export)."""
    assert _payout_date_in_window("04/15/2026", **window) is True
