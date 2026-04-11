# Responsive Redesign — Full Design Spec

## Overview

Complete UI refactoring of the Rentero Property Manager web application. Goals: mobile-first responsive design, modern animation system, HTMX page transitions, new design token system, and dual theme (dark + light). No framework migration — vanilla CSS/JS + HTMX.

## Tech Stack

- CSS Custom Properties (OKLCH color system)
- CSS Grid + Container Queries
- CSS Transitions + Keyframe Animations
- HTMX 2.x (14KB, CDN) — SPA-like page transitions
- View Transitions API — browser-native page animation
- Geist font family (sans + mono)

---

## 1. Style Architecture

### Current state
One large `base_styles.html` (~600+ lines), inline `style="..."` scattered across templates. No consistent token system.

### New structure
```
templates/partials/
  base_styles.html            — tokens (custom properties), reset, base typography
  base_styles_layout.html     — app shell: sidebar, main area, bottom bar, sheet
  base_styles_components.html — buttons, cards, tables, badges, forms, inputs
  base_styles_animations.html — transitions, keyframes, reduced-motion
  base_styles_responsive.html — media queries, container queries, mobile overrides
```

### Design tokens
```css
/* Spacing (4pt scale) */
--space-xs: 4px;
--space-sm: 8px;
--space-md: 12px;
--space-lg: 16px;
--space-xl: 24px;
--space-2xl: 32px;
--space-3xl: 48px;
--space-4xl: 64px;

/* Radius */
--radius-sm: 4px;
--radius-md: 8px;
--radius-lg: 12px;

/* Timing */
--duration-fast: 150ms;
--duration-normal: 250ms;
--duration-slow: 350ms;
--ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);

/* Breakpoints (used in media queries, not as properties) */
/* mobile: < 640px */
/* tablet: 640px – 1024px */
/* desktop: > 1024px */
```

### Migration
All inline `style="..."` attributes in templates replaced with semantic CSS classes. This is prerequisite for responsive behavior.

---

## 2. Mobile Shell — Bottom Bar + Sheet

### Bottom bar (< 640px only)
- Fixed at bottom, 56px height
- 3 items: Přehled (grid icon), Objekty (building icon), Více (··· icon)
- Active item highlighted with accent color
- `main` gets `padding-bottom: 72px` to avoid overlap
- Hidden on desktop/tablet via `display: none` at `>= 640px`

### "Více" sheet
- Semi-transparent backdrop + panel slides up from bottom
- Contains all remaining nav items: Výdaje, Banka, Klienti, Inventory, Srovnání, Zdroje, Logy, Změny, Uživatelé (role-filtered)
- User info + logout at bottom of sheet
- Closes via: tap backdrop, swipe-down, or close button
- Animation: `translate3d(0, 100%, 0) → translate3d(0, 0, 0)`, 350ms expo-out

### Desktop sidebar (>= 640px)
- Remains as current design with CSS transition on resize
- Month navigation stays in sidebar

### Mobile month navigation
- Moves to top of `main` content area as compact strip
- Left/right arrows + current month centered
- Sticky on scroll

---

## 3. HTMX + Page Transitions

### Setup
- HTMX 2.x via CDN `<script>` in `base.html`
- `hx-boost="true"` on `<body>` — all standard links become AJAX transitions
- Server returns full HTML as before; HTMX swaps `<main id="content">` content

### Benefits
- No white-screen page reload
- Sidebar/bottom bar persist across navigation
- Scroll position preserved where appropriate

### View Transitions
- `<meta name="view-transition" content="same-origin">` for browser-native animation
- Fade + subtle slide on page change
- Pure CSS, no JS logic

### Backend changes
- Detect `HX-Request` header
- If HTMX request → return only `<main>` content (no sidebar/head/scripts)
- Fallback: normal requests work as before (full page)

### Template structure
```
base.html              — shell: head, sidebar, bottom-bar, <main id="content">
  {% block content %}{% endblock %}

dashboard.html         — extends base.html
property.html          — extends base.html
expenses.html          — extends base.html
...etc
```

Formalize `{% block content %}` pattern and add HTMX partial rendering support.

---

## 4. Animation System

### Easing
Expo-out (`cubic-bezier(0.16, 1, 0.3, 1)`) everywhere. No bounce/elastic.

### State transitions
| Element | Animation | Duration | Easing |
|---------|-----------|----------|--------|
| Hover (cards, buttons) | `translate(0, -2px)` + shadow | 150ms | ease-out |
| Button active | `scale(0.97)` | instant | — |
| Sidebar accordion | `grid-template-rows: 0fr → 1fr` | 250ms | expo-out |
| Sheet open/close | `translate3d(0, 100%, 0)` toggle | 350ms | expo-out |
| Page transition | fade + slide | 200ms | ease-out |
| Stagger per list item | delay `calc(var(--i) * 40ms)` | 300ms | expo-out |

### Entry animations
- Staggered `fadeSlideUp` on initial page load for list items (dashboard rows, reservations)
- `@keyframes fadeSlideUp` with `animation-delay: calc(var(--i) * 40ms)`
- Only on first load, NOT on filter changes

### Filtering
- Hiding rows: fade out → `display: none` (no layout jump)
- KPI numbers: CSS transition on opacity when values update

### Reduced motion
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 5. Design System — Color + Typography

