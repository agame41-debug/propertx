# Responsive Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Rentero's web interface into a mobile-first, responsive application with bottom navigation, HTMX page transitions, Geist typography, OKLCH color system, and polished animations — while preserving all existing functionality.

**Architecture:** Split the monolithic 1837-line `base_styles.html` into 5 focused partials (tokens, layout, components, animations, responsive). Add HTMX for SPA-like transitions. Add mobile bottom bar + sheet navigation. Migrate from hex/rgba colors to OKLCH. Replace Inter with Geist font family. Remove all inline `style="..."` from templates in favor of semantic CSS classes.

**Tech Stack:** CSS Custom Properties (OKLCH), CSS Grid, Container Queries, HTMX 2.x (CDN), View Transitions API, Geist font (CDN/self-hosted), vanilla JS.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `templates/partials/base_styles.html` | REWRITE | Design tokens (OKLCH colors, spacing, timing, typography), CSS reset, base typography, theme switching |
| `templates/partials/base_styles_layout.html` | CREATE | App shell: sidebar (desktop), bottom bar (mobile), sheet overlay, main content area, month nav |
| `templates/partials/base_styles_components.html` | CREATE | Buttons, cards, KPI cards, badges, tables, forms, inputs, notifications, meta chips, filter tabs |
| `templates/partials/base_styles_animations.html` | CREATE | Keyframes, transitions, stagger system, reduced-motion |
| `templates/partials/base_styles_responsive.html` | CREATE | Media queries for mobile/tablet/desktop, container queries, mobile card transforms |
| `templates/base.html` | EDIT | Add HTMX 2.x, Geist font, View Transitions meta, include new style partials, add bottom bar + sheet markup |
| `templates/partials/base_sidebar.html` | EDIT | Hide on mobile via class, add bottom bar HTML, add sheet HTML |
| `templates/partials/base_scripts.html` | EDIT | Add bottom bar JS (sheet toggle, swipe-to-close), HTMX config |
| `report/web.py` | EDIT | HTMX partial rendering — detect `HX-Request` header, return `<main>` only |
| `templates/dashboard.html` | EDIT | Remove inline styles, add semantic classes, add mobile card layout |
| `templates/expenses.html` | EDIT | Remove inline styles, add responsive table-to-card classes |
| `templates/property.html` | EDIT | Remove inline styles, responsive layout |
| `templates/inventory.html` | EDIT | Remove inline styles, responsive layout |
| `templates/bank.html` | EDIT | Remove inline styles, responsive table-to-card |
| `templates/client.html` | EDIT | Remove inline styles, responsive layout |
| `templates/clients.html` | EDIT | Remove inline styles, responsive layout |
| `templates/admin_users.html` | EDIT | Remove inline styles, responsive layout |
| `templates/reconciliation.html` | EDIT | Remove inline styles, responsive layout |
| `templates/sources.html` | EDIT | Remove inline styles, responsive layout |
| `templates/logs.html` | EDIT | Remove inline styles, responsive layout |
| `templates/audit.html` | EDIT | Remove inline styles, responsive layout |
| `templates/login.html` | EDIT | Remove inline styles, responsive layout |

---

## Task 1: Design Tokens — `base_styles.html` rewrite

**Files:**
- Rewrite: `templates/partials/base_styles.html` (currently 1837 lines → ~120 lines)

This task replaces the entire contents of `base_styles.html` with ONLY design tokens, reset, and base typography. All component/layout/animation styles move to new files in later tasks.

- [ ] **Step 1: Back up current file**

```bash
cp templates/partials/base_styles.html templates/partials/base_styles_backup.html
```

- [ ] **Step 2: Write new `base_styles.html` — tokens + reset + typography**

Replace the entire contents of `templates/partials/base_styles.html` with:

