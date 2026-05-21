# Phase 6t — UI overhaul: BookOrbit visual parity + mobile-first PWA

Status: design • Owner: biblichor • Date: 2026-05-21

## Goal

Bring biblichor's web UI to the same look, feel, and mobile
behavior as BookOrbit so the two apps feel like one product when
the user swipes between them on their phone.

## Non-goals

- No backend API changes. UI-only.
- No CLI changes.
- No new pages. Existing 9 routes stay (Queue, Sources, Scrapers,
  Mirrors, Scoring, Schedule, Settings, Logs, Library).
- No removal of features. Every action available today stays
  available; some get rearranged.
- No PWA push notifications. Just installable + offline shell.

## What "match BookOrbit" means concretely

BookOrbit and biblichor already share:
Vue 3, Vite, Tailwind, shadcn-vue (via radix-vue), Lucide, Pinia.

Gaps to close:

| | biblichor today | BookOrbit | After 6t |
|---|---|---|---|
| Tailwind | v3.4 (JS config, hsl tokens) | v4 (CSS `@theme`, oklch tokens with tintable hue) | v4 + oklch |
| PWA | none | manifest + service worker (vite-plugin-pwa) | same |
| App shell | desktop-only sidebar | side-rail >=768px, bottom-strip <768px | same |
| Touch targets | 32-40px | 44px+ everywhere | 44px+ everywhere |
| Theme tokens | `--background: hsl(..)`, fixed | `--background: oklch(L C H)` with `var(--tint-h)` parameter | parameter-tinted oklch |
| Hue picker | n/a | user picks accent hue in Settings | user picks accent hue in Settings |
| Mobile meta tags | viewport only | full Apple/web-app meta set | full set |
| Page density | desktop tables | tables collapse to card lists on narrow | tables collapse to card lists |

## Architecture

Single Vue app, no backend changes. Five units:

1. **`webapp/src/styles/theme.css`** (new) — the design-token file.
   Defines `:root` and `.dark` with oklch + tint variables. Tailwind
   v4 reads this via `@theme` in `styles/tailwind.css`. Replaces
   the old `hsl(var(--...))` chain in `tailwind.config.js`.

2. **`webapp/src/composables/useTint.ts`** (new) — Pinia-backed
   composable that writes `--tint-h` to `<html>` based on user
   selection. Persists in localStorage.

3. **`webapp/src/components/AppShell.vue`** (rewrite) — the layout
   shell becomes a responsive grid:
   - `>=768px`: left side-rail (current layout, restyled)
   - `<768px`: top header bar + bottom horizontal scroll-strip with
     all 9 nav items. Active item highlighted. Touch-scroll snap.

4. **`webapp/vite.config.ts`** (modify) — wire `vite-plugin-pwa`
   for manifest + service worker. App becomes installable.

5. **Per-page mobile passes** — every page card layout audited for
   narrow-screen flow. Tables (Queue, Sources, Scrapers, Mirrors,
   Schedule, Logs) collapse to card-list at `<768px`. Forms
   reflow to single column. Action buttons grow to 44px+ tap targets.

## Theme tokens (the actual values we're adopting)

Extracted from BookOrbit's compiled CSS. Light + dark variants.
Note `var(--tint-h)` is a runtime variable so the user can pick
the accent hue; default is BookOrbit's blue `265`.

