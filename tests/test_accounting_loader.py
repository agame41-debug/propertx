"""Tests for load_hlavni_kniha_from_bytes.

Critically covers sign preservation on Dal / MD columns: korekce / klavbéky
arrive as negative numbers in the source CSV and must NOT be silently
converted to positive (which used to happen via abs(), masking the
correction in Srovnání).
"""

import pytest

from report.accounting import load_hlavni_kniha_from_bytes


def _build_csv(*rows: list[str]) -> bytes:
    return ("\r\n".join(";".join(r) for r in rows) + "\r\n").encode("cp1250")


def _row(*, doc="FKV250008", datum="31.01.2025", popis="test", dal="0",
         md="0", stredisko="M515", ucet="315001"):
    """Build a single 'Detail 2 - Detail 1' row matching the column layout
    expected by load_hlavni_kniha_from_bytes (col indices used: 3, 5, 8,
    10, 13, 16, 17)."""
    cols = [""] * 18
    cols[0] = "Detail 2 - Detail 1"
    cols[1] = "1"
    cols[3] = doc
    cols[5] = datum
    cols[8] = popis
    cols[10] = dal
    cols[13] = md
    cols[16] = stredisko
    cols[17] = ucet
    return cols


class TestBpsKeepsSign:
    """BPS / JINY entries (raw bank movements, manual klavbéky) preserve sign."""

    def test_positive_dal_stored_positive(self):
        csv = _build_csv(_row(doc="BPS2500026", dal="11595,49",
                              popis="Eksteinová (MM515 - 11595,49)"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["castka"] == pytest.approx(11595.49)
        assert out[0]["doc_type"] == "JINY"

    def test_negative_dal_keeps_sign_on_klavbek(self):
        # BPS2500032 had Dal = -3511,43 (klavbék on Veronika Eksteinová's
        # Airbnb payout). Without sign preservation, korekce never offsets
        # the original payout in Srovnání and the host appears to be owed
        # the full 11595.49 instead of the net 8084.06.
        csv = _build_csv(_row(doc="BPS2500032", dal="-3511,43",
                              popis="Eksteinová (MM515 - -3511,43)"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["castka"] == pytest.approx(-3511.43)

    def test_negative_md_keeps_sign_on_klavbek(self):
        csv = _build_csv(_row(doc="BPS2500099", dal="0", md="-200,00"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["castka"] == pytest.approx(-200.0)


class TestInternalZapoctyTakeMagnitude:
    """FKV / FHS / FHO / FU / RF entries flip sign across Money S3 exports
    depending on which protistrana row gets written. For Srovnání we need
    the magnitude, otherwise an interní zápočet exported on the MD-side
    cancels the same one exported on the Dal-side from a re-export.
    """

    def test_negative_fkv_dal_treated_as_positive_magnitude(self):
        csv = _build_csv(_row(doc="FKV250008", dal="-20485,37",
                              popis="M515 01/25 - výplata od Airbnb"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["doc_type"] == "FKV"
        assert out[0]["castka"] == pytest.approx(20485.37)

    def test_positive_fhs_dal_unchanged(self):
        csv = _build_csv(_row(doc="FHS250008", dal="6543,52",
                              popis="M515 01/25 - zápočet s platbou od Airbnb"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["doc_type"] == "FHS"
        assert out[0]["castka"] == pytest.approx(6543.52)

    def test_md_column_used_when_dal_zero(self):
        csv = _build_csv(_row(doc="BPS2500003", dal="0", md="500,00"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["castka"] == pytest.approx(500.0)

    def test_dal_takes_priority_when_both_filled(self):
        csv = _build_csv(_row(doc="BPS2500003", dal="100,00", md="200,00"))
        out = load_hlavni_kniha_from_bytes(csv)
        assert len(out) == 1
        assert out[0]["castka"] == pytest.approx(100.0)