```html
<style>
  /* ═══════════════════════════════════════════════════════════════════════
     DESIGN TOKENS — Rentero Design System v2
     ═══════════════════════════════════════════════════════════════════════ */
  :root {
    /* ── Spacing (4pt scale) ── */
    --space-xs:  4px;
    --space-sm:  8px;
    --space-md:  12px;
    --space-lg:  16px;
    --space-xl:  24px;
    --space-2xl: 32px;
    --space-3xl: 48px;
    --space-4xl: 64px;
    --space-5xl: 96px;

    /* ── Radius ── */
    --radius-sm:   4px;
    --radius-md:   8px;
    --radius-lg:   12px;
    --radius-xl:   16px;
    --radius-full: 9999px;

    /* ── Shadows ── */
    --shadow-sm: 0 1px 3px oklch(0 0 0 / 0.25);
    --shadow-md: 0 4px 16px oklch(0 0 0 / 0.35);
    --shadow-lg: 0 12px 40px oklch(0 0 0 / 0.5);

    /* ── Timing ── */
    --duration-fast:   150ms;
    --duration-normal: 250ms;
    --duration-slow:   350ms;
    --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
    --ease-out:      cubic-bezier(0.33, 1, 0.68, 1);

    /* ── Typography scale (fixed rem — product UI) ── */
    --text-xs:   0.6875rem;  /* 11px */
    --text-sm:   0.8125rem;  /* 13px */
    --text-base: 0.875rem;   /* 14px */
    --text-lg:   1rem;       /* 16px */
    --text-xl:   1.25rem;    /* 20px */
    --text-2xl:  1.5rem;     /* 24px */
    --text-3xl:  2rem;       /* 32px */

    /* ── Sidebar width ── */
    --sidebar-w: 220px;
    /* ── Bottom bar height ── */
    --bottombar-h: 56px;

    /* ── Dark theme (default) ── */
    --bg-0:   oklch(0.11 0.015 260);
    --bg-1:   oklch(0.14 0.015 260);
    --bg-2:   oklch(0.18 0.012 260);
    --bg-3:   oklch(0.22 0.010 260);
    --bg-4:   oklch(0.26 0.008 260);

    --text-100: oklch(0.90 0.010 260);
    --text-200: oklch(0.60 0.015 260);
    --text-300: oklch(0.40 0.010 260);

    --border-base:   oklch(0.25 0.010 260 / 0.5);
    --border-top:    oklch(0.35 0.010 260 / 0.5);
    --border-active: oklch(0.55 0.15 280 / 0.4);

    /* ── Accent colors (OKLCH) ── */
    --color-primary: oklch(0.65 0.18 280);
    --color-green:   oklch(0.70 0.18 155);
    --color-amber:   oklch(0.75 0.15 75);
    --color-red:     oklch(0.65 0.20 20);
    --color-blue:    oklch(0.65 0.15 250);
    --color-teal:    oklch(0.72 0.12 180);
    --color-orange:  oklch(0.72 0.15 55);

    /* ── Accent dim variants (for backgrounds) ── */
    --green-dim:   oklch(0.70 0.18 155 / 0.12);
    --red-dim:     oklch(0.65 0.20 20 / 0.12);
    --amber-dim:   oklch(0.75 0.15 75 / 0.12);
    --blue-dim:    oklch(0.65 0.15 250 / 0.12);
    --primary-dim: oklch(0.65 0.18 280 / 0.12);
    --teal-dim:    oklch(0.72 0.12 180 / 0.12);

    /* ── Legacy aliases (keep during migration, remove later) ── */
    --bg-root:        var(--bg-1);
    --bg-panel:       var(--bg-2);
    --bg-card:        var(--bg-3);
    --bg-card-hover:  var(--bg-4);
    --border-subtle:  var(--border-base);
    --bg:             var(--bg-1);
    --surface:        var(--bg-3);
    --surface-raised: var(--bg-4);
    --text-primary:   var(--text-100);
    --text-secondary: var(--text-200);
    --text-muted:     var(--text-300);
    --border:         var(--border-base);
    --accent:         var(--color-primary);
    --accent-dim:     var(--primary-dim);
    --green:          var(--color-green);
    --red:            var(--color-red);
    --amber:          var(--color-amber);
    --blue:           var(--color-blue);
  }

  /* ── Light theme overrides ── */
  [data-theme="light"] {
    --bg-0:   oklch(0.97 0.005 260);
    --bg-1:   oklch(0.98 0.004 260);
    --bg-2:   oklch(0.95 0.006 260);
    --bg-3:   oklch(1.00 0.000 0);
    --bg-4:   oklch(0.93 0.006 260);

    --text-100: oklch(0.15 0.015 260);
    --text-200: oklch(0.45 0.015 260);
    --text-300: oklch(0.65 0.010 260);

    --border-base: oklch(0.85 0.005 260 / 0.5);
    --border-top:  oklch(0.80 0.008 260 / 0.5);

    --shadow-sm: 0 1px 3px oklch(0 0 0 / 0.08);
    --shadow-md: 0 4px 16px oklch(0 0 0 / 0.12);
    --shadow-lg: 0 12px 40px oklch(0 0 0 / 0.18);
  }

  /* ── Reset ── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }

  body {
    font-family: 'Geist', system-ui, -apple-system, sans-serif;
    background: var(--bg-0);
    color: var(--text-100);
    min-height: 100vh;
    font-size: var(--text-base);
    line-height: 1.5;
  }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: oklch(0.50 0 0 / 0.15); border-radius: var(--radius-sm); }
  ::-webkit-scrollbar-thumb:hover { background: oklch(0.50 0 0 / 0.25); }

  /* ── Base type utilities ── */
  .num { font-variant-numeric: tabular-nums; }
  a { color: inherit; text-decoration: none; }
  code {
    font-family: 'Geist Mono', 'SF Mono', monospace;
    background: oklch(0.50 0 0 / 0.08);
    padding: 2px 6px;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
  }
  details > summary { list-style: none; }
  details > summary::-webkit-details-marker { display: none; }
</style>
```

- [ ] **Step 3: Verify the app still loads (will look broken — expected)**

```bash
python run_web.py &
sleep 2
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/
kill %1
```

Expected: HTTP 200 (page loads, but styling is broken because layout/component styles are gone — this is expected and will be fixed in Tasks 2-4).

- [ ] **Step 4: Commit**

```bash
git add templates/partials/base_styles.html templates/partials/base_styles_backup.html
git commit -m "refactor: rewrite base_styles.html with OKLCH tokens, Geist font, spacing scale

Replaces hex/rgba color system with OKLCH for perceptual uniformity.
Migrates from Inter to Geist font family.
Adds semantic spacing/timing/radius tokens.
Component and layout styles temporarily removed (next commits)."
```

---

## Task 2: Layout Shell — `base_styles_layout.html`

**Files:**
- Create: `templates/partials/base_styles_layout.html`
- Modify: `templates/base.html:18` (add include)

This file contains all layout CSS: app shell, sidebar, main content, bottom bar, sheet overlay, month navigation, property page header, section divider.

- [ ] **Step 1: Create `base_styles_layout.html`**

Create `templates/partials/base_styles_layout.html` with the following content. This is extracted from the original `base_styles.html` lines 80-159 (app layout, sidebar, main-content) + lines 583-678 (prop-page-header, month-nav, section-divider) + lines 690-740 (sidebar month block, sidebar objects) + NEW bottom bar and sheet styles:

