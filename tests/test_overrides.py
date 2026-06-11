"""
tests/test_overrides.py — Phase 4: reservation override events.

Tests:
- create_override_event records old + new value, reason, actor
- get_override_events returns all events ordered newest-first
- revert_override_event sets is_active=0 + reverted_at/by
- get_active_overrides_for_month: active only, latest value wins
- apply_overrides_to_rows: numeric field (payout_czk) + text field (verification_status)
- apply_overrides_to_rows: reverted override does NOT affect rows
- apply_overrides_to_rows: rows with no override returned unchanged
- _overridden dict carries original value
"""

import pytest

from report.db import (
    apply_overrides_to_rows,
    create_override_event,
    get_active_overrides_for_month,
    get_connection,
    get_override_events,
    normalize_override_value,
    revert_override_event,
    OVERRIDE_FIELD_LABELS,
    OVERRIDE_SCOPE_RESERVATION,
    VERIFICATION_STATUS_OPTIONS,
)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


def _make_event(conn, *, code="HMXYZ123", field="payout_czk",
                old="10000", new="12000", reason="oprava kurzu", slug="test_prop",
                year=2025, month=3):
    return create_override_event(conn, {
        "scope_type": OVERRIDE_SCOPE_RESERVATION,
        "scope_id": code,
        "slug": slug,
        "year": year,
        "month": month,
        "field": field,
        "old_value": old,
        "new_value": new,
        "reason": reason,
        "actor": "admin",
    })


class TestCreateOverrideEvent:
    def test_returns_event_dict(self, conn):
        ev = _make_event(conn)
        assert ev["id"] > 0
        assert ev["scope_id"] == "HMXYZ123"
        assert ev["field"] == "payout_czk"
        assert ev["old_value"] == "10000"
        assert ev["new_value"] == "12000"
        assert ev["reason"] == "oprava kurzu"
        assert ev["actor"] == "admin"
        assert ev["is_active"] == 1
        assert ev["reverted_at"] is None

    def test_default_scope_type(self, conn):
        ev = _make_event(conn)
        assert ev["scope_type"] == "reservation"

    def test_empty_reason_allowed(self, conn):
        ev = create_override_event(conn, {
            "scope_id": "X", "slug": "s", "year": 2025, "month": 1,
            "field": "payout_czk", "old_value": "0", "new_value": "100",
        })
        assert ev["reason"] == ""
        assert ev["is_active"] == 1

    def test_legacy_verification_status_alias_is_normalized(self, conn):
        ev = create_override_event(conn, {
            "scope_id": "X", "slug": "s", "year": 2025, "month": 1,
            "field": "verification_status", "old_value": "ROZDÍL", "new_value": "CHYBÍ_CSV",
        })
        assert ev["new_value"] == "CHYBÍ_V_CSV"

    def test_invalid_verification_status_is_rejected(self, conn):
        with pytest.raises(ValueError):
            create_override_event(conn, {
                "scope_id": "X", "slug": "s", "year": 2025, "month": 1,
                "field": "verification_status", "old_value": "ROZDÍL", "new_value": "BOOM",
            })


class TestGetOverrideEvents:
    def test_returns_events_for_month(self, conn):
        _make_event(conn, code="A", slug="prop1", year=2025, month=3)
        _make_event(conn, code="B", slug="prop1", year=2025, month=3)
        evs = get_override_events(conn, "prop1", 2025, 3)
        assert len(evs) == 2

    def test_scoped_to_slug_year_month(self, conn):
        _make_event(conn, code="A", slug="prop1", year=2025, month=3)
        _make_event(conn, code="B", slug="prop2", year=2025, month=3)
        _make_event(conn, code="C", slug="prop1", year=2025, month=4)
        evs = get_override_events(conn, "prop1", 2025, 3)
        assert len(evs) == 1
        assert evs[0]["scope_id"] == "A"

    def test_includes_reverted(self, conn):
        ev = _make_event(conn)
        revert_override_event(conn, ev["id"])
        evs = get_override_events(conn, "test_prop", 2025, 3)
        assert len(evs) == 1
        assert evs[0]["is_active"] == 0

    def test_ordered_newest_first(self, conn):
        ev1 = _make_event(conn, code="FIRST")
        ev2 = _make_event(conn, code="SECOND")
        evs = get_override_events(conn, "test_prop", 2025, 3)
        # newest (higher id) first
        assert evs[0]["id"] == ev2["id"]
        assert evs[1]["id"] == ev1["id"]


