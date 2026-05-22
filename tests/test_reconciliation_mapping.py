"""Tests for object-name mapping in the Srovnání (reconciliation) view.

Covers:
- Universal fold patterns (lublanska 13 leva/prava → lublanska 13) on both channels.
- Booking-only fold patterns (malostranska 1p/3p → malostranska) only fold on Booking.
- New aliases for Czech-form variants and slug typos:
    * "Lublaň 13" (315 short form) → lublanska 13
    * "Stranokicka 21 NOVA" (slug typo) → strakonicka 21 / nova
    * "Žitná 208/308 NOVÁ" (slug suffix) → zitna 208 / zitna 308
    * "Delnicka 44 nova" (slug suffix) → delnicka 44
    * "Svornosti 1" / "Svornosti 1497 1" → svornosti 1497/1
"""

import json
import sqlite3

import pytest

from report.accounting import (
    _OBJEKT_315_ALIASES,
    _apply_fold,
    build_payout_aggregate,
    compute_l3_reconciliation,
)


def _make_payout_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE report_objects (slug TEXT PRIMARY KEY, display_name TEXT);
        CREATE TABLE report_rows (
            slug TEXT, year INTEGER, month INTEGER, data TEXT
        );
        """
    )
    return conn


def _seed_payout(conn, slug, display, source, payout_czk, year=2026, month=3):
    conn.execute(
        "INSERT OR IGNORE INTO report_objects(slug, display_name) VALUES(?,?)",
        (slug, display),
    )
    conn.execute(
        "INSERT INTO report_rows(slug, year, month, data) VALUES(?,?,?,?)",
        (
            slug,
            year,
            month,
            json.dumps({"source": source, "payout_czk": payout_czk}),
        ),
    )


# ---------------------------------------------------------------------------
# _apply_fold — channel scoping
# ---------------------------------------------------------------------------


class TestApplyFold:
    def test_lublanska_leva_folds_for_airbnb(self):
        assert _apply_fold("lublanska 13, leva", "airbnb") == "lublanska 13"

    def test_lublanska_prava_folds_for_airbnb(self):
        assert _apply_fold("lublanska 13, prava", "airbnb") == "lublanska 13"

    def test_lublanska_leva_folds_for_booking(self):
        assert _apply_fold("lublanska 13, leva", "booking") == "lublanska 13"

    def test_lublanska_bare_does_not_fold(self):
        # The 315 form "Lublanska 13" must NOT be touched by the fold pattern,
        # otherwise we'd lose group_map's distinct→folded contract.
        assert _apply_fold("lublanska 13", "airbnb") == "lublanska 13"
        assert _apply_fold("lublanska 13", "booking") == "lublanska 13"

    def test_malostranska_folds_only_for_booking(self):
        assert _apply_fold("malostranska 1p", "booking") == "malostranska"
        assert _apply_fold("malostranska 3p", "booking") == "malostranska"

    def test_malostranska_does_not_fold_for_airbnb(self):
        # Slugs Malostranska_1P / Malostranska_3P are independent units on
        # Airbnb. 315 also tracks them separately on Airbnb.
        assert _apply_fold("malostranska 1p", "airbnb") == "malostranska 1p"
        assert _apply_fold("malostranska 3p", "airbnb") == "malostranska 3p"

    def test_unrelated_objects_pass_through(self):
        assert _apply_fold("kremencova 2", "booking") == "kremencova 2"
        assert _apply_fold("v haji 10", "airbnb") == "v haji 10"


# ---------------------------------------------------------------------------
# Aliases — keys must resolve to canonical forms
# ---------------------------------------------------------------------------


class TestObjektAliases:
    def test_lublan_short_form(self):
        # "Lublaň 13" decomposes to "lublan 13" after diacritic-strip.
        assert _OBJEKT_315_ALIASES["lublan 13"] == "lublanska 13"

    def test_stranokicka_typo(self):
        assert _OBJEKT_315_ALIASES["stranokicka 21 nova"] == "strakonicka 21 / nova"
        assert (
            _OBJEKT_315_ALIASES["stranokicka 21 / nova"] == "strakonicka 21 / nova"
        )

    def test_zitna_nova_suffix(self):
        assert _OBJEKT_315_ALIASES["zitna 208 nova"] == "zitna 208"
        assert _OBJEKT_315_ALIASES["zitna 308 nova"] == "zitna 308"

    def test_delnicka_44_nova(self):
        assert _OBJEKT_315_ALIASES["delnicka 44 nova"] == "delnicka 44"

    def test_svornosti_variants(self):
        assert _OBJEKT_315_ALIASES["svornosti 1"] == "svornosti 1497/1"
        assert _OBJEKT_315_ALIASES["svornosti 1497 1"] == "svornosti 1497/1"


# ---------------------------------------------------------------------------
# build_payout_aggregate — slug → (objekt_norm, mesic) with fold applied
# ---------------------------------------------------------------------------


class TestBuildPayoutAggregate:
    def test_lublanska_leva_prava_fold_to_single_key_for_airbnb(self):
        conn = _make_payout_conn()
        _seed_payout(conn, "Lublanska_13_Leva", "Lublanska 13, leva", "Airbnb", 70635.35)
        _seed_payout(conn, "Lublanska_13_Prava", "Lublanska 13, prava", "Airbnb", 63661.47)

        agg = build_payout_aggregate(conn, "airbnb", 2026, 3)

        assert agg == {("lublanska 13", "2026-03"): pytest.approx(134296.82, abs=0.01)}

    def test_stranokicka_aliased_to_strakonicka(self):
        conn = _make_payout_conn()
        _seed_payout(conn, "Stranokicka_21_NOVA", "Stranokicka 21 / NOVA", "Airbnb", 18514.22)

        agg = build_payout_aggregate(conn, "airbnb", 2026, 3)

        assert agg == {("strakonicka 21 / nova", "2026-03"): pytest.approx(18514.22)}

    def test_zitna_nova_drops_suffix(self):
        conn = _make_payout_conn()
        _seed_payout(conn, "Zitna_308_NOVA", "Žitná 308 NOVÁ", "Airbnb", 25594.96, year=2026, month=2)

        agg = build_payout_aggregate(conn, "airbnb", 2026, 2)

        assert agg == {("zitna 308", "2026-02"): pytest.approx(25594.96)}

    def test_delnicka_44_nova_aliased_for_booking(self):
        conn = _make_payout_conn()
        _seed_payout(conn, "Delnicka_44_Nova", "Dělnická 44 - nová", "Booking.com", 22694.87, year=2026, month=2)

        agg = build_payout_aggregate(conn, "booking", 2026, 2)

        assert agg == {("delnicka 44", "2026-02"): pytest.approx(22694.87)}

    def test_excluded_rows_are_skipped(self):
        conn = _make_payout_conn()
        slug = "Lublanska_13_Leva"
        conn.execute(
            "INSERT INTO report_objects(slug, display_name) VALUES(?,?)",
            (slug, "Lublanska 13, leva"),
        )
        conn.execute(
            "INSERT INTO report_rows(slug, year, month, data) VALUES(?,?,?,?)",
            (slug, 2026, 3, json.dumps({"source": "Airbnb", "payout_czk": 1000.0, "is_excluded": True})),
        )

        agg = build_payout_aggregate(conn, "airbnb", 2026, 3)

        assert agg == {}


# ---------------------------------------------------------------------------
# compute_l3_reconciliation — end-to-end mapping behaviour
# ---------------------------------------------------------------------------


def _acct(objekt, channel, castka, doc_type="FKV", mesic="2026-03"):
    return {
        "doc_type": doc_type,
        "channel": channel,
        "objekt": objekt,
        "castka": castka,
        "mesic": mesic,
        "datum": f"{mesic}-31",
        "popis": "",
    }


class TestComputeL3Reconciliation:
    def test_lublanska_leva_prava_match_single_315_entry(self):
        # Payout side has leva and prava already aggregated via fold (as
        # build_payout_aggregate would produce).
        payout_agg = {("lublanska 13", "2026-03"): 134296.82}
        # 315 side has a single "Lublanska 13" entry covering both units.
        acct = [_acct("Lublanska 13", "Airbnb", 134296.82)]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["objekt_src"] == "lublanska 13"
        assert results[0]["objekt_315"] == "lublanska 13"
        assert results[0]["diff"] == pytest.approx(0.0, abs=0.01)

    def test_lublan_short_form_matches_lublanska(self):
        # 315 in Jan 2026 used "Lublaň 13" → diacritic-stripped to "lublan 13".
        # Alias must resolve it onto the same key as "lublanska 13".
        payout_agg = {("lublanska 13", "2026-01"): 50000.0}
        acct = [_acct("Lublaň 13", "Airbnb", 50000.0, mesic="2026-01")]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["objekt_315"] == "lublanska 13"

    def test_stranokicka_typo_matches_strakonicka(self):
        payout_agg = {("strakonicka 21 / nova", "2026-03"): 18514.22}
        acct = [_acct("Strakonicka 21 / NOVA", "Airbnb", 18514.22)]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["score"] == 1.0  # exact match after alias

    def test_zitna_nova_matches_short_form(self):
        payout_agg = {("zitna 308", "2026-02"): 25000.0}
        acct = [_acct("Žitná 308", "Airbnb", 25000.0, mesic="2026-02")]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["objekt_src"] == "zitna 308"
        assert results[0]["objekt_315"] == "zitna 308"

    def test_delnicka_44_nova_matches_for_booking(self):
        payout_agg = {("delnicka 44", "2026-02"): 22694.87}
        acct = [_acct("Dělnická 44", "Booking", 22694.87, mesic="2026-02")]

        results = compute_l3_reconciliation(payout_agg, acct, "booking")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"

    def test_malostranska_units_remain_separate_for_airbnb(self):
        # On Airbnb side the units must NOT fold — they're independent.
        payout_agg = {
            ("malostranska 1p", "2026-01"): 16711.21,
            ("malostranska 3p", "2026-01"): 15670.21,
        }
        acct = [_acct("Malostranska 1P", "Airbnb", 19143.76, mesic="2026-01")]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        statuses = {(r["objekt_src"], r["status"]) for r in results}
        assert ("malostranska 1p", "PARTIAL") in statuses
        assert ("malostranska 3p", "UNMATCHED") in statuses

    def test_malostranska_units_fold_for_booking(self):
        payout_agg = {("malostranska", "2026-01"): 3774.37}
        acct = [
            _acct("Malostranska 1P", "Booking", 1317.54, mesic="2026-01"),
            _acct("Malostranska 3P", "Booking", 2456.83, mesic="2026-01"),
        ]

        results = compute_l3_reconciliation(payout_agg, acct, "booking")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["objekt_src"] == "malostranska"
        assert results[0]["objekt_315"] == "malostranska"

    def test_negative_correction_subtracts_from_315_aggregate(self):
        # Korekce / klavbék (Money S3 line with negative Dal) must reduce the
        # 315-side total. Before the loader fix this entry was stored with
        # abs() and showed up as +X, inflating Srovnání instead of cancelling.
        payout_agg = {("mymozart 515", "2025-01"): 8085.47}
        acct = [
            _acct("MyMozart 515", "Airbnb", 11596.90, mesic="2025-01"),
            _acct("MyMozart 515", "Airbnb", -3511.43, mesic="2025-01"),
        ]

        results = compute_l3_reconciliation(payout_agg, acct, "airbnb")

        assert len(results) == 1
        assert results[0]["status"] == "MATCHED"
        assert results[0]["diff"] == pytest.approx(0.0, abs=0.01)
