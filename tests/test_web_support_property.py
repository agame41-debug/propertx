import pytest
from report.web_support import (
    attach_mock_status,
    compute_status_counts,
    group_expenses_by_category,
    get_adjacent_month,
)


class TestAttachMockStatus:
    def test_excluded_row(self):
        rows = [{"is_excluded": 1, "verification_status": "MATCHED"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "EXCLUDED"
        assert rows[0]["_mock_status_class"] == "badge-mute"
        assert rows[0]["_mock_status_label"] == "VYLOUČENO"

    def test_payout_adjustment_row(self):
        rows = [{"is_payout_adjustment": 1, "verification_status": ""}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "ADJUSTMENT"
        assert rows[0]["_mock_status_class"] == "badge-brand"
        assert rows[0]["_mock_status_label"] == "ÚPRAVA"

    def test_split_transaction_row(self):
        rows = [{"is_split_transaction": 1, "verification_status": ""}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "SPLIT"
        assert rows[0]["_mock_status_class"] == "badge-brand"

    def test_moved_in_row(self):
        rows = [{"adjustment_original_year": 2026, "adjustment_original_month": 3, "verification_status": "MATCHED"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "MOVED_IN"
        assert "PŘESUN Z 03" in rows[0]["_mock_status_label"]
        assert rows[0]["_mock_status_class"] == "badge-info"

    def test_matched_row(self):
        rows = [{"verification_status": "MATCHED"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "MATCHED"
        assert rows[0]["_mock_status_class"] == "badge-ok"

    def test_rozdil_row(self):
        rows = [{"verification_status": "ROZDÍL"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "ROZDIL"
        assert rows[0]["_mock_status_class"] == "badge-warn"

    def test_chybi_v_csv_row(self):
        rows = [{"verification_status": "CHYBÍ_V_CSV"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "CHYBI_V_CSV"
        assert rows[0]["_mock_status_class"] == "badge-err"

    def test_chybi_v_hostify_row(self):
        rows = [{"verification_status": "CHYBÍ_V_HOSTIFY"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "CHYBI_V_HOSTIFY"
        assert rows[0]["_mock_status_class"] == "badge-err"
        assert rows[0]["_mock_status_label"] == "CHYBÍ V HOSTIFY"

    def test_zruseno_row(self):
        rows = [{"verification_status": "ZRUŠENO"}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "ZRUSENO"
        assert rows[0]["_mock_status_class"] == "badge-mute"
        assert rows[0]["_mock_status_label"] == "ZRUŠENO"

    def test_ke_kontrole_default(self):
        rows = [{"verification_status": ""}]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "KE_KONTROLE"
        assert rows[0]["_mock_status_class"] == "badge-mute"

    def test_in_place_mutation(self):
        """attach_mock_status modifies rows in-place, returns None."""
        rows = [{"verification_status": "MATCHED"}]
        result = attach_mock_status(rows)
        assert result is None
        assert "_mock_status" in rows[0]

    def test_matched_with_incomplete_tax_verification_downgrades_to_ke_kontrole(self):
        """If tax_verification_required=True and checkin_verified=False, MATCHED downgrades."""
        rows = [{
            "verification_status": "MATCHED",
            "tax_verification_required": True,
            "checkin_verified": False,
            "checkin_missing_age_guests": 0,
        }]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "KE_KONTROLE"
        assert rows[0]["_mock_status_label"] == "KE KONTROLE"

    def test_matched_with_completed_tax_verification_stays_matched(self):
        """If tax_verification_required=True and checkin_verified=True, MATCHED stays."""
        rows = [{
            "verification_status": "MATCHED",
            "tax_verification_required": True,
            "checkin_verified": True,
            "checkin_missing_age_guests": 0,
        }]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "MATCHED"

    def test_matched_when_no_tax_verification_required_stays_matched(self):
        """If tax_verification_required=False, MATCHED stays regardless of checkin status."""
        rows = [{
            "verification_status": "MATCHED",
            "tax_verification_required": False,
            "checkin_verified": False,
        }]
        attach_mock_status(rows)
        assert rows[0]["_mock_status"] == "MATCHED"


class TestComputeStatusCounts:
    def test_empty(self):
        assert compute_status_counts([]) == {
            "all_rows": 0, "active": 0, "nights": 0, "adjustments": 0,
            "excluded": 0, "moved": 0, "problems": 0,
        }

    def test_mixed(self):
        rows = [
            {"verification_status": "MATCHED", "_mock_status": "MATCHED", "nights": 3},
            {"verification_status": "ROZDÍL", "_mock_status": "ROZDIL", "nights": 4},
            {"verification_status": "CHYBÍ_V_CSV", "_mock_status": "CHYBI_V_CSV", "nights": 2},
            {"is_excluded": 1, "_mock_status": "EXCLUDED", "nights": 5},
            {"is_payout_adjustment": 1, "_mock_status": "ADJUSTMENT", "nights": 0},
            {"adjustment_original_year": 2026, "adjustment_original_month": 3, "_mock_status": "MOVED_IN", "nights": 3},
        ]
        c = compute_status_counts(rows)
        assert c["all_rows"] == 6
        assert c["active"] == 4  # excludes EXCLUDED + ADJUSTMENT
        assert c["nights"] == 12  # 3 + 4 + 2 + 3
        assert c["adjustments"] == 1
        assert c["excluded"] == 1
        assert c["moved"] == 1
        assert c["problems"] == 2  # ROZDIL + CHYBI_V_CSV

    def test_composes_with_attach_mock_status(self):
        """Realistic composition: attach_mock_status THEN compute_status_counts."""
        rows = [
            {"verification_status": "MATCHED", "nights": 3},
            {"verification_status": "ROZDÍL", "nights": 4},
            {"is_excluded": 1, "verification_status": "MATCHED", "nights": 5},
            {"adjustment_original_year": 2026, "adjustment_original_month": 3, "verification_status": "MATCHED", "nights": 2},
        ]
        attach_mock_status(rows)
        counts = compute_status_counts(rows)
        assert counts["all_rows"] == 4
        assert counts["active"] == 3        # MATCHED + ROZDIL + MOVED_IN
        assert counts["nights"] == 9        # 3 + 4 + 2
        assert counts["excluded"] == 1
        assert counts["moved"] == 1
        assert counts["problems"] == 1      # ROZDIL


class TestGroupExpensesByCategory:
    def test_empty(self):
        assert group_expenses_by_category([]) == {}

    def test_grouping(self):
        ex = [
            {"id": 1, "category_name": "Energie", "amount_czk": 1000},
            {"id": 2, "category_name": "Služby", "amount_czk": 500},
            {"id": 3, "category_name": "Energie", "amount_czk": 800},
        ]
        groups = group_expenses_by_category(ex)
        assert list(groups.keys()) == ["Energie", "Služby"]
        assert len(groups["Energie"]) == 2
        assert len(groups["Služby"]) == 1

    def test_null_category_falls_to_ostatni(self):
        ex = [{"id": 1, "category_name": None, "amount_czk": 100}]
        groups = group_expenses_by_category(ex)
        assert "Ostatní" in groups


class TestGetAdjacentMonth:
    def test_prev_normal(self):
        assert get_adjacent_month(2026, 4, "prev") == (2026, 3)

    def test_next_normal(self):
        assert get_adjacent_month(2026, 4, "next") == (2026, 5)

    def test_prev_january_wraps_to_december(self):
        assert get_adjacent_month(2026, 1, "prev") == (2025, 12)

    def test_next_december_wraps_to_january(self):
        assert get_adjacent_month(2026, 12, "next") == (2027, 1)

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="prev.*next"):
            get_adjacent_month(2026, 4, "sideways")
