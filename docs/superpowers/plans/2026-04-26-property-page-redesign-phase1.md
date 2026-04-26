# Property page redesign — Phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Visually redesign the property-month page (`/property/{slug}/{year}/{month}`) per the reference mock at `/Users/nikitashlykov/Downloads/315n/property/`, preserving every existing flow (lock, override, expenses CRUD, FloatingPanel, generation status, flash). Disabled stubs for Phase 2/3 buttons.

**Architecture:** Jinja2 partials + vanilla JS + HTMX. New tokens `--brand` (purple) and `--dph` (teal) added to `base_styles.html`. Property-specific CSS lives in a new partial `property_styles_property.html` (inline `<style>` injected at the top of the content block). All new component classes are prefixed (`kpi-/rs-/bd-/dph-/exp-/ae-/rex-/ph-`) and the `.badge` collision with global `base_styles_components.html` is resolved by scoping new badge styles under `.page .badge`. Backend gets four new helpers in `web_support.py`, a new `expenses_validation.py` module, six new fields in `summary.py`, and an idempotent ALTER TABLE migration adding `amount_net_czk / amount_dph_czk / vat_rate` to `expenses`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Jinja2, vanilla JS, HTMX 2.0.4, pytest.

**Spec reference:** `docs/superpowers/specs/2026-04-26-property-page-redesign-design.md`

**Phases not in this plan:**
- Phase 2 (`Vyloučit` button) — separate plan when ready
- Phase 3 (`Přesunout` `←/→` buttons) — separate plan when ready

---

## File structure

### Created

| Path | Responsibility |
|---|---|
| `report/expenses_validation.py` | `validate_and_canonicalize` — DPH consistency check + canonical net/dph computation |
| `templates/partials/property_styles_property.html` | Inline `<style>` block with all property-page-specific CSS (~700 lines, ported from mock `styles.css` with adaptations) |
| `templates/partials/property_kpi.html` | 4 KPI cards (extracted from current intro), variant logic by `client_type` |
| `templates/partials/property_notify_stack.html` | Stack of notify-strips above KPI |
| `templates/partials/property_generation_progress.html` | Generation spinner with auto-reload (extracted from current intro) |
| `templates/partials/property_dph_summary.html` | "Vyúčtování DPH" card, conditional on `client.platce_dph` |
| `templates/partials/property_expense_form.html` | Reusable add/edit calculator-strip form |
| `templates/partials/property_reservation_detail.html` | Expanded-row body: 3 groups + action row + inline override-form |
| `tests/test_expenses_validation.py` | Unit tests for `validate_and_canonicalize` |
| `tests/test_web_support_property.py` | Unit tests for `attach_mock_status`, `compute_status_counts`, `group_expenses_by_category`, `get_adjacent_month` |
| `tests/test_summary_new_fields.py` | Unit tests for new summary fields |

### Modified

| Path | Change |
|---|---|
| `report/db.py` | Add migration calls in `_run_migrations` (3 columns + category seed) |
| `report/db_admin.py` | Extend `add_expense` and `update_expense` to persist `amount_net_czk`, `amount_dph_czk`, `vat_rate` |
| `report/summary.py` | Refactor return to `result = {…}; result.update(…); return result`; add 6 new fields |
| `report/web_support.py` | Add 4 new helpers |
| `report/routes/property_routes.py` | Replace `_resolve_expense_amount_czk` with `validate_and_canonicalize`; extend route context |
| `templates/property.html` | Compose new partials in correct order |
| `templates/partials/base_styles.html` | Add `--brand` + `--dph` tokens (dark + light) |
| `templates/partials/property_intro.html` | Rewrite — page-header only (KPI/notify extracted) |
| `templates/partials/property_reservations.html` | Rewrite — 7-col table + filter pills + click-to-expand |
| `templates/partials/property_breakdown.html` | Rewrite — new column semantics, standalone card |
| `templates/partials/property_expenses.html` | Rewrite — calc-form + grouped-by-category table + pencil/trash |
| `templates/partials/property_override_history.html` | Restyle in new tokens |
| `templates/partials/property_scripts.html` | Rewrite — vanilla JS for filter/expand/calc-form/override-form |

---

## Task 1: Schema migration — add 3 columns to `expenses`

**Files:**
- Modify: `report/db.py` (extend `_run_migrations` near line 718)
- Test: `tests/test_db_migration_expenses_dph.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_migration_expenses_dph.py`:

```python
import sqlite3
from report.db import get_connection


def test_expenses_table_has_dph_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    assert "amount_net_czk" in cols
    assert "amount_dph_czk" in cols
    assert "vat_rate" in cols


def test_legacy_expenses_table_gets_columns_added(tmp_path):
    """Simulate an old DB that has expenses without the new columns."""
    db_path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(db_path)
    raw.execute("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_slug TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            date TEXT DEFAULT '',
            category_id INTEGER,
            description TEXT NOT NULL,
            amount_czk REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    raw.execute(
        "INSERT INTO expenses (property_slug, year, month, description, amount_czk, created_at) "
        "VALUES ('legacy-slug', 2026, 1, 'Old expense', 1000.0, '2026-01-01T00:00:00')"
    )
    raw.commit()
    raw.close()
    
    conn = get_connection(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    assert {"amount_net_czk", "amount_dph_czk", "vat_rate"}.issubset(cols)
    
    # Legacy row must remain accessible with NULL in new columns
    row = conn.execute("SELECT * FROM expenses WHERE property_slug='legacy-slug'").fetchone()
    assert row["amount_czk"] == 1000.0
    assert row["amount_net_czk"] is None
    assert row["amount_dph_czk"] is None
    assert row["vat_rate"] is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_db_migration_expenses_dph.py -v
```

Expected: both tests FAIL — assertion error on missing columns.

- [ ] **Step 3: Add migration calls in `_run_migrations`**

In `report/db.py`, locate `_run_migrations` (line ~718) and add three lines among the existing `_ensure_column` calls (after the `report_month_state` lines, before the index creation):

```python
    _ensure_column(conn, "expenses", "amount_net_czk", "amount_net_czk REAL")
    _ensure_column(conn, "expenses", "amount_dph_czk", "amount_dph_czk REAL")
    _ensure_column(conn, "expenses", "vat_rate", "vat_rate REAL")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db_migration_expenses_dph.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add report/db.py tests/test_db_migration_expenses_dph.py
git commit -m "feat(db): add amount_net_czk/amount_dph_czk/vat_rate columns to expenses"
```

---

## Task 2: Default categories seed

**Files:**
- Modify: `report/db.py` (extend `_run_migrations`)
- Test: `tests/test_db_migration_expenses_dph.py` (extend)

- [ ] **Step 1: Add tests to existing test file**

Append to `tests/test_db_migration_expenses_dph.py`:

```python
DEFAULT_CATEGORIES = ["Sub-nájem", "Energie", "Služby", "Opravy", "Pojištění", "Ostatní"]


def test_default_categories_seeded_on_empty_db(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = get_connection(db_path)
    names = [row["name"] for row in conn.execute("SELECT name FROM expense_categories ORDER BY id")]
    assert names == DEFAULT_CATEGORIES


def test_existing_categories_not_overwritten(tmp_path):
    db_path = str(tmp_path / "existing.db")
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE TABLE expense_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL)")
    raw.execute("INSERT INTO expense_categories (name) VALUES ('Custom Category')")
    raw.commit()
    raw.close()
    
    conn = get_connection(db_path)
    names = [row["name"] for row in conn.execute("SELECT name FROM expense_categories ORDER BY id")]
    # Existing single category preserved, defaults NOT seeded
    assert names == ["Custom Category"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db_migration_expenses_dph.py::test_default_categories_seeded_on_empty_db -v
```

Expected: FAIL — `expense_categories` is empty.

- [ ] **Step 3: Add seed helper and call**

In `report/db.py`, add this helper after `_ensure_column` (around line 716):

```python
DEFAULT_EXPENSE_CATEGORIES = ("Sub-nájem", "Energie", "Služby", "Opravy", "Pojištění", "Ostatní")


def _seed_default_expense_categories(conn: sqlite3.Connection) -> None:
    """Seed the standard 6 categories only if expense_categories is empty.
    
    Skips entirely if the user already has any categories — never overwrites.
    """
    n = conn.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]
    if n > 0:
        return
    for name in DEFAULT_EXPENSE_CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO expense_categories (name) VALUES (?)", (name,))
```

In `_run_migrations`, after the `_ensure_column` calls for expenses (added in Task 1), add:

```python
    _seed_default_expense_categories(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db_migration_expenses_dph.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add report/db.py tests/test_db_migration_expenses_dph.py
git commit -m "feat(db): seed 6 default expense categories on empty DB"
```

---

## Task 3: `expenses_validation.py` — full module + tests

**Files:**
- Create: `report/expenses_validation.py`
- Test: `tests/test_expenses_validation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_expenses_validation.py`:

```python
import pytest
from report.expenses_validation import (
    validate_and_canonicalize,
    ExpenseValidationError,
    EPSILON_CZK,
    ALLOWED_VAT_RATES,
)


class TestValidateAndCanonicalize:
    def test_gross_only_with_21_percent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=None, dph=None, vat_rate=0.21)
        assert gross == 121.0
        assert net == 100.0
        assert dph == 21.0
        assert rate == 0.21

    def test_gross_only_with_zero_rate(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=500.0, net=None, dph=None, vat_rate=0.0)
        assert (gross, net, dph, rate) == (500.0, 500.0, 0.0, 0.0)

    def test_gross_only_with_12_percent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=112.0, net=None, dph=None, vat_rate=0.12)
        assert gross == 112.0
        assert net == 100.0
        assert dph == 12.0
        assert rate == 0.12

    def test_all_three_consistent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=100.0, dph=21.0, vat_rate=0.21)
        assert (gross, net, dph, rate) == (121.0, 100.0, 21.0, 0.21)

    def test_net_within_epsilon_passes(self):
        # 100.01 vs canonical 100.00 — within 0.02 tolerance
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=100.01, dph=21.0, vat_rate=0.21)
        # Persisted values are canonical, not user input
        assert net == 100.0

    def test_net_outside_epsilon_raises(self):
        with pytest.raises(ExpenseValidationError, match="Bez DPH"):
            validate_and_canonicalize(gross=121.0, net=99.0, dph=21.0, vat_rate=0.21)

    def test_dph_outside_epsilon_raises(self):
        with pytest.raises(ExpenseValidationError, match="DPH"):
            validate_and_canonicalize(gross=121.0, net=100.0, dph=15.0, vat_rate=0.21)

    def test_zero_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=0.0, net=None, dph=None, vat_rate=0.21)

    def test_negative_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=-50.0, net=None, dph=None, vat_rate=0.21)

    def test_none_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=None, net=None, dph=None, vat_rate=0.21)

    def test_none_rate_raises(self):
        with pytest.raises(ExpenseValidationError, match="Sazba DPH"):
            validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=None)

    def test_invalid_rate_raises(self):
        with pytest.raises(ExpenseValidationError, match="Sazba DPH"):
            validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=0.15)

    def test_rounding_boundary_100_kc_at_21(self):
        # Real-world rounding edge case
        gross, net, dph, rate = validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=0.21)
        assert net == 82.64
        assert dph == 17.36

    def test_constants_exposed(self):
        assert EPSILON_CZK == 0.02
        assert ALLOWED_VAT_RATES == (0.0, 0.12, 0.21)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_expenses_validation.py -v
```

Expected: All 14 FAIL with `ModuleNotFoundError: No module named 'report.expenses_validation'`.

- [ ] **Step 3: Implement the module**

Create `report/expenses_validation.py`:

```python
"""Validates expense amount triplet (gross/net/dph) for consistency.

Used by /expenses/add and /expenses/{id}/edit endpoints. Persists canonical values
regardless of what the client sent — the calculator-strip form computes them on the
client for UX, but the server is the source of truth.
"""
from __future__ import annotations

EPSILON_CZK: float = 0.02
ALLOWED_VAT_RATES: tuple[float, ...] = (0.0, 0.12, 0.21)


class ExpenseValidationError(ValueError):
    """Raised when client-supplied gross/net/dph triplet is internally inconsistent."""


def validate_and_canonicalize(
    *,
    gross: float | None,
    net: float | None,
    dph: float | None,
    vat_rate: float | None,
) -> tuple[float, float, float, float]:
    """Returns ``(gross, canonical_net, canonical_dph, vat_rate)`` or raises.
    
    Canonical formulas:
        canonical_net = round(gross / (1 + vat_rate), 2)
        canonical_dph = round(gross - canonical_net, 2)
    
    If `net` or `dph` are provided and diverge from the canonical values by more
    than EPSILON_CZK, ExpenseValidationError is raised with a Czech-localized
    message suitable for displaying as a flash error.
    """
    if gross is None or gross <= 0:
        raise ExpenseValidationError("Celková částka (Celkem) musí být větší než 0 Kč.")
    if vat_rate is None or vat_rate not in ALLOWED_VAT_RATES:
        raise ExpenseValidationError(
            f"Sazba DPH musí být jedna z {ALLOWED_VAT_RATES} ({{0%, 12%, 21%}}); dostal: {vat_rate!r}."
        )

    canonical_net = round(gross / (1 + vat_rate), 2)
    canonical_dph = round(gross - canonical_net, 2)

    if net is not None and abs(net - canonical_net) > EPSILON_CZK:
        raise ExpenseValidationError(
            f"Bez DPH ({net:.2f} Kč) neodpovídá Celkem ({gross:.2f} Kč) při sazbě "
            f"{int(vat_rate * 100)}%. Očekáváno: {canonical_net:.2f} Kč."
        )
    if dph is not None and abs(dph - canonical_dph) > EPSILON_CZK:
        raise ExpenseValidationError(
            f"DPH ({dph:.2f} Kč) neodpovídá Celkem ({gross:.2f} Kč) při sazbě "
            f"{int(vat_rate * 100)}%. Očekáváno: {canonical_dph:.2f} Kč."
        )

    return gross, canonical_net, canonical_dph, vat_rate
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_expenses_validation.py -v
```

Expected: 14 PASSED.

- [ ] **Step 5: Commit**

```bash
git add report/expenses_validation.py tests/test_expenses_validation.py
git commit -m "feat(expenses): add validate_and_canonicalize for DPH triplet consistency"
```

---

## Task 4: `web_support.py` — 4 helpers + tests

**Files:**
- Modify: `report/web_support.py` (append at end of file)
- Test: `tests/test_web_support_property.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_support_property.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_web_support_property.py -v
```

Expected: All FAIL with `ImportError`.

- [ ] **Step 3: Implement the helpers**

Append to `report/web_support.py`:

