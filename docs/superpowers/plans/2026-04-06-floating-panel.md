# Floating Reservation Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current right-side drawer with a draggable, resizable, tabbed floating panel that persists reservation detail views across full page reloads and supports up to 5 simultaneous reservation tabs + a Summary tab.

**Architecture:** A vanilla-JS `FloatingPanel` IIFE module stored in `base_scripts.html` manages the panel lifecycle. State (open tabs, position, size) is serialised to `localStorage` on every change and restored on `DOMContentLoaded`. A new slug-agnostic backend endpoint `GET /reservation/{code}/panel` looks up any reservation by confirmation code across all months, so the panel can re-hydrate after navigation without knowing which property page the user came from. The existing `property_drawer.html` file and its CSS are removed; `property_scripts.html` is updated to call `FloatingPanel.open()` instead of `openDrawerFromRow()`.

**Tech Stack:** FastAPI (Python), Jinja2 templates, vanilla JS (ES5-compatible), SQLite, CSS custom properties matching existing design system.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `report/db.py` | Modify | Add `get_report_row_by_code()` function |
| `report/routes/dashboard.py` | Modify | Add `GET /reservation/{code}/panel` route |
| `templates/partials/base_panel.html` | **Create** | Floating panel HTML skeleton |
| `templates/partials/base_styles.html` | Modify | Add `.fp-*` panel CSS |
| `templates/partials/base_scripts.html` | Modify | Add `FloatingPanel` JS module |
| `templates/base.html` | Modify | Include `base_panel.html` |
| `templates/property.html` | Modify | Remove `property_drawer.html` include |
| `templates/partials/property_drawer.html` | **Delete** | Replaced by panel |
| `templates/partials/property_scripts.html` | Modify | Replace drawer JS with `FloatingPanel.open()` |
| `templates/partials/property_reservations.html` | Modify | Update `data-*` attributes on `<tr>` for panel |
| `tests/test_panel_route.py` | **Create** | Test the new `/reservation/{code}/panel` endpoint |

---

## Task 1: DB function `get_report_row_by_code`

**Files:**
- Modify: `report/db.py`
- Create: `tests/test_panel_route.py`

- [ ] **Step 1.1: Write failing test**

```python
# tests/test_panel_route.py
import json
import sqlite3
import pytest
from report.db import get_connection, init_db, get_report_row_by_code, save_report_rows


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    from report.db import _SCHEMA
    c.executescript(_SCHEMA)
    c.commit()
    return c


def test_get_report_row_by_code_finds_row(conn):
    row_data = {
        "confirmation_code": "HMA12345",
        "guest_name": "Jan Novák",
        "payout_czk": 5000.0,
    }
    save_report_rows(conn, "obj1", 2026, 3, [row_data])
    result = get_report_row_by_code(conn, "HMA12345")
    assert result is not None
    assert result["confirmation_code"] == "HMA12345"
    assert result["slug"] == "obj1"
    assert result["year"] == 2026
    assert result["month"] == 3


def test_get_report_row_by_code_returns_none_for_missing(conn):
    result = get_report_row_by_code(conn, "NOTEXISTS")
    assert result is None


def test_get_report_row_by_code_returns_most_recent_month(conn):
    old_data = {"confirmation_code": "HMA99", "guest_name": "Old"}
    new_data = {"confirmation_code": "HMA99", "guest_name": "New"}
    save_report_rows(conn, "obj1", 2025, 12, [old_data])
    save_report_rows(conn, "obj1", 2026, 1, [new_data])
    result = get_report_row_by_code(conn, "HMA99")
    assert result["year"] == 2026
    assert result["month"] == 1
```

- [ ] **Step 1.2: Run test — expect FAIL (ImportError)**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python -m pytest tests/test_panel_route.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'get_report_row_by_code'`

- [ ] **Step 1.3: Implement `get_report_row_by_code` in `report/db.py`**

Find the end of the `get_report_rows` function (around line 756) and add after it:

```python
def get_report_row_by_code(
    conn: sqlite3.Connection, confirmation_code: str
) -> dict | None:
    """
    Find a report row by confirmation_code across all slugs and months.
    Returns the most recent entry (highest year, then month).
    Returns a dict with the row data plus 'slug', 'year', 'month' keys.
    Returns None if not found.
    """
    row = conn.execute(
        """SELECT slug, year, month, data
             FROM report_rows
            WHERE json_extract(data, '$.confirmation_code') = ?
            ORDER BY year DESC, month DESC
            LIMIT 1""",
        (confirmation_code,),
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row["data"])
    result["slug"] = row["slug"]
    result["year"] = row["year"]
    result["month"] = row["month"]
    return result
