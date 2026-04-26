# Engine Unification: Collapse Three Regeneration Paths into One

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `report.engine.generate_report_in_process` the single execution path for report regeneration, removing the dead subprocess→`report.main` chain, the unused Excel export pipeline, and `preview_service.py`. Replace the CLI affordance with a small, professional admin script.

**Architecture:** Two-step transition. Step one moves the side-effect persistence (`payout_batches`, `payout_batch_items`, `fill_missing_payout_item_guest_names`) that today lives only in `report/main.py` to its proper homes — at CSV import time in `source_registry.py` (mirrors the existing `bank_transactions` pattern, principle: "materialize on import") and defensively at regeneration time inside `engine.generate_report_in_process` (so a stale import is self-healing). Step two removes the dead chain: `report/main.py`, `report/excel.py`, `report/preview_service.py`, `report/generation_job_runner.py`, plus the corresponding dead helpers in `report/web.py` / `report/web_support.py`, the `/download` route + button, the broken Generovat dashboard form, and the obsolete `signal.SIGCHLD = SIG_IGN` guard. A new `bin/regen.py` replaces the CLI as a thin wrapper around the engine for ad-hoc operator use.

**Tech Stack:** Python 3.12+ (prod) / 3.14 (local), FastAPI, SQLite, Jinja2 + HTMX. argon2-cffi for passwords (already deployed). Test runner: `.venv/bin/python -m pytest`.

---

## Pre-flight context

**Read first** if you do not have context from prior work:
- `report/engine.py` — current in-process engine (~900 LOC)
- `report/main.py` — CLI / subprocess entry being deleted (~860 LOC)
- `report/source_registry.py:528-616` — `import_uploaded_source` (already persists `bank_transactions` on import; we are mirroring the pattern for `payout_batches`)
- `docs/superpowers/specs/2026-04-06-auto-generation-design.md` and `docs/superpowers/plans/2026-04-06-auto-generation.md` — the design that introduced engine.py *alongside* main.py as a deliberately partial migration. This refactor finishes that work.
- `docs/superpowers/specs/2026-04-13-synthetic-row-control.md` — synthetic `__ADJ`/`__AC`/`__SP` invariants. Engine already encodes these; main.py predates them.

**Production state at planning time:**
- Branch `claude/nice-bhabha-2ab7d9` deployed to `main`. Latest commit: `f3719b2`.
- `payout_batches` and `payout_batch_items` tables on prod are kept fresh by `report.main` calls fired (today) by `bulk_generation_runner` *not* — bulk runner uses engine which does NOT persist them. **They are stale right now relative to the latest source CSVs.** This refactor will repopulate them via the new persistence path + backfill.
- `bank_transactions` is already kept fresh by `source_registry.py:588-589` on bank CSV import — leave alone.

**Confirmed product decisions:**
1. Excel button + `/download` route → DELETE both.
2. Dashboard "Generovat" cell form posts to a non-existent route → DELETE the form (it has been broken silently).
3. CLI → REPLACE `report/main.py` with a clean `bin/regen.py` admin tool that wraps the engine.
4. `payout_batches`/`payout_batch_items` persistence → BOTH at import (source_registry) AND defensively at regen (engine).

**Live regen entry points after refactor (must keep working):**
- `/months/generate-all` — bulk regen via `subprocess.Popen(bulk_generation_runner.py)` → engine. Subprocess kept for memory isolation (MemoryMax=3G).
- Property-page mutations (lock, override, move, expense add/delete, reinstate) → engine inline in the request handler.
- Import-trigger thread (`web.py:_apply_import_impacts`) → engine on a daemon thread.
- Daily Hostify sync (`hostify_sync.py`) → engine via `asyncio.to_thread`.