```python
# ─────────────────────────────────────────────────────────────────────────
# Property-page helpers (Phase 1 of property-page-redesign)
# ─────────────────────────────────────────────────────────────────────────

_STATUS_MAP_BY_VS: dict[str, tuple[str, str, str]] = {
    # verification_status (Czech, encoded form) → (mock_status, badge_class, label)
    "MATCHED":          ("MATCHED",         "badge-ok",   "MATCHED"),
    "ROZDÍL":           ("ROZDIL",          "badge-warn", "ROZDÍL"),
    "CHYBÍ_V_CSV":      ("CHYBI_V_CSV",     "badge-err",  "CHYBÍ V CSV"),
    "CHYBÍ_V_HOSTIFY":  ("CHYBI_V_HOSTIFY", "badge-err",  "CHYBÍ V HOSTIFY"),
    "ZRUŠENO":          ("ZRUSENO",         "badge-mute", "ZRUŠENO"),
    "KE KONTROLE":      ("KE_KONTROLE",     "badge-mute", "KE KONTROLE"),
}


def attach_mock_status(rows: list[dict]) -> None:
    """In-place: adds ``_mock_status``, ``_mock_status_class``, ``_mock_status_label`` to each row.
    
    Maps production fields (``is_excluded``, ``is_payout_adjustment``, ``is_split_transaction``,
    ``adjustment_original_year/month``, ``display_year/month``, ``verification_status``)
    to the mock STATUS keys used by filter-pills and badge rendering.
    
    Precedence (first match wins):
        1. is_excluded → EXCLUDED
        2. is_payout_adjustment → ADJUSTMENT (child row)
        3. is_split_transaction → SPLIT
        4. display_year/month set → MOVED_OUT (Phase 3 only)
        5. adjustment_original_year/month set → MOVED_IN
        6. verification_status (table lookup)
        7. fallback → KE_KONTROLE
    """
    for r in rows:
        if r.get("is_excluded"):
            r["_mock_status"] = "EXCLUDED"
            r["_mock_status_class"] = "badge-mute"
            r["_mock_status_label"] = "VYLOUČENO"
            continue
        if r.get("is_payout_adjustment"):
            r["_mock_status"] = "ADJUSTMENT"
            r["_mock_status_class"] = "badge-brand"
            r["_mock_status_label"] = "ÚPRAVA"
            continue
        if r.get("is_split_transaction"):
            r["_mock_status"] = "SPLIT"
            r["_mock_status_class"] = "badge-brand"
            r["_mock_status_label"] = "SPLÁTKA"
            continue
        # MOVED_OUT (Phase 3 only — display_year/month override): rows that were moved
        # to a different month show in their *target* month as MOVED_IN, and disappear
        # from their original. We detect MOVED_OUT only when display_* override exists
        # AND points to a different month than the row's natural year/month.
        # In Phase 1 these fields are absent so the branch is dead; harmless.
        if r.get("display_year") and r.get("display_month"):
            r["_mock_status"] = "MOVED_OUT"
            r["_mock_status_class"] = "badge-info"
            r["_mock_status_label"] = "PŘESUN DO %02d" % int(r["display_month"])
            continue
        if r.get("adjustment_original_year") and r.get("adjustment_original_month"):
            r["_mock_status"] = "MOVED_IN"
            r["_mock_status_class"] = "badge-info"
            r["_mock_status_label"] = "PŘESUN Z %02d" % int(r["adjustment_original_month"])
            continue
        vs = r.get("verification_status") or ""
        triplet = _STATUS_MAP_BY_VS.get(vs)
        if triplet is None:
            r["_mock_status"], r["_mock_status_class"], r["_mock_status_label"] = (
                "KE_KONTROLE", "badge-mute", "KE KONTROLE",
            )
        else:
            r["_mock_status"], r["_mock_status_class"], r["_mock_status_label"] = triplet


def compute_status_counts(rows: list[dict]) -> dict:
    """Counts for filter-pills and card-meta on the property page.
    
    Returns dict with keys: all_rows, active, nights, adjustments, excluded, moved, problems.
    
    "active" = not excluded and not a payout-adjustment child row.
    "nights" = sum of nights over active rows.
    """
    active = [r for r in rows if not r.get("is_excluded") and not r.get("is_payout_adjustment")]
    return {
        "all_rows": len(rows),
        "active": len(active),
        "nights": sum(int(r.get("nights") or 0) for r in active),
        "adjustments": sum(1 for r in rows if r.get("is_payout_adjustment")),
        "excluded": sum(1 for r in rows if r.get("is_excluded")),
        "moved": sum(1 for r in rows if r.get("_mock_status") in ("MOVED_IN", "MOVED_OUT")),
        "problems": sum(1 for r in rows if r.get("_mock_status") in ("ROZDIL", "CHYBI_V_CSV", "CHYBI_V_HOSTIFY")),
    }


def group_expenses_by_category(expenses: list[dict]) -> dict[str, list[dict]]:
    """Group expenses by category_name. Preserves first-encountered order.
    
    None or missing category_name buckets into 'Ostatní'.
    """
    groups: dict[str, list[dict]] = {}
    for e in expenses:
        cat = e.get("category_name") or "Ostatní"
        groups.setdefault(cat, []).append(e)
    return groups


def get_adjacent_month(year: int, month: int, direction: str) -> tuple[int, int]:
    """Return (target_year, target_month) for the previous or next calendar month.
    
    Wraps year boundaries: (2026, 1) prev → (2025, 12); (2026, 12) next → (2027, 1).
    """
    if direction == "prev":
        if month == 1:
            return year - 1, 12
        return year, month - 1
    if direction == "next":
        if month == 12:
            return year + 1, 1
        return year, month + 1
    raise ValueError(f"direction must be 'prev' or 'next', got {direction!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_web_support_property.py -v
```

Expected: All PASSED.

- [ ] **Step 5: Commit**

```bash
git add report/web_support.py tests/test_web_support_property.py
git commit -m "feat(web_support): property-page helpers (mock-status, counts, expense-grouping, adjacent-month)"
```

---

## Task 5: `summary.py` — refactor + 6 new fields

**Files:**
- Modify: `report/summary.py`
- Test: `tests/test_summary_new_fields.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_summary_new_fields.py`:

```python
from report.summary import build_report_summary


def _rentero_config() -> dict:
    return {"client_type": "rentero", "rentero_commission": 0.15, "vat_rate": 0.21}


def _klient_config() -> dict:
    return {"client_type": "klient", "rentero_commission": 0.15, "vat_rate": 0.21}


def _row(payout=10000, accommodation=8000, dph_uklid=210):
    return {
        "payout_czk": payout,
        "cena_ubytovani_czk": accommodation,
        "city_tax_czk": 200,
        "priprava_pokoje_czk": 1000,
        "dph_uklid_balicky_czk": dph_uklid,
        "bank_status": "DORAZILO",
    }


def _expense(gross=1000, dph=210, rate=0.21):
    return {
        "amount_czk": gross,
        "amount_dph_czk": dph,
        "amount_net_czk": gross - dph,
        "vat_rate": rate,
        "category_name": "Energie",
    }


def test_vat_output_alias_for_rentero():
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["vat_output_czk"] == s["dph_prefakturace_klient_czk"]


def test_vat_input_sums_only_rated_expenses():
    expenses = [
        _expense(gross=1210, dph=210, rate=0.21),
        _expense(gross=560, dph=60, rate=0.12),
        _expense(gross=300, dph=0, rate=0.0),  # zero-rate excluded
        {"amount_czk": 100, "amount_dph_czk": None, "amount_net_czk": None, "vat_rate": None, "category_name": "Legacy"},  # NULL excluded
    ]
    s = build_report_summary([_row()], _rentero_config(), expenses=expenses)
    assert s["vat_input_czk"] == 270.0
    assert s["vat_input_count"] == 2  # only the two with rate > 0


def test_vat_balance_positive_means_owed():
    s = build_report_summary([_row(dph_uklid=500)], _rentero_config(), expenses=[_expense(gross=121, dph=21)])
    # vat_output = vat_rentero_fee + vat_room_prep_total; vat_input = 21
    assert s["vat_balance_czk"] == round(s["vat_output_czk"] - s["vat_input_czk"], 2)


def test_zisk_present_for_rentero(): 
    s = build_report_summary([_row()], _rentero_config(), expenses=[_expense()])
    assert s["zisk_czk"] is not None
    expected = round(s["gross_payout_czk"] - s["expenses_total_czk"] - s["vat_balance_czk"], 2)
    assert s["zisk_czk"] == expected


def test_zisk_none_for_klient():
    s = build_report_summary([_row()], _klient_config(), expenses=[_expense()])
    assert s["zisk_czk"] is None


def test_expenses_net_total_uses_amount_net_czk_when_present():
    expenses = [
        _expense(gross=121, dph=21),  # net=100
        {"amount_czk": 500, "amount_net_czk": None, "vat_rate": None, "category_name": "Legacy"},  # falls back to gross
    ]
    s = build_report_summary([_row()], _rentero_config(), expenses=expenses)
    assert s["expenses_net_total_czk"] == 600.0  # 100 + 500
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_summary_new_fields.py -v
```

Expected: All FAIL with `KeyError: 'vat_output_czk'` etc.

- [ ] **Step 3: Refactor `summary.py` and add new fields**

In `report/summary.py`, replace the trailing `return { … }` block. Locate the existing `return {` (line 80), and replace **everything from line 80 onwards** with:

```python
    result = {
        "gross_payout_czk": gross_payout_czk,
        "accommodation_income_czk": accommodation_income_czk,
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
    }

    # ── New fields (property-page redesign Phase 1) ──────────────────────
    # Alias: dph_prefakturace_klient_czk == vat_output_czk semantically.
    # Kept under both names for template clarity without renaming the field
    # used elsewhere (Excel, other consumers).
    result["vat_output_czk"] = result["dph_prefakturace_klient_czk"]

    # vat_input: sum of DPH from expenses that have a VAT rate set.
    # Legacy expenses with NULL vat_rate are excluded from the aggregate
    # so we don't lie about the deduction.
    rated_expenses = [
        e for e in expenses
        if (e.get("vat_rate") is not None) and (float(e.get("vat_rate") or 0) > 0)
    ]
    result["vat_input_czk"] = _r(sum(float(e.get("amount_dph_czk") or 0) for e in rated_expenses))
    result["vat_input_count"] = len(rated_expenses)
    result["vat_balance_czk"] = _r(result["vat_output_czk"] - result["vat_input_czk"])

    # Net total for the expense-table footer (same exclusion rule as above).
    # Falls back to amount_czk for legacy rows so the footer still adds up.
    result["expenses_net_total_czk"] = _r(sum(
        float(e.get("amount_net_czk") if e.get("amount_net_czk") is not None else (e.get("amount_czk") or 0))
        for e in expenses
    ))

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

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_summary_new_fields.py -v
pytest tests/test_engine.py tests/test_calculator.py -v 2>&1 | tail -10
```

Expected: New tests pass; existing engine/calculator tests still pass.

- [ ] **Step 5: Commit**

```bash
git add report/summary.py tests/test_summary_new_fields.py
git commit -m "feat(summary): add vat_output/input/balance/zisk fields for property-page redesign"
```

---

## Task 6: `db_admin.py` — extend add/update to persist DPH columns

**Files:**
- Modify: `report/db_admin.py:329-361` (`add_expense`, `update_expense`)
- Test: `tests/test_db_admin_expenses_dph.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_admin_expenses_dph.py`:

```python
from report.db import get_connection
from report.db_admin import add_expense, update_expense, get_expense, get_expense_categories


def _setup(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    # Property must exist for _assert_report_month_mutable; cheap stub:
    conn.execute("""INSERT OR IGNORE INTO report_objects (slug, display_name, listing_nickname, channels_json, active, client_type, updated_at)
                    VALUES ('p1', 'P1', 'P1', '{}', 1, 'rentero', '2026-01-01')""")
    conn.commit()
    return conn


def test_add_expense_persists_dph_columns(tmp_path):
    conn = _setup(tmp_path)
    cats = get_expense_categories(conn)
    cat_id = cats[0]["id"]
    
    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Test",
        "amount_czk": 121.0, "amount_net_czk": 100.0, "amount_dph_czk": 21.0, "vat_rate": 0.21,
    })
    
    row = get_expense(conn, expense_id)
    assert row["amount_czk"] == 121.0
    assert row["amount_net_czk"] == 100.0
    assert row["amount_dph_czk"] == 21.0
    assert row["vat_rate"] == 0.21


def test_update_expense_overwrites_dph_columns(tmp_path):
    conn = _setup(tmp_path)
    cat_id = get_expense_categories(conn)[0]["id"]
    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Test",
        "amount_czk": 121.0, "amount_net_czk": 100.0, "amount_dph_czk": 21.0, "vat_rate": 0.21,
    })
    update_expense(conn, expense_id, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-16", "category_id": cat_id, "description": "Updated",
        "amount_czk": 1120.0, "amount_net_czk": 1000.0, "amount_dph_czk": 120.0, "vat_rate": 0.12,
    })
    row = get_expense(conn, expense_id)
    assert row["vat_rate"] == 0.12
    assert row["amount_net_czk"] == 1000.0


def test_legacy_add_without_dph_fields_keeps_nulls(tmp_path):
    """Backward-compat: callers that only pass amount_czk still work; new fields are NULL."""
    conn = _setup(tmp_path)
    cat_id = get_expense_categories(conn)[0]["id"]
    expense_id = add_expense(conn, {
        "property_slug": "p1", "year": 2026, "month": 4,
        "date": "2026-04-15", "category_id": cat_id, "description": "Legacy",
        "amount_czk": 500.0,
        # No DPH fields supplied
    })
    row = get_expense(conn, expense_id)
    assert row["amount_czk"] == 500.0
    assert row["amount_net_czk"] is None
    assert row["amount_dph_czk"] is None
    assert row["vat_rate"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db_admin_expenses_dph.py -v
```

Expected: First two FAIL — INSERT only writes `amount_czk`. Third may pass (fields default to NULL via SQLite).

- [ ] **Step 3: Modify `add_expense`**

In `report/db_admin.py:329-343`, replace the `add_expense` function with:

```python
def add_expense(conn: sqlite3.Connection, data: dict) -> int:
    _assert_report_month_mutable(
        conn,
        str(data["property_slug"]),
        int(data["year"]),
        int(data["month"]),
    )
    payload = {
        "property_slug": data["property_slug"],
        "year": data["year"],
        "month": data["month"],
        "date": data.get("date"),
        "category_id": data.get("category_id"),
        "description": data["description"],
        "amount_czk": data["amount_czk"],
        "amount_net_czk": data.get("amount_net_czk"),
        "amount_dph_czk": data.get("amount_dph_czk"),
        "vat_rate": data.get("vat_rate"),
        "created_at": _now(),
    }
    cur = conn.execute(
        """INSERT INTO expenses
           (property_slug, year, month, date, category_id, description,
            amount_czk, amount_net_czk, amount_dph_czk, vat_rate, created_at)
           VALUES (:property_slug, :year, :month, :date, :category_id, :description,
                   :amount_czk, :amount_net_czk, :amount_dph_czk, :vat_rate, :created_at)""",
        payload,
    )
    conn.commit()
    return int(cur.lastrowid)
```