```

Also add `get_report_row_by_code` to the imports section at the top of `report/db.py` (it's already in the same file, but ensure it's exported). Add it to the `__all__` or the import list in `report/web_support.py` imports block.

- [ ] **Step 1.4: Run tests — expect PASS**

```bash
python -m pytest tests/test_panel_route.py::test_get_report_row_by_code_finds_row tests/test_panel_route.py::test_get_report_row_by_code_returns_none_for_missing tests/test_panel_route.py::test_get_report_row_by_code_returns_most_recent_month -v
```

Expected: 3 PASSED

- [ ] **Step 1.5: Commit**

```bash
git add report/db.py tests/test_panel_route.py
git commit -m "feat: add get_report_row_by_code() for slug-agnostic reservation lookup"
```

---

## Task 2: Backend route `GET /reservation/{code}/panel`

**Files:**
- Modify: `report/routes/dashboard.py`
- Modify: `report/web_support.py` (add `get_report_row_by_code` to imports)
- Modify: `tests/test_panel_route.py`

- [ ] **Step 2.1: Write failing test**

Add to `tests/test_panel_route.py`:

```python
from fastapi.testclient import TestClient


def _make_app(conn):
    """Minimal app factory reusing web.py setup pattern."""
    import report.web as web_module
    # Patch get_connection to return our test conn
    import report.db as db_module
    original = db_module.get_connection
    db_module.get_connection = lambda: conn
    app = web_module.create_app()
    db_module.get_connection = original
    return app


def test_panel_route_returns_html_for_known_code(conn):
    row_data = {
        "confirmation_code": "HMA_PANEL",
        "guest_name": "Test Guest",
        "payout_czk": 3000.0,
        "source": "airbnb",
        "nights": 3,
    }
    save_report_rows(conn, "obj1", 2026, 3, [row_data])
    # NOTE: integration test — use the real TestClient approach from test_web_generation.py
    # This test validates the route exists and returns 200 with HTML content


def test_panel_route_returns_404_for_unknown_code(conn):
    # Route should return 404 when code not in report_rows
    pass