```html
<style>
  /* ═══════════════════════════════════════════════════════════════════════
     LAYOUT — App shell, sidebar, bottom bar, sheet, main content
     ═══════════════════════════════════════════════════════════════════════ */

  .app-layout {
    display: flex;
    min-height: 100vh;
  }

  /* ── Desktop sidebar ────────────────────────────────────────────────── */
  .sidebar {
    width: var(--sidebar-w);
    background: var(--bg-0);
    border-right: 1px solid var(--border-base);
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0; left: 0; bottom: 0;
    z-index: 30;
    overflow-y: auto;
    transition: transform var(--duration-normal) var(--ease-out-expo);
  }
  .sidebar-logo {
    padding: 18px var(--space-lg);
    border-bottom: 1px solid var(--border-base);
    display: flex;
    align-items: center;
    gap: var(--space-md);
  }
  .sidebar-logo-icon {
    width: 30px; height: 30px;
    background: var(--color-primary);
    border-radius: var(--radius-md);
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 2px 10px oklch(0.55 0.18 280 / 0.35);
  }
  .sidebar-section-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--text-300);
    padding: var(--space-lg) var(--space-md) 6px;
  }
  .nav-item {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 7px var(--space-md);
    margin: 0 6px;
    border-radius: 7px;
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--text-300);
    transition: all var(--duration-fast) var(--ease-out);
    text-decoration: none;
    border: 1px solid transparent;
    cursor: pointer;
    font-family: inherit;
    background: none;
    width: calc(100% - 12px);
    text-align: left;
    position: relative;
  }
  .nav-item:hover {
    background: oklch(0.50 0 0 / 0.05);
    color: var(--text-100);
    border-color: oklch(0.50 0 0 / 0.05);
  }
  .nav-item.active {
    background: oklch(0.55 0.15 280 / 0.09);
    color: var(--color-primary);
    border-color: transparent;
  }
  .nav-item svg { width: 15px; height: 15px; flex-shrink: 0; opacity: 0.5; }
  .nav-item:hover svg, .nav-item.active svg { opacity: 1; }

  /* ── Sidebar month block ── */
  .sb-month-block {
    padding: var(--space-md) var(--space-md);
    border-bottom: 1px solid var(--border-base);
  }
  .sb-month-label {
    font-size: 9px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .1em; color: var(--text-300); margin-bottom: 6px;
  }
  .sb-month-nav { display: flex; align-items: center; gap: 6px; }
  .sb-month-btn {
    background: oklch(0.50 0 0 / 0.04); border: 1px solid var(--border-base);
    border-radius: 5px; color: var(--text-200); cursor: pointer;
    font-size: 17px; line-height: 1; padding: 1px 7px 2px;
    transition: all var(--duration-fast); font-family: inherit;
  }
  .sb-month-btn:hover { background: oklch(0.50 0 0 / 0.08); color: var(--text-100); }
  .sb-month-val {
    flex: 1; text-align: center; font-size: var(--text-sm); font-weight: 600;
    color: var(--text-100); font-variant-numeric: tabular-nums;
  }

  /* ── Sidebar objects list ── */
  .sb-obj-item {
    display: flex; align-items: center; gap: var(--space-sm);
    padding: 5px var(--space-md) 5px var(--space-xl);
    font-size: 12px; color: var(--text-300); text-decoration: none;
    transition: all var(--duration-fast); border-radius: 6px; margin: 1px 6px;
    border: 1px solid transparent;
  }
  .sb-obj-item:hover {
    color: var(--text-100);
    background: oklch(0.50 0 0 / 0.04);
    border-color: oklch(0.50 0 0 / 0.05);
  }
  .sb-obj-item.sb-current {
    color: var(--color-primary);
    background: oklch(0.55 0.15 280 / 0.09);
    border-color: transparent;
  }
  .sb-obj-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: oklch(0.50 0 0 / 0.18);
    transition: box-shadow var(--duration-normal);
  }
  .sb-obj-dot[data-h="ok"]     { background: var(--color-green);   box-shadow: 0 0 5px oklch(0.70 0.18 155 / 0.55); }
  .sb-obj-dot[data-h="issues"] { background: var(--color-amber);   box-shadow: 0 0 5px oklch(0.75 0.15 75 / 0.55); }
  .sb-obj-dot[data-h="action"] { background: var(--color-primary); box-shadow: 0 0 5px oklch(0.55 0.18 280 / 0.55); }
  .sb-obj-dot[data-h="empty"]  { background: oklch(0.50 0 0 / 0.15); }

  /* ── Main content ── */
  .main-content {
    margin-left: var(--sidebar-w);
    flex: 1;
    background: var(--bg-1);
    min-height: 100vh;
    padding: var(--space-3xl) var(--space-2xl);
  }

  /* ── Property page header ── */
  .prop-page-header {
    position: sticky;
    top: 0;
    z-index: 20;
    margin: calc(-1 * var(--space-3xl)) calc(-1 * var(--space-2xl)) var(--space-xl);
    padding: var(--space-lg) var(--space-2xl) var(--space-lg);
    background: oklch(from var(--bg-1) l c h / 0.85);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border-base);
  }
  .prop-page-title {
    font-size: var(--text-xl);
    font-weight: 800;
    color: var(--text-100);
    letter-spacing: -0.02em;
  }
  .prop-breadcrumb {
    font-size: var(--text-xs);
    color: var(--text-300);
    display: flex;
    align-items: center;
    gap: 5px;
    font-weight: 500;
  }
  .prop-breadcrumb-link {
    color: var(--text-300);
    text-decoration: none;
    transition: color var(--duration-fast);
  }
  .prop-breadcrumb-link:hover { color: var(--text-100); }
  .prop-breadcrumb-sep { opacity: 0.3; }

  /* ── Month navigation ── */
  .month-nav {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    background: oklch(0.50 0 0 / 0.04);
    border: 1px solid var(--border-base);
    border-radius: 9px;
    padding: 3px;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
  .month-nav-arrow {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px; height: 26px;
    border-radius: 6px;
    color: var(--text-300);
    text-decoration: none;
    transition: all var(--duration-fast);
    font-size: 15px;
  }
  .month-nav-arrow:hover {
    background: oklch(0.50 0 0 / 0.08);
    color: var(--text-100);
  }
  .month-nav-label {
    padding: 0 var(--space-md);
    font-size: var(--text-sm);
    font-weight: 700;
    color: var(--text-100);
    font-variant-numeric: tabular-nums;
    min-width: 64px;
    text-align: center;
    background: none; border: none; cursor: pointer;
    font-family: inherit;
    letter-spacing: 0.01em;
  }

  /* ── Section divider ── */
  .section-divider {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    margin: 0 0 var(--space-lg);
  }
  .section-divider-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--text-300);
    white-space: nowrap;
  }
  .section-divider-line {
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, var(--border-base), transparent);
  }

  /* ── Mobile bottom bar ── */
  .bottom-bar {
    display: none; /* shown via media query in responsive partial */
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: var(--bottombar-h);
    background: oklch(from var(--bg-0) l c h / 0.92);
    backdrop-filter: blur(20px) saturate(1.5);
    -webkit-backdrop-filter: blur(20px) saturate(1.5);
    border-top: 1px solid var(--border-base);
    z-index: 40;
    padding: 0 var(--space-xl);
  }
  .bottom-bar-inner {
    display: flex;
    align-items: center;
    justify-content: space-around;
    height: 100%;
    max-width: 400px;
    margin: 0 auto;
  }
  .bottom-bar-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 6px var(--space-md);
    color: var(--text-300);
    font-size: 10px;
    font-weight: 500;
    text-decoration: none;
    border: none;
    background: none;
    cursor: pointer;
    font-family: inherit;
    transition: color var(--duration-fast);
    -webkit-tap-highlight-color: transparent;
  }
  .bottom-bar-item svg { width: 22px; height: 22px; }
  .bottom-bar-item.active { color: var(--color-primary); }
  .bottom-bar-item:active { transform: scale(0.92); }

  /* ── Sheet overlay (mobile "Více" menu) ── */
  .sheet-backdrop {
    display: none;
    position: fixed;
    inset: 0;
    background: oklch(0 0 0 / 0.5);
    z-index: 50;
    opacity: 0;
    transition: opacity var(--duration-slow) var(--ease-out);
  }
  .sheet-backdrop.open {
    display: block;
    opacity: 1;
  }
  .sheet {
    position: fixed;
    bottom: 0; left: 0; right: 0;
    max-height: 80vh;
    background: var(--bg-2);
    border-top: 1px solid var(--border-top);
    border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    z-index: 51;
    transform: translate3d(0, 100%, 0);
    transition: transform var(--duration-slow) var(--ease-out-expo);
    overflow-y: auto;
    overscroll-behavior: contain;
    padding-bottom: env(safe-area-inset-bottom, 0);
  }
  .sheet.open {
    transform: translate3d(0, 0, 0);
  }
  .sheet-handle {
    width: 36px;
    height: 4px;
    background: oklch(0.50 0 0 / 0.2);
    border-radius: var(--radius-full);
    margin: var(--space-sm) auto var(--space-lg);
  }
  .sheet-nav {
    display: flex;
    flex-direction: column;
    padding: 0 var(--space-lg) var(--space-xl);
    gap: 2px;
  }
  .sheet-nav-item {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-md) var(--space-lg);
    border-radius: var(--radius-md);
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-200);
    text-decoration: none;
    transition: background var(--duration-fast);
    border: none;
    background: none;
    cursor: pointer;
    font-family: inherit;
    width: 100%;
    text-align: left;
  }
  .sheet-nav-item:active {
    background: oklch(0.50 0 0 / 0.06);
  }
  .sheet-nav-item svg { width: 20px; height: 20px; opacity: 0.5; }
  .sheet-section-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--text-300);
    padding: var(--space-lg) var(--space-lg) var(--space-sm);
  }
  .sheet-user {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: var(--space-lg);
    border-top: 1px solid var(--border-base);
    margin-top: var(--space-sm);
  }

  /* ── Mobile month nav strip (top of main on mobile) ── */
  .mobile-month-strip {
    display: none; /* shown via media query */
    position: sticky;
    top: 0;
    z-index: 15;
    padding: var(--space-sm) var(--space-lg);
    background: oklch(from var(--bg-1) l c h / 0.9);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border-base);
    margin: calc(-1 * var(--space-lg)) calc(-1 * var(--space-lg)) var(--space-lg);
  }

  /* ── Caret ── */
  .caret-icon { transition: transform var(--duration-normal) var(--ease-out); flex-shrink: 0; color: var(--text-300); }
  .caret-icon.rotated { transform: rotate(-90deg); }
  .collapsed { display: none !important; }
</style>
```