- [ ] **Step 4: Modify `update_expense`**

In `report/db_admin.py:346-361`, replace `update_expense` with:

```python
def update_expense(conn: sqlite3.Connection, expense_id: int, data: dict) -> None:
    _assert_report_month_mutable(
        conn,
        str(data["property_slug"]),
        int(data["year"]),
        int(data["month"]),
    )
    payload = {
        "id": expense_id,
        "property_slug": data["property_slug"],
        "year": data["year"],
        "month": data["month"],
        "date": data.get("date"),
        "category_id": data.get("category_id"),
        "description": data["description"],
        "amount_czk": data["amount_czk"],
        "amount_net_czk": data.get("amount_net_czk"),
        "amount_dph_czk": data.get("amount_dph_czk"),
        "vat_rate": data.get("vat_rate"),
    }
    conn.execute(
        """UPDATE expenses SET
             property_slug=:property_slug, year=:year, month=:month,
             date=:date, category_id=:category_id, description=:description,
             amount_czk=:amount_czk, amount_net_czk=:amount_net_czk,
             amount_dph_czk=:amount_dph_czk, vat_rate=:vat_rate
           WHERE id=:id""",
        payload,
    )
    conn.commit()
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_db_admin_expenses_dph.py tests/test_expense_inputs.py -v
```

Expected: New tests PASS; existing `test_expense_inputs.py` still passes.

- [ ] **Step 6: Commit**

```bash
git add report/db_admin.py tests/test_db_admin_expenses_dph.py
git commit -m "feat(db_admin): persist amount_net_czk/amount_dph_czk/vat_rate on expense add+update"
```

---

## Task 7: `property_routes.py` — wire validation + extend context

**Files:**
- Modify: `report/routes/property_routes.py:48` (GET property route — context expansion)
- Modify: `report/routes/property_routes.py:182-268` (`expense_add`, `expense_edit`)

- [ ] **Step 1: Replace `_resolve_expense_amount_czk` calls in both routes**

In `report/routes/property_routes.py:182-223` (`expense_add`), replace the body up to the `add_expense` call with:

```python
    @app.post("/expenses/add")
    async def expense_add(
        request: Request,
        property_slug: str = Form(...),
        year: int = Form(...),
        month: int = Form(...),
        date_str: str = Form(""),
        category_id: str = Form(""),
        description: str = Form(...),
        amount_czk: str = Form(""),
        amount_net_czk: str = Form(""),
        amount_dph_czk: str = Form(""),
        vat_rate: str = Form(""),
        _csrf=Depends(require_csrf),
        _=Depends(require_auth),
        _w=Depends(require_write_access),
        conn=Depends(get_db),
        config=Depends(get_config),
    ):
        from report.expenses_validation import validate_and_canonicalize, ExpenseValidationError
        try:
            gross, net, dph, rate = validate_and_canonicalize(
                gross=_parse_decimal(amount_czk),
                net=_parse_decimal(amount_net_czk) if amount_net_czk.strip() else None,
                dph=_parse_decimal(amount_dph_czk) if amount_dph_czk.strip() else None,
                vat_rate=_parse_decimal(vat_rate),
            )
        except ExpenseValidationError as e:
            state["_set_flash"](request, level="error", message=str(e))
            referer = request.headers.get("referer", "/expenses")
            return RedirectResponse(referer, status_code=303)

        state["_ensure_month_open"](conn, property_slug, year, month)
        state["add_expense"](
            conn,
            {
                "property_slug": property_slug,
                "year": year,
                "month": month,
                "date": date_str or None,
                "category_id": int(category_id) if category_id else None,
                "description": description,
                "amount_czk": gross,
                "amount_net_czk": net,
                "amount_dph_czk": dph,
                "vat_rate": rate,
            },
        )
        try:
            state["generate_report_in_process"](conn, property_slug, year, month, config)
        except Exception:
            state["mark_report_month_stale"](conn, property_slug, year, month)
        referer = request.headers.get("referer", "/expenses")
        return RedirectResponse(referer, status_code=303)
```

Same shape change for `expense_edit` (lines 225-268) — copy the validation block, pass the canonical 4-tuple to `update_expense`. Add `amount_dph_czk: str = Form("")` to its signature.

- [ ] **Step 2: Add `_parse_decimal` helper near the top of `property_routes.py`**

If not already present, add near the top of the `register` function:

```python
    def _parse_decimal(raw: str) -> float | None:
        s = (raw or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
```

(Or reuse existing `_resolve_expense_amount_czk`'s parser if it lives in scope; in that case skip this step.)

- [ ] **Step 3: Extend GET /property route context**

In `report/routes/property_routes.py` around the existing `_render_property_template` call (line ~48 area), before invoking `_render_property_template`, add:

```python
    from report.web_support import (
        attach_mock_status,
        compute_status_counts,
        group_expenses_by_category,
        get_adjacent_month,
    )

    attach_mock_status(rows)
    counts = compute_status_counts(rows)
    expenses_by_cat = group_expenses_by_category(expenses)
    prev_year, prev_month = get_adjacent_month(year, month, "prev")
    next_year, next_month = get_adjacent_month(year, month, "next")
    totals = {
        "payout": sum(float(r.get("payout_czk") or 0) for r in rows if not r.get("is_excluded")),
        "ubyt":   sum(float(r.get("cena_ubytovani_czk") or 0) for r in rows if not r.get("is_excluded")),
    }
    cat_dot_class = {
        "Sub-nájem": "exp-dot-rent",
        "Energie":   "exp-dot-energy",
        "Služby":    "exp-dot-svc",
        "Opravy":    "exp-dot-fix",
        "Pojištění": "exp-dot-ins",
    }
```

Then pass these into the template-context dict that `_render_property_template` constructs. Look for the existing `context = {…}` (or similar) inside `_render_property_template` in `web_support.py` and add:

```python
    "counts": counts,
    "expenses_by_cat": expenses_by_cat,
    "prev_month_target": {"year": prev_year, "month": prev_month},
    "next_month_target": {"year": next_year, "month": next_month},
    "totals": totals,
    "cat_dot_class": cat_dot_class,
```

Confirm `client` (with `platce_dph` field) is already in context. If not, add a `client = state["get_client"](conn, slug)` call and include it.

- [ ] **Step 4: Run integration test against the route**

```bash
pytest tests/test_csrf_enforcement.py -v 2>&1 | tail -20
pytest tests/ -k expense -v 2>&1 | tail -20
```

Expected: existing tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add report/routes/property_routes.py
git commit -m "feat(property_routes): wire validate_and_canonicalize + new template context vars"
```

---

## Task 8: New tokens in `base_styles.html`

**Files:**
- Modify: `templates/partials/base_styles.html`

- [ ] **Step 1: Add tokens to `:root`**

Find the `:root {` block and append (just before the closing `}`):

```css
  /* ── property-page redesign: brand (Rentero) + DPH (teal) ── */
  --brand:        oklch(0.66 0.18 285);
  --brand-soft:   oklch(0.66 0.18 285 / 0.12);
  --brand-line:   oklch(0.66 0.18 285 / 0.35);
  --brand-text:   oklch(0.80 0.14 285);
  --dph:          oklch(0.72 0.12 185);
  --dph-soft:     oklch(0.72 0.12 185 / 0.10);
  --dph-line:     oklch(0.72 0.12 185 / 0.30);
  --dph-text:     oklch(0.78 0.10 185);
  --glass-hi:     inset 0 1px 0 oklch(1 0 0 / 0.04);
```

- [ ] **Step 2: Add light-theme overrides**

Find the `[data-theme="light"] {` block and append (just before its closing `}`):

```css
  --brand:        oklch(0.55 0.18 285);
  --brand-text:   oklch(0.45 0.18 285);
  --dph:          oklch(0.55 0.12 185);
  --dph-text:     oklch(0.42 0.13 185);
  --glass-hi:     inset 0 1px 0 oklch(1 0 0 / 0.6);
```

- [ ] **Step 3: Smoke test the existing site renders**

Start the dev server: `bash start_web.sh` (or however the dev server is run locally). Open `/` in browser. Confirm page loads, no CSS errors in console.

- [ ] **Step 4: Commit**

```bash
git add templates/partials/base_styles.html
git commit -m "feat(styles): add --brand and --dph design tokens"
```

---

## Task 9: `property_styles_property.html` — full property-page CSS

**Files:**
- Create: `templates/partials/property_styles_property.html`

This task ports the entire property-page CSS from the reference mock. The CSS lives inline in a `<style>` block (Jinja partial), included from `property.html`.

- [ ] **Step 1: Read the reference CSS**

Source: `/Users/nikitashlykov/Downloads/315n/property/styles.css` (~1180 lines).

- [ ] **Step 2: Create the partial**

Create `templates/partials/property_styles_property.html`:

```html
<style>
/* ═══════════════════════════════════════════════════════════
   Property page (DPH redesign)
   Tokens (--brand, --dph, --bg-*, --text-*, --color-*) live in base_styles.html.
   This file holds property-page-specific component styles only.
   ═══════════════════════════════════════════════════════════ */

/* ───────── page shell ───────── */
.page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 24px 32px 96px;
  position: relative;  /* sits above aurora ::before */
  z-index: 1;
}

/* ───────── page header ───────── */
.ph-crumbs {
  display: flex; align-items: center; gap: 6px;
  font-family: var(--font-mono); font-size: 11px;
  color: var(--text-300); letter-spacing: .02em;
}
.ph-crumbs .sep { opacity: .4; }
.ph-crumbs a:hover { color: var(--text-200); }

.ph-title-row {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 24px; margin-top: 8px;
}
.ph-title {
  font-size: 28px; font-weight: 700; letter-spacing: -0.025em;
  color: var(--text-100);
}
.ph-title-meta {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 8px;
}
.ph-actions {
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  padding-top: 6px;
  flex-wrap: wrap; justify-content: flex-end;
}

/* PORT REMAINDER OF styles.css INTO THIS BLOCK.
   The full content to copy is in /Users/nikitashlykov/Downloads/315n/property/styles.css
   from line 148 onwards (after :root and [data-theme="light"] which we already
   put in base_styles.html in Task 8).
   
   Sections to include (in order):
     - .btn / .btn-* (lines 204-240)
     - .badge (lines 242-283) — IMPORTANT: scope these under .page so they don't
       collide with the global .badge in base_styles_components.html. Wrap the
       entire .badge { ... } and .badge-* { ... } blocks like:
         .page .badge { ... }
         .page .badge-dot { ... }
         .page .badge-ok { ... }
         etc.
     - .kpi-row, .kpi, .kpi-* (lines 285-365)
     - .card, .card-h, .card-title, .card-meta, .card-caret (lines 367-402)
     - .t (table base) and .num (lines 404-451)
     - .rs-filters, .rs-filter (lines 456-484)
     - .ch-logo (lines 485-491)
     - .rs-table, .rs-idx, .rs-guest, .rs-date-stack, .rs-payout-stack,
       .rs-stav-stack, .rs-stav-bank, .rs-bank, .rs-row, .rs-row-open/muted/child,
       .rs-caret, .rs-ex (lines 493-650)
     - .rs-ex-groups, .rs-ex-group, .rex-k, .rex-v, .rs-ex-actions, .rex-btn (lines 651-731)
     - .bd-table semantics (lines 732-783)
     - .dph-box, .dph-col, .dph-k, .dph-v, .dph-hint, .dph-op (lines 785-826)
     - .exp-cat-row, .exp-cat-inner, .exp-cat-name/dot/count/sum, .exp-dot-* (lines 828-873)
     - .vat-21/12/0, .exp-edit, .exp-delete (lines 885-908)
     - .btn-add, .btn-add-solid, .btn-link (lines 910-953)
     - .ae-form, .ae-h, .ae-row, .ae-field, .ae-k, .ae-in (lines 955-1031)
     - .ae-amount-wrap (lines 1033-1045)
     - .ae-seg, .ae-seg-mini (lines 1047-1066, 1131-1156)
     - .ae-calc, .ae-cell, .ae-cell-input, .ae-calc-op (lines 1068-1128)
     - .ae-actions-row (lines 1158-1162)
     - .hr-soft, .dim, .mono utilities (lines 1164-1171)
   
   DO NOT include:
     - lines 1-7 (file comment)
     - lines 8-112 (token blocks — already in base_styles.html)
     - lines 114-128 (base * { box-sizing... }) — global, already covered
     - lines 130-146 (.theme-switch — we use the existing sidebar toggle)
     - lines 1173-1183 (@media) — those go in Task 23 (mobile)
   
   Adaptations required:
     1. Every selector starting with `.badge` becomes `.page .badge` (scoping).
     2. Existing rule `.kpi { background: var(--bg-2); ... }` etc. — keep as-is;
        the .page-prefixed class names don't collide with anything else.
     3. Remove the .ae-calc rule's `border-radius: var(--radius);` line (typo
        in mock — should be var(--radius-md)). Replace with:
            border-radius: var(--radius-md);
*/

/* paste full ported CSS here per the instructions above */