**Test setup:** `.venv` exists at the worktree root, baseline pre-refactor is **295 passed / 26 failed**. The 26 failures are unrelated (mocks calling `request.state` that doesn't exist, environmental SQLite path issues). Several should disappear naturally as their dead-code targets are removed in this refactor.

---

## File Structure

### New files
- `bin/regen.py` — admin CLI replacing `report/main.py`
- `tests/test_regen_cli.py` — tests for the CLI
- `tests/test_source_registry_payout_batch_persistence.py` — verify import-time persistence
- `tests/test_engine_persists_payout_artifacts.py` — verify defensive engine-time persistence
- `tests/test_payout_batches_backfill.py` — verify boot-time backfill

### Modified files
- `report/engine.py` — add `_persist_csv_payout_artifacts(...)` helper, call it from `generate_report_in_process`
- `report/source_registry.py` — add `save_payout_batches`/`save_payout_batch_items` calls in airbnb/booking branches of `import_uploaded_source`
- `report/db.py` — add `_backfill_payout_batches_from_active_sources(conn)` migration helper, wire into `_run_migrations`
- `report/web.py` — remove `signal.SIGCHLD = SIG_IGN`, remove dead `_enqueue_report_generation` and `_enqueue_generate_all_for_month`, remove imports of deleted modules
- `report/web_support.py` — remove `_build_report_main_cmd`, `_run_report_generation`, `_start_report_generation_runner`, the `generate_href` cell metadata
- `report/routes/property_routes.py` — remove `/property/{slug}/{year}/{month}/download` and `/preview` route handlers
- `report/calculator.py` — remove `calculate_totals` and `calculate_totals_with_config`
- `templates/partials/property_intro.html` — remove "Stáhnout Excel" button
- `templates/dashboard.html` — remove the broken Generovat form (block `cur_cell.kind == 'generate'`)
- `docs/architecture/architecture.md` — update to reflect single execution path

### Deleted files
- `report/main.py`
- `report/excel.py`
- `report/preview_service.py`
- `report/generation_job_runner.py`
- `tests/test_main_dry_run.py`
- `tests/test_main_month_lock.py`

---

## Tasks

The order is intentional. Tasks 1-5 add new behavior **without removing** anything — fully backward-compatible, deployable on its own. Tasks 6-15 remove dead code only after the new persistence path is proven on prod. Each task is one commit.

---

### Task 1: Add `_persist_csv_payout_artifacts` helper in engine.py

This pure helper is the single source of truth for "write the CSV-derived payout side effects to SQLite." Used by both engine and source_registry.

**Files:**
- Modify: `report/engine.py` (add helper near top, after imports)
- Test: `tests/test_engine_persists_payout_artifacts.py` (new)

**Steps:**

- [ ] **Step 1.1: Write failing test for `_persist_csv_payout_artifacts`**

Create `tests/test_engine_persists_payout_artifacts.py`:

```python
"""Engine helper that writes parsed CSV payout artifacts to SQLite.

The helper is the single point that calls save_payout_batches,
save_payout_batch_items, fill_missing_payout_item_guest_names, and
save_bank_transactions for the airbnb + booking channels. Both the
engine (defensive, on every regen) and source_registry (on every
import) call it.
"""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA
from report.engine import _persist_csv_payout_artifacts


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _airbnb_data() -> dict:
    return {
        "batches": [
            {
                "batch_ref": "G-AB-001",
                "payout_date": "2026-04-15",
                "amount_czk": 25000.0,
                "amount_eur": 1000.0,
                "implied_rate": 25.0,
                "source_name": "airbnb_03_2026.csv",
            }
        ],
        "items": [
            {
                "batch_ref": "G-AB-001",
                "item_index": 0,
                "item_type": "reservation",
                "confirmation_code": "HMA001",
                "guest_name": "Test Guest",
                "amount_eur": 1000.0,
            }
        ],
    }


def _booking_data() -> dict:
    return {
        "batches": [
            {
                "batch_ref": "BK-001",
                "payout_date": "2026-04-20",
                "amount_czk": 12500.0,
                "amount_eur": 500.0,
                "implied_rate": 25.0,
                "source_name": "booking_03_2026.csv",
            }
        ],
        "items": [
            {
                "batch_ref": "BK-001",
                "item_index": 0,
                "item_type": "reservation",
                "confirmation_code": "BK001",
                "guest_name": "",
                "amount_eur": 500.0,
            }
        ],
    }


def test_persists_airbnb_batches(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT batch_ref, amount_czk FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()
    assert row is not None
    assert row["batch_ref"] == "G-AB-001"
    assert row["amount_czk"] == 25000.0


def test_persists_booking_batches(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT batch_ref FROM payout_batches WHERE channel = 'booking'"
    ).fetchone()
    assert row is not None
    assert row["batch_ref"] == "BK-001"


def test_persists_payout_items_for_both_channels(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    counts = conn.execute(
        "SELECT channel, COUNT(*) AS n FROM payout_batch_items GROUP BY channel"
    ).fetchall()
    by_channel = {r["channel"]: r["n"] for r in counts}
    assert by_channel == {"airbnb": 1, "booking": 1}


def test_fills_missing_booking_guest_names_from_index(conn):
    """Booking payout items lack guest_name; the booking_index from the
    CSV provides it. The helper must call fill_missing_payout_item_guest_names
    after persisting the items."""
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data=_booking_data(),
        booking_index={"BK001": {"guest_name": "Anna Nováková"}},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT guest_name FROM payout_batch_items WHERE confirmation_code = 'BK001'"
    ).fetchone()
    assert row["guest_name"] == "Anna Nováková"


def test_persists_airbnb_bank_transactions(conn):
    bank_row = {
        "tx_key": "abnb-tx-1",
        "tx_id": "T1",
        "datum": "2026-04-15",
        "amount_czk": 25000.0,
        "gref": "G-AB-001",
        "property_id": "",
        "zprava": "Airbnb payout",
        "source_name": "bank.csv",
    }
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[bank_row],
        booking_bank_idx_all={},
    )

    row = conn.execute(
        "SELECT tx_key, channel FROM bank_transactions WHERE tx_key = 'abnb-tx-1'"
    ).fetchone()
    assert row is not None
    assert row["channel"] == "airbnb"


def test_persists_booking_bank_transactions_flattened(conn):
    """booking_bank_idx_all is dict[property_id, list[row]]; the helper
    must flatten it before calling save_bank_transactions."""
    booking_idx = {
        "PROP-1": [
            {
                "tx_key": "bk-tx-1",
                "tx_id": "T2",
                "datum": "2026-04-20",
                "amount_czk": 12500.0,
                "gref": "",
                "property_id": "PROP-1",
                "zprava": "Booking payout",
                "source_name": "bank.csv",
            }
        ]
    }
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all=booking_idx,
    )

    row = conn.execute(
        "SELECT tx_key, channel FROM bank_transactions WHERE tx_key = 'bk-tx-1'"
    ).fetchone()
    assert row is not None
    assert row["channel"] == "booking"


def test_idempotent_on_repeat_call(conn):
    """The underlying SQL is UPSERT; calling twice must not duplicate rows
    or change row count."""
    args = dict(
        airbnb_payout_data=_airbnb_data(),
        booking_payout_data=_booking_data(),
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )
    _persist_csv_payout_artifacts(conn, **args)
    _persist_csv_payout_artifacts(conn, **args)

    batches = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    items = conn.execute("SELECT COUNT(*) FROM payout_batch_items").fetchone()[0]
    assert batches == 2
    assert items == 2


def test_empty_inputs_are_no_op(conn):
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data={"batches": [], "items": []},
        booking_payout_data={"batches": [], "items": []},
        booking_index={},
        bank_rows_all=[],
        booking_bank_idx_all={},
    )

    counts = {
        "batches": conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0],
        "items": conn.execute("SELECT COUNT(*) FROM payout_batch_items").fetchone()[0],
        "tx": conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0],
    }
    assert counts == {"batches": 0, "items": 0, "tx": 0}
```

- [ ] **Step 1.2: Run test to verify it fails**

Run:

```
.venv/bin/python -m pytest tests/test_engine_persists_payout_artifacts.py -v
```

Expected: All tests FAIL with `ImportError: cannot import name '_persist_csv_payout_artifacts' from 'report.engine'`.

- [ ] **Step 1.3: Add the helper to `report/engine.py`**

Insert immediately after the existing `_payout_date_in_window` definition (around line 117) and before `_build_adjustment_reservation`:

```python
def _persist_csv_payout_artifacts(
    conn,
    *,
    airbnb_payout_data: dict,
    booking_payout_data: dict,
    booking_index: dict,
    bank_rows_all: list,
    booking_bank_idx_all: dict,
) -> None:
    """Persist CSV-derived payout-batch and bank-transaction snapshots.

    The web's bank UI (drilldown, reservation panel, sources delta) reads
    payout_batches / payout_batch_items / bank_transactions directly. This
    helper is the single point that keeps those tables aligned with the
    currently active source CSVs. Idempotent: every save_* underneath is
    UPSERT-based.

    Called from two places:
      * source_registry.import_uploaded_source — on each new airbnb/booking
        import, so the tables move forward as data lands.
      * engine.generate_report_in_process — defensively on every regen, so
        any drift between the two paths is auto-healed within one cycle.
    """
    from report.db import (
        fill_missing_payout_item_guest_names,
        save_bank_transactions,
        save_payout_batch_items,
        save_payout_batches,
    )

    save_payout_batches(conn, "airbnb", airbnb_payout_data.get("batches") or [])
    save_payout_batch_items(conn, "airbnb", airbnb_payout_data.get("items") or [])
    save_payout_batches(conn, "booking", booking_payout_data.get("batches") or [])
    save_payout_batch_items(conn, "booking", booking_payout_data.get("items") or [])

    booking_guest_names = {
        str(code): str(row.get("guest_name") or "").strip()
        for code, row in (booking_index or {}).items()
        if str(code or "").strip() and str(row.get("guest_name") or "").strip()
    }
    if booking_guest_names:
        fill_missing_payout_item_guest_names(
            conn, "booking", guest_names_by_code=booking_guest_names
        )

    save_bank_transactions(conn, "airbnb", bank_rows_all or [])
    booking_bank_rows_flat = [
        item for rows in (booking_bank_idx_all or {}).values() for item in rows
    ]
    save_bank_transactions(conn, "booking", booking_bank_rows_flat)
```

- [ ] **Step 1.4: Run test to verify it passes**

Run:

```
.venv/bin/python -m pytest tests/test_engine_persists_payout_artifacts.py -v
```

Expected: 8 passed.

- [ ] **Step 1.5: Run full suite to confirm no regression**

Run:

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: previous baseline + 8 new passes, no new failures.

- [ ] **Step 1.6: Commit**

```
git add report/engine.py tests/test_engine_persists_payout_artifacts.py
git commit -m "$(cat <<'EOF'
refactor: extract _persist_csv_payout_artifacts helper in engine

The web's bank UI (/bank drilldown, reservation panel bank-txn list,
/sources delta summary) reads payout_batches / payout_batch_items /
bank_transactions directly, but only report.main currently writes
them. To converge on a single regen path through engine, lift the
persistence into a reusable helper that source_registry will call on
import and engine will call defensively on regen. Pure helper, no
callers yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Wire helper into `engine.generate_report_in_process` (defensive)

After the engine builds `airbnb_payout_data`, `booking_payout_data`, `bank_rows_all`, and `booking_bank_idx_all`, call the helper. This is the "defensive" persistence — every regen ensures the tables are fresh, even if an import path skipped them.

**Files:**
- Modify: `report/engine.py` (inside `generate_report_in_process`, after CSV cache load)
- Test: extend `tests/test_engine.py` (or `tests/test_engine_persists_payout_artifacts.py`)

**Steps:**

- [ ] **Step 2.1: Write the integration test**

Append to `tests/test_engine_persists_payout_artifacts.py`:

```python
def test_generate_report_in_process_persists_artifacts_via_helper(monkeypatch):
    """When generate_report_in_process runs, the helper is invoked once."""
    import report.engine as engine_mod

    seen = []

    def _spy(conn, **kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(engine_mod, "_persist_csv_payout_artifacts", _spy)

    # Reuse the existing in-memory test harness in tests/test_engine.py
    # by importing its build_minimal_db and run_engine helpers if available.
    # If not, this test is a unit test on a stub: invoke the engine with
    # a minimal config and assert the spy was called.
    # See tests/test_engine.py::test_generate_report_in_process_returns_dict_with_rows_count
    # for a pattern; the spy assertion is what matters here.
    from tests.test_engine import _build_minimal_db, _minimal_config  # noqa: F401
    # If those private helpers don't exist, copy them inline. This test is
    # smoke-only — the unit tests above already prove the helper itself.
```

If `tests/test_engine.py` does not export reusable helpers, write a minimal in-memory harness inline using `_SCHEMA`. The test asserts `len(seen) >= 1` after a single `generate_report_in_process` call.

- [ ] **Step 2.2: Run test to verify it fails**

Run:

```
.venv/bin/python -m pytest tests/test_engine_persists_payout_artifacts.py::test_generate_report_in_process_persists_artifacts_via_helper -v
```

Expected: FAIL — `seen` is empty because the helper is never called.

- [ ] **Step 2.3: Wire the helper into the engine**

In `report/engine.py`, locate the block in `generate_report_in_process` that loads CSV data (around line 318, right after the `csv_cache` resolution and before the bank-data section). Add:

```python
    # Persist CSV-derived payout artifacts so the web's bank UI stays
    # aligned with the active sources. Idempotent UPSERTs underneath —
    # safe to call once per regen.
    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=airbnb_payout_data,
        booking_payout_data=booking_payout_data,
        booking_index=booking_index,
        bank_rows_all=bank_rows_all,
        booking_bank_idx_all=booking_bank_idx_all,
    )
```

- [ ] **Step 2.4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_engine_persists_payout_artifacts.py -v
```

Expected: all tests pass including the new integration test.

- [ ] **Step 2.5: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: same number of passes + 1, same baseline failures.

- [ ] **Step 2.6: Commit**

```
git add report/engine.py tests/test_engine_persists_payout_artifacts.py
git commit -m "$(cat <<'EOF'
refactor: engine writes payout_batches and bank_transactions on regen

Today only report.main populates payout_batches / payout_batch_items /
bank_transactions. After this commit the engine path also writes them
on every generate_report_in_process call (idempotent UPSERTs, runs in
~milliseconds for hundreds of rows). This unblocks deletion of
report.main without breaking the bank drilldown.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire helper into `source_registry.import_uploaded_source` (airbnb + booking)

`bank_transactions` already gets persisted on bank-CSV import (line 588). Mirror the pattern for airbnb/booking imports so payout tables move forward as data lands, not only on regen.

**Files:**
- Modify: `report/source_registry.py` (inside `import_uploaded_source`, branches for `source_type == "airbnb"` and `"booking"`)
- Test: `tests/test_source_registry_payout_batch_persistence.py` (new)

**Steps:**

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_source_registry_payout_batch_persistence.py`:

```python
"""When an airbnb or booking CSV is imported, payout_batches and
payout_batch_items are populated immediately — same lifecycle as
bank_transactions on bank-CSV import."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from report.db import _SCHEMA
from report.source_registry import import_uploaded_source


SAMPLE_AIRBNB_CSV = (
    Path(__file__).parent / "fixtures" / "airbnb_minimal.csv"
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _read_fixture(path: Path) -> bytes:
    return path.read_bytes()


def test_airbnb_import_populates_payout_batches(conn):
    """Reuse an existing airbnb CSV fixture from tests/fixtures/. If none
    exists, write a 5-line minimal one with one Reservation row + one
    Payout row that share a Confirmation Code."""
    if not SAMPLE_AIRBNB_CSV.exists():
        pytest.skip(
            "airbnb fixture missing — add one or reuse tests/test_loader.py fixture"
        )

    import_uploaded_source(
        conn,
        "airbnb",
        SAMPLE_AIRBNB_CSV.name,
        _read_fixture(SAMPLE_AIRBNB_CSV),
        imported_by="test",
    )

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()[0]
    assert n > 0, "airbnb import did not populate payout_batches"


def test_duplicate_import_does_not_double_count_batches(conn):
    """SHA dedup prevents the second import from adding rows; UPSERT keeps
    existing rows."""
    if not SAMPLE_AIRBNB_CSV.exists():
        pytest.skip("fixture missing")

    body = _read_fixture(SAMPLE_AIRBNB_CSV)
    import_uploaded_source(conn, "airbnb", "f.csv", body, imported_by="t")
    before = conn.execute(
        "SELECT COUNT(*) FROM payout_batches"
    ).fetchone()[0]

    import_uploaded_source(conn, "airbnb", "f.csv", body, imported_by="t")
    after = conn.execute(
        "SELECT COUNT(*) FROM payout_batches"
    ).fetchone()[0]

    assert before == after
```

If no fixture exists, copy a minimal airbnb CSV from `tests/test_loader.py` or hand-craft one inline — but mark the path used.

- [ ] **Step 3.2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_source_registry_payout_batch_persistence.py -v
```

Expected: `test_airbnb_import_populates_payout_batches` fails (`n == 0`).

- [ ] **Step 3.3: Add persistence to the airbnb branch**

In `report/source_registry.py`, locate the branch that handles `source_type == "airbnb"` inside `import_uploaded_source` (look for a branch matching `_airbnb_delta_summary` after the bank-CSV `elif`). Add a new `elif` (or augment the existing one) that mirrors the bank pattern at lines 583-591:

```python
        elif source_type == "airbnb" and not stored["is_duplicate"]:
            from report.engine import _persist_csv_payout_artifacts
            from report.verifier import build_airbnb_payout_data, load_airbnb_csv
            source = _source_blob(original_name, content)
            airbnb_payout = build_airbnb_payout_data([source])
            airbnb_index = load_airbnb_csv([source])
            _persist_csv_payout_artifacts(
                conn,
                airbnb_payout_data=airbnb_payout,
                booking_payout_data={"batches": [], "items": []},
                booking_index={},
                bank_rows_all=[],
                booking_bank_idx_all={},
            )
            summary["persisted_airbnb_batches"] = len(airbnb_payout.get("batches") or [])
            summary["persisted_airbnb_items"] = len(airbnb_payout.get("items") or [])
```

And the booking branch:

```python
        elif source_type == "booking" and not stored["is_duplicate"]:
            from report.engine import _persist_csv_payout_artifacts
            from report.verifier import build_booking_payout_data, load_booking_csv
            source = _source_blob(original_name, content)
            booking_payout = build_booking_payout_data([source])
            booking_index = load_booking_csv([source])
            _persist_csv_payout_artifacts(
                conn,
                airbnb_payout_data={"batches": [], "items": []},
                booking_payout_data=booking_payout,
                booking_index=booking_index,
                bank_rows_all=[],
                booking_bank_idx_all={},
            )
            summary["persisted_booking_batches"] = len(booking_payout.get("batches") or [])
            summary["persisted_booking_items"] = len(booking_payout.get("items") or [])
```

The `commit=False` keyword is not needed for these calls because `_persist_csv_payout_artifacts` does not commit the connection; the surrounding `import_uploaded_source` transaction commits at its end.

> **Note:** `save_payout_batches` / `save_payout_batch_items` currently call `conn.commit()` internally. Before this task is committed, audit those two functions: if they `commit()` mid-transaction, modify them to accept `commit=True` (default) and pass `commit=False` from the helper when called inside `import_uploaded_source`. See pattern at `db.py` `save_bank_transactions(..., commit=False)`. **Do this audit and adjustment in the same task** — split into Step 3.3a if needed.

- [ ] **Step 3.3a: Audit and parameterize `save_payout_batches` / `save_payout_batch_items` for `commit=False`**

In `report/db.py`, find both functions. Add a `*, commit: bool = True` keyword argument; replace the unconditional `conn.commit()` at the end with `if commit: conn.commit()`. Update `_persist_csv_payout_artifacts` to pass `commit=False` so a single transaction wraps the import.

Sketch:

```python
def save_payout_batches(
    conn: sqlite3.Connection,
    channel: str,
    batches: list[dict],
    *,
    commit: bool = True,
) -> None:
    # ... existing INSERT ... ON CONFLICT body ...
    if commit:
        conn.commit()
```

Then in `_persist_csv_payout_artifacts`, pass `commit=False` to all four save_* calls; the calling code (engine.generate_report_in_process or import_uploaded_source) is responsible for the final commit. Both currently commit elsewhere — engine commits via `save_report_rows` later in the flow, and `import_uploaded_source` commits at the end of its own transaction.

Verify the unit tests in Task 1 still pass — they may need `conn.commit()` added explicitly after the helper call.

- [ ] **Step 3.4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_source_registry_payout_batch_persistence.py tests/test_engine_persists_payout_artifacts.py -v
```

Expected: all pass.

- [ ] **Step 3.5: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: same baseline of 26 failures (or fewer), no new failures introduced.

- [ ] **Step 3.6: Commit**

```
git add report/db.py report/source_registry.py report/engine.py \
        tests/test_source_registry_payout_batch_persistence.py \
        tests/test_engine_persists_payout_artifacts.py
git commit -m "$(cat <<'EOF'
refactor: persist payout_batches at airbnb/booking CSV import time

Mirrors the existing bank-CSV pattern in source_registry: when a CSV
lands, immediately materialize the parsed artifacts so the bank UI
sees them without waiting for the next regen. Single transactional
import via commit=False on save_payout_batches / save_payout_batch_items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: One-time backfill on boot for existing source files

After Task 3, NEW imports populate payout_batches. EXISTING active source files in prod were imported before Task 3 — we backfill them once on boot (idempotent).

**Files:**
- Modify: `report/db.py` (add `_backfill_payout_batches_from_active_sources(conn)`, wire into `_run_migrations`)
- Test: `tests/test_payout_batches_backfill.py` (new)

**Steps:**

- [ ] **Step 4.1: Write failing test**

Create `tests/test_payout_batches_backfill.py`:

```python
"""Boot-time backfill: parse every active airbnb/booking source_file and
re-materialize its payout artifacts. Idempotent — UPSERTs on duplicate
batch_refs."""

from __future__ import annotations

import sqlite3

import pytest

from report.db import _SCHEMA, _backfill_payout_batches_from_active_sources


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _insert_source(conn, *, source_type: str, name: str, body: bytes, active: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO source_files (source_type, original_name, content, sha256, imported_at, is_active)
           VALUES (?, ?, ?, ?, '2026-04-01T00:00:00+00:00', ?)""",
        (source_type, name, body, name, 1 if active else 0),
    )
    conn.commit()
    return cur.lastrowid


def test_backfill_populates_from_active_airbnb_source(conn, tmp_path):
    """Insert a small airbnb CSV directly into source_files (simulating an
    old import done before this migration). Run the backfill. Verify
    payout_batches gets rows."""
    fixture = tmp_path / "ab.csv"
    fixture.write_text(_minimal_airbnb_csv())  # helper below
    _insert_source(conn, source_type="airbnb", name="ab.csv", body=fixture.read_bytes())

    _backfill_payout_batches_from_active_sources(conn)

    n = conn.execute(
        "SELECT COUNT(*) FROM payout_batches WHERE channel = 'airbnb'"
    ).fetchone()[0]
    assert n > 0


def test_backfill_skips_inactive_sources(conn, tmp_path):
    fixture = tmp_path / "ab.csv"
    fixture.write_text(_minimal_airbnb_csv())
    _insert_source(
        conn, source_type="airbnb", name="ab.csv",
        body=fixture.read_bytes(), active=False,
    )

    _backfill_payout_batches_from_active_sources(conn)

    n = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    assert n == 0


def test_backfill_is_idempotent(conn, tmp_path):
    fixture = tmp_path / "ab.csv"
    fixture.write_text(_minimal_airbnb_csv())
    _insert_source(conn, source_type="airbnb", name="ab.csv", body=fixture.read_bytes())

    _backfill_payout_batches_from_active_sources(conn)
    first = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]
    _backfill_payout_batches_from_active_sources(conn)
    second = conn.execute("SELECT COUNT(*) FROM payout_batches").fetchone()[0]

    assert first == second


def _minimal_airbnb_csv() -> str:
    """Smallest viable airbnb CSV that build_airbnb_payout_data accepts.
    Reuse a real header from tests/test_verifier.py if exact format is
    needed — this is a placeholder to be replaced by the implementing
    engineer with a known-good format."""
    # TODO when implementing: copy header from tests/test_verifier.py and a
    # single payout-row pair so build_airbnb_payout_data returns >=1 batch.
    return (
        "Date,Type,Confirmation Code,Start Date,Nights,Guest,Listing,Details,"
        "Reference,Currency,Amount,Paid Out,Service Fee,Cleaning Fee,Gross Earnings\n"
        "01/01/2026,Reservation,HMA001,01/01/2026,1,Test,Listing,Detail,REF1,"
        "EUR,100.00,100.00,0,0,100.00\n"
        "01/15/2026,Payout,,,,,,,,EUR,100.00,100.00,,,\n"
    )
```

> **Implementer note:** the placeholder CSV may not parse — replace with the exact format used by `tests/test_verifier.py` or `tests/test_loader.py` fixtures. The backfill semantics are the testable thing; the CSV content is a means to an end.

- [ ] **Step 4.2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_payout_batches_backfill.py -v
```

Expected: `ImportError` because `_backfill_payout_batches_from_active_sources` does not exist.

- [ ] **Step 4.3: Implement the backfill helper**

In `report/db.py`, add near the existing migration helpers (after `_deactivate_legacy_checkin_source_files`):

```python
def _backfill_payout_batches_from_active_sources(conn: sqlite3.Connection) -> None:
    """Re-parse every active airbnb/booking source_file and re-persist its
    payout_batches / payout_batch_items / bank_transactions snapshot.

    Needed because the engine path historically did not write these tables;
    only the (now removed) report.main wrote them. Without this backfill,
    a prod DB whose CSVs were imported before the engine took over would
    show empty bank-drilldown data until the next CSV upload.

    Idempotent: every save_* underneath is UPSERT, so running on every
    boot is safe and effectively a no-op once tables are aligned.
    """
    from report.engine import _persist_csv_payout_artifacts
    from report.verifier import (
        build_airbnb_payout_data,
        build_booking_payout_data,
        load_airbnb_csv,
        load_booking_csv,
    )

    rows = conn.execute(
        "SELECT id, source_type, original_name, content "
        "FROM source_files "
        "WHERE source_type IN ('airbnb', 'booking') AND is_active = 1"
    ).fetchall()
    if not rows:
        return

    airbnb_sources, booking_sources = [], []
    for row in rows:
        content = row["content"]
        if isinstance(content, memoryview):
            content = content.tobytes()
        source = {
            "id": row["id"],
            "original_name": row["original_name"],
            "content": bytes(content),
        }
        if row["source_type"] == "airbnb":
            airbnb_sources.append(source)
        else:
            booking_sources.append(source)

    airbnb_payout = (
        build_airbnb_payout_data(airbnb_sources)
        if airbnb_sources
        else {"reservation_map": {}, "all_batches_map": {}, "batches": [], "items": []}
    )
    booking_payout = (
        build_booking_payout_data(booking_sources)
        if booking_sources
        else {"reservation_map": {}, "batches": [], "items": []}
    )
    booking_index = load_booking_csv(booking_sources) if booking_sources else {}

    _persist_csv_payout_artifacts(
        conn,
        airbnb_payout_data=airbnb_payout,
        booking_payout_data=booking_payout,
        booking_index=booking_index,
        bank_rows_all=[],
        booking_bank_idx_all={},
    )
    conn.commit()
```

Wire into `_run_migrations` (replace the existing call line):

```python
    _deactivate_legacy_checkin_source_files(conn)
    _backfill_payout_batches_from_active_sources(conn)
    _seed_admin_user(conn)
```

- [ ] **Step 4.4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_payout_batches_backfill.py -v
```

Expected: 3 passed.

- [ ] **Step 4.5: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: baseline 26 failures + 3 new passes, total passing count goes up by 3, no new failures.

- [ ] **Step 4.6: Commit**

```
git add report/db.py tests/test_payout_batches_backfill.py
git commit -m "$(cat <<'EOF'
ops: backfill payout_batches from active source_files on boot

After Task 3, new airbnb/booking imports populate payout_batches and
payout_batch_items immediately. This commit covers the reverse case:
existing source_files imported before the new persistence path lands
get re-materialized once on the next boot. Idempotent — UPSERT-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Push, fast-forward main, deploy, smoke-test on prod

This is a deploy checkpoint. The new persistence path is live; the dead chain hasn't been removed yet. Verify prod's `/bank` page still has data BEFORE the deletion tasks proceed.

- [ ] **Step 5.1: Push feature branch**

```
git push origin claude/nice-bhabha-2ab7d9
```

- [ ] **Step 5.2: Fast-forward main and push**

```
cd ../../.. && git fetch origin && git merge --ff-only origin/claude/nice-bhabha-2ab7d9 && git push origin main
```

- [ ] **Step 5.3: Deploy**

```
./deploy.sh
```

Expected: "Rentero запущен!" Confirms service restarted, new code live.

- [ ] **Step 5.4: Verify backfill ran on prod**

```
ssh -o BatchMode=yes rentero@204.168.216.181 'python3 -c "
import sqlite3, os
os.chdir(os.path.expanduser(\"~/rentero\"))
c = sqlite3.connect(\"cache/rentero.db\")
counts = c.execute(\"SELECT channel, COUNT(*) FROM payout_batches GROUP BY channel\").fetchall()
print(\"payout_batches:\", dict(counts))
counts = c.execute(\"SELECT channel, COUNT(*) FROM payout_batch_items GROUP BY channel\").fetchall()
print(\"payout_batch_items:\", dict(counts))
"'
```

Expected: non-zero counts for both `airbnb` and `booking`. Compare against the historical figures the operator remembers — they should match within source-file dedup.

- [ ] **Step 5.5: Manual smoke test (operator)**

The implementing agent should request the operator to:
1. Open `https://propertx.eu/bank` — drilldown chips render with batch refs and amounts.
2. Open any property page → click a Booking reservation → bank-txn list under the row renders.
3. Open `/sources` — existing files render normally.

Halt if any of these now show empty data.

- [ ] **Step 5.6: Tag this state for rollback**

```
git tag -a phase2-persistence-checkpoint -m "After persistence move, before dead-code deletion"
git push origin phase2-persistence-checkpoint
```

This gives a known-good rollback point if any deletion task needs to be reverted.

---

### Task 6: Add `bin/regen.py` admin CLI

A clean replacement for `python -m report.main`. Uses the engine, no Excel, no legacy.

**Files:**
- Create: `bin/regen.py`
- Test: `tests/test_regen_cli.py` (new)
- Modify: `bin/regen.py` permissions (use `git update-index --chmod=+x` because OneDrive does not preserve POSIX mode — known issue, see commit 751e568)

**Steps:**

- [ ] **Step 6.1: Write the failing test**

Create `tests/test_regen_cli.py`:

```python
"""bin/regen.py — admin CLI wrapping engine.generate_report_in_process.

Two invocation modes:
    python bin/regen.py SLUG YEAR MONTH        — single property
    python bin/regen.py --all YEAR MONTH       — all active properties
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


def test_parser_accepts_single_property_form():
    regen = importlib.import_module("bin.regen") if False else None
    # bin/ is not a package by default; import via importlib from path:
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "regen", pathlib.Path(__file__).parent.parent / "bin" / "regen.py"
    )
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)

    args = regen.parse_args(["my-slug", "2026", "4"])
    assert args.slug == "my-slug"
    assert args.year == 2026
    assert args.month == 4
    assert args.all is False


