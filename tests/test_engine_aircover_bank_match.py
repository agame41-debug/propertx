"""Tests for AirCover / "Vyrovnání z řešení" bank matching.

Without an explicit aircover-pass in engine.py, AirCover batches end up in
payout_batches but never receive a payout_batch_bank_matches row, so the
bank tx (which has the right G-ref) shows as nespárovaná on /bank.
"""

from report.engine import _match_aircover_to_bank


def _ac(code, gref, eur=98.0, payout_date="10/07/2025"):
    return {
        "batch_ref": gref,
        "gref": gref,
        "payout_date": payout_date,
        "amount_eur": eur,
        "amount_czk": eur * 24.25,
        "guest_name": "Anat Bar Or",
        "item_type": "Výplata jako výsledek řešení",
        "details": "",
    }


def _bank_row(gref, czk):
    return {"tx_key": f"tx::{gref}", "datum": "2025-10-07",
            "amount_czk": czk, "gref": gref}


class TestMatchAircoverToBank:
    def test_aircover_gref_in_bank_index_creates_match(self):
        ac_map = {"HM3YMMTFJD": [_ac("HM3YMMTFJD", "G-QJFWK6NYLSRAN")]}
        bank_index = {"G-QJFWK6NYLSRAN": _bank_row("G-QJFWK6NYLSRAN", 2376.45)}

        matches = _match_aircover_to_bank(ac_map, bank_index, None, [])

        assert len(matches) == 1
        m = matches[0]
        assert m["batch_ref"] == "G-QJFWK6NYLSRAN"
        assert m["tx_key"] == "tx::G-QJFWK6NYLSRAN"
        assert m["match_method"] == "gref"
        assert m["matched_amount_czk"] == 2376.45

    def test_aircover_with_no_bank_tx_returns_empty(self):
        ac_map = {"HM3YMMTFJD": [_ac("HM3YMMTFJD", "G-MISSING")]}
        bank_index = {}

        matches = _match_aircover_to_bank(ac_map, bank_index, None, [])

        assert matches == []

    def test_aircover_skips_already_matched_batch(self):
        ac_map = {"HM3YMMTFJD": [_ac("HM3YMMTFJD", "G-Q")]}
        bank_index = {"G-Q": _bank_row("G-Q", 2376.45)}
        already = [{"batch_ref": "G-Q", "tx_key": "tx::G-Q",
                    "match_method": "gref", "matched_amount_czk": 2376.45}]

        matches = _match_aircover_to_bank(ac_map, bank_index, None, already)

        assert matches == []

    def test_aircover_falls_back_to_full_index(self):
        # Cross-cutoff bank tx (e.g. AC paid in next month's window).
        ac_map = {"X": [_ac("X", "G-X")]}
        bank_index = {}
        bank_index_full = {"G-X": _bank_row("G-X", 999.0)}

        matches = _match_aircover_to_bank(ac_map, bank_index, bank_index_full, [])

        assert len(matches) == 1
        assert matches[0]["matched_amount_czk"] == 999.0
