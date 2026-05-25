# Object Profile Versioning (Plan A1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each report object's owner + business state (client_type, rates, active, středisko) month-versioned, so a manual change applies from a chosen month forward (or for one month only) while past months keep their old values.

**Architecture:** A new `report_object_profiles` table stores non-overlapping monthly segments per object (`valid_from_ym`/`valid_to_ym`, `"YYYY-MM"`). One resolver returns the segment covering a given month; the engine, dashboard, and property page resolve per `(slug, year, month)`. The existing `/clients/{slug}/save` route writes a segment with a scope selector ("this month" / "from this month onward"). `report_objects` stays the slug registry; `clients` data is backfilled into profiles.

**Tech Stack:** Python 3.14, SQLite (stdlib `sqlite3`, no ORM), FastAPI, Jinja2, pytest.

**Scope:** This is plan **A1** of the spec `docs/superpowers/specs/2026-05-25-object-profiles-and-recurring-expenses-design.md` (Part A). Follow-up plans: **A2** = `Objekty.tsv` import via Zdroje; **C** = recurring expenses + TSV auto-expenses. A1 delivers the versioning foundation + per-month editing UI on its own.

**Conventions verified in the codebase:**
- Schema lives in the `_SCHEMA` string in `report/db.py` (run via `executescript` on every `get_connection`, so `CREATE TABLE IF NOT EXISTS` covers new and existing DBs). Data backfills go in `_run_migrations()`.
- `get_connection(":memory:")` opens a fully-migrated DB for tests.
- `_now()` format is `"%Y-%m-%d %H:%M:%S"` UTC.
- Existing month-aware alias resolution already uses `valid_from`/`valid_to` (`report/config.py`).

---

### Task 1: `report_object_profiles` schema

**Files:**
- Modify: `report/db.py` (the `_SCHEMA` string, ends at `report/db.py:~700`; add table near the other `report_object_*` tables around line 171)
- Test: `tests/test_object_profiles.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_object_profiles.py
from report.db import get_connection


def test_report_object_profiles_table_exists():
    conn = get_connection(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(report_object_profiles)")}
    assert {"id", "slug", "valid_from_ym", "valid_to_ym", "owner_name",
            "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
            "rentero_commission", "stredisko", "active", "source"} <= cols
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_object_profiles.py::test_report_object_profiles_table_exists -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: report_object_profiles`

- [ ] **Step 3: Add the table to `_SCHEMA`**

In `report/db.py`, inside the `_SCHEMA = """ ... """` string, immediately after the
`report_object_aliases` block (after its `CREATE INDEX ... idx_report_object_aliases_lookup`,
around line 174), insert:

```sql
CREATE TABLE IF NOT EXISTS report_object_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL REFERENCES report_objects(slug) ON DELETE CASCADE,
    valid_from_ym   TEXT,            -- "YYYY-MM"; NULL = open start
    valid_to_ym     TEXT,            -- "YYYY-MM" inclusive; NULL = open-ended
    owner_name      TEXT NOT NULL DEFAULT '',
    ico             TEXT NOT NULL DEFAULT '',
    dic             TEXT NOT NULL DEFAULT '',
    platce_dph      INTEGER NOT NULL DEFAULT 0,
    adresa          TEXT NOT NULL DEFAULT '',
    bank_account    TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    client_type     TEXT NOT NULL DEFAULT 'rentero',
    city_tax_rate   REAL NOT NULL DEFAULT 0,
    balicky_per_person REAL NOT NULL DEFAULT 0,
    vat_rate        REAL NOT NULL DEFAULT 0.21,
    rentero_commission REAL NOT NULL DEFAULT 0.15,
    stredisko       TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    source          TEXT NOT NULL DEFAULT 'ui',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_object_profiles_lookup
    ON report_object_profiles(slug, valid_from_ym);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_object_profiles.py::test_report_object_profiles_table_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add report/db.py tests/test_object_profiles.py
git commit -m "feat(db): report_object_profiles table for month-versioned object state"
```

---

### Task 2: Month helpers + resolution + segment listing

**Files:**
- Create: `report/db_object_profiles.py`
- Test: `tests/test_object_profiles.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_object_profiles.py
from report.db_object_profiles import (
    ym, prev_ym, next_ym, insert_segment, get_object_profile,
    list_object_profile_segments,
)


def test_ym_helpers():
    assert ym(2026, 5) == "2026-05"
    assert prev_ym("2026-01") == "2025-12"
    assert next_ym("2026-12") == "2027-01"


def test_resolution_picks_segment_covering_month():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "owner_name": "Old"})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient", "owner_name": "New"})
    assert get_object_profile(conn, "x", 2026, 3)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 4)["client_type"] == "klient"
    assert get_object_profile(conn, "x", 2026, 5)["client_type"] == "z_klient"
    assert get_object_profile(conn, "x", 2026, 9)["owner_name"] == "New"
    segs = list_object_profile_segments(conn, "x")
    assert [s["valid_from_ym"] for s in segs] == [None, "2026-05"]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_object_profiles.py -k "ym_helpers or resolution" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report.db_object_profiles'`