- [ ] **Step 2: Include in `base.html`**

In `templates/base.html`, after line 18 (`{% include "partials/base_styles.html" %}`), add:

```html
  {% include "partials/base_styles_layout.html" %}
```

- [ ] **Step 3: Verify sidebar and main layout render**

Start the server and check that the sidebar and main content area display correctly on desktop. The page will still be missing component styles (buttons, cards, etc.) — that's expected.

- [ ] **Step 4: Commit**

```bash
git add templates/partials/base_styles_layout.html templates/base.html
git commit -m "feat: add layout partial with sidebar, bottom bar, sheet, mobile month strip

Extracts all layout styles from base_styles.html into dedicated partial.
Adds new mobile bottom bar (hidden by default, activated in responsive partial).
Adds sheet overlay for mobile 'Více' navigation.
Adds mobile month navigation strip."
```

---

## Task 3: Components — `base_styles_components.html`

**Files:**
- Create: `templates/partials/base_styles_components.html`
- Modify: `templates/base.html` (add include)

This file contains all reusable component styles: cards, KPI cards, badges, buttons, tables, forms, notifications, filter tabs, meta chips, prop rows, reservation compact strip, floating panel, override badges, copy toast, back-to-top, theme toggle. Extracted from original `base_styles.html` lines 160-548 + 742-940 + 923-1465 + light theme component overrides.

- [ ] **Step 1: Create `base_styles_components.html`**

Create `templates/partials/base_styles_components.html`. Extract ALL component styles from `base_styles_backup.html`, converting hex/rgba colors to use token variables where possible. This is the largest file — it should contain everything that is NOT tokens, NOT layout, NOT keyframes, and NOT media queries.

Key components to include (with OKLCH conversion):
- `.card`, `.card-header`, `.card-title`
- `.kpi-card`, `.kpi-card-*::before`, `.kpi-label`, `.kpi-value`, `.kpi-sub`, `.kpi-delta`
- `.badge`, `.badge-*` variants, `.channel-*` variants
- `.btn`, `.btn-*` variants, `.btn-sm`, `.btn-icon`
- `.data-table`, `th`, `td`, `tfoot`, sorting classes
- `.filter-tabs`, `.filter-tab`
- `.form-input`, `.form-label`
- `.notify`, `.notify-*` variants
- `.prop-row`, `.pr-*` classes
- `.prop-bar`, `.prop-bar-fill`
- `.override-badge`, `.meta-chip`
- `.res-compact-strip`, `.rcs-*` classes
- `.fp-panel` and all floating panel classes
- `#copy-toast`, `#back-to-top`
- `.theme-toggle-btn`
- `.col-drag`, `.tbl-reset-btn`, sorting classes
- ALL corresponding `[data-theme="light"]` overrides for each component

Since this is a direct extraction (not a rewrite), preserve all existing visual behavior. Convert `rgba(255,255,255,X)` to `oklch(1 0 0 / X)` and `rgba(0,0,0,X)` to `oklch(0 0 0 / X)` where they appear as inline values. Replace hardcoded color values with `var(--color-*)` tokens where they match.

- [ ] **Step 2: Include in `base.html`**

In `templates/base.html`, after the layout include, add:

```html
  {% include "partials/base_styles_components.html" %}
```

- [ ] **Step 3: Verify all components render correctly**

Start the server and check:
- Dashboard: KPI cards, property rows, filter tabs render
- Property page: table, badges, buttons render
- Login page: form inputs render
- Floating panel: opens and displays correctly

- [ ] **Step 4: Commit**

```bash
git add templates/partials/base_styles_components.html templates/base.html
git commit -m "feat: extract component styles to dedicated partial

Moves cards, buttons, badges, tables, forms, KPI cards, prop rows,
floating panel, and all other component styles from backup.
Converts inline rgba to oklch where possible.
Includes light theme overrides for all components."
```