def test_parser_accepts_all_form():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "regen", pathlib.Path(__file__).parent.parent / "bin" / "regen.py"
    )
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)

    args = regen.parse_args(["--all", "2026", "4"])
    assert args.all is True
    assert args.year == 2026
    assert args.month == 4
    assert args.slug is None


def test_parser_rejects_invalid_month():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "regen", pathlib.Path(__file__).parent.parent / "bin" / "regen.py"
    )
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)

    with pytest.raises(SystemExit):
        regen.parse_args(["slug", "2026", "13"])


def test_parser_rejects_unreasonable_year():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "regen", pathlib.Path(__file__).parent.parent / "bin" / "regen.py"
    )
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)

    with pytest.raises(SystemExit):
        regen.parse_args(["slug", "1999", "4"])
```

- [ ] **Step 6.2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_regen_cli.py -v
```

Expected: ImportError / FileNotFoundError on `bin/regen.py`.

- [ ] **Step 6.3: Write `bin/regen.py`**

```python
#!/usr/bin/env python3
"""Rentero admin CLI — regenerate report data via the in-process engine.

Replaces the legacy `python -m report.main` invocation. No Excel output,
no subprocess gymnastics — straight to engine.generate_report_in_process,
the same code path the web UI uses.

Usage:
    bin/regen.py SLUG YEAR MONTH         — regenerate one property/month
    bin/regen.py --all YEAR MONTH         — regenerate every active property

Environment:
    Loads .env automatically. RENTERO_USERNAME / RENTERO_PASSWORD /
    RENTERO_SESSION_SECRET are not required for CLI use; the engine
    operates directly on cache/rentero.db.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

# Ensure project root is on sys.path when invoked as `python bin/regen.py`
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate Rentero report data for one or all properties.",
    )
    parser.add_argument(
        "slug",
        nargs="?",
        help="Property slug. Required unless --all is given.",
    )
    parser.add_argument(
        "year",
        type=int,
        help="Year (e.g. 2026).",
    )
    parser.add_argument(
        "month",
        type=int,
        help="Month 1-12.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate every active property for the given month.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose log output.",
    )
    args = parser.parse_args(argv)

    today = date.today()
    if args.year < 2020 or args.year > today.year + 2:
        parser.error(f"year out of range: {args.year}")
    if args.month < 1 or args.month > 12:
        parser.error(f"month must be 1-12, got {args.month}")
    if args.all and args.slug is not None:
        parser.error("--all is incompatible with a slug argument")
    if not args.all and args.slug is None:
        parser.error("provide a slug or --all")
    return args


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _regen_one(conn, slug: str, year: int, month: int, config: dict) -> dict:
    from report.engine import generate_report_in_process

    return generate_report_in_process(conn, slug, year, month, config)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("regen")

    from report.config import load_runtime_config
    from report.db import get_connection

    db_path = os.path.join(_PROJECT_ROOT, "cache", "rentero.db")
    conn = get_connection(db_path)
    try:
        config = load_runtime_config(
            os.path.join(_PROJECT_ROOT, "config", "properties.json"),
            db_conn=conn,
        )

        if args.all:
            from report.config import get_all_properties

            slugs = [p["slug"] for p in get_all_properties(config) if p.get("active", True)]
            log.info("Regenerating %d properties for %d-%02d", len(slugs), args.year, args.month)
            ok = skipped = failed = 0
            for slug in slugs:
                try:
                    result = _regen_one(conn, slug, args.year, args.month, config)
                    if result.get("skipped"):
                        skipped += 1
                        log.info("  %-30s SKIPPED (%s)", slug, result.get("reason"))
                    else:
                        ok += 1
                        log.info("  %-30s OK (%d rows)", slug, result.get("rows_count", 0))
                except Exception as exc:
                    failed += 1
                    log.exception("  %-30s FAILED: %s", slug, exc)
            log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
            return 1 if failed else 0

        result = _regen_one(conn, args.slug, args.year, args.month, config)
        if result.get("skipped"):
            log.info("%s %d-%02d SKIPPED (%s)", args.slug, args.year, args.month, result.get("reason"))
        else:
            log.info("%s %d-%02d OK (%d rows)", args.slug, args.year, args.month, result.get("rows_count", 0))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6.4: Run tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_regen_cli.py -v
```