class TestRevertOverrideEvent:
    def test_sets_inactive(self, conn):
        ev = _make_event(conn)
        revert_override_event(conn, ev["id"], reverted_by="admin")
        evs = get_override_events(conn, "test_prop", 2025, 3)
        assert evs[0]["is_active"] == 0
        assert evs[0]["reverted_by"] == "admin"
        assert evs[0]["reverted_at"] is not None

    def test_revert_already_inactive_is_noop(self, conn):
        ev = _make_event(conn)
        revert_override_event(conn, ev["id"])
        revert_override_event(conn, ev["id"])  # second call: noop
        evs = get_override_events(conn, "test_prop", 2025, 3)
        assert evs[0]["is_active"] == 0


class TestGetActiveOverridesForMonth:
    def test_returns_active_only(self, conn):
        ev = _make_event(conn, code="RES1", field="payout_czk", new="15000")
        revert_override_event(conn, ev["id"])
        active = get_active_overrides_for_month(conn, "test_prop", 2025, 3)
        assert active == {}

    def test_latest_value_wins_for_same_field(self, conn):
        _make_event(conn, code="RES1", field="payout_czk", new="11000")
        _make_event(conn, code="RES1", field="payout_czk", new="99000")
        active = get_active_overrides_for_month(conn, "test_prop", 2025, 3)
        assert active["RES1"]["payout_czk"] == "99000"

    def test_multiple_codes(self, conn):
        _make_event(conn, code="A", field="payout_czk", new="5000")
        _make_event(conn, code="B", field="verification_status", new="MATCHED")
        active = get_active_overrides_for_month(conn, "test_prop", 2025, 3)
        assert active["A"]["payout_czk"] == "5000"
        assert active["B"]["verification_status"] == "MATCHED"