---

## Task 4: Animations — `base_styles_animations.html`

**Files:**
- Create: `templates/partials/base_styles_animations.html`
- Modify: `templates/base.html` (add include)

- [ ] **Step 1: Create `base_styles_animations.html`**

```html
<style>
  /* ═══════════════════════════════════════════════════════════════════════
     ANIMATIONS — Keyframes, transitions, stagger system, reduced motion
     ═══════════════════════════════════════════════════════════════════════ */

  /* ── Keyframes ── */
  @keyframes fadeSlideUp {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  @keyframes rowIn {
    from { opacity: 0; transform: translateY(-3px); }
    to   { opacity: 1; transform: none; }
  }

  @keyframes copyFlash {
    0%   { background: oklch(0.55 0.15 280 / 0.18); }
    100% { background: transparent; }
  }

  @keyframes pulse-ring {
    0%   { box-shadow: 0 0 0 0 oklch(0.72 0.12 180 / 0.4); }
    70%  { box-shadow: 0 0 0 6px oklch(0.72 0.12 180 / 0); }
    100% { box-shadow: 0 0 0 0 oklch(0.72 0.12 180 / 0); }
  }

  @keyframes sheetSlideUp {
    from { transform: translate3d(0, 100%, 0); }
    to   { transform: translate3d(0, 0, 0); }
  }

  /* ── Utility classes ── */
  .animate-spin { animation: spin 1.2s linear infinite; }
  .animate-fade-in { animation: fadeIn var(--duration-normal) var(--ease-out) both; }
  .animate-slide-up { animation: fadeSlideUp var(--duration-normal) var(--ease-out-expo) both; }

  /* ── Stagger system for lists ── */
  .stagger-list > * {
    animation: fadeSlideUp var(--duration-normal) var(--ease-out-expo) both;
  }
  .stagger-list > *:nth-child(1)  { animation-delay: 0ms; }
  .stagger-list > *:nth-child(2)  { animation-delay: 40ms; }
  .stagger-list > *:nth-child(3)  { animation-delay: 80ms; }
  .stagger-list > *:nth-child(4)  { animation-delay: 120ms; }
  .stagger-list > *:nth-child(5)  { animation-delay: 160ms; }
  .stagger-list > *:nth-child(6)  { animation-delay: 200ms; }
  .stagger-list > *:nth-child(7)  { animation-delay: 240ms; }
  .stagger-list > *:nth-child(8)  { animation-delay: 280ms; }
  .stagger-list > *:nth-child(9)  { animation-delay: 320ms; }
  .stagger-list > *:nth-child(10) { animation-delay: 360ms; }
  .stagger-list > *:nth-child(n+11) { animation-delay: 400ms; }

  /* ── Accordion expand (grid-rows trick) ── */
  .accordion-content {
    display: grid;
    grid-template-rows: 0fr;
    transition: grid-template-rows var(--duration-normal) var(--ease-out-expo);
  }
  .accordion-content.expanded {
    grid-template-rows: 1fr;
  }
  .accordion-content > * {
    overflow: hidden;
  }

  /* ── Click-to-copy flash ── */
  td.copyable { cursor: copy; position: relative; }
  td.copyable:hover { color: var(--text-100); }
  td.copy-flash { animation: copyFlash 0.4s ease forwards; }

  /* ── Badge pulse ── */
  .badge-teal.pulse { animation: pulse-ring 2s ease-out infinite; }

  /* ── View Transitions ── */
  @view-transition {
    navigation: auto;
  }
  ::view-transition-old(root) {
    animation: fadeIn var(--duration-fast) var(--ease-out) reverse;
  }
  ::view-transition-new(root) {
    animation: fadeIn var(--duration-fast) var(--ease-out);
  }

  /* ── Reduced motion ── */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
    .stagger-list > * { animation: none; opacity: 1; transform: none; }
    ::view-transition-old(root),
    ::view-transition-new(root) { animation: none; }
  }
</style>
```

- [ ] **Step 2: Include in `base.html`**

```html
  {% include "partials/base_styles_animations.html" %}
```

- [ ] **Step 3: Commit**

```bash
git add templates/partials/base_styles_animations.html templates/base.html
git commit -m "feat: add animation partial with stagger system, accordion, view transitions

Adds fadeSlideUp, fadeIn, spin, rowIn, sheetSlideUp keyframes.
Adds stagger-list class for automatic entry animations.
Adds accordion-content with grid-row transition.
Adds View Transitions API support.
Includes prefers-reduced-motion fallback."
```

---

## Task 5: Responsive — `base_styles_responsive.html`

**Files:**
- Create: `templates/partials/base_styles_responsive.html`
- Modify: `templates/base.html` (add include)

- [ ] **Step 1: Create `base_styles_responsive.html`**