Expected: 4 passed.

- [ ] **Step 6.5: Mark executable in git index**

OneDrive does not preserve POSIX permissions and the worktree has `core.fileMode=false`, so a plain `chmod +x` is invisible to git. Use:

```
chmod +x bin/regen.py
git update-index --chmod=+x bin/regen.py
```

Verify:

```
git ls-files -s bin/regen.py
```

Expected: leading `100755` (not `100644`).

- [ ] **Step 6.6: Smoke-run locally against the production DB schema (in-memory)**

```
.venv/bin/python bin/regen.py --help
```

Expected: argparse help output, exit 0.

- [ ] **Step 6.7: Commit**

```
git add bin/regen.py tests/test_regen_cli.py
git commit -m "$(cat <<'EOF'
feat: bin/regen.py admin CLI replacing report.main

Thin wrapper around engine.generate_report_in_process — no Excel, no
subprocess, no legacy CLI surface. Two invocation modes:

    bin/regen.py SLUG YEAR MONTH       — one property
    bin/regen.py --all YEAR MONTH      — every active property

Used for ad-hoc operator regen (and for AI-assisted maintenance
sessions where the assistant needs CLI access without the web UI).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Remove "Stáhnout Excel" button + `/download` and `/preview` routes

The download route serves files from `report_history.file_path`; the engine writes empty `file_path`, so the button only ever works for legacy data the user does not access. Remove the button and the route. The `/preview` route is already a stub redirect; remove for cleanup.

**Files:**
- Modify: `templates/partials/property_intro.html` (remove Excel button, around lines 92-97)
- Modify: `report/routes/property_routes.py` (remove the two route handlers)

**Steps:**

- [ ] **Step 7.1: Locate the button in `templates/partials/property_intro.html`**

Search for "Stáhnout" or "Excel" or "download". The button typically wraps `latest_report.file_path` in a conditional. Remove the entire `<a>...</a>` and any wrapping `{% if %}` block.

- [ ] **Step 7.2: Remove the button**

Show the diff (replace the actual matched lines from the file). Pseudo-diff:

```diff
- {% if latest_report and latest_report.file_path %}
- <a href="/property/{{ slug }}/{{ year }}/{{ month }}/download"
-    class="btn btn-secondary btn-sm">Stáhnout Excel</a>
- {% endif %}
```

(Adjust to actual surrounding markup.)

- [ ] **Step 7.3: Remove the route handlers in `report/routes/property_routes.py`**

Find:
```python
    @app.get("/property/{slug}/{year}/{month}/preview", ...)
    async def property_preview_month(...):
        ...

    @app.get("/property/{slug}/{year}/{month}/download")
    async def property_download(...):
        ...