- [ ] **Step 3: Create `report/db_object_profiles.py`**

```python
"""report/db_object_profiles.py — month-versioned object profile segments.

Effective-dated, non-overlapping monthly segments per report object. Each report
month resolves to exactly one segment. Holds owner identity + business state
(client_type, rates, active, středisko) that change over time. See
docs/superpowers/specs/2026-05-25-object-profiles-and-recurring-expenses-design.md
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# Editable profile fields, in insert order. valid_from_ym/valid_to_ym/slug/source
# are handled separately.
PROFILE_FIELDS = (
    "owner_name", "ico", "dic", "platce_dph", "adresa", "bank_account",
    "email", "phone", "notes",
    "client_type", "city_tax_rate", "balicky_per_person", "vat_rate",
    "rentero_commission", "stredisko", "active",
)

_DEFAULTS = {
    "owner_name": "", "ico": "", "dic": "", "platce_dph": 0, "adresa": "",
    "bank_account": "", "email": "", "phone": "", "notes": "",
    "client_type": "rentero", "city_tax_rate": 0, "balicky_per_person": 0,
    "vat_rate": 0.21, "rentero_commission": 0.15, "stredisko": "", "active": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ym(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def _ym_add(ym_str: str, delta: int) -> str:
    y, m = int(ym_str[:4]), int(ym_str[5:7])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def prev_ym(ym_str: str) -> str:
    return _ym_add(ym_str, -1)


def next_ym(ym_str: str) -> str:
    return _ym_add(ym_str, 1)


def insert_segment(
    conn: sqlite3.Connection,
    slug: str,
    valid_from_ym: str | None,
    valid_to_ym: str | None,
    fields: dict,
    *,
    source: str = "ui",
) -> int:
    now = _now()
    merged = {**_DEFAULTS, **{k: v for k, v in fields.items() if k in PROFILE_FIELDS}}
    cols = ["slug", "valid_from_ym", "valid_to_ym", *PROFILE_FIELDS, "source", "created_at", "updated_at"]
    vals = [slug, valid_from_ym, valid_to_ym, *[merged[f] for f in PROFILE_FIELDS], source, now, now]
    placeholders = ",".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO report_object_profiles ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.commit()
    return int(cur.lastrowid)


def get_object_profile(conn: sqlite3.Connection, slug: str, year: int, month: int) -> dict | None:
    m = ym(year, month)
    row = conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
             AND (valid_from_ym IS NULL OR valid_from_ym <= ?)
             AND (valid_to_ym IS NULL OR valid_to_ym >= ?)
           ORDER BY valid_from_ym DESC
           LIMIT 1""",
        (slug, m, m),
    ).fetchone()
    return dict(row) if row else None


def list_object_profile_segments(conn: sqlite3.Connection, slug: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
           ORDER BY (valid_from_ym IS NOT NULL), valid_from_ym""",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]
```

Note: the `ORDER BY (valid_from_ym IS NOT NULL), valid_from_ym` puts the open-start
(`NULL`) segment first, then ascending by month.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_object_profiles.py -k "ym_helpers or resolution" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add report/db_object_profiles.py tests/test_object_profiles.py
git commit -m "feat(profiles): month helpers, segment insert, resolution, listing"
```

---

### Task 3: Segment edit operations (from-onward, this-month-only, update)

**Files:**
- Modify: `report/db_object_profiles.py`
- Test: `tests/test_object_profiles.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_object_profiles.py
from report.db_object_profiles import (
    set_profile_from_month_onward, set_profile_this_month_only,
    update_profile_segment,
)


def _seed_open(conn, slug="x", **fields):
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES (?,?,?)", (slug, "t", "t"))
    insert_segment(conn, slug, None, None, {"client_type": "klient", "owner_name": "Old", **fields})


def test_from_month_onward_trims_prior_and_carries_forward():
    conn = get_connection(":memory:")
    _seed_open(conn, city_tax_rate=50)
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "New"})
    # Past keeps old; May onward = new owner, city_tax copied forward
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    may = get_object_profile(conn, "x", 2026, 5)
    assert may["owner_name"] == "New" and may["city_tax_rate"] == 50
    assert get_object_profile(conn, "x", 2027, 1)["owner_name"] == "New"
    segs = list_object_profile_segments(conn, "x")
    assert segs[0]["valid_to_ym"] == "2026-04"
    assert segs[1]["valid_from_ym"] == "2026-05" and segs[1]["valid_to_ym"] is None
    conn.close()