```css
/* webapp/src/styles/theme.css */
:root {
  --tint-h: 265;                  /* default blue; user can change */
  --tint-c-surface: 0.005;        /* chroma for surfaces */
  --tint-c-content: 0.001;        /* chroma for text */
  --bg-lift: 0;                   /* dark mode levitation offset */
  --radius: 8px;

  /* light mode */
  --background:          oklch(0.99  var(--tint-c-surface) var(--tint-h));
  --foreground:          oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --card:                oklch(0.975 var(--tint-c-surface) var(--tint-h));
  --card-foreground:     oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --popover:             oklch(0.975 var(--tint-c-surface) var(--tint-h));
  --popover-foreground:  oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --primary:             oklch(0.21  var(--tint-c-surface) var(--tint-h));
  --primary-foreground:  oklch(0.985 0.004 var(--tint-h));
  --secondary:           oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --muted:               oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --muted-foreground:    oklch(0.52  0.001 var(--tint-h));
  --accent:              oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --accent-foreground:   oklch(0.21  var(--tint-c-surface) var(--tint-h));
  --destructive:         oklch(0.577 0.245 27.325);
  --destructive-foreground: oklch(0.985 0 0);
  --border:              oklch(0.878 var(--tint-c-surface) var(--tint-h));
  --input:               oklch(0.878 var(--tint-c-surface) var(--tint-h));
  --ring:                oklch(0.708 var(--tint-c-surface) var(--tint-h));
}

.dark {
  --bg-lift: 0;
  --background:          oklch(calc(0.145 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --foreground:          oklch(0.985 0.001 var(--tint-h));
  --card:                oklch(calc(0.18 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --card-foreground:     oklch(0.985 0.001 var(--tint-h));
  --popover:             oklch(calc(0.18 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --popover-foreground:  oklch(0.985 0.001 var(--tint-h));
  --primary:             oklch(0.91  var(--tint-c-surface) var(--tint-h));
  --primary-foreground:  oklch(0.18  var(--tint-c-surface) var(--tint-h));
  --secondary:           oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --muted:               oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --muted-foreground:    oklch(0.725 0.001 var(--tint-h));
  --accent:              oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --accent-foreground:   oklch(0.985 0.001 var(--tint-h));
  --destructive:         oklch(0.704 0.191 22.216);
  --destructive-foreground: oklch(0.985 0 0);
  --border:              oklch(1     0 0 / 0.1);
  --input:               oklch(1     0 0 / 0.15);
  --ring:                oklch(0.556 0 0);
}
```

## Mobile shell

```
<768px (narrow / phone)
+---------------------------------------+
| <-back   Page Title         theme-tog |  top header (h=56)
+---------------------------------------+
|                                       |
|         page content                  |
|         (full-bleed lists,            |
|          single-column forms)         |
|                                       |
|                                       |
+---------------------------------------+
|  Queue  Lib  Src  Scr  ...    >       |  bottom scroll-strip
+---------------------------------------+    (all 9, scroll-snap)

>=768px (desktop)
+-------+-------------------------------+
|       | <-back  Page Title    toggle  |  top header
|       +-------------------------------+
| Queue |                               |
| Lib   |                               |
| Src   |       page content            |
| Scr   |       (cards, tables)         |
| Mir   |                               |
| Sco   |                               |
| Sch   |                               |
| Set   |                               |
| Logs  |                               |
+-------+-------------------------------+
```

Bottom strip pinned to viewport bottom, all 9 items in a horizontal
`overflow-x-auto snap-x snap-mandatory` row. Each item is 64px wide
with a 24px icon + 12px label. The currently-active item is
highlighted with `--primary` background.

## Per-page mobile passes

Each page gets:

- **Cards stack vertically** on narrow (no side-by-side card pairs).
- **Tables collapse** to card-list rows at `<768px` (the per-row
  cells become labeled rows inside a card). Headers visible inside
  each card so the data stays self-describing.
- **Forms single-column** below 600px.
- **Primary action buttons** grow to `min-height: 44px` and become
  full-width below 480px.
- **Sticky page-level header** with the page title + 1-2 primary
  action buttons (e.g. "Run now" on Schedule, "Add source" on
  Sources, "Test SMTP" on Settings).

Pages and their narrow-screen plan:

| Page | Narrow plan |
|---|---|
| Queue | Book row table -> card-list. Each card: title (big) + author + status pill + bulk-select checkbox. Bulk-action bar pinned to bottom above the nav strip. |
| Sources | List of source cards (one per source). Add-source form opens as a bottom sheet. |
| Scrapers | Strategy list as cards. Drag-to-reorder via a long-press handle on the right. Bench section collapsible. |
| Mirrors | Mirror probe list as cards. Tap card -> see probe history modal. |
| Scoring | Sliders + inputs in a single column. Visible feedback chip ("Auto-pick at 70+"). |
| Schedule | Job table -> card-list. Pause/Resume/Run buttons as 44px icon buttons. |
| Settings | Section accordion (SMTP / Kindle / Pushover / Calibre / Scrapers / Security / BookOrbit). Test buttons inline. |
| Logs | Event stream sticks to current desktop layout; no narrow change needed (it's already a vertical list). |
| Library | Already mobile-friendly post-Phase 6p; refresh tokens to match the new theme. |

## PWA manifest + service worker

```ts
// webapp/vite.config.ts (additions only)
import { VitePWA } from 'vite-plugin-pwa'

plugins: [
  vue(),
  VitePWA({
    registerType: 'autoUpdate',
    workbox: {
      cleanupOutdatedCaches: true,
      navigateFallback: '/',
      navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/healthz/],
    },
    manifest: {
      name: 'biblichor',
      short_name: 'biblichor',
      description: 'Goodreads -> Anna -> Kindle automation + BookOrbit library',
      theme_color: '#0a0a0a',
      background_color: '#fafafa',
      display: 'standalone',
      orientation: 'any',
      start_url: '/queue',
      scope: '/',
      icons: [
        { src: '/pwa-192.png', sizes: '192x192', type: 'image/png' },
        { src: '/pwa-512.png', sizes: '512x512', type: 'image/png' },
        { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
      ],
    },
  }),
],
```

`index.html` gets the full mobile meta set:

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0a0a0a" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#fafafa" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="biblichor">
<meta name="mobile-web-app-capable" content="yes">
```

## Sub-phase rollout

Each sub-phase is independently shippable; user can pause between
any two without losing functionality.

| Phase | Scope | Effort |
|---|---|---|
| **6t.1** | Tailwind v3 -> v4 upgrade + `theme.css` + `useTint` composable. Build still produces the same UI; only tokens change. Verify visually on every page. | 1.5d |
| **6t.2** | App shell rewrite. Side-rail on `>=768px`, bottom scroll-strip on `<768px`. Top header bar shared. | 1d |
| **6t.3** | PWA wiring: manifest, service worker, icons, install prompt. App installable on iOS + Android + desktop browsers. | 0.5d |
| **6t.4** | Page mobile passes — Queue, Sources, Library (the 3 most-used routes). | 1.5d |
| **6t.5** | Page mobile passes — Scrapers, Mirrors, Settings. | 1d |
| **6t.6** | Page mobile passes — Scoring, Schedule, Logs + final polish + animations + 44px audit pass. | 1d |
| **6t.7** | Theme picker UI (the hue ring) on Settings page so users can choose accent. Persists per-user. | 0.5d |
| **6t.8** | Live verification on claude-1 (mobile + desktop), iOS PWA install test, doctor sweep, commit + wiki. | 0.5d |

**Total: ~7.5 days.**

## Risk register

| Risk | Mitigation |
|---|---|
| Tailwind v4 breaks existing `class` strings (some v3 utilities renamed) | Run the official `@tailwindcss/upgrade` codemod first; manual audit of arbitrary-value classes; visual diff each page before merging |
| Service worker caches stale SPA after rebuild | `registerType: 'autoUpdate'` + `cleanupOutdatedCaches`; force-refresh prompt in UI when an update is detected |
| Existing test files target current selectors/structure | UI tests are minimal today (mostly E2E smoke + backend unit). Visual regressions caught manually during live verify |
| `oklch()` not supported in Safari < 15.4 | Vite handles fallback via PostCSS; we accept Safari 15.4+ baseline (modern phones) |
| Bottom nav strip overlap with iOS home indicator | `viewport-fit=cover` + `env(safe-area-inset-bottom)` in the strip's padding |
| BookOrbit's tint-c-surface chroma is so low it looks gray | Default tint matches BookOrbit's blue (265); user can pick a richer hue via the Settings picker |

## Acceptance criteria

- iPhone Safari + Chrome Android: open biblichor, every page is usable with one thumb
- "Add to Home Screen" produces an icon that opens biblichor full-bleed (no browser chrome)
- Service worker registered; offline shell loads when network is down
- All 9 nav items reachable from the bottom strip on mobile, sidebar on desktop
- Hue picker on Settings page changes the accent across the whole app instantly
- Tablet (iPad) breakpoint at `>=768px` shows the side-rail
- Existing tests + suite stay green; full mobile audit done as a manual smoke pass

## Out of scope (defer)

- BookOrbit-style hero card carousels (we don't have books displayed as cover art)
- Multi-language i18n (single locale today)
- Animated route transitions (Tailwind v4 has them; we evaluate after the v4 migration)
- Custom font (we keep Inter / system stack from BookOrbit)