```html
<style>
  /* ═══════════════════════════════════════════════════════════════════════
     RESPONSIVE — Mobile, tablet, desktop breakpoints
     ═══════════════════════════════════════════════════════════════════════ */

  /* ── Mobile (< 640px) ───────────────────────────────────────────────── */
  @media (max-width: 639px) {
    /* Hide desktop sidebar, show bottom bar */
    .sidebar { display: none; }
    .bottom-bar { display: block; }
    .mobile-month-strip { display: flex; align-items: center; justify-content: center; }

    .main-content {
      margin-left: 0;
      padding: var(--space-lg);
      padding-bottom: calc(var(--bottombar-h) + var(--space-lg));
    }

    /* Prop page header — full width on mobile */
    .prop-page-header {
      margin: calc(-1 * var(--space-lg)) calc(-1 * var(--space-lg)) var(--space-lg);
      padding: var(--space-md) var(--space-lg);
    }
    .prop-page-title { font-size: var(--text-lg); }

    /* KPI cards — 2x2 grid */
    .kpi-grid {
      grid-template-columns: 1fr 1fr !important;
      gap: var(--space-sm) !important;
    }
    .kpi-card { padding: var(--space-md) var(--space-lg); }
    .kpi-value { font-size: var(--text-xl); }
    .kpi-label { font-size: 9px; margin-bottom: var(--space-sm); }

    /* Dashboard prop rows — stack as cards */
    .prop-row {
      grid-template-columns: 1fr auto !important;
      grid-template-rows: auto auto;
      gap: var(--space-sm) !important;
      padding: var(--space-md) var(--space-lg);
    }
    .prop-row .pr-name { grid-column: 1; }
    .prop-row .pr-arrow { grid-column: 2; grid-row: 1; }
    .prop-row .pr-financial { grid-column: 1 / -1; }
    .prop-row .pr-hide-mobile { display: none; }

    /* Tables → card layout */
    .data-table.responsive-cards thead { display: none; }
    .data-table.responsive-cards tbody tr {
      display: flex;
      flex-direction: column;
      padding: var(--space-md) var(--space-lg);
      border-bottom: 1px solid var(--border-base);
      gap: var(--space-xs);
    }
    .data-table.responsive-cards td {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 2px 0;
      border-bottom: none;
      background: none !important;
    }
    .data-table.responsive-cards td::before {
      content: attr(data-label);
      font-size: var(--text-xs);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-300);
      flex-shrink: 0;
      margin-right: var(--space-md);
    }

    /* Filter tabs — scroll horizontal */
    .filter-tabs { overflow-x: auto; flex-wrap: nowrap; }

    /* Res compact strip — stack vertical */
    .res-compact-strip {
      flex-direction: column;
    }
    .rcs-group { flex-wrap: wrap; }
    .rcs-right { border-left: none; border-top: 1px solid oklch(0.50 0 0 / 0.05); }

    /* Floating panel — full width at bottom on mobile */
    .fp-panel {
      position: fixed !important;
      left: 0 !important;
      right: 0 !important;
      bottom: 0 !important;
      top: auto !important;
      width: 100% !important;
      min-width: unset !important;
      max-height: 80vh;
      border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    }

    /* Back to top / copy toast — above bottom bar */
    #back-to-top { bottom: calc(var(--bottombar-h) + var(--space-lg)); }
    #copy-toast   { bottom: calc(var(--bottombar-h) + var(--space-lg)); }

    /* Hide elements marked desktop-only */
    .desktop-only { display: none !important; }
  }

  /* ── Tablet (640px – 1024px) ────────────────────────────────────────── */
  @media (min-width: 640px) and (max-width: 1024px) {
    .sidebar { width: 60px; overflow: hidden; }
    .sidebar .sidebar-logo div:last-child { display: none; }
    .sidebar .sidebar-section-label { display: none; }
    .sidebar .nav-item span { display: none; }
    .sidebar .nav-item { justify-content: center; padding: 10px; margin: 2px 4px; }
    .sidebar .sb-month-block { display: none; }
    .sidebar #sb-obj-panel { display: none !important; }
    .sidebar #sb-acc-btn span { display: none; }
    .sidebar #sb-acc-btn .caret-icon { display: none; }

    .main-content {
      margin-left: 60px;
      padding: var(--space-xl);
    }

    /* Dashboard rows — hide Odměna + Zdraví columns */
    .prop-row { grid-template-columns: 2fr 150px 100px 100px 20px; }
    .prop-row .pr-hide-tablet { display: none; }

    .kpi-grid { gap: var(--space-sm); }
  }

  /* ── Desktop (> 1024px) ─────────────────────────────────────────────── */
  @media (min-width: 1025px) {
    .mobile-only { display: none !important; }
  }

  /* ── Safe area (iPhone notch) ── */
  @supports (padding-bottom: env(safe-area-inset-bottom)) {
    .bottom-bar {
      padding-bottom: env(safe-area-inset-bottom);
      height: calc(var(--bottombar-h) + env(safe-area-inset-bottom));
    }
  }
</style>
```

- [ ] **Step 2: Include in `base.html`**

```html
  {% include "partials/base_styles_responsive.html" %}
```

- [ ] **Step 3: Test on mobile viewport**

Open browser DevTools → toggle device toolbar → iPhone 14 (390px wide):
- Sidebar hidden, bottom bar visible at bottom
- Main content full width
- Page loads without errors

- [ ] **Step 4: Commit**

```bash
git add templates/partials/base_styles_responsive.html templates/base.html
git commit -m "feat: add responsive partial with mobile/tablet/desktop breakpoints

Mobile: hides sidebar, shows bottom bar, stacks prop rows as cards,
converts tables to card layout, repositions floating panel.
Tablet: collapses sidebar to icon-only 60px rail.
Includes safe-area-inset for iPhone notch."
```

---

## Task 6: Base HTML + HTMX + Geist Font + Bottom Bar Markup

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/partials/base_sidebar.html`

- [ ] **Step 1: Update `base.html`**

Replace the entire `templates/base.html` with:

```html
<!DOCTYPE html>
<html lang="cs">
<head>
  <script>
    (function() {
      var t = localStorage.getItem('rentero_theme') || 'dark';
      document.documentElement.setAttribute('data-theme', t);
    })();
  </script>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="view-transition" content="same-origin">
  <title>{% block title %}Rentero{% endblock %}</title>
  <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
  <link href="https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/style.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/style.min.css" rel="stylesheet">
  <script src="https://unpkg.com/htmx.org@2.0.4" integrity="sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+" crossorigin="anonymous"></script>
  {% include "partials/base_styles.html" %}
  {% include "partials/base_styles_layout.html" %}
  {% include "partials/base_styles_components.html" %}
  {% include "partials/base_styles_animations.html" %}
  {% include "partials/base_styles_responsive.html" %}
  {% block head %}{% endblock %}
</head>
<body hx-boost="true" hx-target="#content" hx-swap="innerHTML transition:true" hx-push-url="true">

<div class="app-layout">
  {% include "partials/base_sidebar.html" %}

  <main class="main-content" id="content">
    {% block content %}{% endblock %}
  </main>
</div>

<!-- Mobile bottom bar -->
<nav class="bottom-bar" id="bottom-bar">
  <div class="bottom-bar-inner">
    <a href="/" class="bottom-bar-item {% if request.url.path == '/' %}active{% endif %}" data-month-aware="1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      <span>Přehled</span>
    </a>
    <a href="#" class="bottom-bar-item" id="bb-objekty" onclick="event.preventDefault(); document.getElementById('sb-obj-panel-mobile') && (document.getElementById('sb-obj-panel-mobile').style.display = 'block');">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
      <span>Objekty</span>
    </a>
    <button class="bottom-bar-item" id="bb-vice" onclick="openSheet()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
      <span>Více</span>
    </button>
  </div>
</nav>