def test_from_month_onward_preserves_future_segment():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_from_month_onward(conn, "x", 2026, 8, {"owner_name": "Future"})
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "Mid"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["owner_name"] == "Mid"
    assert get_object_profile(conn, "x", 2026, 7)["owner_name"] == "Mid"
    assert get_object_profile(conn, "x", 2026, 8)["owner_name"] == "Future"  # future not clobbered
    conn.close()


def test_this_month_only_splits_into_three():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_this_month_only(conn, "x", 2026, 5, {"owner_name": "JustMay"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["owner_name"] == "JustMay"
    assert get_object_profile(conn, "x", 2026, 6)["owner_name"] == "Old"  # restored
    conn.close()


def test_no_overlap_invariant_holds():
    conn = get_connection(":memory:")
    _seed_open(conn)
    set_profile_from_month_onward(conn, "x", 2026, 5, {"owner_name": "A"})
    set_profile_this_month_only(conn, "x", 2026, 3, {"owner_name": "B"})
    # Every month Jan..Dec resolves to exactly one segment (no None, no crash)
    for mth in range(1, 13):
        assert get_object_profile(conn, "x", 2026, mth) is not None
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_object_profiles.py -k "from_month_onward or this_month_only or no_overlap" -v`
Expected: FAIL — `ImportError: cannot import name 'set_profile_from_month_onward'`

- [ ] **Step 3: Implement the three operations**

Append to `report/db_object_profiles.py`:

```python
def _covering_segment_row(conn, slug, m):
    return conn.execute(
        """SELECT * FROM report_object_profiles
           WHERE slug = ?
             AND (valid_from_ym IS NULL OR valid_from_ym <= ?)
             AND (valid_to_ym IS NULL OR valid_to_ym >= ?)
           ORDER BY valid_from_ym DESC LIMIT 1""",
        (slug, m, m),
    ).fetchone()


def _next_segment_start_after(conn, slug, m):
    row = conn.execute(
        """SELECT MIN(valid_from_ym) AS b FROM report_object_profiles
           WHERE slug = ? AND valid_from_ym IS NOT NULL AND valid_from_ym > ?""",
        (slug, m),
    ).fetchone()
    return row["b"] if row else None


def _merged_fields(base_row: dict | None, changes: dict) -> dict:
    base = {f: (base_row[f] if base_row is not None else _DEFAULTS[f]) for f in PROFILE_FIELDS}
    base.update({k: v for k, v in changes.items() if k in PROFILE_FIELDS})
    return base


def set_profile_from_month_onward(conn, slug: str, year: int, month: int, changes: dict, *, source: str = "ui") -> int:
    m = ym(year, month)
    covering = _covering_segment_row(conn, slug, m)
    cov = dict(covering) if covering else None
    next_start = _next_segment_start_after(conn, slug, m)
    new_to = prev_ym(next_start) if next_start else None

    if cov is not None:
        if (cov["valid_from_ym"] or "") and cov["valid_from_ym"] == m:
            # covering segment starts exactly at M → it gets replaced
            conn.execute("DELETE FROM report_object_profiles WHERE id = ?", (cov["id"],))
        elif cov["valid_from_ym"] is None or cov["valid_from_ym"] < m:
            # trim covering to end at M-1
            conn.execute(
                "UPDATE report_object_profiles SET valid_to_ym = ?, updated_at = ? WHERE id = ?",
                (prev_ym(m), _now(), cov["id"]),
            )
    return insert_segment(conn, slug, m, new_to, _merged_fields(cov, changes), source=source)


def set_profile_this_month_only(conn, slug: str, year: int, month: int, changes: dict, *, source: str = "ui") -> int:
    m = ym(year, month)
    covering = _covering_segment_row(conn, slug, m)
    cov = dict(covering) if covering else None

    # Single-month covering segment already at M → edit in place.
    if cov is not None and cov["valid_from_ym"] == m and cov["valid_to_ym"] == m:
        update_profile_segment(conn, cov["id"], changes)
        return cov["id"]

    orig_from = cov["valid_from_ym"] if cov else None
    orig_to = cov["valid_to_ym"] if cov else None

    if cov is not None:
        if orig_from is None or orig_from < m:
            # left part [orig_from, M-1]
            conn.execute(
                "UPDATE report_object_profiles SET valid_to_ym = ?, updated_at = ? WHERE id = ?",
                (prev_ym(m), _now(), cov["id"]),
            )
        else:
            # covering starts at M (and extends beyond) → no left part
            conn.execute("DELETE FROM report_object_profiles WHERE id = ?", (cov["id"],))
        # right part [M+1, orig_to] with ORIGINAL values
        if orig_to is None or orig_to > m:
            insert_segment(conn, slug, next_ym(m), orig_to, _merged_fields(cov, {}), source=cov["source"])

    return insert_segment(conn, slug, m, m, _merged_fields(cov, changes), source=source)


def update_profile_segment(conn, segment_id: int, changes: dict) -> None:
    sets = [f"{f} = ?" for f in PROFILE_FIELDS if f in changes]
    if not sets:
        return
    params = [changes[f] for f in PROFILE_FIELDS if f in changes]
    params.append(_now())
    params.append(segment_id)
    conn.execute(
        f"UPDATE report_object_profiles SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
        params,
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_object_profiles.py -v`
Expected: PASS (all profile tests)

- [ ] **Step 5: Commit**

```bash
git add report/db_object_profiles.py tests/test_object_profiles.py
git commit -m "feat(profiles): from-month-onward / this-month-only / update segment ops"
```

---

### Task 4: Backfill legacy `report_objects` + `clients` into one open segment

**Files:**
- Modify: `report/db_object_profiles.py` (add `backfill_object_profiles`)
- Modify: `report/db.py` (`_run_migrations`, around line 855 before `_seed_admin_user(conn)`)
- Test: `tests/test_object_profiles.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_object_profiles.py
from report.db_object_profiles import backfill_object_profiles


def test_backfill_collapses_legacy_into_open_segment():
    conn = get_connection(":memory:")
    conn.execute(
        """INSERT INTO report_objects (slug, display_name, client_type, city_tax_rate,
              vat_rate, rentero_commission, balicky_per_person, active, created_at, updated_at)
           VALUES ('a','A','z_klient',50,0.12,0.03,249,1,'t','t')"""
    )
    conn.execute(
        """INSERT INTO clients (property_slug, name, ico, platce_dph, adresa, updated_at)
           VALUES ('a','Owner s.r.o.','123',1,'Praha','t')"""
    )
    conn.commit()
    # Wipe the segment auto-created by migrations to test the backfill directly
    conn.execute("DELETE FROM report_object_profiles WHERE slug='a'")
    n = backfill_object_profiles(conn)
    assert n == 1
    seg = get_object_profile(conn, "a", 2026, 5)
    assert seg["client_type"] == "z_klient"
    assert seg["owner_name"] == "Owner s.r.o."
    assert seg["ico"] == "123" and seg["platce_dph"] == 1
    assert seg["valid_from_ym"] is None and seg["valid_to_ym"] is None
    # Idempotent
    assert backfill_object_profiles(conn) == 0
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_object_profiles.py::test_backfill_collapses_legacy_into_open_segment -v`
Expected: FAIL — `ImportError: cannot import name 'backfill_object_profiles'`

- [ ] **Step 3: Implement backfill and wire it into migrations**

Append to `report/db_object_profiles.py`:

```python
def backfill_object_profiles(conn: sqlite3.Connection) -> int:
    """Create one open segment ([NULL, NULL]) per report_object that has no
    profile segment yet, merging legacy report_objects + clients values.
    Idempotent: skips objects that already have any segment."""
    inserted = 0
    objs = conn.execute("SELECT * FROM report_objects").fetchall()
    for o in objs:
        slug = o["slug"]
        if conn.execute(
            "SELECT 1 FROM report_object_profiles WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone():
            continue
        c = conn.execute(
            "SELECT * FROM clients WHERE property_slug = ?", (slug,)
        ).fetchone()
        c = dict(c) if c else {}
        fields = {
            "owner_name": c.get("name", ""),
            "ico": c.get("ico", ""),
            "dic": c.get("dic", ""),
            "platce_dph": c.get("platce_dph", 0) or 0,
            "adresa": c.get("adresa", ""),
            "bank_account": c.get("bank_account", ""),
            "email": c.get("email", ""),
            "phone": c.get("phone", ""),
            "notes": c.get("notes", ""),
            "client_type": o["client_type"],
            "city_tax_rate": o["city_tax_rate"],
            "balicky_per_person": o["balicky_per_person"],
            "vat_rate": o["vat_rate"],
            "rentero_commission": o["rentero_commission"],
            "stredisko": "",
            "active": o["active"],
        }
        insert_segment(conn, slug, None, None, fields, source="migration")
        inserted += 1
    return inserted
```

In `report/db.py`, add the import near the other migration helpers and call it in
`_run_migrations` right before `_seed_admin_user(conn)` (line ~855):

```python
    from report.db_object_profiles import backfill_object_profiles
    backfill_object_profiles(conn)
    _seed_admin_user(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_object_profiles.py::test_backfill_collapses_legacy_into_open_segment -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add report/db_object_profiles.py report/db.py tests/test_object_profiles.py
git commit -m "feat(profiles): backfill legacy report_objects+clients into open segment"
```

---

### Task 5: `resolve_property_config` — month overlay in config

**Files:**
- Modify: `report/config.py` (add resolver after `get_property_config`, line ~392)
- Test: `tests/test_get_property_config_month_aware.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_get_property_config_month_aware.py
from report.db import get_connection
from report.db_object_profiles import insert_segment
from report.config import resolve_property_config


def test_resolve_overlays_month_profile():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "rentero_commission": 0.15})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient", "rentero_commission": 0.03})
    base = {"properties": {"x": {"channels": {"airbnb": {"listing_names": ["L"]}}, "client_type": "rentero"}}}
    apr = resolve_property_config(conn, "x", 2026, 4, base)
    may = resolve_property_config(conn, "x", 2026, 5, base)
    assert apr["client_type"] == "klient"
    assert may["client_type"] == "z_klient" and may["rentero_commission"] == 0.03
    # Channels / identity preserved from base config
    assert may["channels"]["airbnb"]["listing_names"] == ["L"]
    assert may["slug"] == "x"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_get_property_config_month_aware.py::test_resolve_overlays_month_profile -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_property_config'`

- [ ] **Step 3: Implement the resolver**

In `report/config.py`, add at module level (after the existing `get_property_config`
function, line ~392):

```python
def resolve_property_config(conn, slug: str, year: int, month: int, config: dict) -> dict:
    """Return the property config for a slug as of (year, month).

    Starts from the base config (channels / aliases / identity) and overlays the
    month-resolved profile segment (owner + client_type + rates + active + středisko).
    Falls back to base values when no segment exists (legacy DBs).
    """
    from report.db_object_profiles import get_object_profile

    prop = dict((config.get("properties") or {}).get(slug) or {})
    prop["slug"] = slug
    seg = get_object_profile(conn, slug, year, month)
    if seg:
        for f in ("client_type", "city_tax_rate", "balicky_per_person",
                  "vat_rate", "rentero_commission"):
            if seg.get(f) is not None:
                prop[f] = seg[f]
        prop["active"] = bool(seg.get("active", prop.get("active", True)))
        prop["stredisko"] = seg.get("stredisko") or prop.get("stredisko", "")
        prop["owner"] = {
            "name": seg.get("owner_name", ""), "ico": seg.get("ico", ""),
            "dic": seg.get("dic", ""), "platce_dph": seg.get("platce_dph", 0),
            "adresa": seg.get("adresa", ""), "bank_account": seg.get("bank_account", ""),
            "email": seg.get("email", ""), "phone": seg.get("phone", ""),
            "notes": seg.get("notes", ""),
        }
    return prop
```

The import is function-local to avoid any import cycle between `config` and
`db_object_profiles`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_get_property_config_month_aware.py::test_resolve_overlays_month_profile -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add report/config.py tests/test_get_property_config_month_aware.py
git commit -m "feat(config): resolve_property_config overlays month profile segment"
```

---

### Task 6: Engine uses the month-resolved profile

**Files:**
- Modify: `report/engine.py:453` (right after `prop = props[slug]`)
- Test: `tests/test_get_property_config_month_aware.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_get_property_config_month_aware.py
import report.engine as engine


def test_engine_resolves_month_profile(monkeypatch):
    """Engine must overlay the month segment so client_type/rates match the month."""
    captured = {}
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient"})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient"})
    config = {"properties": {"x": {"channels": {}, "client_type": "rentero"}}}

    # Stub the heavy generation body: capture the resolved prop, then bail early.
    orig_resolve = engine_resolve = __import__("report.config", fromlist=["resolve_property_config"]).resolve_property_config

    def spy(conn_, slug_, y_, m_, cfg_):
        p = orig_resolve(conn_, slug_, y_, m_, cfg_)
        captured[(y_, m_)] = p["client_type"]
        raise RuntimeError("stop-after-resolve")

    monkeypatch.setattr("report.engine.resolve_property_config", spy, raising=False)
    for mth in (4, 5):
        try:
            engine.generate_report_in_process(conn, "x", 2026, mth, config)
        except RuntimeError:
            pass
    assert captured[(2026, 4)] == "klient"
    assert captured[(2026, 5)] == "z_klient"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_get_property_config_month_aware.py::test_engine_resolves_month_profile -v`
Expected: FAIL — `AttributeError: module 'report.engine' has no attribute 'resolve_property_config'` (the spy target does not yet exist / is not imported)

- [ ] **Step 3: Wire the resolver into the engine**

In `report/engine.py`, add to the `from report.config import ...` block (line ~35):

```python
from report.config import get_booking_config, get_hostify_listing_names, get_all_properties, resolve_property_config
```

Then in `generate_report_in_process`, replace line 453 (`prop = props[slug]`) with:

```python
    prop = props[slug]
    prop = resolve_property_config(conn, slug, year, month, config)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_get_property_config_month_aware.py::test_engine_resolves_month_profile -v`
Expected: PASS

- [ ] **Step 5: Run the engine regression tests to ensure no break**

Run: `pytest tests/test_engine.py tests/test_summary.py -q`
Expected: same pass/fail counts as the baseline (no NEW failures vs. the 16-known-failing baseline).

- [ ] **Step 6: Commit**

```bash
git add report/engine.py tests/test_get_property_config_month_aware.py
git commit -m "feat(engine): resolve month-versioned object profile during generation"
```

---

### Task 7: Dashboard aggregation joins the profile segment

**Files:**
- Modify: `report/web_support.py:491-492` (the `LEFT JOIN report_objects o` in `_build_dashboard_maps`)
- Test: `tests/test_dashboard_rentero_fee.py` (extend) or new `tests/test_dashboard_month_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_month_profile.py
import json
from report.db import get_connection
from report.db_object_profiles import insert_segment
from report.web_support import _build_dashboard_maps


def _add_row(conn, slug, y, m, payout, cena):
    data = json.dumps({"payout_czk": payout, "cena_ubytovani_czk": cena,
                        "provize_czk": 0, "verification_status": "MATCHED"})
    conn.execute(
        """INSERT INTO report_rows (slug, year, month, confirmation_code, data, generated_at)
           VALUES (?,?,?,?,?,'t')""",
        (slug, y, m, f"C{y}{m}", data),
    )
    conn.execute(
        """INSERT INTO report_history (slug, year, month, file_path, rows_count, generated_at)
           VALUES (?,?,?,'',1,'t')""", (slug, y, m))
    conn.commit()


def test_dashboard_uses_month_profile_for_fee():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    # klient in April (15% fee on cena), z_klient in May (3% of payout)
    insert_segment(conn, "x", None, "2026-04", {"client_type": "klient", "rentero_commission": 0.15, "vat_rate": 0.0})
    insert_segment(conn, "x", "2026-05", None, {"client_type": "z_klient"})
    _add_row(conn, "x", 2026, 4, payout=1000, cena=800)
    _add_row(conn, "x", 2026, 5, payout=1000, cena=800)
    props = [{"slug": "x"}]
    history, agg, *_rest = _build_dashboard_maps(conn, props, [(2026, 4), (2026, 5)])
    # April: klient fee = cena * 0.15 * (1+0) = 120
    assert round(agg["x"][(2026, 4)]["rentero_fee_sum_czk"]) == 120
    # May: z_klient fee = payout * 0.03 = 30
    assert round(agg["x"][(2026, 5)]["rentero_fee_sum_czk"]) == 30
    conn.close()
```

Note: confirm the return arity/shape of `_build_dashboard_maps` while implementing
(it returns a 4-tuple; the test unpacks `history, agg, *rest`). Confirm the agg dict
key names (`rentero_fee_sum_czk`) against `report/web_support.py:471` before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_month_profile.py::test_dashboard_uses_month_profile_for_fee -v`
Expected: FAIL — fees computed from the single `report_objects` row, not per month (May would use the wrong client_type, or both months identical).

- [ ] **Step 3: Switch the JOIN to the profile segment**

In `report/web_support.py`, in `_build_dashboard_maps`, replace the join (line ~492):

```sql
            FROM report_rows r
            LEFT JOIN report_objects o ON o.slug = r.slug
```

with:

```sql
            FROM report_rows r
            LEFT JOIN report_object_profiles o
                   ON o.slug = r.slug
                  AND (o.valid_from_ym IS NULL OR o.valid_from_ym <= printf('%04d-%02d', r.year, r.month))
                  AND (o.valid_to_ym IS NULL OR o.valid_to_ym >= printf('%04d-%02d', r.year, r.month))
```

The referenced columns (`client_type`, `rentero_commission`, `vat_rate`) exist on
`report_object_profiles`. Segments are non-overlapping, so exactly one matches per row.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_month_profile.py::test_dashboard_uses_month_profile_for_fee -v`
Expected: PASS

- [ ] **Step 5: Run dashboard regression**

Run: `pytest tests/test_dashboard_rentero_fee.py -q`
Expected: no new failures vs. baseline.

- [ ] **Step 6: Commit**

```bash
git add report/web_support.py tests/test_dashboard_month_profile.py
git commit -m "feat(dashboard): aggregate fees via month-versioned profile segment"
```

---

### Task 8: Edit UI — scope selector writes a profile segment + segment history

**Files:**
- Modify: `report/routes/operations.py:233-360` (`client_save`)
- Modify: `templates/client.html` (add scope radio near line 116; segment-history block)
- Modify: `report/web.py` (expose `set_profile_*` + `list_object_profile_segments` to routes `state` dict, and `get_object_profile`)
- Test: `tests/test_object_profiles.py` (route-level via the existing test client pattern) — see note

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_object_profiles.py
from report.db_object_profiles import set_profile_from_month_onward, set_profile_this_month_only


def test_client_save_payload_writes_segment_from_month_onward():
    """The save handler's profile write path: 'from month onward' creates a segment
    starting at the anchor month and leaves earlier months on the old owner."""
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO report_objects (slug, created_at, updated_at) VALUES ('x','t','t')")
    insert_segment(conn, "x", None, None, {"owner_name": "Old", "client_type": "klient"})
    # Simulate handler decision for scope='onward', anchor 2026-05
    set_profile_from_month_onward(conn, "x", 2026, 5,
                                  {"owner_name": "New s.r.o.", "ico": "999", "client_type": "z_klient"})
    assert get_object_profile(conn, "x", 2026, 4)["owner_name"] == "Old"
    assert get_object_profile(conn, "x", 2026, 5)["client_type"] == "z_klient"
    assert get_object_profile(conn, "x", 2026, 5)["ico"] == "999"
    conn.close()
```

(The pure segment-write behavior is the testable core; the HTTP wiring is verified
manually + by the regression suite. If the repo has a FastAPI `TestClient` fixture in
`tests/`, add an HTTP-level test mirroring `tests/test_reservation_action_routes.py`.)

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `pytest tests/test_object_profiles.py::test_client_save_payload_writes_segment_from_month_onward -v`
Expected: PASS (functions exist from Task 3) — this test pins the handler's intended
behavior before wiring the route.

- [ ] **Step 3: Add the scope selector + anchor month to `client.html`**

In `templates/client.html`, replace the "Config effective from" block (lines 114-117):

```html
        <div>
          <label class="form-label">Config effective from</label>
          <input type="date" name="config_effective_from" value="" class="form-input">
        </div>
```

with:

```html
        <div>
          <label class="form-label">Rozsah změny</label>
          <select name="profile_scope" class="form-input">
            <option value="onward">Od tohoto měsíce dál</option>
            <option value="month_only">Jen tento měsíc</option>
          </select>
        </div>
        <div>
          <label class="form-label">Měsíc změny</label>
          <input type="month" name="profile_anchor_ym" value="{{ profile_anchor_ym or '' }}" class="form-input">
        </div>
        <input type="hidden" name="config_effective_from" value="">
```

After the Source Mapping card (after line 126's closing `</div>` for the identity card,
before the Source Mapping card), add a segment-history card:

```html
    {% if profile_segments %}
    <div class="card">
      <div class="card-header"><span class="card-title">Historie profilu</span></div>
      <div style="padding:12px 20px;">
        <table class="t"><thead><tr>
          <th>Od</th><th>Do</th><th>Vlastník</th><th>Typ</th><th>Provize</th>
        </tr></thead><tbody>
        {% for s in profile_segments %}
          <tr>
            <td class="num">{{ s.valid_from_ym or '—' }}</td>
            <td class="num">{{ s.valid_to_ym or '…' }}</td>
            <td>{{ s.owner_name or '—' }}</td>
            <td>{{ s.client_type }}</td>
            <td class="num">{{ "%.0f"|format((s.rentero_commission or 0) * 100) }}%</td>
          </tr>
        {% endfor %}
        </tbody></table>
      </div>
    </div>
    {% endif %}
```

- [ ] **Step 4: Wire `client_save` to write a profile segment**

In `report/web.py`, register the profile helpers in the `state` dict passed to route
modules (find where `state["save_client"]` and `state["get_report_object_aliases"]` are
set, near the operations route wiring) and add:

```python
    from report.db_object_profiles import (
        set_profile_from_month_onward, set_profile_this_month_only,
        list_object_profile_segments, get_object_profile, ym as _profile_ym,
    )
    state["set_profile_from_month_onward"] = set_profile_from_month_onward
    state["set_profile_this_month_only"] = set_profile_this_month_only
    state["list_object_profile_segments"] = list_object_profile_segments
    state["get_object_profile"] = get_object_profile
    state["profile_ym"] = _profile_ym
```

In `report/routes/operations.py`, in `client_save`, add two new `Form` params to the
signature (after `client_type: str = Form("")`, line 259):

```python
        profile_scope: str = Form("onward"),
        profile_anchor_ym: str = Form(""),
```

After the existing `save_client(...)` call (line 270-284) — which we keep for one
release — add the profile-segment write:

```python
        # Resolve anchor month: explicit input, else current month.
        import datetime as _dt
        anchor = (profile_anchor_ym or "").strip()
        if anchor and len(anchor) == 7 and anchor[4] == "-":
            anchor_y, anchor_m = int(anchor[:4]), int(anchor[5:7])
        else:
            today = _dt.date.today()
            anchor_y, anchor_m = today.year, today.month

        profile_changes = {
            "owner_name": name, "ico": ico, "dic": dic,
            "platce_dph": 1 if platce_dph else 0,
            "adresa": adresa or address, "bank_account": bank_account,
            "email": email, "phone": phone, "notes": notes,
            "active": 1 if active else 0,
        }
        if client_type in ("rentero", "klient", "z_klient"):
            profile_changes["client_type"] = client_type
        for fld, raw in (("city_tax_rate", city_tax_rate),
                         ("balicky_per_person", balicky_per_person),
                         ("vat_rate", vat_rate)):
            if raw.strip():
                try:
                    profile_changes[fld] = float(raw.replace(",", ".").strip())
                except ValueError:
                    pass
        if rentero_commission.strip():
            try:
                profile_changes["rentero_commission"] = float(
                    rentero_commission.replace(",", ".").replace("%", "").strip()) / 100
            except ValueError:
                pass

        if profile_scope == "month_only":
            state["set_profile_this_month_only"](conn, slug, anchor_y, anchor_m, profile_changes)
        else:
            state["set_profile_from_month_onward"](conn, slug, anchor_y, anchor_m, profile_changes)
```

The existing `sync_property_to_db` + `_apply_object_config_change_impacts` calls remain;
the impact function already regenerates the slug's open months, and each month now
resolves its own segment, so "from month onward" produces correct per-month output.

Finally, in the GET handler that renders `client.html` (the route that returns
`templates...client.html`), add `profile_segments` and `profile_anchor_ym` to the
template context:

```python
        "profile_segments": state["list_object_profile_segments"](conn, slug),
        "profile_anchor_ym": state["profile_ym"](*__import__("datetime").date.today().timetuple()[:2]),
```

(If the existing GET handler builds its context dict elsewhere, add the two keys there;
locate it by grepping `client.html` in `report/`.)

- [ ] **Step 5: Run the route + profile tests**

Run: `pytest tests/test_object_profiles.py -q`
Expected: PASS

Then smoke-test the page manually:
Run: `python run_web.py` → open `/clients/<slug>` → edit owner, choose "Od tohoto měsíce
dál" + a month → save → verify the "Historie profilu" card shows the new segment and the
previous owner remains on earlier months.

- [ ] **Step 6: Commit**

```bash
git add report/routes/operations.py report/web.py templates/client.html tests/test_object_profiles.py
git commit -m "feat(profiles): edit UI scope selector writes month segment + history view"
```

---

## Self-Review

- **Spec coverage (Part A foundation):** schema (Task 1), non-overlap segment model + 3 ops
  (Tasks 2-3), migration/backfill collapsing `clients`+`report_objects` (Task 4), resolver
  + engine/summary integration (Tasks 5-6), dashboard per-month categorization (Task 7),
  UI scope selector + segment history (Task 8). LOCKED-month safety and range-aware regen
  are covered by the **existing** `_apply_object_config_change_impacts` (reused, documented
  in Task 8). **Deferred to later plans (noted in scope):** `Objekty.tsv` import (A2),
  recurring expenses + TSV auto-expenses (C), `clients` table physical drop (follow-up).
- **Name consistency:** `get_object_profile`, `insert_segment`, `set_profile_from_month_onward`,
  `set_profile_this_month_only`, `update_profile_segment`, `list_object_profile_segments`,
  `backfill_object_profiles`, `resolve_property_config` — used identically across tasks.
- **Open items to confirm during execution (flagged inline, not placeholders):**
  `_build_dashboard_maps` return arity/agg key names (Task 7 Step 1 note); exact GET
  handler that renders `client.html` (Task 8 Step 4 note). Both are "verify then apply the
  shown edit", with the concrete edit provided.

## Post-audit notes (2026-05-25)

- **Dashboard `active` IS now month-resolved (resolved 2026-05-25).** `_resolve_dashboard_profile_overlay`
  now returns the covering segment's `active`, and the dashboard route drops any object the
  profile marks inactive as of the selected month — so an object deactivated from month M
  disappears from the board for M onward (even though base `report_objects.active` stays 1).
  Objects with no covering segment keep their base active (already filtered by
  `get_accessible_properties`). **Residual edge (not handled):** an object that is
  base-inactive but reactivated for a past month *only* in its profile still won't appear,
  because `get_accessible_properties` excludes it before the overlay runs; closing that
  would require making the property fetch itself month-aware (larger change, deferred).