</style>
```

- [ ] **Step 3: Smoke test**

Restart dev server. Open `/property/<known-slug>/2026/4`. Page should still render — no styling yet (template untouched), but no console errors from new CSS.

- [ ] **Step 4: Commit**

```bash
git add templates/partials/property_styles_property.html
git commit -m "feat(styles): add property-page-specific CSS partial (ports mock styles.css)"
```

---

## Task 10: `property_intro.html` — page-header rewrite

**Files:**
- Rewrite: `templates/partials/property_intro.html`

The new intro is **just** the page-header (breadcrumb + title + 3 badges + 3 action buttons). KPI/notify/generation extracted to separate partials in subsequent tasks.

- [ ] **Step 1: Replace the file content**

Replace the entire `templates/partials/property_intro.html` with:

```jinja
{# Page header: breadcrumb + title + status badges + action buttons.
   KPI moved to property_kpi.html. Notifications moved to property_notify_stack.html.
   Generation spinner moved to property_generation_progress.html. #}

<div class="ph-crumbs">
  <a href="/" data-month-aware="1">Přehled</a>
  <span class="sep">/</span>
  <span>{{ prop.display_name or prop.listing_nickname }}</span>
</div>

<div class="ph-title-row">
  <div>
    <h1 class="ph-title">{{ prop.display_name or prop.listing_nickname }}</h1>
    <div class="ph-title-meta">
      {# 1. Lock-state badge #}
      {% if month_state.status == 'LOCKED' %}
        <span class="badge badge-warn"><span class="badge-dot"></span>Uzamčeno</span>
      {% else %}
        <span class="badge badge-ok"><span class="badge-dot"></span>Otevřeno</span>
      {% endif %}

      {# 2. Client-type badge #}
      {% if prop.client_type == 'rentero' %}
        <span class="badge badge-brand">RENTERO</span>
      {% elif prop.client_type == 'klient' %}
        <span class="badge badge-info">KLIENT</span>
      {% else %}
        <span class="badge badge-mute">Z KLIENTA</span>
      {% endif %}

      {# 3. VAT-payer badge — only shown if the client is registered for VAT #}
      {% if client and client.platce_dph %}
        <span class="badge badge-dph">Plátce DPH</span>
      {% endif %}
    </div>
  </div>

  <div class="ph-actions">
    <a href="/audit?slug={{ slug }}&year={{ year }}&month={{ month }}" class="btn btn-ghost btn-sm">Změny</a>
    <a href="/property/{{ slug }}/{{ year }}/{{ month }}/evidence-hostu" class="btn btn-ghost btn-sm">Checkin report</a>
    {% if request.session.get('role') != 'client' %}
      {% if month_state.status == 'LOCKED' %}
        <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/unlock" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button class="btn btn-ghost btn-sm" type="submit">Odemknout</button>
        </form>
      {% else %}
        <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/lock" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button class="btn btn-ghost btn-sm" type="submit">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            Uzamknout
          </button>
        </form>
      {% endif %}
    {% endif %}
  </div>
</div>
```

- [ ] **Step 2: Smoke test**

Reload `/property/<slug>/2026/4`. The page will be visually broken (old intro had KPI inline; new one only renders the header). This is expected — we'll wire the rest in subsequent tasks. Confirm at minimum:
- No template errors (the page returns 200, even if visually incomplete)
- Breadcrumb, title, badges and three buttons render

- [ ] **Step 3: Commit**

```bash
git add templates/partials/property_intro.html
git commit -m "feat(intro): page-header only; KPI/notify/generation extracted"
```

---

## Task 11: `property_kpi.html` — new partial, 4 cards with client_type variants

**Files:**
- Create: `templates/partials/property_kpi.html`

- [ ] **Step 1: Create the partial**

```jinja
{# 4 KPI cards. Layout is identical for all client_types; first card's content varies:
   - rentero  → "Zisk"  (gross − expenses − vat_balance)
   - klient   → "Zisk"  (client_payout_after_expenses_czk)
   - z_klient → "Zisk"  (client_payout_after_expenses_czk)
   "Zisk" is the unified label; the meaning shifts with role. #}

<div class="kpi-row">

  {# ── KPI 1: Zisk ────────────────────────────────────────── #}
  <div class="kpi kpi-ok kpi-zisk">
    <div class="kpi-label">Zisk</div>
    {% if prop.client_type == 'rentero' %}
      <div class="kpi-value ok" data-copy="{{ '%.2f'|format(summary.zisk_czk or 0) }}">
        {{ fmt_czk(summary.zisk_czk or 0) }}
      </div>
      <div class="kpi-sub">
        <span class="pair">
          <span>Hrubý zisk</span>
          <span class="v">{{ fmt_czk((summary.gross_payout_czk or 0) - (summary.expenses_total_czk or 0)) }}</span>
        </span>
        {% if client and client.platce_dph %}
          <span class="sep">·</span>
          <span class="pair" style="color:var(--dph-text)">
            <span>DPH&nbsp;=</span>
            <span class="v">{% if (summary.vat_balance_czk or 0) < 0 %}+{% else %}−{% endif %}{{ fmt_czk((summary.vat_balance_czk or 0)|abs) }}</span>
          </span>
        {% endif %}
      </div>
    {% else %}
      <div class="kpi-value ok" data-copy="{{ '%.2f'|format(summary.client_payout_after_expenses_czk or 0) }}">
        {{ fmt_czk(summary.client_payout_after_expenses_czk or 0) }}
      </div>
      <div class="kpi-sub">
        <span class="pair">
          <span>Před výdaji</span>
          <span class="v">{{ fmt_czk(summary.client_payout_before_expenses_czk or 0) }}</span>
        </span>
        {% if client and client.bank_account %}
          <span class="sep">·</span>
          <span class="pair" title="{{ client.bank_account }}">
            <span class="v" style="overflow:hidden;text-overflow:ellipsis;max-width:160px;display:inline-block;vertical-align:bottom;">{{ client.bank_account }}</span>
          </span>
        {% endif %}
      </div>
    {% endif %}
  </div>

  {# ── KPI 2: Vyplaceno platformami ────────────────────────── #}
  <div class="kpi kpi-blue">
    <div class="kpi-label">Vyplaceno platformami</div>
    <div class="kpi-value" data-copy="{{ '%.2f'|format(summary.gross_payout_czk or 0) }}">
      {{ fmt_czk(summary.gross_payout_czk or 0) }}
    </div>
    <div class="kpi-sub">
      <span class="pair">
        <span>Cena ubytování</span>
        <span class="v">{{ fmt_czk(summary.accommodation_income_czk or 0) }}</span>
      </span>
    </div>
  </div>

  {# ── KPI 3: Rentero fee ────────────────────────────────── #}
  <div class="kpi kpi-err">
    <div class="kpi-label">
      {% if prop.client_type == 'z_klient' %}
        Odměna Rentero (3 %)
      {% else %}
        Rentero fee ({{ '%.0f'|format((summary.rentero_commission_rate or 0) * 100) }} %)
      {% endif %}
    </div>
    <div class="kpi-value neg" data-copy="{{ '%.2f'|format((summary.rentero_fee_czk or 0) + (summary.vat_rentero_fee_czk or 0)) }}">
      −{{ fmt_czk((summary.rentero_fee_czk or 0) + (summary.vat_rentero_fee_czk or 0)) }}
    </div>
    <div class="kpi-sub">
      <span class="pair"><span>Net</span><span class="v">−{{ fmt_czk(summary.rentero_fee_czk or 0) }}</span></span>
      {% if (summary.vat_rentero_fee_czk or 0) > 0 %}
        <span class="sep">·</span>
        <span class="pair" style="color:var(--dph-text)">
          <span>DPH</span>
          <span class="v">−{{ fmt_czk(summary.vat_rentero_fee_czk) }}</span>
        </span>
      {% endif %}
    </div>
  </div>

  {# ── KPI 4: Výdaje ────────────────────────────────────── #}
  <div class="kpi {% if summary.expenses_total_czk %}kpi-warn{% else %}kpi-mute{% endif %}">
    <div class="kpi-label">Výdaje</div>
    <div class="kpi-value {% if summary.expenses_total_czk %}warn{% endif %}"
         {% if summary.expenses_total_czk %}data-copy="{{ '%.2f'|format(summary.expenses_total_czk) }}"{% endif %}>
      {% if summary.expenses_total_czk %}−{{ fmt_czk(summary.expenses_total_czk) }}{% else %}—{% endif %}
    </div>
    <div class="kpi-sub">
      {% if expenses %}
        <span class="pair"><span class="v">{{ expenses|length }}</span><span>položek</span></span>
        {% if client and client.platce_dph and (summary.vat_input_czk or 0) > 0 %}
          <span class="sep">·</span>
          <span class="pair" style="color:var(--dph-text)">
            <span>DPH</span>
            <span class="v">+{{ fmt_czk(summary.vat_input_czk) }}</span>
          </span>
        {% endif %}
      {% endif %}
    </div>
  </div>

</div>
```

- [ ] **Step 2: Smoke test (deferred)**

Cannot smoke test until `property.html` includes this partial (Task 22). Skip for now.

- [ ] **Step 3: Commit**

```bash
git add templates/partials/property_kpi.html
git commit -m "feat(kpi): new 4-card KPI partial with client_type variants"
```

---

## Task 12: `property_notify_stack.html` — new partial

**Files:**
- Create: `templates/partials/property_notify_stack.html`

- [ ] **Step 1: Create the partial**

```jinja
{# Stack of notify-strips above the KPI row. Each strip is conditional on its
   driving context var. Order: highest-severity first.
   The .notify, .notify-error, .notify-info, .notify-success, .notify-warn classes
   come from base_styles_components.html and are reused as-is. #}

{# 1. Generation FAILED #}
{% if generation_job and generation_job.status == 'FAILED' %}
  <div class="notify notify-error" style="margin-top:16px">
    <strong>Generace selhala.</strong>
    {% if generation_job.error_detail %}
      <details style="margin-top:6px"><summary style="cursor:pointer">Detail</summary><pre style="white-space:pre-wrap;font-size:11px;margin-top:6px">{{ generation_job.error_detail }}</pre></details>
    {% endif %}
  </div>
{% endif %}

{# 2. Locked-month warning #}
{% if month_state.status == 'LOCKED' %}
  <div class="notify notify-warn" style="margin-top:12px">
    <strong>Měsíc je uzamčen.</strong>
    Změny v rezervacích a výdajích nejsou možné, dokud nebude odemčen.
  </div>
{% endif %}

{# 3. New data available #}
{% if month_state.has_new_data_since_generation %}
  <div class="notify notify-warn" style="margin-top:12px">
    <strong>Nová data jsou k dispozici.</strong>
    Doporučujeme znovu vygenerovat report.
  </div>
{% endif %}

{# 4. Month notifications (change-log lines from previous regenerations) #}
{% if month_notifications %}
  {% for n in month_notifications %}
    <div class="notify notify-info" style="margin-top:12px">
      <strong>Změny:</strong> {{ n.message }}
      {% if n.created_at %}<span class="dim" style="font-size:11px">· {{ n.created_at }}</span>{% endif %}
    </div>
  {% endfor %}
{% endif %}

{# 5. Flash (transient, always last so user sees it on top after scroll-to-top) #}
{% if flash %}
  <div class="notify notify-{{ flash.level }}" style="margin-top:12px" data-flash="1">
    {{ flash.message }}
    {% if flash.detail %}
      <details style="margin-top:6px"><summary style="cursor:pointer">Detail</summary><div style="margin-top:6px;font-size:12px">{{ flash.detail }}</div></details>
    {% endif %}
  </div>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_notify_stack.html
git commit -m "feat(notify): new partial for stacked notification strips above KPI"
```

---

## Task 13: `property_generation_progress.html` — new partial

**Files:**
- Create: `templates/partials/property_generation_progress.html`

- [ ] **Step 1: Create the partial**

```jinja
{# Generation spinner — shown only while a generation job is PENDING or RUNNING.
   The auto-reload JS in property_scripts.html polls and refreshes the page
   when status changes. #}

{% if generation_job and generation_job.status in ('PENDING', 'RUNNING') %}
  <div class="notify notify-info" style="margin-top:12px;display:flex;align-items:center;gap:12px"
       data-generation-status="{{ generation_job.status }}"
       data-generation-id="{{ generation_job.id or '' }}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" style="animation:spin 1s linear infinite">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    <span>
      {% if generation_job.status == 'PENDING' %}
        Generace ve frontě…
      {% else %}
        Probíhá generace reportu…
      {% endif %}
      <span class="dim" style="font-size:11px;margin-left:8px">stránka se obnoví automaticky</span>
    </span>
  </div>
  <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_generation_progress.html
git commit -m "feat(generation): extracted generation-progress spinner partial"
```

---

## Task 14: `property_reservation_detail.html` — expanded-row body

**Files:**
- Create: `templates/partials/property_reservation_detail.html`

This partial renders the body of an expanded reservation row: 3 groups + action row + inline override-form. It's included from inside `property_reservations.html`'s loop.

- [ ] **Step 1: Create the partial**

```jinja
{# Expanded reservation row body. Receives `r` (the row) via include's context.
   Structure:
     - rs-ex-groups: 3 sémantické skupiny (Finance / Identifikátor / Stav)
     - rs-ex-actions: 5 buttons (← MM/YYYY) (MM/YYYY →) Vyloučit Úprava │ Otevřít panel →
     - rs-ov-form: hidden inline override-form (toggled by Úprava button) #}

<div class="rs-ex-inner">
  <div class="rs-ex-groups">

    {# ── Group 1 — Finance ── #}
    <div class="rs-ex-group">
      <div>
        <div class="rex-k">Provize platformy</div>
        <div class="rex-v neg">{% if r.provize_czk %}−{{ fmt_czk(r.provize_czk) }}{% else %}—{% endif %}</div>
      </div>
      <div>
        <div class="rex-k">City tax</div>
        <div class="rex-v">{% if r.city_tax_czk %}{{ fmt_czk(r.city_tax_czk) }}{% else %}—{% endif %}</div>
      </div>
      <div>
        <div class="rex-k">Úklid + balíček</div>
        <div class="rex-v">{% set total_prep = (r.uklid_czk or 0) + (r.balicky_czk or 0) %}{% if total_prep > 0 %}{{ fmt_czk(total_prep) }}{% else %}—{% endif %}</div>
      </div>
    </div>

    {# ── Group 2 — Identifikátor ── #}
    <div class="rs-ex-group cols-2">
      <div>
        <div class="rex-k">Kód rezervace</div>
        <div class="rex-v code">{{ r.confirmation_code }}</div>
      </div>
      <div>
        <div class="rex-k">Kurz EUR/CZK</div>
        <div class="rex-v {% if not r.kurz %}mute{% endif %}">{{ r.kurz or '—' }}</div>
      </div>
    </div>

    {# ── Group 3 — Stav ── #}
    <div class="rs-ex-group cols-2">
      {% if r.bank_status == 'DORAZILO' and r.bank_datum %}
        <div>
          <div class="rex-k ok">Dorazilo na účet</div>
          <div class="rex-v ok">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            {{ r.bank_datum }}
          </div>
        </div>
      {% elif not r.is_excluded and not r.is_payout_adjustment and r._mock_status not in ('MOVED_IN', 'MOVED_OUT') %}
        <div>
          <div class="rex-k warn">Banka</div>
          <div class="rex-v warn">Nedorazilo</div>
        </div>
      {% endif %}

      {% if r._mock_status == 'ROZDIL' and r.bank_diff_czk %}
        <div>
          <div class="rex-k warn">Rozdíl vs. CSV</div>
          <div class="rex-v warn">{{ fmt_czk(r.bank_diff_czk) }}</div>
        </div>
      {% endif %}

      {% if r._mock_status == 'CHYBI_V_CSV' %}
        <div>
          <div class="rex-k err">CSV payout</div>
          <div class="rex-v">Čeká na platbu</div>
        </div>
      {% endif %}

      {% if r._mock_status == 'MOVED_IN' and r.adjustment_original_month %}
        <div>
          <div class="rex-k info">Původní měsíc</div>
          <div class="rex-v">{{ '%02d/%d'|format(r.adjustment_original_month, r.adjustment_original_year) }}</div>
        </div>
      {% endif %}

      {% if r.comment %}
        <div style="grid-column:span 2">
          <div class="rex-k">Poznámka</div>
          <div class="rex-v mute">{{ r.comment }}</div>
        </div>
      {% endif %}
    </div>
  </div>

  {# ── Action row: 5 buttons (Move-prev, Move-next, Vyloučit, Úprava | Otevřít panel) ── #}
  <div class="rs-ex-actions">
    {% set prev_label = '%02d/%d'|format(prev_month_target.month, prev_month_target.year) %}
    {% set next_label = '%02d/%d'|format(next_month_target.month, next_month_target.year) %}

    <button type="button" class="rex-btn" disabled
            data-action="move" data-direction="prev"
            title="Připravujeme">← {{ prev_label }}</button>
    <button type="button" class="rex-btn" disabled
            data-action="move" data-direction="next"
            title="Připravujeme">{{ next_label }} →</button>

    <button type="button" class="rex-btn" disabled
            data-action="exclude"
            title="Připravujeme">Vyloučit</button>

    {% if month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
      <button type="button" class="rex-btn" data-action="override-toggle">Úprava</button>
    {% endif %}

    <button type="button" class="rex-btn primary" data-action="open-panel"
            data-code="{{ r.confirmation_code }}"
            data-slug="{{ slug }}"
            data-year="{{ year }}"
            data-month="{{ month }}"
            data-guest="{{ r.guest_name|default('') }}"
            data-channel="{{ r.source|default('') }}">Otevřít panel →</button>
  </div>

  {# ── Inline override-form, hidden by default; toggled by Úprava ── #}
  {% if month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
    <div class="rs-ov-form" hidden data-ov-form>
      <form method="post"
            action="/property/{{ slug }}/{{ year }}/{{ month }}/reservation/{{ r.confirmation_code }}/override">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="ae-row" style="grid-template-columns:200px 1fr 1fr 1fr;margin-top:14px">
          <div class="ae-field">
            <label class="ae-k">Pole</label>
            <select class="ae-in" name="field" data-ov-field-select>
              {% for f, lbl in override_field_labels.items() %}
                <option value="{{ f }}">{{ lbl }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="ae-field">
            <label class="ae-k">Původní hodnota</label>
            <div class="ae-in" style="background:transparent;border:0;color:var(--text-400);padding-top:8px" data-ov-original></div>
          </div>
          <div class="ae-field">
            <label class="ae-k">Nová hodnota</label>
            <input class="ae-in" name="new_value" data-ov-new-input
                   list="ov-options-{{ r.confirmation_code }}" required>
            <datalist id="ov-options-{{ r.confirmation_code }}" data-ov-datalist></datalist>
          </div>
          <div class="ae-field">
            <label class="ae-k">Důvod</label>
            <input class="ae-in" name="reason" placeholder="Volitelně" maxlength="200">
          </div>
        </div>
        <div class="ae-actions-row">
          <button type="button" class="btn-link" data-ov-cancel>Zrušit</button>
          <button type="submit" class="btn-add btn-add-solid">Uložit úpravu</button>
        </div>
      </form>
    </div>
  {% endif %}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_reservation_detail.html
git commit -m "feat(reservations): expanded-row body with 3 groups + actions + inline override form"
```

---

## Task 15: `property_reservations.html` — rewrite

**Files:**
- Rewrite: `templates/partials/property_reservations.html`

- [ ] **Step 1: Replace file content**

```jinja
{# Reservations card: header (title + counts + filter-pills) + 7-col table.
   Each row has a hidden detail-row (.rs-ex) underneath, included from
   property_reservation_detail.html, toggled by JS click-to-expand. #}

<div class="card">
  <div class="card-h">
    <div class="card-h-left">
      <span class="card-title">Rezervace</span>
      <span class="card-meta">
        <span class="v">{{ counts.active }}</span> aktivních
        <span style="opacity:.3;margin:0 5px">·</span>
        <span class="v">{{ counts.nights }}</span> nocí
        {% if counts.adjustments > 0 %}
          <span style="opacity:.3;margin:0 5px">·</span>
          <span class="v">{{ counts.adjustments }}</span> úprav
        {% endif %}
      </span>
    </div>
    <div class="rs-filters" data-filter-group="rs">
      <button class="rs-filter active" type="button" data-filter="all">VŠE<span class="n">{{ counts.all_rows }}</span></button>
      <button class="rs-filter" type="button" data-filter="problems" {% if counts.problems == 0 %}disabled{% endif %}>
        PROBLÉMY{% if counts.problems > 0 %}<span class="n err">{{ counts.problems }}</span>{% endif %}
      </button>
      <button class="rs-filter" type="button" data-filter="moved" {% if counts.moved == 0 %}disabled{% endif %}>
        PŘESUNY{% if counts.moved > 0 %}<span class="n">{{ counts.moved }}</span>{% endif %}
      </button>
      <button class="rs-filter" type="button" data-filter="excluded" {% if counts.excluded == 0 %}disabled{% endif %}>
        VYLOUČENÉ{% if counts.excluded > 0 %}<span class="n">{{ counts.excluded }}</span>{% endif %}
      </button>
    </div>
  </div>

  <table class="t rs-table">
    <thead>
      <tr>
        <th style="width:40px"></th>
        <th class="c" style="width:48px">Kanál</th>
        <th>Host</th>
        <th class="r" style="width:148px">Datum</th>
        <th class="r" style="width:130px">Výplata</th>
        <th class="c" style="width:150px">Stav</th>
        <th style="width:24px"></th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
        <tr class="rs-row{% if r.is_excluded %} rs-row-muted{% endif %}{% if r.is_payout_adjustment %} rs-row-child{% endif %}"
            data-code="{{ r.confirmation_code }}"
            data-status="{{ r._mock_status }}"
            data-channel="{{ r.source|default('') }}"
            data-payout="{{ r.payout_czk or 0 }}"
            data-ubyt="{{ r.cena_ubytovani_czk or 0 }}"
            data-nights="{{ r.nights or 0 }}">

          <td class="rs-idx">{{ '%02d'|format(loop.index) }}</td>

          <td class="c">
            {% if r.source == 'airbnb' %}
              <span class="ch-logo ch-logo-airbnb" title="Airbnb">
                <svg width="16" height="16" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M16 5.2c-1.4 0-2.6.85-3.35 2.4L4.95 22.5c-.55 1.1-.85 2.05-.85 2.95 0 2.4 1.85 4.15 4.3 4.15 1.4 0 2.7-.6 4.05-1.85L16 25l3.55 2.75c1.35 1.25 2.65 1.85 4.05 1.85 2.45 0 4.3-1.75 4.3-4.15 0-.9-.3-1.85-.85-2.95L19.35 7.6c-.75-1.55-1.95-2.4-3.35-2.4z"/>
                  <path d="M11.5 19.4c0-1.05.85-1.9 1.9-1.9h5.2c1.05 0 1.9.85 1.9 1.9 0 .65-.35 1.2-.75 1.7L16 26l-3.75-4.9c-.4-.5-.75-1.05-.75-1.7z"/>
                </svg>
              </span>
            {% elif r.source == 'booking' %}
              <span class="ch-logo ch-logo-booking" title="Booking.com">
                <svg width="16" height="16" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M9 6.5v17"/><path d="M9 6.5h6.2c2.7 0 4.4 1.5 4.4 3.7 0 2.2-1.7 3.7-4.4 3.7H9"/>
                  <path d="M9 13.9h7c2.8 0 4.7 1.6 4.7 4.05 0 2.45-1.9 4.05-4.7 4.05H9"/>
                  <circle cx="24" cy="22" r="1.7" fill="currentColor" stroke="none"/>
                </svg>
              </span>
            {% else %}
              <span class="ch-logo" title="{{ r.source|default('Jiné') }}" style="color:var(--text-400);font-size:11px;font-family:var(--font-mono);text-transform:uppercase">{{ (r.source or 'jin')[:3] }}</span>
            {% endif %}
          </td>

          <td>
            <span class="rs-guest">{{ r.guest_name or '—' }}</span>
            {% if r.guests and r.guests > 0 %}
              <span class="hint">·&nbsp;{{ r.guests }}&nbsp;{% if r.guests == 1 %}host{% else %}hosté{% endif %}</span>
            {% endif %}
          </td>

          <td class="r rs-date">
            {% if r.is_payout_adjustment %}
              <div class="rs-date-child">
                <span class="rs-date-arrow">↳</span>
                <span class="rs-date-num">{{ r.adjustment_anchor_date or r.checkin_date or '—' }}</span>
              </div>
            {% else %}
              <div class="rs-date-stack">
                <span class="rs-date-num">{{ r.stay_label or '—' }}</span>
                {% if r.nights and r.nights > 0 %}
                  <span class="rs-date-sub">{{ r.nights }}&nbsp;{% if r.nights == 1 %}noc{% elif r.nights < 5 %}noci{% else %}nocí{% endif %}</span>
                {% endif %}
              </div>
            {% endif %}
          </td>

          <td class="r rs-payout">
            <div class="rs-payout-stack">
              <span class="rs-payout-num{% if (r.payout_czk or 0) < 0 %} warn{% endif %}">
                {% if r.payout_czk %}{{ fmt_czk(r.payout_czk) }}{% else %}—{% endif %}
              </span>
              {% if r.cena_ubytovani_czk and not r.is_payout_adjustment %}
                <span class="rs-payout-sub">ubyt.&nbsp;{{ fmt_czk(r.cena_ubytovani_czk) }}</span>
              {% endif %}
            </div>
          </td>

          <td class="c rs-stav">
            <div class="rs-stav-stack">
              <span class="badge {{ r._mock_status_class }}">{{ r._mock_status_label }}</span>
              {% if not r.is_payout_adjustment %}
                {% if r.bank_status == 'DORAZILO' %}
                  <span class="rs-stav-bank ok">
                    <span class="rs-stav-bank-k">Banka</span>
                    <span class="rs-stav-bank-mk">
                      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    </span>
                  </span>
                {% elif r.bank_status == 'N/A' or r.is_excluded or r._mock_status == 'MOVED_OUT' %}
                  <span class="rs-stav-bank na"><span class="rs-stav-bank-k">Banka</span><span class="rs-stav-bank-mk">—</span></span>
                {% else %}
                  <span class="rs-stav-bank err">
                    <span class="rs-stav-bank-k">Banka</span>
                    <span class="rs-stav-bank-mk">
                      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </span>
                  </span>
                {% endif %}
              {% endif %}
            </div>
          </td>

          <td class="rs-caret">
            {% if not r.is_payout_adjustment %}
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            {% endif %}
          </td>
        </tr>

        {# Hidden detail row, toggled by JS click-to-expand #}
        <tr class="rs-ex" data-for="{{ r.confirmation_code }}" hidden>
          <td colspan="7">
            {% include "partials/property_reservation_detail.html" %}
          </td>
        </tr>
      {% endfor %}
    </tbody>
    <tfoot>
      <tr>
        <td colspan="4" class="mono" style="color:var(--text-400);font-size:11px;letter-spacing:.1em;text-transform:uppercase">
          Celkem · <span data-totals="active">{{ counts.active }}</span> aktivních ·
          <span data-totals="nights">{{ counts.nights }}</span> nocí
        </td>
        <td class="r rs-payout">
          <div class="rs-payout-stack">
            <span class="rs-payout-num strong" data-totals="payout">{{ fmt_czk(totals.payout) }}</span>
            <span class="rs-payout-sub">ubyt.&nbsp;<span data-totals="ubyt">{{ fmt_czk(totals.ubyt) }}</span></span>
          </div>
        </td>
        <td colspan="2"></td>
      </tr>
    </tfoot>
  </table>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_reservations.html
git commit -m "feat(reservations): rewrite to 7-col table with filter-pills + click-to-expand"
```

---

## Task 16: `property_breakdown.html` — rewrite

**Files:**
- Rewrite: `templates/partials/property_breakdown.html`

- [ ] **Step 1: Replace file content**

```jinja
{# Finanční přehled dle kanálu — collapsible card with channel breakdown.
   Column semantics: Ubytování=income (white,bold), Provize=deduction (muted,−),
   Příprava+Citytax=neutral, Výplata=highlight (bold,bg-tint). #}

<div class="card">
  <div class="card-h" data-section-toggle="breakdown" style="cursor:pointer">
    <div class="card-h-left">
      <svg class="card-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      <span class="card-title">Finanční přehled dle kanálu</span>
    </div>
    <span class="card-meta-r"><span class="v">{{ row_breakdown.total.count }}</span> rezervací</span>
  </div>

  <table class="t bd-table" data-section-body="breakdown">
    <thead>
      <tr>
        <th style="width:140px">Kanál</th>
        <th class="c" style="width:50px">Rez.</th>
        <th class="r">Ubytování</th>
        <th class="r">Provize</th>
        <th class="r">Příprava pokoje</th>
        <th class="r">City tax</th>
        <th class="r bd-payout-h">Výplata</th>
      </tr>
    </thead>
    <tbody>
      {% for key, label, b in [
        ('airbnb',  'Airbnb',  row_breakdown.airbnb),
        ('booking', 'Booking', row_breakdown.booking),
        ('other',   'Jiné',    row_breakdown.other),
      ] %}
        {% if b.count > 0 %}
          <tr>
            <td><span class="ch ch-{{ key }}">{{ label }}</span></td>
            <td class="c num">{{ b.count }}</td>
            <td class="r num income">{{ fmt_czk(b.cena_ubytovani_czk or 0) }}</td>
            <td class="r num deduction">−{{ fmt_czk(b.provize_czk or 0) }}</td>
            <td class="r num neutral">{{ fmt_czk(b.priprava_pokoje_czk or 0) }}</td>
            <td class="r num neutral">{{ fmt_czk(b.city_tax_czk or 0) }}</td>
            <td class="r num bd-payout">{{ fmt_czk(b.payout_czk or 0) }}</td>
          </tr>
        {% endif %}
      {% endfor %}
    </tbody>
    <tfoot>
      <tr>
        <td class="bd-total-lbl">Celkem</td>
        <td class="c num strong">{{ row_breakdown.total.count }}</td>
        <td class="r num income strong">{{ fmt_czk(row_breakdown.total.cena_ubytovani_czk or 0) }}</td>
        <td class="r num deduction strong">−{{ fmt_czk(row_breakdown.total.provize_czk or 0) }}</td>
        <td class="r num neutral strong">{{ fmt_czk(row_breakdown.total.priprava_pokoje_czk or 0) }}</td>
        <td class="r num neutral strong">{{ fmt_czk(row_breakdown.total.city_tax_czk or 0) }}</td>
        <td class="r num bd-payout strong">{{ fmt_czk(row_breakdown.total.payout_czk or 0) }}</td>
      </tr>
    </tfoot>
  </table>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_breakdown.html
git commit -m "feat(breakdown): rewrite with new column semantics, standalone card"
```

---

## Task 17: `property_dph_summary.html` — new partial

**Files:**
- Create: `templates/partials/property_dph_summary.html`

- [ ] **Step 1: Create the partial**

```jinja
{# DPH summary card — only included by property.html when client.platce_dph == 1.
   Three-column box: vat_output | − | vat_input | = | vat_balance.
   Saldo color and label flip based on sign (refund vs owed). #}

{% set vat_balance = summary.vat_balance_czk or 0 %}
{% set is_refund = vat_balance < 0 %}

<div class="card">
  <div class="card-h">
    <div class="card-h-left">
      <span class="card-title">Vyúčtování DPH</span>
      <span class="badge {% if is_refund %}badge-ok{% else %}badge-dph{% endif %}">
        {% if is_refund %}Nadměrný odpočet{% else %}K odvedení{% endif %}
        &nbsp;·&nbsp;{{ fmt_czk(vat_balance|abs) }}
      </span>
    </div>
  </div>

  <div class="dph-box">
    <div class="dph-col">
      <div class="dph-k">DPH na výstupu</div>
      <div class="dph-v teal">+&nbsp;{{ fmt_czk(summary.vat_output_czk or 0) }}</div>
      <div class="dph-hint">
        Rentero fee&nbsp;&nbsp;<span class="dim">·</span>&nbsp;&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}<br>
        Příprava pokoje&nbsp;&nbsp;<span class="dim">·</span>&nbsp;&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}
      </div>
    </div>

    <div class="dph-op">−</div>

    <div class="dph-col">
      <div class="dph-k">DPH na vstupu (odpočet)</div>
      <div class="dph-v teal">−&nbsp;{{ fmt_czk(summary.vat_input_czk or 0) }}</div>
      <div class="dph-hint">
        Z výdajů (sub-nájem, energie, služby…)<br>
        {{ summary.vat_input_count or 0 }} {% if (summary.vat_input_count or 0) == 1 %}položka{% elif (summary.vat_input_count or 0) < 5 %}položky{% else %}položek{% endif %} s&nbsp;DPH
      </div>
    </div>

    <div class="dph-op">=</div>

    <div class="dph-col dph-col-result">
      <div class="dph-k">{% if is_refund %}Nadměrný odpočet{% else %}K odvedení státu{% endif %}</div>
      <div class="dph-v {% if is_refund %}ok{% else %}neg{% endif %}">
        {% if is_refund %}+{% else %}−{% endif %}&nbsp;{{ fmt_czk(vat_balance|abs) }}
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_dph_summary.html
git commit -m "feat(dph-summary): new partial for VAT input/output/balance card"
```

---

## Task 18: `property_expense_form.html` — calculator-strip form (reusable)

**Files:**
- Create: `templates/partials/property_expense_form.html`

- [ ] **Step 1: Create the partial**

```jinja
{# Add/Edit expense form — calculator-strip with three editable cells (Bez DPH, DPH, Celkem)
   linked by JS in property_scripts.html. Single DOM node reused for both add and edit;
   property_scripts.html flips action URL, title, submit-label, and prefills values
   when entering edit-mode. Hidden by default; .ae-form[hidden] removes it from layout. #}

<form class="ae-form" hidden
      method="post"
      data-expense-form
      action="/expenses/add">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="property_slug" value="{{ slug }}">
  <input type="hidden" name="year" value="{{ year }}">
  <input type="hidden" name="month" value="{{ month }}">

  <div class="ae-h">
    <span class="ae-h-title" data-form-title>Nový výdaj</span>
    <span class="ae-h-hint">Vyplňte libovolné pole — ostatní se přepočítají</span>
  </div>

  <div class="ae-row">
    <div class="ae-field">
      <label class="ae-k">Datum</label>
      <input class="ae-in" type="date" name="date_str" required>
    </div>
    <div class="ae-field">
      <label class="ae-k">Kategorie</label>
      <select class="ae-in" name="category_id">
        {% for c in categories %}
          <option value="{{ c.id }}" data-cat-name="{{ c.name }}">{{ c.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="ae-field">
      <label class="ae-k">Popis</label>
      <input class="ae-in" name="description" required placeholder="Např. Elektřina PRE za duben" maxlength="200">
    </div>
  </div>

  <div class="ae-calc">
    <label class="ae-cell">
      <span class="ae-cell-k">Bez DPH</span>
      <div class="ae-cell-input">
        <input type="text" inputmode="decimal" name="amount_net_czk" data-calc-net placeholder="0,00">
        <span class="ae-cell-suf">Kč</span>
      </div>
    </label>

    <span class="ae-calc-op">+</span>

    <label class="ae-cell ae-cell-dph">
      <span class="ae-cell-k">
        DPH
        <span class="ae-seg ae-seg-mini" data-calc-rate-tabs>
          <button type="button" data-rate="0">0%</button>
          <button type="button" data-rate="0.12">12%</button>
          <button type="button" class="active" data-rate="0.21">21%</button>
        </span>
      </span>
      <div class="ae-cell-input">
        <input type="text" inputmode="decimal" name="amount_dph_czk" data-calc-dph placeholder="0,00">
        <span class="ae-cell-suf">Kč</span>
      </div>
    </label>

    <span class="ae-calc-op">=</span>

    <label class="ae-cell ae-cell-total">
      <span class="ae-cell-k">Celkem</span>
      <div class="ae-cell-input">
        <input type="text" inputmode="decimal" name="amount_czk" data-calc-gross placeholder="0,00" required>
        <span class="ae-cell-suf">Kč</span>
      </div>
    </label>
  </div>

  <input type="hidden" name="vat_rate" value="0.21" data-calc-rate-input>

  <div class="ae-actions-row">
    <button type="button" class="btn-link" data-form-cancel>Zrušit</button>
    <button type="submit" class="btn-add btn-add-solid">
      <span data-form-submit-label>Uložit výdaj</span>
    </button>
  </div>
</form>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_expense_form.html
git commit -m "feat(expenses): reusable calculator-strip add/edit form partial"
```

---

## Task 19: `property_expenses.html` — rewrite

**Files:**
- Rewrite: `templates/partials/property_expenses.html`

- [ ] **Step 1: Replace file content**

```jinja
{# Expenses card: header + (toggleable) calculator-form + grouped-by-category table.
   Form is included once and reused for both add and edit modes via JS. #}

<div class="card">
  <div class="card-h">
    <div class="card-h-left">
      <span class="card-title">Výdaje</span>
      <span class="card-meta">
        <span class="v">{{ expenses|length }}</span> položek
        {% if summary.expenses_total_czk %}
          <span style="opacity:.3;margin:0 5px">·</span>
          <span class="v" style="color:var(--err)">−{{ fmt_czk(summary.expenses_total_czk) }}</span>
        {% endif %}
        {% if client and client.platce_dph and (summary.vat_input_czk or 0) > 0 %}
          <span style="opacity:.3;margin:0 5px">·</span>
          <span style="color:var(--dph-text)">DPH&nbsp;+{{ fmt_czk(summary.vat_input_czk) }}</span>
        {% endif %}
      </span>
    </div>
    {% if month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
      <button class="btn-add" type="button" data-action="expense-form-toggle" aria-expanded="false">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 5v14M5 12h14"/></svg>
        <span class="exp-add-label">Přidat výdaj</span>
      </button>
    {% endif %}
  </div>

  {% if month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
    {% include "partials/property_expense_form.html" %}
  {% endif %}

  <table class="t exp-table">
    <thead>
      <tr>
        <th style="width:110px">Datum</th>
        <th>Popis</th>
        <th class="c" style="width:68px">Sazba</th>
        <th class="r" style="width:120px">Bez DPH</th>
        <th class="r" style="width:110px">DPH</th>
        <th class="r" style="width:130px">Celkem</th>
        <th style="width:84px"></th>
      </tr>
    </thead>
    <tbody>
      {% if not expenses %}
        <tr><td colspan="7" style="text-align:center;color:var(--text-400);padding:32px">Žádné výdaje</td></tr>
      {% else %}
        {% for cat_name, items in expenses_by_cat.items() %}
          {% set cat_sum = items | sum(attribute='amount_czk') %}
          <tr class="exp-cat-row">
            <td colspan="7">
              <div class="exp-cat-inner">
                <span class="exp-cat-dot {{ cat_dot_class.get(cat_name, 'exp-dot-other') }}"></span>
                <span class="exp-cat-name">{{ cat_name }}</span>
                <span class="exp-cat-count">· {{ items|length }}</span>
                <span class="exp-cat-sum">−<b>{{ fmt_czk(cat_sum) }}</b></span>
              </div>
            </td>
          </tr>
          {% for e in items %}
            {% set rate = e.vat_rate %}
            {% set rate_pct = (rate * 100)|int if rate is not none else None %}
            <tr class="exp-row" data-expense-id="{{ e.id }}">
              <td class="num">{{ e.date or '—' }}</td>
              <td style="color:var(--text-100)">{{ e.description }}</td>
              <td class="c">
                {% if rate_pct is not none %}
                  <span class="badge vat-{{ rate_pct }}">DPH {{ rate_pct }}%</span>
                {% else %}
                  <span class="badge badge-mute">—</span>
                {% endif %}
              </td>
              <td class="r num">
                {% if e.amount_net_czk is not none %}{{ fmt_czk_2dp(e.amount_net_czk) }}
                {% else %}—{% endif %}
              </td>
              <td class="r num" style="color:{% if rate and rate > 0 %}var(--dph-text){% else %}var(--text-400){% endif %}">
                {% if rate and rate > 0 and e.amount_dph_czk is not none %}{{ fmt_czk_2dp(e.amount_dph_czk) }}{% else %}—{% endif %}
              </td>
              <td class="r num strong">{{ fmt_czk(e.amount_czk) }}</td>
              <td class="r exp-actions">
                {% if month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
                  <button class="exp-edit" type="button"
                          data-action="expense-edit"
                          data-expense-id="{{ e.id }}"
                          data-expense-date="{{ e.date or '' }}"
                          data-expense-cat-id="{{ e.category_id or '' }}"
                          data-expense-desc="{{ e.description|e }}"
                          data-expense-net="{{ e.amount_net_czk if e.amount_net_czk is not none else '' }}"
                          data-expense-dph="{{ e.amount_dph_czk if e.amount_dph_czk is not none else '' }}"
                          data-expense-gross="{{ e.amount_czk }}"
                          data-expense-rate="{{ e.vat_rate if e.vat_rate is not none else '' }}"
                          title="Upravit" aria-label="Upravit">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
                  </button>
                  <form method="post" action="/expenses/{{ e.id }}/delete" style="display:inline"
                        onsubmit="return confirm('Smazat výdaj «{{ e.description|e }}»?');">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button class="exp-delete exp-edit" type="submit" title="Smazat" aria-label="Smazat" style="color:var(--err)">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
                    </button>
                  </form>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
        {% endfor %}
      {% endif %}
    </tbody>
    {% if expenses %}
      <tfoot>
        <tr>
          <td colspan="3" style="font-weight:600">Celkem</td>
          <td class="r num strong">{{ fmt_czk_2dp(summary.expenses_net_total_czk or 0) }}</td>
          <td class="r num strong" style="color:var(--dph-text)">{{ fmt_czk_2dp(summary.vat_input_czk or 0) }}</td>
          <td class="r num strong" style="color:var(--err)">−{{ fmt_czk(summary.expenses_total_czk or 0) }}</td>
          <td></td>
        </tr>
      </tfoot>
    {% endif %}
  </table>
</div>
```

- [ ] **Step 2: Verify `fmt_czk_2dp` filter exists**

Check `report/web.py` (or wherever Jinja env is set up) for filter registrations. If `fmt_czk_2dp` doesn't exist, add it next to `fmt_czk`:

```python
def fmt_czk_2dp(value) -> str:
    """Like fmt_czk but always 2 decimal places (for net/dph cells)."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    sign = "−" if n < 0 else ""
    s = f"{abs(n):,.2f}".replace(",", " ").replace(".", ",")
    return sign + s
```

Register it: `templates.env.filters["fmt_czk_2dp"] = fmt_czk_2dp` (locate the line that registers `fmt_czk` and mirror).

- [ ] **Step 3: Commit**

```bash
git add templates/partials/property_expenses.html report/web.py
git commit -m "feat(expenses): rewrite with category groups + calculator form integration"
```

---

## Task 20: `property_override_history.html` — restyle

**Files:**
- Rewrite: `templates/partials/property_override_history.html`

- [ ] **Step 1: Replace file content**

```jinja
{# Override audit-trail: read-only history of manual reservation field overrides.
   Default-collapsed card. Pure restyle — same data, same revert endpoint as before. #}

{% if override_events %}
  <div class="card">
    <div class="card-h" data-section-toggle="overrides" style="cursor:pointer">
      <div class="card-h-left">
        <svg class="card-caret closed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
        <span class="card-title">Historie ručních úprav</span>
        <span class="card-meta"><span class="v">{{ override_events|length }}</span> změn</span>
      </div>
    </div>

    <table class="t" data-section-body="overrides" hidden>
      <thead>
        <tr>
          <th style="width:140px">Rezervace</th>
          <th style="width:120px">Pole</th>
          <th>Původní → Nová</th>
          <th>Důvod</th>
          <th class="r" style="width:130px">Datum</th>
          <th class="c" style="width:90px">Stav</th>
          <th style="width:84px"></th>
        </tr>
      </thead>
      <tbody>
        {% for ev in override_events %}
          <tr>
            <td class="num">{{ ev.scope_id }}</td>
            <td>{{ override_field_labels.get(ev.field, ev.field) }}</td>
            <td>
              <span class="num dim" style="text-decoration:line-through">{{ ev.old_value or '—' }}</span>
              <span class="dim" style="margin:0 6px">→</span>
              <span class="num strong">{{ ev.new_value }}</span>
            </td>
            <td class="dim" title="{{ ev.reason }}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ ev.reason or '—' }}</td>
            <td class="r num">{{ ev.created_at }}</td>
            <td class="c">
              {% if ev.is_active %}
                <span class="badge badge-warn"><span class="badge-dot"></span>Aktivní</span>
              {% else %}
                <span class="badge badge-mute">Obnoveno</span>
              {% endif %}
            </td>
            <td class="r exp-actions">
              {% if ev.is_active and month_state.status != 'LOCKED' and request.session.get('role') != 'client' %}
                <form method="post" action="/property/{{ slug }}/{{ year }}/{{ month }}/override/{{ ev.id }}/revert" style="display:inline" onsubmit="return confirm('Obnovit původní hodnotu pro {{ ev.scope_id }}?');">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <button class="exp-edit" type="submit" title="Obnovit" aria-label="Obnovit původní hodnotu">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                  </button>
                </form>
              {% elif not ev.is_active %}
                <span class="num dim" style="font-size:11px">{{ ev.reverted_at }}</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_override_history.html
git commit -m "feat(override-history): restyle into new card/table tokens"
```

---

## Task 21: `property_scripts.html` — full vanilla JS

**Files:**
- Rewrite: `templates/partials/property_scripts.html`

- [ ] **Step 1: Replace file content**

```jinja
<script>
(function() {
  'use strict';

  // ─────────────────────────────────────────────────────────────
  // Section toggles — collapse/expand cards via [data-section-toggle]
  // State persisted in sessionStorage keyed per (slug,year,month,section).
  // ─────────────────────────────────────────────────────────────
  var SLUG = {{ slug|tojson }};
  var YEAR = {{ year }};
  var MONTH = {{ month }};
  var SEC_KEY_PREFIX = 'rentero_sec_' + SLUG + '_' + YEAR + '_' + MONTH + '_';

  function _getSecState(name) {
    try { return sessionStorage.getItem(SEC_KEY_PREFIX + name); } catch (e) { return null; }
  }
  function _setSecState(name, collapsed) {
    try { sessionStorage.setItem(SEC_KEY_PREFIX + name, collapsed ? '1' : '0'); } catch (e) {}
  }

  document.querySelectorAll('[data-section-toggle]').forEach(function(header) {
    var name = header.getAttribute('data-section-toggle');
    var body = document.querySelector('[data-section-body="' + name + '"]');
    var caret = header.querySelector('.card-caret');
    if (!body) return;

    // Restore state
    if (_getSecState(name) === '1') {
      body.hidden = true;
      if (caret) caret.classList.add('closed');
    }

    header.addEventListener('click', function(e) {
      // Don't toggle if click was on a button/link inside the header
      if (e.target.closest('button,a,input,select')) return;
      var collapsed = !body.hidden;
      body.hidden = collapsed;
      if (caret) caret.classList.toggle('closed', collapsed);
      _setSecState(name, collapsed);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Reservation filter pills
  // ─────────────────────────────────────────────────────────────
  var FILTER_KEY = 'rentero_rs_filter_' + SLUG + '_' + YEAR + '_' + MONTH;
  var filterGroup = document.querySelector('[data-filter-group="rs"]');
  var rsRows = Array.prototype.slice.call(document.querySelectorAll('tr.rs-row'));
  var rsExRows = Array.prototype.slice.call(document.querySelectorAll('tr.rs-ex'));

  var FILTER_BUCKETS = {
    all: function(s) { return true; },
    problems: function(s) { return s === 'ROZDIL' || s === 'CHYBI_V_CSV' || s === 'CHYBI_V_HOSTIFY'; },
    moved: function(s) { return s === 'MOVED_IN' || s === 'MOVED_OUT'; },
    excluded: function(s) { return s === 'EXCLUDED' || s === 'ZRUSENO'; },
  };

  function _applyFilter(name) {
    var match = FILTER_BUCKETS[name] || FILTER_BUCKETS.all;
    var totals = { active: 0, nights: 0, payout: 0, ubyt: 0 };

    rsRows.forEach(function(row, i) {
      var status = row.getAttribute('data-status') || '';
      var visible = match(status);
      row.hidden = !visible;
      // Hide the corresponding rs-ex row too if its parent row is filtered out.
      var code = row.getAttribute('data-code');
      var ex = document.querySelector('tr.rs-ex[data-for="' + code + '"]');
      if (ex && !visible) ex.hidden = true;

      // Recompute totals for visible non-EXCLUDED rows
      if (visible && status !== 'EXCLUDED' && status !== 'ZRUSENO' && status !== 'ADJUSTMENT') {
        totals.active += 1;
        totals.nights += parseFloat(row.getAttribute('data-nights') || 0);
        totals.payout += parseFloat(row.getAttribute('data-payout') || 0);
        totals.ubyt += parseFloat(row.getAttribute('data-ubyt') || 0);
      }
    });

    // Update <tfoot> data-totals cells
    var fmt = function(n) { return _formatCzk(Math.round(n)); };
    var setTot = function(key, val) {
      var el = document.querySelector('[data-totals="' + key + '"]');
      if (el) el.textContent = val;
    };
    setTot('active', totals.active);
    setTot('nights', totals.nights);
    setTot('payout', fmt(totals.payout));
    setTot('ubyt', fmt(totals.ubyt));

    try { sessionStorage.setItem(FILTER_KEY, name); } catch (e) {}
  }

  function _formatCzk(n) {
    var sign = n < 0 ? '−' : '';
    var s = Math.abs(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return sign + s + ' Kč';
  }

  if (filterGroup) {
    filterGroup.querySelectorAll('[data-filter]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        if (btn.disabled) return;
        filterGroup.querySelectorAll('[data-filter]').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        _applyFilter(btn.getAttribute('data-filter'));
      });
    });
    // Restore filter from sessionStorage
    try {
      var saved = sessionStorage.getItem(FILTER_KEY) || 'all';
      var savedBtn = filterGroup.querySelector('[data-filter="' + saved + '"]:not(:disabled)');
      if (savedBtn) {
        filterGroup.querySelectorAll('[data-filter]').forEach(function(b) { b.classList.remove('active'); });
        savedBtn.classList.add('active');
        _applyFilter(saved);
      }
    } catch (e) {}
  }

  // ─────────────────────────────────────────────────────────────
  // Click-to-expand reservation row (one open at a time)
  // ─────────────────────────────────────────────────────────────
  var openedCode = null;

  function _toggleRow(code) {
    rsRows.forEach(function(r) { r.classList.remove('rs-row-open'); });
    rsExRows.forEach(function(ex) { ex.hidden = true; });

    if (openedCode === code) {
      openedCode = null;
      return;
    }
    var row = document.querySelector('tr.rs-row[data-code="' + code + '"]');
    var ex = document.querySelector('tr.rs-ex[data-for="' + code + '"]');
    if (row && ex) {
      row.classList.add('rs-row-open');
      ex.hidden = false;
      openedCode = code;
    }
  }

  rsRows.forEach(function(row) {
    if (row.classList.contains('rs-row-child')) return;  // child rows don't expand
    row.addEventListener('click', function(e) {
      if (e.target.closest('button,a,input,select,[data-action]')) return;
      var code = row.getAttribute('data-code');
      _toggleRow(code);
    });
    row.style.cursor = 'pointer';
  });

  // ─────────────────────────────────────────────────────────────
  // Override-form toggle inside expanded row
  // ─────────────────────────────────────────────────────────────
  document.querySelectorAll('[data-action="override-toggle"]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var ex = btn.closest('.rs-ex-inner');
      if (!ex) return;
      var form = ex.querySelector('[data-ov-form]');
      if (form) form.hidden = !form.hidden;
    });
  });

  document.querySelectorAll('[data-ov-cancel]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var form = btn.closest('[data-ov-form]');
      if (form) form.hidden = true;
    });
  });

  // Field selector — update Original-value display + datalist on change
  document.querySelectorAll('[data-ov-field-select]').forEach(function(sel) {
    sel.addEventListener('change', function() {
      var form = sel.closest('[data-ov-form]');
      if (!form) return;
      var origCell = form.querySelector('[data-ov-original]');
      if (origCell) origCell.textContent = '—';  // backend has no row→original-value bridge in Phase 1; user knows the field
      // Datalist: only verification_status has finite options; clear for others.
      var dl = form.querySelector('[data-ov-datalist]');
      if (dl) {
        dl.innerHTML = '';
        if (sel.value === 'verification_status') {
          {% if verification_status_options %}
            var opts = {{ verification_status_options|tojson }};
            opts.forEach(function(o) {
              var opt = document.createElement('option');
              opt.value = o;
              dl.appendChild(opt);
            });
          {% endif %}
        }
      }
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Open-panel (FloatingPanel from base_scripts.html)
  // ─────────────────────────────────────────────────────────────
  document.querySelectorAll('[data-action="open-panel"]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      if (typeof FloatingPanel === 'undefined' || !FloatingPanel.open) {
        console.warn('[property] FloatingPanel not available');
        return;
      }
      FloatingPanel.open(
        btn.getAttribute('data-code'),
        btn.getAttribute('data-slug'),
        parseInt(btn.getAttribute('data-year'), 10),
        parseInt(btn.getAttribute('data-month'), 10),
        btn.getAttribute('data-guest') || '',
        btn.getAttribute('data-channel') || ''
      );
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Expense form — calculator binding (add + edit modes)
  // ─────────────────────────────────────────────────────────────
  var expForm = document.querySelector('[data-expense-form]');
  var expToggleBtn = document.querySelector('[data-action="expense-form-toggle"]');

  function _parseNum(s) {
    var n = parseFloat(String(s || '').replace(/\s/g, '').replace(',', '.'));
    return isNaN(n) ? 0 : n;
  }
  function _fmtNum(n) {
    if (!n || n <= 0) return '';
    return n.toLocaleString('cs-CZ', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  if (expForm) {
    var fNet = expForm.querySelector('[data-calc-net]');
    var fDph = expForm.querySelector('[data-calc-dph]');
    var fGross = expForm.querySelector('[data-calc-gross]');
    var fRate = expForm.querySelector('[data-calc-rate-input]');
    var rateTabs = expForm.querySelector('[data-calc-rate-tabs]');

    function _currentRate() {
      return parseFloat(fRate.value || '0.21');
    }
    function _recomputeFromGross() {
      var g = _parseNum(fGross.value);
      if (g <= 0) return;
      var r = _currentRate();
      var net = r > 0 ? g - g / (1 + r) : 0;
      var dph = r > 0 ? g - (g - net) : 0;
      net = g / (1 + r); dph = g - net;
      fNet.value = _fmtNum(net);
      fDph.value = _fmtNum(dph);
      fGross.value = _fmtNum(g);
    }
    function _recomputeFromNet() {
      var n = _parseNum(fNet.value);
      if (n <= 0) return;
      var r = _currentRate();
      var dph = n * r;
      fNet.value = _fmtNum(n);
      fDph.value = _fmtNum(dph);
      fGross.value = _fmtNum(n + dph);
    }
    function _recomputeFromDph() {
      var d = _parseNum(fDph.value);
      var g = _parseNum(fGross.value);
      var n = _parseNum(fNet.value);
      if (g > 0) {
        fDph.value = _fmtNum(d);
        fNet.value = _fmtNum(g - d);
      } else if (n > 0) {
        fDph.value = _fmtNum(d);
        fGross.value = _fmtNum(n + d);
      } else {
        fDph.value = _fmtNum(d);
      }
    }

    fNet.addEventListener('blur', _recomputeFromNet);
    fDph.addEventListener('blur', _recomputeFromDph);
    fGross.addEventListener('blur', _recomputeFromGross);

    if (rateTabs) {
      rateTabs.querySelectorAll('button').forEach(function(b) {
        b.addEventListener('click', function() {
          rateTabs.querySelectorAll('button').forEach(function(x) { x.classList.remove('active'); });
          b.classList.add('active');
          fRate.value = b.getAttribute('data-rate');
          // Recompute from gross if available, else from net
          if (_parseNum(fGross.value) > 0) _recomputeFromGross();
          else if (_parseNum(fNet.value) > 0) _recomputeFromNet();
        });
      });
    }

    // Toggle (add mode)
    if (expToggleBtn) {
      expToggleBtn.addEventListener('click', function() {
        if (expForm.hidden) {
          // Open in add mode: reset, switch action
          expForm.action = '/expenses/add';
          expForm.querySelector('[data-form-title]').textContent = 'Nový výdaj';
          expForm.querySelector('[data-form-submit-label]').textContent = 'Uložit výdaj';
          expForm.querySelector('input[name="date_str"]').value = new Date().toISOString().slice(0, 10);
          expForm.querySelector('input[name="description"]').value = '';
          fNet.value = ''; fDph.value = ''; fGross.value = '';
          fRate.value = '0.21';
          if (rateTabs) {
            rateTabs.querySelectorAll('button').forEach(function(b) {
              b.classList.toggle('active', b.getAttribute('data-rate') === '0.21');
            });
          }
          expForm.hidden = false;
          expToggleBtn.setAttribute('aria-expanded', 'true');
          expForm.querySelector('input[name="description"]').focus();
        } else {
          expForm.hidden = true;
          expToggleBtn.setAttribute('aria-expanded', 'false');
        }
      });
    }

    // Cancel button
    var cancelBtn = expForm.querySelector('[data-form-cancel]');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function() {
        expForm.hidden = true;
        if (expToggleBtn) expToggleBtn.setAttribute('aria-expanded', 'false');
      });
    }

    // Edit mode — invoked from pencil button
    document.querySelectorAll('[data-action="expense-edit"]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-expense-id');
        expForm.action = '/expenses/' + id + '/edit';
        expForm.querySelector('[data-form-title]').textContent = 'Upravit výdaj #' + id;
        expForm.querySelector('[data-form-submit-label]').textContent = 'Uložit změny';
        expForm.querySelector('input[name="date_str"]').value = btn.getAttribute('data-expense-date') || '';
        var catSel = expForm.querySelector('select[name="category_id"]');
        if (catSel) {
          var catId = btn.getAttribute('data-expense-cat-id');
          if (catId) catSel.value = catId;
        }
        expForm.querySelector('input[name="description"]').value = btn.getAttribute('data-expense-desc') || '';
        fNet.value = btn.getAttribute('data-expense-net') || '';
        fDph.value = btn.getAttribute('data-expense-dph') || '';
        fGross.value = btn.getAttribute('data-expense-gross') || '';
        var rate = btn.getAttribute('data-expense-rate') || '0.21';
        fRate.value = rate;
        if (rateTabs) {
          rateTabs.querySelectorAll('button').forEach(function(b) {
            b.classList.toggle('active', b.getAttribute('data-rate') === String(rate));
          });
        }
        expForm.hidden = false;
        if (expToggleBtn) expToggleBtn.setAttribute('aria-expanded', 'true');
        expForm.scrollIntoView({behavior: 'smooth', block: 'center'});
      });
    });
  }

  // ─────────────────────────────────────────────────────────────
  // Generation auto-reload (PENDING/RUNNING)
  // ─────────────────────────────────────────────────────────────
  var genStatus = document.querySelector('[data-generation-status]');
  if (genStatus) {
    setTimeout(function() { window.location.reload(); }, 4000);
  }

})();
</script>
```

- [ ] **Step 2: Commit**

```bash
git add templates/partials/property_scripts.html
git commit -m "feat(scripts): vanilla JS for filter/expand/calculator/override-form"
```

---

## Task 22: `property.html` — wire all partials

**Files:**
- Rewrite: `templates/property.html`

- [ ] **Step 1: Replace file content**

```jinja
{% extends "base.html" %}
{% block title %}{{ prop.display_name or prop.listing_nickname }} {{ "%02d/%d"|format(month, year) }} — Rentero{% endblock %}

{% block content %}
{% include "partials/property_styles_property.html" %}
<div class="page">
  {% include "partials/property_intro.html" %}
  {% include "partials/property_notify_stack.html" %}
  {% include "partials/property_generation_progress.html" %}
  {% include "partials/property_kpi.html" %}
  {% include "partials/property_reservations.html" %}
  {% include "partials/property_breakdown.html" %}
  {% if client and client.platce_dph %}
    {% include "partials/property_dph_summary.html" %}
  {% endif %}
  {% include "partials/property_expenses.html" %}
  {% if override_events %}
    {% include "partials/property_override_history.html" %}
  {% endif %}
</div>
{% include "partials/property_scripts.html" %}
{% endblock %}
```

- [ ] **Step 2: Smoke test the full page**

Restart dev server. Open `/property/<known-slug>/2026/4` (use a property with several reservations and 3-5 expenses).

Verify:
- Page loads, no template errors
- Header: breadcrumb, title, badges (lock-state, client-type, optionally Plátce DPH), action buttons
- KPI row renders with correct values matching client_type
- Reservations table: 7 columns; click expand toggles detail row
- Filter pills work and update tfoot
- Breakdown card renders, collapse-toggle works
- DPH summary appears only for `platce_dph=1` properties
- Expenses card: add-form opens via "+ Přidat výdaj"; calculator math works (type gross, blur → net+dph fill); pencil → edit-mode prefills; trash → confirm dialog
- Override history: collapsed by default; expand shows table; revert button submits
- FloatingPanel still opens via "Otevřít panel →"
- Lock/Unlock works; flash appears in notify-stack
- Aurora background still visible behind content

- [ ] **Step 3: Commit**

```bash
git add templates/property.html
git commit -m "feat(property): wire all redesigned partials in property.html"
```

---

## Task 23: Mobile breakpoints

**Files:**
- Modify: `templates/partials/property_styles_property.html` (append `@media` blocks at end)

- [ ] **Step 1: Append tablet breakpoint**

Just before `</style>` in `property_styles_property.html`:

```css
/* ═══════════════════════════════════════════════════
   MOBILE — tablet ≤1100px
   ═══════════════════════════════════════════════════ */
@media (max-width: 1100px) {
  .page { padding: 20px 16px 80px; }
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
  .ph-actions { padding-top: 0; }
  .rs-ex-groups {
    grid-template-columns: 1fr;
    gap: 14px 0;
  }
  .rs-ex-group { padding: 0 0 14px 0; }
  .rs-ex-group + .rs-ex-group {
    border-left: 0;
    border-top: 1px solid var(--line-hair);
    padding-top: 14px;
  }
  .dph-box {
    grid-template-columns: 1fr;
    gap: 14px;
    padding: 18px;
  }
  .dph-col-result {
    border-left: 0;
    padding-left: 0;
    border-top: 1px solid var(--dph-line);
    padding-top: 14px;
  }
  .dph-op { display: none; }
  .ae-row { grid-template-columns: 1fr 1fr; }
  .ae-row .ae-field:nth-child(3) { grid-column: span 2; }
  .ae-calc { flex-wrap: wrap; }
  .ae-calc-op { display: none; }
}
```

- [ ] **Step 2: Append phone breakpoint**

Right after the tablet block:

```css
/* ═══════════════════════════════════════════════════
   MOBILE — phone ≤640px
   ═══════════════════════════════════════════════════ */
@media (max-width: 640px) {
  .ph-title { font-size: 22px; }
  .ph-title-row { flex-direction: column; }
  .ph-actions { width: 100%; }

  .kpi-row { grid-template-columns: 1fr 1fr; }  /* 2×2 */
  .kpi { padding: 14px 14px 12px; }
  .kpi-value { font-size: 22px; }

  /* Reservation table → card-list */
  .rs-table thead { display: none; }
  .rs-table, .rs-table tbody, .rs-table tr, .rs-table td {
    display: block;
    width: 100%;
    box-sizing: border-box;
  }
  .rs-table tr.rs-row {
    padding: 12px 14px;
    border-bottom: 1px solid var(--line-hair);
    position: relative;
  }
  .rs-table tr.rs-row td { padding: 0; border: 0; }
  .rs-table .rs-idx {
    position: absolute; top: 12px; right: 14px;
    font-size: 10px; opacity: .5;
  }
  .rs-table .rs-row td:nth-child(2) {  /* channel */
    display: inline-block; width: auto; vertical-align: middle;
  }
  .rs-table .rs-row td:nth-child(3) {  /* host */
    display: inline-block; width: auto; margin-left: 8px;
  }
  .rs-table .rs-row td.rs-date,
  .rs-table .rs-row td.rs-payout,
  .rs-table .rs-row td.rs-stav {
    display: flex; justify-content: space-between; padding: 4px 0;
  }
  .rs-table .rs-row td.rs-date::before,
  .rs-table .rs-row td.rs-payout::before,
  .rs-table .rs-row td.rs-stav::before {
    content: attr(data-mobile-label);
    font-family: var(--font-mono); font-size: 10px;
    color: var(--text-400); text-transform: uppercase; letter-spacing: .1em;
  }
  .rs-table .rs-caret { display: none; }
  .rs-table tfoot { display: none; }
  .rs-table tr.rs-ex { display: block; }
  .rs-table tr.rs-ex td { padding: 0; border: 0; }

  /* Expenses table — hide net/dph columns on phone */
  .exp-table thead th:nth-child(4),
  .exp-table thead th:nth-child(5),
  .exp-table tbody td:nth-child(4),
  .exp-table tbody td:nth-child(5),
  .exp-table tfoot td:nth-child(4),
  .exp-table tfoot td:nth-child(5) { display: none; }

  /* Override history — hide reason column on phone */
  /* Targets the 4th column (Důvod) */
}
```

Add `data-mobile-label` attributes to the `<td>` elements in `property_reservations.html` (Task 15) — locate the three relevant `<td>`'s and add:
- Date td: `data-mobile-label="Pobyt"`
- Payout td: `data-mobile-label="Výplata"`
- Stav td: `data-mobile-label="Stav"`

Use Edit on `templates/partials/property_reservations.html`. Example for the date column:

```jinja
<td class="r rs-date" data-mobile-label="Pobyt">
```

- [ ] **Step 3: Smoke test on narrow viewport**

In browser, open dev tools and resize to:
- 1024×768 (tablet) — verify KPI 2-col, DPH stacked, no overflow
- 375×667 (phone) — verify KPI 2×2, reservations as card-list, expense table simplified

- [ ] **Step 4: Commit**

```bash
git add templates/partials/property_styles_property.html templates/partials/property_reservations.html
git commit -m "feat(mobile): tablet (≤1100) and phone (≤640) breakpoints"
```

---

## Task 24: Manual smoke test against staging — full functional preservation

**Files:**
- None (manual verification)

This task is a structured manual verification that no Phase 1 functionality regressed. Must be done before merging Phase 1 to main.

- [ ] **Step 1: Start dev server with a test DB**

```bash
bash start_web.sh
```

Open `http://127.0.0.1:8000/property/<known-slug>/2026/4` in browser.

- [ ] **Step 2: Run the smoke checklist**

Walk through each item; check off when verified. If any fails, debug and re-run.

- [ ] **Header**
  - [ ] Breadcrumb "Přehled / {prop name}" links back to dashboard preserving year/month
  - [ ] Title shows property display_name
  - [ ] Lock badge reflects month_state.status
  - [ ] Client-type badge correct for the property
  - [ ] Plátce DPH badge appears only if `clients.platce_dph=1`
  - [ ] Změny → /audit?slug=...&year=...&month=...
  - [ ] Checkin report → /property/.../evidence-hostu
  - [ ] Uzamknout → POST /lock → flash + redirect; status flips to LOCKED; button changes to Odemknout

- [ ] **Notify stack**
  - [ ] Lock-state warning shows when LOCKED
  - [ ] flash messages render after any POST (try lock/unlock cycle)
  - [ ] notify-error shows when validate_and_canonicalize fails (try bogus expense input)

- [ ] **KPI row**
  - [ ] Rentero property: Zisk = gross − expenses − vat_balance
  - [ ] Klient property: Zisk = client_payout_after_expenses_czk
  - [ ] Vyplaceno platformami matches sum of payouts
  - [ ] Rentero fee: label includes 15% (or 3% for z_klient)
  - [ ] Výdaje: shows count + total or "—" when empty
  - [ ] DPH sub-rows appear only on platce_dph properties

- [ ] **Reservations table**
  - [ ] All rows render with correct status badge
  - [ ] CHYBÍ V HOSTIFY rows render with err-color badge
  - [ ] ZRUŠENO rows appear under VYLOUČENÉ filter
  - [ ] Filter pills enabled/disabled by counts
  - [ ] Click on PROBLÉMY filters to ROZDIL/CHYBI rows
  - [ ] Tfoot totals recompute correctly
  - [ ] Click on a row toggles expand; only one open at a time
  - [ ] Expanded row shows 3 groups
  - [ ] Move buttons (← / →) are disabled with tooltip "Připravujeme"
  - [ ] Vyloučit button is disabled with tooltip
  - [ ] Úprava button opens inline override-form
  - [ ] Override field-select changes original-value display
  - [ ] Submit override → flash success → row reflects new value
  - [ ] Otevřít panel → opens FloatingPanel for the reservation

- [ ] **Breakdown card**
  - [ ] Three rows for airbnb/booking/other (only if count > 0)
  - [ ] Column semantics correct (income white, deduction muted with −, payout highlighted)
  - [ ] Collapse-toggle works; state persists across page reloads (sessionStorage)

- [ ] **DPH summary**
  - [ ] Renders ONLY when client.platce_dph=1
  - [ ] vat_output = vat_rentero_fee + vat_room_prep
  - [ ] vat_input excludes legacy NULL-rate expenses
  - [ ] Saldo color flips (ok/refund vs neg/owed)

- [ ] **Expenses card**
  - [ ] Header meta shows count + total + DPH (if platce_dph)
  - [ ] "+ Přidat výdaj" opens calculator-form
  - [ ] Calculator: type gross 121 → blur → net=100, dph=21 fills automatically
  - [ ] Switch rate to 12% → recompute (net=108.04, dph=12.96 for gross=121)
  - [ ] Switch rate to 0% → dph=0, net=gross
  - [ ] Submit → flash + redirect; row appears in table under correct category
  - [ ] Pencil button on a row → form prefills, action switches to /edit, scroll-to
  - [ ] Trash button shows confirm dialog; OK → row removed
  - [ ] Legacy rows (NULL net/dph/rate) render with "—" in net/dph cells
  - [ ] Footer totals match

- [ ] **Override history**
  - [ ] Card collapsed by default
  - [ ] Expand shows audit trail
  - [ ] Aktivní badge on active rows; Obnoveno on reverted
  - [ ] Revert button confirms + submits successfully

- [ ] **Mobile**
  - [ ] At 1024px width: KPI 2-col, DPH-box vertical, calc-form 2-col
  - [ ] At 375px width: KPI 2×2, reservations as card-list, expenses 5 cols

- [ ] **Aurora**
  - [ ] Aurora gradient still visible behind content
  - [ ] Cosmos particle canvas still animates (dark mode)

- [ ] **Step 3: Run full Python test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -10
```

Expected: ALL PASSED. If any fail, debug before proceeding.

- [ ] **Step 4: Final commit (any cleanup) and ready for PR**

```bash
git status
# If anything uncommitted: review and commit, e.g.:
git add -A && git commit -m "chore: smoke-test cleanup"
```

- [ ] **Step 5: Open PR back to main (or follow your usual flow)**

```bash
git push -u origin claude/happy-boyd-d935b9
gh pr create --title "Property page redesign — Phase 1" --body "$(cat <<'EOF'
## Summary
- Visual redesign of /property/{slug}/{year}/{month} per mock at ~/Downloads/315n/property/
- New DPH summary card (only for plátce DPH)
- Calculator-strip add/edit form for expenses with three-field consistency validation
- Inline reservation override form via Úprava button
- Mobile breakpoints (≤1100, ≤640)
- Disabled stubs for Phase 2/3 buttons (Vyloučit, ←/→ Přesunout)

## Changes
- 11 partials (8 new, 5 rewritten, 1 restyled)
- New backend: expenses_validation.py, 4 web_support helpers, 6 summary fields
- Schema migration: 3 new columns on expenses, 6 default categories seeded

## Test plan
- [x] Pytest suite passes
- [x] Manual smoke per docs/superpowers/plans/2026-04-26-property-page-redesign-phase1.md Task 24
- [ ] Reviewer manual smoke after merge

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

This plan covers Phase 1 of the spec. Phases 2 (Vyloučit) and Phase 3 (Přesunout) are not in this plan and need their own when scheduled — both depend on Phase 1 having shipped first.

**Spec coverage check** — every spec section maps to a task:
- §3 Phases → Tasks 1-24 (Phase 1); Phase 2/3 deferred
- §4.1 Composition → Task 22
- §4.2 Partial catalogue → Tasks 9-20
- §4.3 Tokens → Task 8
- §4.4 Class scoping (`.page .badge`) → Task 9 step 2 instructions
- §5.1 KPI variants → Task 11
- §5.2 Status mapping + reservation row → Tasks 4 (helper) + 15 (template) + 14 (detail)
- §5.3 Breakdown → Task 16
- §5.4 DPH summary → Task 17
- §5.5 Expenses + form → Tasks 18 + 19
- §5.6 Override history → Task 20
- §5.7 Notify stack → Task 12
- §5.8 Generation progress → Task 13
- §5.9 Mobile → Task 23
- §6.1 Summary changes → Task 5
- §6.2 web_support helpers → Task 4
- §6.3 expenses_validation → Task 3
- §6.4 Schema migration → Tasks 1 + 2
- §6.5 Route context → Task 7
- §6.6 Operations validation → Task 7
- §7 JS architecture → Task 21
- §8 What doesn't change → enforced by avoiding mods to those files
- §10 Testing → unit tests in Tasks 1-6; integration smoke in Task 24

**Type consistency check** — all field names match across tasks:
- `_mock_status`, `_mock_status_class`, `_mock_status_label` (set in Task 4, read in Tasks 14/15)
- `vat_output_czk`, `vat_input_czk`, `vat_balance_czk`, `vat_input_count`, `expenses_net_total_czk`, `zisk_czk` (added in Task 5, read in Tasks 11/17/19)
- `counts.{all_rows,active,nights,adjustments,excluded,moved,problems}` (Task 4 → Task 15)
- `prev_month_target.{year,month}`, `next_month_target.{year,month}` (Task 7 → Task 14)
- `cat_dot_class` (Task 7 → Task 19)
- `totals.{payout,ubyt}` (Task 7 → Task 15)
- `data-status`, `data-code`, `data-payout`, `data-ubyt`, `data-nights` (Task 15 → Task 21 JS)
- `data-section-toggle`/`data-section-body` (multiple templates → Task 21 JS)
- `data-action="expense-form-toggle|expense-edit|override-toggle|open-panel"` (templates → Task 21 JS)
- `data-calc-net|dph|gross`, `data-calc-rate-tabs|input` (Task 18 → Task 21)

No undefined references. Plan is self-consistent.