<!-- Sheet overlay (mobile "Více" menu) -->
<div class="sheet-backdrop" id="sheet-backdrop" onclick="closeSheet()"></div>
<div class="sheet" id="sheet">
  <div class="sheet-handle"></div>
  <nav class="sheet-nav">
    {% set current_user = get_current_user(request) %}
    {% set user_role = current_user.role if current_user else 'client' %}
    <div class="sheet-section-label">Finance</div>
    <a href="/expenses" class="sheet-nav-item" data-month-aware="1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>
      Výdaje
    </a>
    <a href="/bank" class="sheet-nav-item" data-month-aware="1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
      Banka
    </a>
    <a href="/clients" class="sheet-nav-item">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
      Klienti
    </a>
    <a href="/inventory" class="sheet-nav-item" data-month-aware="1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 12l9 4 9-4"/><path d="M3 17l9 4 9-4"/></svg>
      Inventory
    </a>
    {% if user_role != 'client' %}
    <div class="sheet-section-label">Nástroje</div>
    <a href="/reconciliation" class="sheet-nav-item" data-month-aware="1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M12 3v18"/><path d="M8 7l-4 4 4 4"/><path d="M16 7l4 4-4 4"/></svg>
      Srovnání
    </a>
    <a href="/sources" class="sheet-nav-item">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
      Zdroje
    </a>
    {% endif %}
    <a href="/audit" class="sheet-nav-item">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      Změny
    </a>
    {% if user_role != 'client' %}
    <a href="/logs" class="sheet-nav-item">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><line x1="3" y1="10" x2="17" y2="10"/><line x1="3" y1="14" x2="17" y2="14"/></svg>
      Logy
    </a>
    {% endif %}
    {% if user_role == 'admin' %}
    <div class="sheet-section-label">Admin</div>
    <a href="/admin/users" class="sheet-nav-item">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
      Uživatelé
    </a>
    {% endif %}
  </nav>
  {% if current_user %}
  <div class="sheet-user">
    <div style="width:28px;height:28px;border-radius:50%;background:var(--color-primary);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0;">
      {{ current_user.username[0]|upper }}
    </div>
    <div style="flex:1;">
      <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-200);">{{ current_user.display_name or current_user.username }}</div>
      <div style="font-size:var(--text-xs);color:var(--text-300);text-transform:uppercase;">{{ user_role }}</div>
    </div>
    <form method="post" action="/logout" style="margin:0;">
      {{ csrf_input(request) }}
      <button type="submit" class="btn btn-ghost btn-sm">Odhlásit</button>
    </form>
  </div>
  {% endif %}
</div>

{% include "partials/base_month_picker.html" %}

<div id="copy-toast">Zkopírováno ✓</div>

<button id="back-to-top" onclick="window.scrollTo({top:0,behavior:'smooth'})" title="Zpět nahoru" aria-label="Zpět nahoru">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
</button>

{% include "partials/base_scripts.html" %}
{% include "partials/base_panel.html" %}

<script>
  /* Sheet open/close */
  function openSheet() {
    document.getElementById('sheet-backdrop').classList.add('open');
    document.getElementById('sheet').classList.add('open');
    document.body.style.overflow = 'hidden';
  }
  function closeSheet() {
    document.getElementById('sheet-backdrop').classList.remove('open');
    document.getElementById('sheet').classList.remove('open');
    document.body.style.overflow = '';
  }
  /* Close sheet on navigation (HTMX) */
  document.body.addEventListener('htmx:afterSwap', function() { closeSheet(); });

  /* Swipe-to-close sheet */
  (function() {
    var sheet = document.getElementById('sheet');
    var startY = 0, currentY = 0, dragging = false;
    sheet.addEventListener('touchstart', function(e) {
      startY = e.touches[0].clientY;
      dragging = true;
    }, { passive: true });
    sheet.addEventListener('touchmove', function(e) {
      if (!dragging) return;
      currentY = e.touches[0].clientY;
      var diff = currentY - startY;
      if (diff > 0) {
        sheet.style.transform = 'translate3d(0,' + diff + 'px,0)';
        sheet.style.transition = 'none';
      }
    }, { passive: true });
    sheet.addEventListener('touchend', function() {
      dragging = false;
      sheet.style.transition = '';
      var diff = currentY - startY;
      if (diff > 80) {
        closeSheet();
      }
      sheet.style.transform = '';
      currentY = 0;
    });
  })();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify complete render**

Start server, check:
- Desktop (> 1024px): sidebar visible, bottom bar hidden
- Mobile (< 640px via DevTools): sidebar hidden, bottom bar visible, "Více" opens sheet
- Sheet closes on swipe-down, tap backdrop
- Links in sheet navigate correctly

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: update base.html with HTMX 2, Geist font, bottom bar, sheet, view transitions

Adds hx-boost for SPA-like navigation on all links.
Adds mobile bottom bar with Přehled/Objekty/Více.
Adds sheet overlay for 'Více' with swipe-to-close.
Switches from Inter to Geist font family.
Adds view-transition meta for browser-native page animations."
```

---

## Task 7: HTMX Partial Rendering — Backend

**Files:**
- Modify: `report/web.py`

- [ ] **Step 1: Add HTMX middleware to detect partial requests**

In `report/web.py`, add a middleware that detects the `HX-Request` header and wraps template responses to return only the `<main>` content. Add this after the SessionMiddleware setup (around line 175 in the original file). Find the section where middleware is added and add:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class HTMXPartialMiddleware(BaseHTTPMiddleware):
    """When HTMX requests a boosted page, strip the shell and return only <main> content."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Only process HTMX-boosted navigation requests (not HTMX fragment fetches)
        if (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") == "true"
            and hasattr(response, "body")
            and response.headers.get("content-type", "").startswith("text/html")
        ):
            body = response.body.decode("utf-8")
            # Extract content between <main ...id="content"> and </main>
            import re as _re
            m = _re.search(r'<main[^>]*id="content"[^>]*>(.*?)</main>', body, _re.DOTALL)
            if m:
                inner = m.group(1)
                # Also extract any <title> for HTMX to update document title
                tm = _re.search(r'<title>(.*?)</title>', body)
                title_tag = f'<title>{tm.group(1)}</title>' if tm else ''
                new_body = title_tag + inner
                return HTMLResponse(content=new_body, status_code=response.status_code)
        return response
```

Then register it:

```python
app.add_middleware(HTMXPartialMiddleware)
```

- [ ] **Step 2: Test HTMX navigation**