### Typography
- **Font family**: Geist (sans) + Geist Mono — loaded from CDN/self-hosted
- **Headings**: Geist, weight 600-700, fixed rem scale
- **Body**: Geist, weight 400, 14px base
- **Tabular data**: Geist Mono, `font-variant-numeric: tabular-nums`
- **Type scale** (fixed rem, not fluid — this is a product UI):
  - `--text-xs`: 0.6875rem (11px)
  - `--text-sm`: 0.8125rem (13px)
  - `--text-base`: 0.875rem (14px)
  - `--text-lg`: 1rem (16px)
  - `--text-xl`: 1.25rem (20px)
  - `--text-2xl`: 1.5rem (24px)
- **Line height**: 1.5 for body, 1.2 for headings
- **Max line length**: 75ch for body text

### Color system (OKLCH)

**Dark mode** (default):
```css
--bg-0:   oklch(0.11 0.015 260);  /* deepest */
--bg-1:   oklch(0.14 0.015 260);  /* app background */
--bg-2:   oklch(0.18 0.012 260);  /* panels */
--bg-3:   oklch(0.22 0.010 260);  /* cards */
--bg-4:   oklch(0.26 0.008 260);  /* hover */

--text-100: oklch(0.90 0.010 260);  /* primary text */
--text-200: oklch(0.60 0.015 260);  /* secondary text */
--text-300: oklch(0.40 0.010 260);  /* muted text */

--border-base: oklch(0.25 0.010 260 / 0.5);
--border-top:  oklch(0.35 0.010 260 / 0.5);
```

**Light mode** (`[data-theme="light"]`):
```css
--bg-0:   oklch(0.97 0.005 260);
--bg-1:   oklch(0.98 0.004 260);
--bg-2:   oklch(0.95 0.006 260);
--bg-3:   oklch(1.00 0.000 0);    /* white cards */
--bg-4:   oklch(0.93 0.006 260);

--text-100: oklch(0.15 0.015 260);
--text-200: oklch(0.45 0.015 260);
--text-300: oklch(0.65 0.010 260);

--border-base: oklch(0.85 0.005 260 / 0.5);
--border-top:  oklch(0.80 0.008 260 / 0.5);
```

**Accent colors** (same for both themes, OKLCH):
```css
--color-primary: oklch(0.65 0.18 280);  /* indigo-violet */
--color-green:   oklch(0.70 0.18 155);
--color-amber:   oklch(0.75 0.15 75);
--color-red:     oklch(0.65 0.20 20);
--color-blue:    oklch(0.65 0.15 250);
```

All neutrals tinted toward hue 260 (indigo) for cohesion.

**Theme switching**: `[data-theme="light"]` on `<html>`, all variables overridden in one CSS block. JS toggles the attribute and persists to localStorage.

---

## 6. Responsive Layout

### Breakpoints
- `< 640px` — mobile: bottom bar, single column
- `640px – 1024px` — tablet: sidebar collapsed to icons, 2 columns
- `> 1024px` — desktop: full sidebar, multi-column

### Dashboard (Přehled)
- **Desktop**: 7-column grid (current)
- **Tablet**: hide Odměna + Zdraví → 5 columns
- **Mobile**: vertical card list. Each card shows: property name + main figure (výplata/ubytování) + status badge. Tap → expands details inline

### Property detail
- **Desktop**: current layout
- **Mobile**: vertical scroll (no tabs). Reservations as compact cards: code + guest + amount + status. Tap → detail sheet slides up

### Expenses / Banka
- **Desktop**: table
- **Mobile**: tables → key-value card layout. Amounts in large font. Horizontal scroll only for tables that cannot transform

### KPI cards (dashboard header)
- **Desktop**: row
- **Mobile**: 2x2 grid, more compact, larger numbers, smaller labels

### Principle
Not shrink — **re-compose**. Each breakpoint is a considered layout, not a compressed desktop.

---

## Files to modify

| File | Action |
|------|--------|
| `templates/partials/base_styles.html` | REWRITE — tokens + reset + typography only |
| `templates/partials/base_styles_layout.html` | CREATE — app shell, sidebar, bottom bar, sheet |
| `templates/partials/base_styles_components.html` | CREATE — buttons, cards, tables, badges, forms |
| `templates/partials/base_styles_animations.html` | CREATE — transitions, keyframes, reduced-motion |
| `templates/partials/base_styles_responsive.html` | CREATE — media queries, container queries |
| `templates/base.html` | EDIT — add HTMX, restructure to shell + block content |
| `templates/partials/base_sidebar.html` | EDIT — add mobile bottom bar, sheet, responsive classes |
| `templates/dashboard.html` | EDIT — remove inline styles, add responsive classes, mobile cards |
| `templates/property.html` | EDIT — responsive layout, mobile card view |
| `templates/expenses.html` | EDIT — table → card transformation on mobile |
| `templates/inventory.html` | EDIT — responsive layout |
| `templates/client.html` | EDIT — responsive layout |
| `templates/admin_users.html` | EDIT — responsive layout |
| `report/web.py` | EDIT — HTMX partial rendering support |
| All other templates | EDIT — remove inline styles, add semantic classes |

---

## Verification

1. Desktop: all pages render correctly, sidebar works, theme toggle works
2. Mobile (< 640px): bottom bar visible, sidebar hidden, sheet opens/closes, all pages usable
3. Tablet (640-1024px): sidebar collapsed, layouts adapted
4. HTMX: page transitions work without full reload, back button works
5. Animations: smooth 60fps, no jank, reduced-motion respected
6. Theme: dark/light switch works, persists across reload
7. No regressions: all existing functionality (filters, KPIs, RBAC) still works