class TestApplyOverridesToRows:
    def _row(self, code="RES1", payout=10000.0, status="ROZDÍL"):
        return {
            "confirmation_code": code,
            "payout_czk": payout,
            "verification_status": status,
            "guest_name": "Test Guest",
            "priprava_pokoje_czk": 597.0,
            "city_tax_czk": 500.0,
            "dph_provize_czk": 378.72,
            "dph_uklid_balicky_czk": 125.37,
            "cena_ubytovani_czk": 8398.91,
        }

    def test_no_overrides_returns_unchanged(self, conn):
        rows = [self._row()]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 10000.0
        assert "_overridden" not in result[0]

    def test_payout_czk_override_applied_as_float(self, conn):
        _make_event(conn, code="RES1", field="payout_czk", old="10000", new="15000")
        rows = [self._row(code="RES1")]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 15000.0

    def test_payout_czk_override_recalculates_cena_ubytovani(self, conn):
        _make_event(conn, code="RES1", field="payout_czk", old="10000", new="8662.79")
        rows = [self._row(code="RES1", payout=10000.0)]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 8662.79
        assert result[0]["cena_ubytovani_czk"] == 7061.7

    def test_verification_status_override_applied(self, conn):
        _make_event(conn, code="RES1", field="verification_status", old="ROZDÍL", new="MATCHED")
        rows = [self._row(code="RES1")]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["verification_status"] == "MATCHED"

    def test_overridden_dict_carries_old_value(self, conn):
        _make_event(conn, code="RES1", field="payout_czk", old="10000", new="15000")
        rows = [self._row(code="RES1", payout=10000.0)]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["_overridden"]["payout_czk"] == 10000.0

    def test_reverted_override_does_not_affect_row(self, conn):
        ev = _make_event(conn, code="RES1", field="payout_czk", old="10000", new="15000")
        revert_override_event(conn, ev["id"])
        rows = [self._row(code="RES1")]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 10000.0
        assert "_overridden" not in result[0]

    def test_other_rows_unchanged(self, conn):
        _make_event(conn, code="RES1", field="payout_czk", new="99999")
        rows = [self._row(code="RES1"), self._row(code="RES2", payout=5000.0)]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 99999.0
        assert result[1]["payout_czk"] == 5000.0
        assert "_overridden" not in result[1]

    def test_invalid_payout_value_rejected_at_creation(self, conn):
        # normalize_override_value now validates payout_czk at write time, so
        # garbage input never reaches the override_events table.
        with pytest.raises(ValueError, match="payout_czk"):
            _make_event(conn, code="RES1", field="payout_czk", new="not_a_number")

    def test_legacy_invalid_payout_value_is_skipped_at_apply(self, conn):
        # Older events created before normalize_override_value validated
        # numeric fields may still hold garbage. apply_overrides_to_rows must
        # skip them (logged) instead of crashing the whole regen.
        from datetime import datetime, timezone
        conn.execute(
            """INSERT INTO override_events
               (scope_type, scope_id, slug, year, month, field,
                old_value, new_value, reason, actor, is_active, created_at)
               VALUES ('reservation', 'RES1', 'test_prop', 2025, 3,
                       'payout_czk', '10000', 'not_a_number', '', 'admin', 1, ?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        rows = [self._row(code="RES1", payout=10000.0)]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 10000.0

    def test_legacy_czech_format_payout_value_is_normalized_at_apply(self, conn):
        # Legacy events with NBSP/comma values must still apply correctly
        # after the parser was hardened.
        from datetime import datetime, timezone
        conn.execute(
            """INSERT INTO override_events
               (scope_type, scope_id, slug, year, month, field,
                old_value, new_value, reason, actor, is_active, created_at)
               VALUES ('reservation', 'RES1', 'test_prop', 2025, 3,
                       'payout_czk', '10000', '8 662,79', '', 'admin', 1, ?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        rows = [self._row(code="RES1", payout=10000.0)]
        result = apply_overrides_to_rows(conn, rows, "test_prop", 2025, 3)
        assert result[0]["payout_czk"] == 8662.79


class TestOverrideFieldLabels:
    def test_known_fields_present(self):
        assert "payout_czk" in OVERRIDE_FIELD_LABELS
        assert "verification_status" in OVERRIDE_FIELD_LABELS

    def test_labels_are_strings(self):
        for key, label in OVERRIDE_FIELD_LABELS.items():
            assert isinstance(label, str)
            assert len(label) > 0


def test_normalize_override_value_accepts_only_canonical_verification_statuses():
    assert normalize_override_value("verification_status", "CHYBÍ_HOSTIFY") == "CHYBÍ_V_HOSTIFY"
    assert "CHYBÍ_V_CSV" in VERIFICATION_STATUS_OPTIONS


class TestNormalizePayoutCzk:
    def test_plain_dot_decimal(self):
        assert normalize_override_value("payout_czk", "8662.79") == "8662.79"

    def test_czech_format_nbsp_and_comma(self):
        # Real value Nikita typed in production: NBSP thousands sep + comma decimal.
        assert normalize_override_value("payout_czk", "8 662,79") == "8662.79"

    def test_narrow_nbsp_thousands_separator(self):
        assert normalize_override_value("payout_czk", "8 662.79") == "8662.79"

    def test_regular_space_thousands_separator(self):
        assert normalize_override_value("payout_czk", "8 662,79") == "8662.79"

    def test_integer_drops_decimal(self):
        assert normalize_override_value("payout_czk", "5000") == "5000"
        assert normalize_override_value("payout_czk", "5000.0") == "5000"

    def test_strips_surrounding_whitespace(self):
        assert normalize_override_value("payout_czk", "  5 000,50  ") == "5000.50"

    def test_empty_input_rejected(self):
        with pytest.raises(ValueError, match="payout_czk"):
            normalize_override_value("payout_czk", "")

    def test_garbage_input_rejected(self):
        with pytest.raises(ValueError, match="payout_czk"):
            normalize_override_value("payout_czk", "abc")

    def test_double_decimal_separator_rejected(self):
        with pytest.raises(ValueError, match="payout_czk"):
            normalize_override_value("payout_czk", "8 662,79.5")


class TestLockedMonthGuard:
    """DB-level backstop: overrides are applied to report_rows at READ time,
    so a bypassing caller could silently change a locked month's displayed
    numbers without a regen. The DB layer must refuse the write itself."""

    def _lock(self, conn, slug, year, month):
        conn.execute(
            """INSERT OR REPLACE INTO report_month_state
               (slug, year, month, status, data_state)
               VALUES (?, ?, ?, 'LOCKED', 'READY')""",
            (slug, year, month),
        )
        conn.commit()

    def test_create_override_event_rejects_locked_month(self, conn):
        from report.db_months import LockedReportMonthError
        self._lock(conn, "test_prop", 2025, 3)
        with pytest.raises(LockedReportMonthError):
            _make_event(conn)
        assert get_override_events(conn, "test_prop", 2025, 3) == []

    def test_revert_override_event_rejects_locked_month(self, conn):
        from report.db_months import LockedReportMonthError
        ev = _make_event(conn)
        self._lock(conn, "test_prop", 2025, 3)
        with pytest.raises(LockedReportMonthError):
            revert_override_event(conn, ev["id"], reverted_by="admin")
        events = get_override_events(conn, "test_prop", 2025, 3)
        assert events[0]["is_active"] == 1

    def test_create_override_event_allows_open_month(self, conn):
        ev = _make_event(conn)
        assert ev["is_active"] == 1