Open browser, navigate between pages. Check:
- No full page reload (sidebar stays, only content area changes)
- Browser back/forward buttons work
- URL updates correctly
- Direct URL access (non-HTMX) still returns full page

- [ ] **Step 3: Commit**

```bash
git add report/web.py
git commit -m "feat: add HTMX partial rendering middleware

Detects HX-Boosted requests and returns only <main> content,
enabling SPA-like navigation without full page reloads.
Falls back to full page for direct URL access."
```

---

## Task 8: Dashboard Template — Remove Inline Styles + Mobile Layout

**Files:**
- Modify: `templates/dashboard.html`

- [ ] **Step 1: Read current dashboard template**

Read the full `templates/dashboard.html` to understand all inline styles that need to be converted.

- [ ] **Step 2: Refactor dashboard template**

Replace all `style="..."` attributes with semantic CSS classes. Key changes:
- KPI cards container: add class `kpi-grid`
- Property rows: add `pr-hide-mobile` and `pr-hide-tablet` classes to columns that should hide on smaller screens (Odměna Rentero, Zdraví)
- Property list container: add class `stagger-list` for entry animations
- Owner filter dropdown: use `form-input` class
- Search input: use `form-input` class
- All inline `font-size`, `color`, `margin`, `padding` → use token variables via classes

Add `.kpi-grid` to components CSS:
```css
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-lg);
  margin-bottom: var(--space-xl);
}
```

- [ ] **Step 3: Test dashboard on desktop and mobile**

- Desktop: KPI cards in a row, property rows with all 7 columns
- Mobile: KPI cards 2x2, property rows as compact cards with name + financial + arrow
- Filter/search still work
- Owner filter still works
- KPI recalculation on filter still works

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard.html templates/partials/base_styles_components.html
git commit -m "refactor: dashboard template — remove inline styles, add responsive classes

Converts all inline styles to semantic classes.
Adds kpi-grid for responsive KPI layout.
Adds pr-hide-mobile/pr-hide-tablet for progressive column hiding.
Adds stagger-list for entry animations."
```

---

## Task 9: Remaining Templates — Remove Inline Styles

**Files:**
- Modify: `templates/expenses.html`
- Modify: `templates/property.html`
- Modify: `templates/bank.html`
- Modify: `templates/inventory.html`
- Modify: `templates/client.html`
- Modify: `templates/clients.html`
- Modify: `templates/admin_users.html`
- Modify: `templates/reconciliation.html`
- Modify: `templates/sources.html`
- Modify: `templates/logs.html`
- Modify: `templates/audit.html`
- Modify: `templates/login.html`

- [ ] **Step 1: Read each template and identify inline styles**

For each template, find all `style="..."` attributes and convert them to:
- Existing CSS classes (`.btn`, `.card`, `.data-table`, etc.)
- Token-based inline styles where classes don't exist yet (keep `style` but use `var(--space-*)` etc.)
- New utility classes if a pattern repeats 3+ times

- [ ] **Step 2: Add `responsive-cards` class to data tables that should transform on mobile**

Tables in `expenses.html`, `bank.html` — add class `responsive-cards` and `data-label` attributes to `<td>` elements:

```html
<td data-label="Datum">{{ row.date }}</td>
<td data-label="Částka">{{ row.amount }}</td>
```

- [ ] **Step 3: Test each page on desktop and mobile**

Check each page loads correctly on both desktop and mobile viewport. The key pages to verify:
- `/expenses` — table transforms to cards on mobile
- `/bank` — table transforms to cards on mobile
- `/property/*/year/month` — readable on mobile
- `/admin/users` — admin panel usable on mobile
- `/login` — centered, usable on mobile

- [ ] **Step 4: Commit**

```bash
git add templates/*.html
git commit -m "refactor: remove inline styles from all templates, add responsive table classes

Converts inline styles to semantic classes across all 12 remaining templates.
Adds responsive-cards + data-label for mobile table-to-card transformation.
All pages now render correctly on mobile, tablet, and desktop."
```

---

## Task 10: Delete Backup + Final Verification

**Files:**
- Delete: `templates/partials/base_styles_backup.html`

- [ ] **Step 1: Full test pass**

Start the server and test every page in both desktop and mobile viewports:

```
Desktop (> 1024px):
  [ ] / (dashboard) — KPI cards, property rows, filters, owner dropdown
  [ ] /property/{slug}/{year}/{month} — header, reservations table, badges
  [ ] /expenses — table with sorting
  [ ] /bank — bank transactions table
  [ ] /clients — client list
  [ ] /inventory — inventory table
  [ ] /reconciliation — reconciliation view
  [ ] /sources — source files list
  [ ] /logs — import logs
  [ ] /audit — audit trail
  [ ] /admin/users — user management
  [ ] /login — login form
  [ ] Theme toggle — dark ↔ light switches correctly
  [ ] HTMX nav — clicking links doesn't full-reload

Mobile (< 640px via DevTools):
  [ ] Bottom bar visible, 3 items
  [ ] "Více" opens sheet with all nav items
  [ ] Sheet closes on swipe-down and backdrop tap
  [ ] Dashboard — KPI 2x2, property cards
  [ ] Expenses — table as cards
  [ ] Bank — table as cards
  [ ] Back-to-top button above bottom bar
  [ ] Floating panel — full width at bottom

Tablet (640-1024px):
  [ ] Sidebar collapsed to icon rail
  [ ] Dashboard — 5 columns
```

- [ ] **Step 2: Delete backup file**

```bash
rm templates/partials/base_styles_backup.html
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: remove style backup, complete responsive redesign

All pages verified on desktop, tablet, and mobile.
HTMX navigation working. Theme switching working.
Bottom bar + sheet navigation working on mobile."
```

---

## Summary

| Task | Description | Estimated scope |
|------|-------------|----------------|
| 1 | Design tokens rewrite | ~120 lines new CSS |
| 2 | Layout shell (sidebar, bottom bar, sheet) | ~250 lines new CSS |
| 3 | Components extraction | ~700 lines (extract + convert) |
| 4 | Animations | ~100 lines new CSS |
| 5 | Responsive breakpoints | ~120 lines new CSS |
| 6 | Base HTML + HTMX + bottom bar markup | Rewrite base.html |
| 7 | HTMX backend middleware | ~30 lines Python |
| 8 | Dashboard template refactor | Edit 1 template |
| 9 | Remaining templates refactor | Edit 12 templates |
| 10 | Final verification + cleanup | Testing only |