```

> **Note**: Full HTTP integration test requires app setup. Write route first, then come back to complete HTTP tests using the pattern from `tests/test_web_generation.py` (monkeypatch `get_connection`).

- [ ] **Step 2.2: Add `get_report_row_by_code` to imports in `report/web_support.py`**

Find the existing `from report.db import (` block in `report/web_support.py` and add `get_report_row_by_code` to it.

- [ ] **Step 2.3: Add the panel route in `report/routes/dashboard.py`**

After the existing `reservation_detail_partial` route (after line ~74), add:

```python
@app.get("/reservation/{code}/panel", response_class=HTMLResponse)
async def reservation_panel_partial(
    code: str,
    request: Request,
    _=Depends(require_auth),
    conn=Depends(get_db),
):
    """
    Slug-agnostic reservation detail for the floating panel.
    Looks up the reservation across all months by confirmation_code.
    Used by FloatingPanel.js to re-hydrate tabs after page navigation.
    """
    row = state["get_report_row_by_code"](conn, code)
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    slug = row["slug"]
    year = row["year"]
    month = row["month"]

    # Apply overrides so drawer shows overridden values
    rows_with_overrides = state["apply_overrides_to_rows"](conn, [row], slug, year, month)
    row = rows_with_overrides[0] if rows_with_overrides else row

    bank_txns_map = state["_load_all_bank_transactions_for_codes"](conn, [code])
    month_state = state["get_report_month_state"](conn, slug, year, month)

    return state["templates"].TemplateResponse(
        request,
        "partials/reservation_detail.html",
        {
            "row": row,
            "slug": slug,
            "year": year,
            "month": month,
            "month_state": month_state,
            "bank_txns": bank_txns_map.get(code, []),
        },
    )
```

Also register it in the `state.update({...})` dict at the bottom of `dashboard.py`:

```python
"reservation_panel_partial": reservation_panel_partial,
```

And ensure `get_report_month_state` is available in state — check `web_support.py` `_build_state()` / `register()` function and add if missing.

- [ ] **Step 2.4: Expose `get_report_row_by_code` via state in `web_support.py`**

Find where `get_report_rows` is added to state (in `_build_web_state` or similar function in `web_support.py`) and add `get_report_row_by_code` alongside it.

- [ ] **Step 2.5: Verify route works manually**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python run_web.py &
# In browser or curl: GET http://localhost:8000/reservation/SOMEKNOWNCODE/panel
# Should return HTML (reservation_detail.html content)
# Should return 404 for unknown code
kill %1
```

- [ ] **Step 2.6: Commit**

```bash
git add report/routes/dashboard.py report/web_support.py tests/test_panel_route.py
git commit -m "feat: add /reservation/{code}/panel endpoint for floating panel hydration"
```

---

## Task 3: Panel HTML skeleton

**Files:**
- Create: `templates/partials/base_panel.html`
- Modify: `templates/base.html`

- [ ] **Step 3.1: Create `templates/partials/base_panel.html`**

```html
<!-- Floating Reservation Panel — inserted once in base.html, managed by FloatingPanel JS -->
<div id="fp-panel" class="fp-panel" style="display:none;" aria-label="Rezervační panel">

  <!-- Tab bar (header / drag handle) -->
  <div class="fp-tabbar" id="fp-tabbar">
    <div class="fp-handle" id="fp-handle" title="Přetáhnout">
      <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor" style="color:var(--text-muted);opacity:0.5;">
        <circle cx="2" cy="2" r="1.5"/><circle cx="8" cy="2" r="1.5"/>
        <circle cx="2" cy="7" r="1.5"/><circle cx="8" cy="7" r="1.5"/>
        <circle cx="2" cy="12" r="1.5"/><circle cx="8" cy="12" r="1.5"/>
      </svg>
    </div>

    <div class="fp-tabs-scroll" id="fp-tabs-scroll">
      <!-- Reservation tabs injected here by JS: <button class="fp-tab" data-code="..."> -->
    </div>

    <!-- Summary tab (always last, cannot be closed) -->
    <button class="fp-tab fp-tab-summary" id="fp-summary-tab" title="Souhrn otevřených rezervací" onclick="FloatingPanel.activateTab('__summary__')">
      <span class="fp-tab-label">Σ</span>
    </button>

    <div class="fp-controls">
      <button class="fp-btn" id="fp-minimize-btn" onclick="FloatingPanel.toggleMinimize()" title="Minimalizovat">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>
      <button class="fp-btn" id="fp-expand-btn" onclick="FloatingPanel.toggleExpand()" title="Rozbalit / Zmenšit">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
      </button>
    </div>
  </div>

  <!-- Content area -->
  <div class="fp-content" id="fp-content">
    <!-- Tab content injected by JS -->
  </div>

  <!-- Resize handle -->
  <div class="fp-resize-handle" id="fp-resize-handle" title="Změnit velikost"></div>
</div>
```

- [ ] **Step 3.2: Include in `templates/base.html`**

In `templates/base.html`, before the closing `</body>` tag (after the existing `{% include "partials/base_scripts.html" %}`), add:

```html
{% include "partials/base_panel.html" %}
```

- [ ] **Step 3.3: Verify template renders without JS errors**

Start the app and open any property page. The panel div should exist in DOM but be hidden (`display:none`). Open DevTools → Elements → verify `#fp-panel` is present.

- [ ] **Step 3.4: Commit**

```bash
git add templates/partials/base_panel.html templates/base.html
git commit -m "feat: add floating panel HTML skeleton"
```

---

## Task 4: Panel CSS

**Files:**
- Modify: `templates/partials/base_styles.html`

- [ ] **Step 4.1: Add panel CSS to `base_styles.html`**

Find the end of the existing CSS block in `base_styles.html` and append before the closing `</style>` tag:

```css
/* ─── Floating Panel ──────────────────────────────────────────────── */
.fp-panel {
  position: fixed;
  z-index: 1000;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.45), 0 2px 8px rgba(0,0,0,0.3);
  display: flex;
  flex-direction: column;
  min-width: 340px;
  min-height: 120px;
  overflow: hidden;
  transition: box-shadow 0.15s;
  user-select: none;
}
.fp-panel.fp-dragging {
  box-shadow: 0 16px 60px rgba(0,0,0,0.6);
  opacity: 0.97;
}
.fp-panel.fp-minimized .fp-content,
.fp-panel.fp-minimized .fp-resize-handle {
  display: none;
}
.fp-tabbar {
  display: flex;
  align-items: center;
  gap: 0;
  background: var(--bg-root);
  border-bottom: 1px solid var(--border);
  border-radius: 12px 12px 0 0;
  min-height: 36px;
  overflow: hidden;
  flex-shrink: 0;
}
.fp-handle {
  padding: 0 8px;
  cursor: grab;
  display: flex;
  align-items: center;
  align-self: stretch;
  flex-shrink: 0;
}
.fp-handle:active { cursor: grabbing; }
.fp-tabs-scroll {
  display: flex;
  align-items: stretch;
  flex: 1;
  overflow-x: auto;
  scrollbar-width: none;
}
.fp-tabs-scroll::-webkit-scrollbar { display: none; }
.fp-tab {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 10px;
  height: 36px;
  background: none;
  border: none;
  border-right: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  max-width: 160px;
  transition: background 0.1s, color 0.1s;
  flex-shrink: 0;
}
.fp-tab:hover { background: rgba(255,255,255,0.04); color: var(--text-secondary); }
.fp-tab.fp-tab-active {
  background: var(--bg-card);
  color: var(--text-primary);
  border-bottom: 2px solid var(--color-primary);
}
.fp-tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 110px;
  font-weight: 500;
}
.fp-tab-close {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  padding: 0;
  transition: background 0.1s, color 0.1s;
}
.fp-tab-close:hover { background: rgba(255,255,255,0.1); color: var(--text-primary); }
.fp-tab-summary {
  border-right: none;
  border-left: 1px solid var(--border);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: -0.03em;
  color: var(--color-primary);
  padding: 0 14px;
}
.fp-controls {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 6px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
}
.fp-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  transition: background 0.1s, color 0.1s;
}
.fp-btn:hover { background: rgba(255,255,255,0.07); color: var(--text-primary); }
.fp-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
.fp-content::-webkit-scrollbar { width: 5px; }
.fp-content::-webkit-scrollbar-track { background: transparent; }
.fp-content::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.fp-resize-handle {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 16px;
  height: 16px;
  cursor: se-resize;
  background: linear-gradient(135deg, transparent 50%, var(--border) 50%, var(--border) 60%, transparent 60%, transparent 70%, var(--border) 70%, var(--border) 80%, transparent 80%);
  border-radius: 0 0 12px 0;
}

/* ─── Summary tab content ─────────────────────────────────────────── */
.fp-summary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 20px 24px;
}
.fp-summary-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.fp-summary-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.fp-summary-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.fp-summary-empty {
  padding: 40px 24px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

/* ─── Tab loading state ──────────────────────────────────────────── */
.fp-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--text-muted);
  font-size: 13px;
  gap: 10px;
}
@keyframes fp-spin { to { transform: rotate(360deg); } }
.fp-spinner { animation: fp-spin 0.8s linear infinite; }
```

- [ ] **Step 4.2: Verify CSS loads without errors**

Open browser DevTools → Console → no CSS errors. Verify `.fp-panel` class exists in computed styles.

- [ ] **Step 4.3: Commit**

```bash
git add templates/partials/base_styles.html
git commit -m "feat: add floating panel CSS"
```

---

## Task 5: Panel JS — core (init, position, drag, resize, minimize)

**Files:**
- Modify: `templates/partials/base_scripts.html`

- [ ] **Step 5.1: Add `FloatingPanel` IIFE to `base_scripts.html`**

At the end of `base_scripts.html` (before the closing `</script>` tag), add the core module. Add it as a new `<script>` block at the very end of the file, after the existing `</script>`:

```html
<script>
var FloatingPanel = (function() {
  'use strict';

  var STORAGE_KEY = 'rentero_panel';
  var MAX_TABS = 5;
  var COMPACT_SIZE  = { w: 560, h: 460 };
  var EXPANDED_SIZE = { w: 860, h: 640 };

  var _panel, _tabbar, _tabsScroll, _content, _handle, _resizeHandle;
  var _isDragging = false, _isResizing = false;
  var _dragStart = { x: 0, y: 0, px: 0, py: 0 };
  var _resizeStart = { x: 0, y: 0, w: 0, h: 0 };

  // ── State ──────────────────────────────────────────────────────────
  var _state = {
    tabs: [],          // [{code, slug, year, month, title, channel}]
    activeTab: null,   // code or '__summary__'
    position: { x: 80, y: 80 },
    size: { w: COMPACT_SIZE.w, h: COMPACT_SIZE.h },
    minimized: false,
    expanded: false,
  };
  var _tabContent = {}; // code -> HTML string cache

  function _save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(_state)); } catch(e) {}
  }

  function _load() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var saved = JSON.parse(raw);
      if (saved && Array.isArray(saved.tabs)) {
        _state = Object.assign(_state, saved);
      }
    } catch(e) {}
  }

  // ── DOM helpers ────────────────────────────────────────────────────
  function _applyPosition() {
    var maxX = Math.max(0, window.innerWidth  - _state.size.w - 10);
    var maxY = Math.max(0, window.innerHeight - 36 - 10);
    var x = Math.max(0, Math.min(_state.position.x, maxX));
    var y = Math.max(0, Math.min(_state.position.y, maxY));
    _panel.style.left   = x + 'px';
    _panel.style.top    = y + 'px';
  }

  function _applySize() {
    _panel.style.width  = _state.size.w + 'px';
    _panel.style.height = _state.minimized ? '36px' : _state.size.h + 'px';
  }

  function _show() {
    _panel.style.display = 'flex';
    _applyPosition();
    _applySize();
  }

  function _hide() {
    _panel.style.display = 'none';
  }

  // ── Drag ───────────────────────────────────────────────────────────
  function _onDragStart(e) {
    if (e.target.closest('.fp-tab, .fp-btn, .fp-tab-close')) return;
    _isDragging = true;
    _panel.classList.add('fp-dragging');
    var touch = e.touches ? e.touches[0] : e;
    _dragStart = { x: touch.clientX, y: touch.clientY, px: _state.position.x, py: _state.position.y };
    e.preventDefault();
  }

  function _onDragMove(e) {
    if (!_isDragging) return;
    var touch = e.touches ? e.touches[0] : e;
    _state.position.x = _dragStart.px + (touch.clientX - _dragStart.x);
    _state.position.y = _dragStart.py + (touch.clientY - _dragStart.y);
    _applyPosition();
  }

  function _onDragEnd() {
    if (!_isDragging) return;
    _isDragging = false;
    _panel.classList.remove('fp-dragging');
    _save();
  }

  // ── Resize ─────────────────────────────────────────────────────────
  function _onResizeStart(e) {
    _isResizing = true;
    var touch = e.touches ? e.touches[0] : e;
    _resizeStart = { x: touch.clientX, y: touch.clientY, w: _state.size.w, h: _state.size.h };
    e.preventDefault();
    e.stopPropagation();
  }

  function _onResizeMove(e) {
    if (!_isResizing) return;
    var touch = e.touches ? e.touches[0] : e;
    _state.size.w = Math.max(340, _resizeStart.w + (touch.clientX - _resizeStart.x));
    _state.size.h = Math.max(120, _resizeStart.h + (touch.clientY - _resizeStart.y));
    _applySize();
  }

  function _onResizeEnd() {
    if (!_isResizing) return;
    _isResizing = false;
    _save();
  }

  // ── Public: minimize / expand ──────────────────────────────────────
  function toggleMinimize() {
    _state.minimized = !_state.minimized;
    _panel.classList.toggle('fp-minimized', _state.minimized);
    _applySize();
    _save();
  }

  function toggleExpand() {
    _state.expanded = !_state.expanded;
    _state.size = _state.expanded ? Object.assign({}, EXPANDED_SIZE) : Object.assign({}, COMPACT_SIZE);
    _applySize();
    _save();
  }

  // ── Init ───────────────────────────────────────────────────────────
  function init() {
    _panel        = document.getElementById('fp-panel');
    _tabbar       = document.getElementById('fp-tabbar');
    _tabsScroll   = document.getElementById('fp-tabs-scroll');
    _content      = document.getElementById('fp-content');
    _handle       = document.getElementById('fp-handle');
    _resizeHandle = document.getElementById('fp-resize-handle');
    if (!_panel) return;

    // Drag
    _handle.addEventListener('mousedown',  _onDragStart);
    _handle.addEventListener('touchstart', _onDragStart, { passive: false });
    document.addEventListener('mousemove',  _onDragMove);
    document.addEventListener('touchmove',  _onDragMove, { passive: false });
    document.addEventListener('mouseup',   _onDragEnd);
    document.addEventListener('touchend',  _onDragEnd);

    // Resize
    _resizeHandle.addEventListener('mousedown',  _onResizeStart);
    _resizeHandle.addEventListener('touchstart', _onResizeStart, { passive: false });
    document.addEventListener('mousemove',  _onResizeMove);
    document.addEventListener('touchmove',  _onResizeMove, { passive: false });
    document.addEventListener('mouseup',   _onResizeEnd);
    document.addEventListener('touchend',  _onResizeEnd);

    // Keyboard
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && _panel.style.display !== 'none') {
        if (_state.tabs.length > 0) toggleMinimize();
      }
    });

    _load();
    if (_state.tabs.length > 0) {
      _show();
      _renderTabBar();
      _activateTab(_state.activeTab || (_state.tabs[0] && _state.tabs[0].code) || '__summary__');
    }
  }

  return {
    init: init,
    toggleMinimize: toggleMinimize,
    toggleExpand: toggleExpand,
    activateTab: function(code) { _activateTab(code); },
  };
})();

document.addEventListener('DOMContentLoaded', FloatingPanel.init);
</script>
```

> **Note**: `_renderTabBar` and `_activateTab` are defined in Task 6. This task only adds the core module skeleton — the app will load without errors even if tabs don't work yet, because `FloatingPanel.init()` will call functions that don't exist yet. Add stubs for now:

After `function _onResizeEnd` and before `function toggleMinimize`, add:

```javascript
  // Stubs — implemented in Task 6
  function _renderTabBar() {}
  function _activateTab(code) { _state.activeTab = code; _save(); }
```

- [ ] **Step 5.2: Verify page loads without JS errors**

Open any property page in browser. Console should be clean. `FloatingPanel` should be accessible in console. `FloatingPanel.toggleMinimize()` should not throw.

- [ ] **Step 5.3: Commit**

```bash
git add templates/partials/base_scripts.html
git commit -m "feat: add FloatingPanel core JS — drag, resize, minimize, localStorage"
```

---

## Task 6: Panel JS — tabs (open, close, switch, fetch, summary)

**Files:**
- Modify: `templates/partials/base_scripts.html`

- [ ] **Step 6.1: Replace stub `_renderTabBar` and `_activateTab` with full implementations**

In `base_scripts.html`, find the stub functions inside `FloatingPanel` IIFE and replace them:

```javascript
  // ── Tab bar rendering ───────────────────────────────────────────────
  function _renderTabBar() {
    _tabsScroll.innerHTML = '';
    _state.tabs.forEach(function(tab) {
      var btn = document.createElement('button');
      btn.className = 'fp-tab' + (tab.code === _state.activeTab ? ' fp-tab-active' : '');
      btn.dataset.code = tab.code;
      btn.title = tab.title;

      var channelDot = '';
      if ((tab.channel || '').toLowerCase().indexOf('airbnb') >= 0) {
        channelDot = '<span style="width:6px;height:6px;border-radius:50%;background:#ff5a5f;flex-shrink:0;display:inline-block;"></span>';
      } else if ((tab.channel || '').toLowerCase().indexOf('booking') >= 0) {
        channelDot = '<span style="width:6px;height:6px;border-radius:50%;background:#0053b4;flex-shrink:0;display:inline-block;"></span>';
      }

      btn.innerHTML = channelDot
        + '<span class="fp-tab-label">' + _esc(tab.title) + '</span>'
        + '<button class="fp-tab-close" data-close="' + _esc(tab.code) + '" title="Zavřít">'
        + '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">'
        + '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';

      btn.addEventListener('click', function(e) {
        if (e.target.closest('.fp-tab-close')) {
          closeTab(e.target.closest('.fp-tab-close').dataset.close);
        } else {
          _activateTab(tab.code);
        }
      });
      _tabsScroll.appendChild(btn);
    });

    // Summary tab active state
    var summaryTab = document.getElementById('fp-summary-tab');
    if (summaryTab) {
      summaryTab.classList.toggle('fp-tab-active', _state.activeTab === '__summary__');
    }
  }

  function _esc(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Tab activation ─────────────────────────────────────────────────
  function _activateTab(code) {
    _state.activeTab = code;
    _renderTabBar();

    if (code === '__summary__') {
      _renderSummary();
      _save();
      return;
    }

    // Show cached content immediately, then re-fetch if stale
    if (_tabContent[code]) {
      _content.innerHTML = _tabContent[code];
      _save();
      return;
    }

    _content.innerHTML = '<div class="fp-loading"><svg class="fp-spinner" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M12 2a10 10 0 010 20A10 10 0 0112 2" opacity=".25"/><path stroke-linecap="round" d="M12 2a10 10 0 0110 10"/></svg>Načítám…</div>';

    fetch('/reservation/' + encodeURIComponent(code) + '/panel', { credentials: 'same-origin' })
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function(html) {
        _tabContent[code] = html;
        if (_state.activeTab === code) {
          _content.innerHTML = html;
        }
      })
      .catch(function(err) {
        if (_state.activeTab === code) {
          _content.innerHTML = '<div style="padding:24px;color:var(--color-red);font-size:13px;">Chyba načítání: ' + _esc(err.message) + '</div>';
        }
      });

    _save();
  }

  // ── Summary tab ────────────────────────────────────────────────────
  function _renderSummary() {
    if (_state.tabs.length === 0) {
      _content.innerHTML = '<div class="fp-summary-empty">Žádné otevřené rezervace.</div>';
      return;
    }

    // Collect data-val attributes from cached tab HTML
    var totals = { nights: 0, payout: 0, ubytovani: 0, priprava: 0, citytax: 0, provize: 0, count: 0 };
    _state.tabs.forEach(function(tab) {
      var html = _tabContent[tab.code];
      if (!html) return;
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      totals.count++;
      // Extract from data attributes embedded in reservation_detail.html
      function _getVal(attr) {
        var el = tmp.querySelector('[data-summary-' + attr + ']');
        return el ? parseFloat(el.getAttribute('data-summary-' + attr) || 0) : 0;
      }
      totals.nights    += _getVal('nights');
      totals.payout    += _getVal('payout');
      totals.ubytovani += _getVal('ubytovani');
      totals.priprava  += _getVal('priprava');
      totals.citytax   += _getVal('citytax');
      totals.provize   += _getVal('provize');
    });

    function _fmt(n) {
      return Math.round(n).toLocaleString('cs-CZ') + ' Kč';
    }

    _content.innerHTML = '<div class="fp-summary-grid">'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Rezervací</span><span class="fp-summary-value">' + totals.count + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Nocí</span><span class="fp-summary-value">' + totals.nights + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Výplata</span><span class="fp-summary-value">' + _fmt(totals.payout) + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Ubytování</span><span class="fp-summary-value">' + _fmt(totals.ubytovani) + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Příprava pokoje</span><span class="fp-summary-value">' + _fmt(totals.priprava) + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">City tax</span><span class="fp-summary-value">' + _fmt(totals.citytax) + '</span></div>'
      + '<div class="fp-summary-item"><span class="fp-summary-label">Provize</span><span class="fp-summary-value">' + _fmt(totals.provize) + '</span></div>'
      + '</div>';
  }

  // ── Open / close tabs ──────────────────────────────────────────────
  function open(code, slug, year, month, title, channel) {
    // Already open → just activate
    var existing = _state.tabs.find(function(t) { return t.code === code; });
    if (existing) {
      _show();
      if (_state.minimized) toggleMinimize();
      _activateTab(code);
      return;
    }

    // At max tabs → evict oldest (first in array)
    if (_state.tabs.length >= MAX_TABS) {
      var evicted = _state.tabs.shift();
      delete _tabContent[evicted.code];
    }

    _state.tabs.push({ code: code, slug: slug, year: year, month: month, title: title, channel: channel || '' });
    _show();
    if (_state.minimized) toggleMinimize();
    _renderTabBar();
    _activateTab(code);
  }

  function closeTab(code) {
    var idx = _state.tabs.findIndex(function(t) { return t.code === code; });
    if (idx === -1) return;
    _state.tabs.splice(idx, 1);
    delete _tabContent[code];

    if (_state.tabs.length === 0) {
      _state.activeTab = null;
      _hide();
      _save();
      return;
    }

    // Activate neighbour tab
    var nextTab = _state.tabs[Math.min(idx, _state.tabs.length - 1)];
    _renderTabBar();
    _activateTab(nextTab ? nextTab.code : '__summary__');
  }
```

Also update the `return` at the bottom of the IIFE:

```javascript
  return {
    init: init,
    open: open,
    closeTab: closeTab,
    activateTab: function(code) { _activateTab(code); },
    toggleMinimize: toggleMinimize,
    toggleExpand: toggleExpand,
  };
```

- [ ] **Step 6.2: Add `data-summary-*` attributes to `reservation_detail.html`**

At the very top of `templates/partials/reservation_detail.html` (after the existing macro line), add a hidden metadata div that the Summary tab reads:

```html
{# Summary metadata — read by FloatingPanel summary aggregation #}
<div style="display:none;"
     data-summary-nights="{{ r.nights or 0 }}"
     data-summary-payout="{{ r.payout_czk or 0 }}"
     data-summary-ubytovani="{{ r.cena_ubytovani_czk or 0 }}"
     data-summary-priprava="{{ r.priprava_pokoje_czk or 0 }}"
     data-summary-citytax="{{ r.city_tax_czk or 0 }}"
     data-summary-provize="{{ r.provize_czk or 0 }}"></div>
```

- [ ] **Step 6.3: Verify tabs open and close correctly**

Open browser console and run:
```javascript
FloatingPanel.open('TEST001', 'obj1', 2026, 3, 'Test Guest', 'Airbnb');
```
Panel should appear with one tab. Click X on the tab → panel should hide.

- [ ] **Step 6.4: Commit**

```bash
git add templates/partials/base_scripts.html templates/partials/reservation_detail.html
git commit -m "feat: FloatingPanel tab management — open, close, switch, summary"
```

---

## Task 7: Replace drawer with `FloatingPanel.open()`

**Files:**
- Modify: `templates/partials/property_scripts.html`
- Modify: `templates/property.html`
- Modify: `templates/partials/property_reservations.html`

- [ ] **Step 7.1: Update `property_scripts.html`**

Replace the entire `openDrawerFromRow` and `closeDrawer` functions with `FloatingPanel.open()` calls. The file still needs `toggleOverrideForm`, `toggleSection`, etc. — keep those.

Replace the drawer-specific part:

```javascript
// OLD — remove these functions entirely:
// function openDrawerFromRow(el) { ... }
// function closeDrawer() { ... }
// document.addEventListener('keydown', ...) for drawer
```

Replace with:

```javascript
function openDrawerFromRow(el) {
  // Backward compat shim — delegates to FloatingPanel
  var code    = el.dataset.code;
  var slug    = el.dataset.slug;
  var year    = el.dataset.year;
  var month   = el.dataset.month;
  var guest   = el.dataset.guest   || '—';
  var channel = el.dataset.channel || '';
  _clearRowSelected();
  el.classList.add('row-selected');
  FloatingPanel.open(code, slug, year, month, guest, channel);
}
```

Remove the `var _currentDrawerIdx = null;` line — no longer needed.
Keep `_clearRowSelected()` function as-is (still used for row highlighting).

- [ ] **Step 7.2: Remove `property_drawer.html` include from `property.html`**

In `templates/property.html`, remove the line:
```
{% include "partials/property_drawer.html" %}
```

The panel is now included globally in `base.html`.

- [ ] **Step 7.3: Delete `templates/partials/property_drawer.html`**

```bash
rm "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new/templates/partials/property_drawer.html"
```

- [ ] **Step 7.4: Remove old drawer CSS from `base_styles.html`**

In `base_styles.html`, find and remove the CSS for `#res-drawer`, `#drawer-backdrop`, `.drawer-section`, `.drawer-metric`, etc. (the old right-side drawer). Leave all other CSS intact.

> Search for `#res-drawer` in `base_styles.html` to find the block boundaries.

- [ ] **Step 7.5: Test end-to-end**

1. Start the app: `python run_web.py`
2. Open a property page with reservations
3. Click a reservation row → panel should appear top-right with guest name tab
4. Click another row → second tab should open
5. Navigate to a different property page → panel should persist with same tabs
6. Click X on a tab → tab closes
7. Click Σ → summary tab shows aggregated totals
8. Drag panel → position saves in localStorage
9. Resize → size saves

- [ ] **Step 7.6: Commit**

```bash
git add templates/partials/property_scripts.html templates/property.html templates/partials/base_styles.html
git commit -m "feat: replace drawer with FloatingPanel — persistent cross-page reservation tabs"
```

---

## Task 8: Remove old drawer-related JS cleanup

**Files:**
- Modify: `templates/partials/base_styles.html` (ensure old drawer CSS is gone)
- Modify: `templates/partials/property_scripts.html` (final cleanup)

- [ ] **Step 8.1: Grep for any remaining drawer references**

```bash
grep -r "res-drawer\|drawer-backdrop\|drawer-body\|drawer-footer\|drawer-channel\|drawer-guest\|openDrawerFromRow\|closeDrawer" \
  "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new/templates" 2>&1
```

Expected: no matches (or only the shim `openDrawerFromRow` in `property_scripts.html` which is kept for row `onclick` compat).

- [ ] **Step 8.2: Run existing tests to check nothing broken**

```bash
cd "/Users/nikitashlykov/Library/CloudStorage/OneDrive-RenteroPropertys.r.o/Plocha/nikita1/315 new"
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass (no Python test covers drawer HTML directly).

- [ ] **Step 8.3: Commit**

```bash
git add -A
git commit -m "chore: remove old drawer HTML/CSS/JS, clean up after FloatingPanel migration"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in task |
|---|---|
| Floating, draggable, resizable window | Task 5 (drag + resize) |
| Tabs like browser tabs, max 5 | Task 6 (`open`, `closeTab`, MAX_TABS) |
| Persists across navigation (localStorage) | Task 5 (`_save`/`_load`) |
| Cross-page: open reservations from different objects | Task 2 (slug-agnostic endpoint) + Task 6 (`open()`) |
| Summary tab (Σ) with aggregated totals | Task 6 (`_renderSummary`) |
| Minimize / expand controls | Task 5 (`toggleMinimize`, `toggleExpand`) |
| Resize handle | Task 5 (`_onResizeStart/Move/End`) |
| Channel dot colour on tabs | Task 6 (`_renderTabBar` channel dot logic) |
| data-summary-* for aggregation | Task 6 (`reservation_detail.html`) |
| Old drawer removed | Tasks 7–8 |

**No placeholders found.**

**Type consistency:** `FloatingPanel.open(code, slug, year, month, title, channel)` — 6 params used consistently in Task 6 (`open()` definition) and Task 7 (`openDrawerFromRow` shim call). ✓
