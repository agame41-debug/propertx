"""Tests for build_airbnb_payout_data special cases.

Covers:
- rate_anomaly fallback (one CSV row had CZK in the EUR column → batch
  rate refused → CNB rate at payout_date used to fill per-item amount_czk
  so /bank drilldown isn't empty);
- "Vyrovnání" item type (refund/correction outside of "řešení" context
  used to fall through unhandled, leaving the parent reservation's
  payout untouched and the bank tx for the refund batch unmatched).
"""

from unittest.mock import patch

import pytest

from report.verifier import build_airbnb_payout_data


def _fake_cnb_rate_for_batch_date(payout_date_str: str) -> float:
    return 24.20


def _make_csv_rows():
    """Mimic _read_csv_rows output: list of (label, list[dict])."""
    return [(
        "test.csv",
        [
            {"Typ": "Payout", "Referenční kód": "G-FS2OGNYOBEB7U/ROC/X",
             "Datum": "12/31/2025", "Datum připsání na účet": "2026-01-02",
             "Vyplaceno": "179537,65", "Částka": "0",
             "Potvrzující kód": "", "Host": "", "Nabídka": "",
             "Datum zahájení": "", "Datum ukončení": ""},
            {"Typ": "Rezervace", "Referenční kód": "",
             "Datum": "", "Datum připsání na účet": "",
             "Vyplaceno": "0", "Částka": "626.14",
             "Potvrzující kód": "HM9HPCENWY", "Host": "Andi Zielecki",
             "Nabídka": "Stylish APT", "Datum zahájení": "12/30/2025",
             "Datum ukončení": "01/01/2026"},
            # The bug trigger: one item with CZK in the EUR column
            {"Typ": "Rezervace", "Referenční kód": "",
             "Datum": "", "Datum připsání na účet": "",
             "Vyplaceno": "0", "Částka": "13266.50",
             "Potvrzující kód": "HM5MSESR8S", "Host": "Reem Hani",
             "Nabídka": "2BR Nest", "Datum zahájení": "12/30/2025",
             "Datum ukončení": "01/01/2026"},
        ],
    )]


class TestRateAnomalyFallback:
    def test_anomalous_batch_uses_cnb_for_per_item_czk(self):
        with patch("report.verifier._read_csv_rows", return_value=_make_csv_rows()), \
             patch("report.verifier._check_required_columns"), \
             patch("report.verifier._cnb_rate_for_batch_date",
                   side_effect=_fake_cnb_rate_for_batch_date):
            data = build_airbnb_payout_data(["test.csv"])

        # Implied batch rate is 179537.65 / (626.14 + 13266.50) = 12.92,
        # which is outside the [_AIRBNB_RATE_MIN, _AIRBNB_RATE_MAX] band →
        # implied_rate = 0, rate_anomaly = True.
        assert len(data["batches"]) == 1
        b = data["batches"][0]
        assert b["batch_ref"] == "G-FS2OGNYOBEB7U"
        assert b["implied_rate"] == 0.0
        assert b["rate_anomaly"] is True

        # display_rate falls back to ČNB at payout_date.
        assert b["display_rate"] == pytest.approx(24.20)

        # Per-item amount_czk is now populated (no longer NULL).
        items = data["items"]
        andi = next(it for it in items
                    if it["confirmation_code"] == "HM9HPCENWY")
        assert andi["amount_czk"] == pytest.approx(626.14 * 24.20, abs=0.01)

    def test_normal_batch_implied_rate_is_used(self):
        normal = [(
            "ok.csv",
            [
                {"Typ": "Payout", "Referenční kód": "G-NORMAL/ROC/X",
                 "Datum": "12/31/2025", "Datum připsání na účet": "",
                 "Vyplaceno": "24250", "Částka": "0",
                 "Potvrzující kód": "", "Host": "", "Nabídka": "",
                 "Datum zahájení": "", "Datum ukončení": ""},
                {"Typ": "Rezervace", "Referenční kód": "",
                 "Datum": "", "Datum připsání na účet": "",
                 "Vyplaceno": "0", "Částka": "1000.00",
                 "Potvrzující kód": "HMOK", "Host": "Test", "Nabídka": "X",
                 "Datum zahájení": "12/30/2025",
                 "Datum ukončení": "01/01/2026"},
            ],
        )]
        with patch("report.verifier._read_csv_rows", return_value=normal), \
             patch("report.verifier._check_required_columns"):
            data = build_airbnb_payout_data(["ok.csv"])

        b = data["batches"][0]
        assert b["rate_anomaly"] is False
        assert b["implied_rate"] == pytest.approx(24.25)
        # display_rate equals the implied rate when not anomalous.
        assert b["display_rate"] == pytest.approx(24.25)

        item = next(it for it in data["items"]
                    if it["confirmation_code"] == "HMOK")
        assert item["amount_czk"] == pytest.approx(24250.0, abs=0.01)