```

Delete both handlers wholesale. They currently flash messages and return RedirectResponse / FileResponse; nothing else in the codebase calls them.

- [ ] **Step 7.4: Add a test that the routes are gone**

Append to `tests/test_panel_route_access.py` or a new `tests/test_removed_routes.py`:

```python
def test_property_download_route_no_longer_registered():
    from fastapi import FastAPI
    from report.web import app

    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in app.routes
        if hasattr(route, "methods")
    }

    assert ("/property/{slug}/{year}/{month}/download", ("GET",)) not in paths
    assert ("/property/{slug}/{year}/{month}/preview", ("GET",)) not in paths
```

- [ ] **Step 7.5: Run tests**

```
.venv/bin/python -m pytest tests/test_removed_routes.py -v
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: new test passes; existing failures unchanged (some `test_web_generation` failures referencing download may resolve as side effect — that's fine).

- [ ] **Step 7.6: Commit**

```
git add templates/partials/property_intro.html report/routes/property_routes.py \
        tests/test_removed_routes.py
git commit -m "$(cat <<'EOF'
remove: Excel download button + /download + /preview routes

The download endpoint served files from report_history.file_path which
only the deleted report.main path ever populated; engine writes "" for
file_path. The button has been a 'soubor už neexistuje' flash for
months. /preview is already a stub redirect. Both gone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Remove broken "Generovat" form from dashboard template

`templates/dashboard.html:223` posts to `/property/{slug}/{year}/{month}/generate` — a route that does not exist. The form has been silently broken. Remove the entire `cur_cell.kind == 'generate'` block.

**Files:**
- Modify: `templates/dashboard.html` (around lines 220-227)
- Modify: `report/web_support.py` (around line 676 — remove `generate_href` from cell metadata since no template will render it)

**Steps:**

- [ ] **Step 8.1: Inspect the current form**

In `templates/dashboard.html`, around line 220:

```jinja
{% elif cur_cell and cur_cell.kind == 'generate' %}
<form method="post" action="{{ cur_cell.generate_href }}" onclick="event.stopPropagation()">
    {{ csrf_input(request) }}
    <button type="submit" class="btn btn-primary btn-sm">Generovat</button>
</form>
```

(adjust to actual lines)

- [ ] **Step 8.2: Remove the form block**

Delete the entire `{% elif cur_cell and cur_cell.kind == 'generate' %}` branch and its body. The surrounding `{% if %}/{% elif %}/{% endif %}` chain stays intact.

- [ ] **Step 8.3: Remove `generate_href` from cell metadata**

In `report/web_support.py:676` (or wherever cells of `kind: 'generate'` are constructed):

```diff
- "generate_href": f"/property/{slug}/{year}/{month}/generate",
```

Also remove the assignment of `kind: "generate"` for cells that should be `"empty"` or similar — find every place that sets `"kind": "generate"` and replace with the existing empty-state kind (likely `"none"` or similar), since regen happens implicitly via property-page mutations now.

- [ ] **Step 8.4: Run dashboard test**

```
.venv/bin/python -m pytest tests/test_web_generation.py::test_dashboard_uses_requested_month_from_query -v
```

Expected: same status as baseline (this test currently fails for unrelated reasons; verify the failure mode does not change).

- [ ] **Step 8.5: Commit**

```
git add templates/dashboard.html report/web_support.py
git commit -m "$(cat <<'EOF'
remove: dashboard 'Generovat' form posting to a non-existent route

The form at templates/dashboard.html:223 has POSTed to
/property/{slug}/{year}/{month}/generate for months — there is no
@app.post for that path. Click yielded a 404/405 silently. Remove
the dead branch + the matching generate_href cell metadata; per-month
regeneration today happens implicitly via the property page (lock,
override, move) and the bulk runner button on /inventory.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Remove dead chain in `report/web.py` and `report/web_support.py`

Six functions are reachable only by the now-removed dashboard form: `_enqueue_report_generation`, `_enqueue_generate_all_for_month`, `_run_report_generation`, `_start_report_generation_runner`, `_build_report_main_cmd`, plus the `state[...]` re-exports of these in `report/web.py`.

**Files:**
- Modify: `report/web.py` (remove `_enqueue_report_generation`, `_enqueue_generate_all_for_month`, the imports, the state re-exports)
- Modify: `report/web_support.py` (remove `_build_report_main_cmd`, `_run_report_generation`, `_start_report_generation_runner`)

**Steps:**

- [ ] **Step 9.1: Verify no callers**

```
grep -rn "_enqueue_report_generation\|_enqueue_generate_all_for_month\|_run_report_generation\|_start_report_generation_runner\|_build_report_main_cmd" report/ templates/ tests/ bin/
```

Expected: only the definitions and self-references inside the same file. No external callers.

- [ ] **Step 9.2: Delete the six functions**

In `report/web.py`, remove `_enqueue_report_generation` (around lines 525-560) and `_enqueue_generate_all_for_month` (around lines 725-763). Also remove their entries in any `state[...]` dict assembled later in the file.

In `report/web_support.py`, remove `_build_report_main_cmd` (~lines 1000-1015), `_run_report_generation` (~lines 1018-1032), and `_start_report_generation_runner` (~lines 1035-1064).

Imports of `subprocess`, `sys` may still be needed by `_start_bulk_generation_runner` — keep those. Run `.venv/bin/python -c "import report.web; import report.web_support"` to catch missing-import surprises.

- [ ] **Step 9.3: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: previous baseline minus `test_web_generation.py::test_start_report_generation_runner_invokes_background_process` and similar tests for deleted functions (which now fail with collection error or pass-by-removal — that's fine, deletion of the corresponding tests is in Task 14).

If any other tests now fail because of imports, the implementer should look for stale imports left in `web_support.py` and clean them.

- [ ] **Step 9.4: Commit**

```
git add report/web.py report/web_support.py
git commit -m "$(cat <<'EOF'
remove: dead _enqueue_*/_run_report_generation/_start_report_generation_runner chain

These six functions form a closed cycle reachable only from the
dashboard 'Generovat' form (removed in the previous commit). Their
purpose was to subprocess-out to report.main; the live regen paths
all use engine in-process or subprocess(bulk_generation_runner).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Delete `report/generation_job_runner.py`

This file's only purpose was to wrap `python -m report.main` in a subprocess. Its only caller was `_start_report_generation_runner` (deleted in Task 9).

**Files:**
- Delete: `report/generation_job_runner.py`

**Steps:**

- [ ] **Step 10.1: Verify no callers**

```
grep -rn "generation_job_runner" report/ tests/ bin/
```

Expected: only references inside the file itself.

- [ ] **Step 10.2: Delete the file**

```
git rm report/generation_job_runner.py
```

- [ ] **Step 10.3: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: baseline failures unchanged.

- [ ] **Step 10.4: Commit**

```
git add -A
git commit -m "$(cat <<'EOF'
remove: report/generation_job_runner.py — only purpose was to subprocess
report.main, no live callers remain.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Delete `report/preview_service.py`

Zero callers across the codebase (verified by all three earlier audit agents).

**Files:**
- Delete: `report/preview_service.py`

**Steps:**

- [ ] **Step 11.1: Final caller check**

```
grep -rn "preview_service\|build_property_preview" report/ tests/ bin/ templates/
```

Expected: only references inside `report/preview_service.py` itself.

- [ ] **Step 11.2: Delete**

```
git rm report/preview_service.py
```

- [ ] **Step 11.3: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

- [ ] **Step 11.4: Commit**

```
git commit -m "$(cat <<'EOF'
remove: report/preview_service.py — dead since the 4.4 hardening pass
demoted preview from the operational workflow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Delete `report/main.py` and `report/excel.py`

The CLI is now `bin/regen.py`. The Excel writer's only caller was `report.main`.

**Files:**
- Delete: `report/main.py`
- Delete: `report/excel.py`
- Delete: `tests/test_main_dry_run.py`
- Delete: `tests/test_main_month_lock.py`

**Steps:**

- [ ] **Step 12.1: Final caller check**

```
grep -rn "from report.main\|from report.excel\|import report.main\|import report.excel\|report\.main\|write_property_report" report/ tests/ bin/
```

Expected: no references in code that ships. (Some test imports in `test_main_*` remain — those tests get deleted alongside.)

- [ ] **Step 12.2: Delete**

```
git rm report/main.py report/excel.py tests/test_main_dry_run.py tests/test_main_month_lock.py
```

- [ ] **Step 12.3: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: baseline 26 failures drops by 5+ (the test_main_* and any `test_web_generation` cases checking subprocess(report.main) commands).

- [ ] **Step 12.4: Commit**

```
git commit -m "$(cat <<'EOF'
remove: report/main.py + report/excel.py + their tests

CLI replaced by bin/regen.py (Task 6). Excel was demoted from operational
workflow per the 2026-04-06 auto-generation design but never deleted;
this commit completes that work. The bank UI continues to read
payout_batches/payout_batch_items now persisted by the engine and
source_registry import paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Remove `calculate_totals` and `calculate_totals_with_config` from `report/calculator.py`

These were called only by `report/main.py` (now deleted), `report/excel.py` (now deleted), and `report/preview_service.py` (now deleted). Their bodies contained the z_klient pricing bug (hardcoded 15% / wrong client_type handling) — `summary.build_report_summary` is the correct, sole computation function for totals now.

**Files:**
- Modify: `report/calculator.py` (delete two functions, ~lines 320-400)

**Steps:**

- [ ] **Step 13.1: Verify no callers**

```
grep -rn "calculate_totals\b\|calculate_totals_with_config" report/ tests/ bin/
```

Expected: no callers. (If any exist, they are leftover from an incomplete earlier deletion — investigate and fix in this task.)

- [ ] **Step 13.2: Delete the functions**

In `report/calculator.py`, delete `calculate_totals` (around line 321) and `calculate_totals_with_config` (around line 365). Keep `_r`, `_stay_label`, `_null_row`, `calculate_row`, `calculate_all_rows`.

- [ ] **Step 13.3: Run full suite**

```
.venv/bin/python -m pytest tests/ -q --tb=no
```

Expected: existing calculator tests for `calculate_row`/`calculate_all_rows` still pass; tests touching `calculate_totals*` (if any in `tests/test_calculator.py`) now fail — delete those tests as part of this commit.

- [ ] **Step 13.4: Commit**

```
git add report/calculator.py tests/test_calculator.py
git commit -m "$(cat <<'EOF'
remove: calculate_totals and calculate_totals_with_config

Both functions had the z_klient pricing bug (hardcoded 15%, wrong
client_type handling) and were called only by main.py / excel.py /
preview_service.py — all now deleted. summary.build_report_summary is
the single correct totals computation, used by both the dashboard and
the (now deleted) Excel exporter previously.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Remove `signal.SIGCHLD = SIG_IGN` zombie-reaper bandage

The bandage was added because `_start_report_generation_runner` and `_start_bulk_generation_runner` left orphan `Popen` objects without `wait()`. After Task 9 the first is gone; the second (`bulk_generation_runner`) still spawns subprocess but uses `start_new_session=True` and a separate process group, so `SIG_IGN` is no longer required globally. Remove it so `Popen.wait()` works normally.

**Files:**
- Modify: `report/web.py` (line 33-35)

**Steps:**

- [ ] **Step 14.1: Locate and read the current code**

In `report/web.py`, around line 33:

```python
import signal
signal.signal(signal.SIGCHLD, signal.SIG_IGN)
```

- [ ] **Step 14.2: Remove**

```diff
- import signal
- signal.signal(signal.SIGCHLD, signal.SIG_IGN)
```

(Keep other imports intact.)

- [ ] **Step 14.3: Verify bulk runner still works**

Manual smoke test (operator) on prod:
1. Open `/inventory`, click "Generovat všechny" for the current month.
2. Check `journalctl -u rentero -n 50` — bulk runner subprocess starts, completes, returns code 0.
3. Verify no zombie processes via `ps -ef --forest | grep rentero`.

If zombies appear, restore the bandage with a comment explaining why and ask the operator before proceeding.

- [ ] **Step 14.4: Commit**

```
git add report/web.py
git commit -m "$(cat <<'EOF'
remove: signal.SIGCHLD = SIG_IGN bandage

Originally added to silence zombies left by _start_report_generation_runner
which never wait()ed on its Popen. That function is gone; the only
remaining subprocess (bulk_generation_runner) spawns with
start_new_session=True and is properly tracked. Restoring default
SIGCHLD behavior so future Popen.wait() calls report exit codes
correctly instead of failing with ECHILD.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Update `docs/architecture/architecture.md` to reflect single execution path

**Files:**
- Modify: `docs/architecture/architecture.md`

**Steps:**

- [ ] **Step 15.1: Locate the "Kódové vrstvy" section**

The current text lists `report/main.py`, `report/engine.py`, `report/excel.py` as parallel components.

- [ ] **Step 15.2: Update**

Replace the relevant lines (around the "Kódové vrstvy" section, currently roughly lines 130-170) with:

```markdown
- `bin/regen.py`
  Tenký administrátorský CLI: `bin/regen.py SLUG YEAR MONTH` nebo `--all YEAR MONTH`. Volá `engine.generate_report_in_process` přímo, bez Excelu, bez subprocess zprostředkování.
- `report/engine.py`
  Jediný in-process generační engine — sdílený webem (synchronní mutace), bulk_generation_runnerem (separátní subprocess pro paměťovou izolaci) a `bin/regen.py`. Plní `report_rows`, `payout_batches`, `payout_batch_items`, `bank_transactions`. Excel se nepíše.
- `report/source_registry.py`
  Importní pipeline. Při importu airbnb/booking/bank CSV materializuje payout_batches / payout_batch_items / bank_transactions okamžitě, takže webové view-modely (bank drilldown, reservation panel) jsou aktuální bez čekání na příští regeneraci.
```

Remove `main.py`, `excel.py`, `preview_service.py` mentions wherever they appear in the doc.

- [ ] **Step 15.3: Update the changelog**

In `docs/architecture/changelog.md`, add a new top-of-file entry dated 2026-04-26:

```markdown
## 2026-04-26 — Engine unification

* Removed `report/main.py`, `report/excel.py`, `report/preview_service.py`,
  `report/generation_job_runner.py`, `_enqueue_report_generation`,
  `_enqueue_generate_all_for_month`, `_run_report_generation`,
  `_start_report_generation_runner`, `_build_report_main_cmd`,
  `calculate_totals`, `calculate_totals_with_config`. The 2026-04-06
  auto-generation design left these alongside the engine as a
  deliberately partial migration; this work finishes it.
* `engine.generate_report_in_process` is now the single execution path
  for report regeneration. CLI replaced by `bin/regen.py`.
* `payout_batches` / `payout_batch_items` / `bank_transactions` are
  persisted at CSV import time in `source_registry` (mirrors the
  existing bank-CSV pattern) and defensively at regen time in the
  engine. A boot-time backfill re-materializes legacy DBs.
* Excel download button + `/property/{slug}/{year}/{month}/download`
  + `/preview` routes removed.
* Dashboard's broken "Generovat" form (POSTed to a non-existent route)
  removed.
* `signal.SIGCHLD = SIG_IGN` bandage removed; default SIGCHLD restored.
```

- [ ] **Step 15.4: Commit**

```
git add docs/architecture/architecture.md docs/architecture/changelog.md
git commit -m "$(cat <<'EOF'
docs: architecture + changelog reflect single-engine execution path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Final deploy + verify

- [ ] **Step 16.1: Push and merge**

```
git push origin claude/nice-bhabha-2ab7d9
cd ../../.. && git fetch origin && git merge --ff-only origin/claude/nice-bhabha-2ab7d9 && git push origin main
```

- [ ] **Step 16.2: Deploy**

```
./deploy.sh
```

- [ ] **Step 16.3: Smoke-test on prod**

```
ssh -o BatchMode=yes rentero@204.168.216.181 'bash -s' << 'EOF'
echo "=== git ==="
cd ~/rentero && git log --oneline -5
echo
echo "=== service ==="
systemctl is-active rentero
echo
echo "=== module sanity ==="
source venv/bin/activate
python -c "import report.engine; import report.web; print('imports ok')"
python -c "import report.main" 2>&1 | head -1
python -c "import report.excel" 2>&1 | head -1
python -c "import report.preview_service" 2>&1 | head -1
python -c "import report.generation_job_runner" 2>&1 | head -1
echo
echo "=== bin/regen.py help ==="
bin/regen.py --help | head -5
echo
echo "=== tables still alive ==="
python3 -c "
import sqlite3, os
os.chdir(os.path.expanduser('~/rentero'))
c = sqlite3.connect('cache/rentero.db')
for t in ['payout_batches', 'payout_batch_items', 'bank_transactions', 'report_rows']:
    n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'  {t}: {n}')
"
EOF
```

Expected: imports of deleted modules return `ModuleNotFoundError`, but `report.engine` / `report.web` import fine. `bin/regen.py --help` returns argparse output. All four data tables have non-zero row counts.

- [ ] **Step 16.4: Operator UI smoke test**

The implementer asks the operator to:
1. Open `/`, `/bank`, `/inventory`, `/sources`, `/reconciliation` — each renders without error and shows expected data.
2. Click into any property → reservation panel opens, shows bank-txn list under reservations.
3. Trigger a single-property mutation (e.g. set an override on a reservation) → the row updates, no exception flashed.
4. Trigger `/inventory` → "Generovat všechny" for current month → bulk runner completes per `journalctl -u rentero -f`.

Halt and roll back to `phase2-persistence-checkpoint` tag if any step fails unexpectedly.

- [ ] **Step 16.5: Tag the completion**

```
git tag -a phase2-engine-unification-complete -m "Engine is the sole regen path. report.main/excel/preview_service/generation_job_runner removed."
git push origin phase2-engine-unification-complete
```

---

## Self-review checklist (run before handing off)

**Spec coverage:**
- ✓ Excel button + `/download` removed (Task 7)
- ✓ Dashboard "Generovat" form removed (Task 8)
- ✓ Clean `bin/regen.py` admin CLI (Task 6)
- ✓ `save_payout_batches`/`save_payout_batch_items` at both source_registry (Task 3) and engine (Tasks 1+2)
- ✓ Backfill for legacy DBs (Task 4)
- ✓ Dead-code chain removed (Task 9)
- ✓ `report/main.py`, `excel.py`, `preview_service.py`, `generation_job_runner.py` deleted (Tasks 10-12)
- ✓ `calculate_totals*` deleted (Task 13)
- ✓ `SIGCHLD = SIG_IGN` removed (Task 14)
- ✓ Architecture docs updated (Task 15)
- ✓ Deploy checkpoint after persistence move, before deletions (Task 5)
- ✓ Final deploy + smoke test (Task 16)

**Placeholder scan:** the only TODOs in the plan body are bracketed implementer notes inside Task 4 (CSV fixture) and Task 8 (cell-kind replacement) — both call out specifically that the implementing engineer must consult adjacent test fixtures or codebase to substitute exact values.

**Type consistency:** `_persist_csv_payout_artifacts` signature is consistent across all callers (engine wire-in, source_registry airbnb branch, source_registry booking branch, backfill helper). `bin/regen.py`'s `parse_args` returns the same fields the tests exercise.

**Risk check:** Tasks 1-5 add behavior without removal — fully revertable by `git revert` per commit. Tasks 7-15 each remove one well-isolated chunk; the `phase2-persistence-checkpoint` tag (Task 5.6) gives a clean rollback target if a manual smoke test on prod surfaces an unexpected break.