class TestVyrovnaniBatch:
    """Bare "Vyrovnání" item type (without "z řešení" suffix) must spawn an
    ADJ entry so the parent reservation's net payout reflects the refund.
    Real-world case: Daniel Klešč HMH2FKHMJ5, where the main payout was
    +581.36 EUR / +14084.44 CZK and a follow-up refund batch had item_type
    "Vyrovnání" of -176.44 EUR / -4278.20 CZK.  Old loader silently dropped
    it because the filter required "řešení" in the type string."""

    def _vyrovnani_csv(self):
        return [(
            "vyr.csv",
            [
                # Main reservation batch: paid out 02/09
                {"Typ": "Payout", "Referenční kód": "G-XWVGMAFT7UTIT/ROC/X",
                 "Datum": "02/09/2026", "Datum připsání na účet": "",
                 "Vyplaceno": "25112,44", "Částka": "0",
                 "Potvrzující kód": "", "Host": "", "Nabídka": "",
                 "Datum zahájení": "", "Datum ukončení": ""},
                {"Typ": "Rezervace", "Referenční kód": "",
                 "Datum": "", "Datum připsání na účet": "",
                 "Vyplaceno": "0", "Částka": "581.36",
                 "Potvrzující kód": "HMH2FKHMJ5", "Host": "Daniel Klešč",
                 "Nabídka": "MyMozart 515", "Datum zahájení": "02/08/2026",
                 "Datum ukončení": "02/16/2026"},
                {"Typ": "Rezervace", "Referenční kód": "",
                 "Datum": "", "Datum připsání na účet": "",
                 "Vyplaceno": "0", "Částka": "455.20",
                 "Potvrzující kód": "HM5DW4DT38", "Host": "Mo Von Loesch",
                 "Nabídka": "MyMozart 515", "Datum zahájení": "02/06/2026",
                 "Datum ukončení": "02/09/2026"},
                # Vyrovnání batch: refund 02/13
                {"Typ": "Payout", "Referenční kód": "G-UTJI2CFMZUVDT/ROC/X",
                 "Datum": "02/13/2026", "Datum připsání na účet": "",
                 "Vyplaceno": "17993,23", "Částka": "0",
                 "Potvrzující kód": "", "Host": "", "Nabídka": "",
                 "Datum zahájení": "", "Datum ukončení": ""},
                {"Typ": "Vyrovnání", "Referenční kód": "",
                 "Datum": "", "Datum připsání na účet": "",
                 "Vyplaceno": "0", "Částka": "-176.44",
                 "Potvrzující kód": "HMH2FKHMJ5", "Host": "Daniel Klešč",
                 "Nabídka": "MyMozart 515", "Datum zahájení": "02/08/2026",
                 "Datum ukončení": "02/16/2026"},
                {"Typ": "Rezervace", "Referenční kód": "",
                 "Datum": "", "Datum připsání na účet": "",
                 "Vyplaceno": "0", "Částka": "280.54",
                 "Potvrzující kód": "HMJF8BKB4Q", "Host": "Cameron Bennett",
                 "Nabídka": "MyMozart 515", "Datum zahájení": "02/12/2026",
                 "Datum ukončení": "02/14/2026"},
            ],
        )]

    def test_vyrovnani_added_to_all_batches_map_as_adjustment(self):
        with patch("report.verifier._read_csv_rows", return_value=self._vyrovnani_csv()), \
             patch("report.verifier._check_required_columns"):
            data = build_airbnb_payout_data(["vyr.csv"])

        # Daniel must have BOTH batches recorded — main + Vyrovnání refund.
        daniel_batches = data["all_batches_map"].get("HMH2FKHMJ5") or []
        grefs = sorted(b["gref"] for b in daniel_batches)
        assert grefs == ["G-UTJI2CFMZUVDT", "G-XWVGMAFT7UTIT"]

        refund = next(b for b in daniel_batches
                      if b["gref"] == "G-UTJI2CFMZUVDT")
        assert refund["payout_eur"] == pytest.approx(-176.44)

        # reservation_map keeps the *largest-magnitude* batch as primary —
        # main payout 581.36 > refund 176.44 → main wins.
        assert data["reservation_map"]["HMH2FKHMJ5"]["gref"] == "G-XWVGMAFT7UTIT"

        # AirCover branch must NOT pick up Vyrovnání (only "výplata jako
        # výsledek řešení" rows go there).
        assert "HMH2FKHMJ5" not in data["aircover_map"]
