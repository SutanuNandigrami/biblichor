# Phase 6t — UI overhaul (BookOrbit parity + mobile-first PWA) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring biblichor's web UI to BookOrbit's exact stack and visual language — Tailwind v4 with oklch tintable theme, reka-ui primitives, vue-router 5, PWA installable, responsive shell with bottom nav on mobile, every page mobile-friendly.

**Architecture:** Single Vue 3 SPA, no backend changes. Adopt BookOrbit's tooling (`@tailwindcss/vite`, `vite-plugin-pwa`, `vue-sonner`, `vue-draggable-plus`, `@tanstack/vue-table`, `vue-virtual-scroller`, bundled `@fontsource-variable/inter`). Design tokens migrate from `hsl(var(--..))` to `oklch(L C var(--tint-h))` so the user can pick their accent hue.

**Tech Stack:** Vue 3.5 · Vite 8 · Tailwind v4 · `@tailwindcss/vite` · `reka-ui` 2.9 · vue-router 5 · pinia 3 · `vite-plugin-pwa` 1.3 · `lucide-vue-next` · `vue-sonner` · `tw-animate-css` · `@tanstack/vue-table` · `vue-draggable-plus` · `vue-virtual-scroller` · `@vite-pwa/assets-generator`.

**Working dir**: `~/endless-library/webapp` on claude-1 unless noted. `COMPOSE = "docker compose -f deploy/compose.yml --env-file .env"`.

**Build sanity** between sections: `cd webapp && npm run build` must succeed.
**Suite sanity** between sections: from repo root, `.venv/bin/python -m pytest -q` must stay green (UI changes don't touch Python tests, so 818 passed should remain).

---

## Section 6t.0 — vue-router 4 → 5 bump

### Task 1: Bump vue-router and verify routing still works

**Files:**
- Modify: `webapp/package.json`
- Modify: `webapp/src/router/index.ts` (if router config uses any 4-only API)
- Verify: `webapp/src/components/AppShell.vue` (uses `<RouterLink>`/`<RouterView>`)

- [ ] **Step 1: Bump dep**

```bash
cd ~/endless-library/webapp
npm install vue-router@^5.0.7
```

Expected: `package.json` shows `"vue-router": "^5.0.7"`.

- [ ] **Step 2: Try build**

```bash
npm run build
```

Expected: passes. vue-router 5 is API-compatible with 4 at the `<RouterView>` / `<RouterLink>` / `useRouter()` level we use.

- [ ] **Step 3: If build fails on type errors**, inspect each error and fix. Common 5.0 changes:
  - `createWebHistory` import path unchanged
  - `useRoute()` return type slightly refined; if you use `route.params.foo`, cast or assert
  - `RouterScrollBehavior` named export changes if used

- [ ] **Step 4: Open the SPA, click through every nav item**, confirm no route errors in console.

- [ ] **Step 5: Commit**

```bash
cd ~/endless-library
git add webapp/package.json webapp/package-lock.json webapp/src/router/
git commit -m "Phase 6t.0: vue-router 4.6 -> 5.0.7

API-compatible bump per BookOrbit's client/package.json. No
behavior change; all <RouterView>/<RouterLink>/useRouter()
call sites stay the same.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.1 — Tailwind v3 → v4 + theme.css + tw-animate-css + bundled Inter

### Task 2: Swap Tailwind v3 plugins for v4 plugin + new entry CSS

**Files:**
- Modify: `webapp/package.json` (deps)
- Delete: `webapp/tailwind.config.js`, `webapp/postcss.config.js`
- Modify: `webapp/vite.config.ts`
- Create: `webapp/src/styles/app.css` (was `index.css` or similar)
- Modify: `webapp/src/main.ts` (import the new entry)

- [ ] **Step 1: Install + remove deps**

```bash
cd ~/endless-library/webapp
npm uninstall tailwindcss tailwindcss-animate autoprefixer postcss
npm install -D tailwindcss@^4.3.0 @tailwindcss/vite@^4.3.0 tw-animate-css@^1.4.0
npm install @fontsource-variable/inter@^5.2.8
```

- [ ] **Step 2: Delete the old config files**

```bash
rm webapp/tailwind.config.js webapp/postcss.config.js
```

- [ ] **Step 3: Update `vite.config.ts`**

Open `webapp/vite.config.ts`. Add the Tailwind v4 plugin and remove the postcss section if present.

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    proxy: {
      '/api':     'http://localhost:8090',
      '/ws':      { target: 'ws://localhost:8090', ws: true },
      '/healthz': 'http://localhost:8090',
    },
  },
})
```

(Keep your existing alias/proxy values if they differ from the example.)

- [ ] **Step 4: Locate the existing CSS entry**

```bash
ls webapp/src/styles/ 2>/dev/null
grep -l "tailwindcss" webapp/src/*.css webapp/src/**/*.css 2>/dev/null
```

You're looking for the file that has `@tailwind base;` / `@tailwind components;` / `@tailwind utilities;`. Rename or move it; the v4 file we create next will be the new entry.

- [ ] **Step 5: Create `webapp/src/styles/app.css`**

```css
/* Tailwind v4 entry. @import 'tailwindcss' is the only required line;
   everything else is theme + plugins + globals. */
@import "tailwindcss";
@import "tw-animate-css";
@import "@fontsource-variable/inter/index.css";

/* Theme tokens live in a separate file for clarity; @import here so
   the @theme block is registered before any utility class is evaluated. */
@import "./theme.css";

/* Make `dark:*` utilities respond to the `.dark` class on <html>, not
   only to OS preference. This matches our existing dark-mode toggle. */
@custom-variant dark (&:where(.dark, .dark *));

html {
  font-family: 'Inter Variable', system-ui, -apple-system, BlinkMacSystemFont,
    'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  background: var(--background);
  color: var(--foreground);
}

/* Respect iOS safe-area-insets globally - the bottom-nav strip uses
   env(safe-area-inset-bottom) for its padding. */
:root {
  --safe-area-bottom: env(safe-area-inset-bottom, 0px);
  --safe-area-top: env(safe-area-inset-top, 0px);
}
```

- [ ] **Step 6: Update `webapp/src/main.ts`**

Replace any `import './index.css'` or `import './styles/...'` line with:

```ts
import './styles/app.css'
```

at the top of the file.

- [ ] **Step 7: Run build to make sure the v4 swap is wired**

```bash
npm run build
```

Expected: build PASSES even though `theme.css` doesn't exist yet (next task). If it complains about the missing import, comment out `@import "./theme.css"` temporarily and re-run.

If utility classes like `bg-background` produce warnings, that's because v4 finds `background` in the v3 config syntax. They'll resolve when `theme.css` lands.

- [ ] **Step 8: Commit**

```bash
cd ~/endless-library
git add webapp/package.json webapp/package-lock.json webapp/vite.config.ts \
        webapp/src/main.ts webapp/src/styles/
git rm webapp/tailwind.config.js webapp/postcss.config.js
git commit -m "Phase 6t.1: Tailwind v3 -> v4 plumbing

Swap postcss-based v3 for @tailwindcss/vite plugin. Drop
tailwind.config.js + postcss.config.js (v4 uses CSS-only
config). Adopt tw-animate-css (replaces tailwindcss-animate,
which doesn't ship for v4). Bundle @fontsource-variable/inter
so we no longer rely on Google Fonts CDN.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: theme.css with oklch tokens + tintable hue (light + dark)

**Files:**
- Create: `webapp/src/styles/theme.css`

- [ ] **Step 1: Write the file**

```css
/* webapp/src/styles/theme.css
 *
 * Phase 6t.1: design tokens for biblichor.
 *
 * Adopts BookOrbit's oklch + tintable-hue scheme so the two apps feel
 * like one product. --tint-h is a runtime variable the user can change
 * via the Settings hue picker (Phase 6t.7); the default is BookOrbit's
 * blue (265). Light + dark variants share the same hue; only L and
 * chroma differ.
 */

:root {
  --tint-h: 265;
  --tint-c-surface: 0.005;
  --tint-c-content: 0.001;
  --bg-lift: 0;
  --radius: 8px;

  --background:           oklch(0.99  var(--tint-c-surface) var(--tint-h));
  --foreground:           oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --card:                 oklch(0.975 var(--tint-c-surface) var(--tint-h));
  --card-foreground:      oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --popover:              oklch(0.975 var(--tint-c-surface) var(--tint-h));
  --popover-foreground:   oklch(0.145 var(--tint-c-surface) var(--tint-h));
  --primary:              oklch(0.21  var(--tint-c-surface) var(--tint-h));
  --primary-foreground:   oklch(0.985 0.004 var(--tint-h));
  --secondary:            oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --secondary-foreground: oklch(0.21  var(--tint-c-surface) var(--tint-h));
  --muted:                oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --muted-foreground:     oklch(0.52  0.001 var(--tint-h));
  --accent:               oklch(0.955 var(--tint-c-surface) var(--tint-h));
  --accent-foreground:    oklch(0.21  var(--tint-c-surface) var(--tint-h));
  --destructive:          oklch(0.577 0.245 27.325);
  --destructive-foreground: oklch(0.985 0 0);
  --border:               oklch(0.878 var(--tint-c-surface) var(--tint-h));
  --input:                oklch(0.878 var(--tint-c-surface) var(--tint-h));
  --ring:                 oklch(0.708 var(--tint-c-surface) var(--tint-h));
}

.dark {
  --bg-lift: 0;

  --background:           oklch(calc(0.145 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --foreground:           oklch(0.985 0.001 var(--tint-h));
  --card:                 oklch(calc(0.18  + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --card-foreground:      oklch(0.985 0.001 var(--tint-h));
  --popover:              oklch(calc(0.18  + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --popover-foreground:   oklch(0.985 0.001 var(--tint-h));
  --primary:              oklch(0.91  var(--tint-c-surface) var(--tint-h));
  --primary-foreground:   oklch(0.18  var(--tint-c-surface) var(--tint-h));
  --secondary:            oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --secondary-foreground: oklch(0.985 0.001 var(--tint-h));
  --muted:                oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --muted-foreground:     oklch(0.725 0.001 var(--tint-h));
  --accent:               oklch(calc(0.245 + var(--bg-lift)) var(--tint-c-surface) var(--tint-h));
  --accent-foreground:    oklch(0.985 0.001 var(--tint-h));
  --destructive:          oklch(0.704 0.191 22.216);
  --destructive-foreground: oklch(0.985 0 0);
  --border:               oklch(1 0 0 / 0.1);
  --input:                oklch(1 0 0 / 0.15);
  --ring:                 oklch(0.556 0 0);
}

/* Tailwind v4 @theme block — registers utility-class color names that
 * map to the variables above. After this block, classes like
 * `bg-background`, `text-foreground`, `bg-card`, `border-border`, etc.
 * Just Work.
 */
@theme inline {
  --color-background:           var(--background);
  --color-foreground:           var(--foreground);
  --color-card:                 var(--card);
  --color-card-foreground:      var(--card-foreground);
  --color-popover:              var(--popover);
  --color-popover-foreground:   var(--popover-foreground);
  --color-primary:              var(--primary);
  --color-primary-foreground:   var(--primary-foreground);
  --color-secondary:            var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted:                var(--muted);
  --color-muted-foreground:     var(--muted-foreground);
  --color-accent:               var(--accent);
  --color-accent-foreground:    var(--accent-foreground);
  --color-destructive:          var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-border:               var(--border);
  --color-input:                var(--input);
  --color-ring:                 var(--ring);

  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 4px);
}
```

- [ ] **Step 2: Un-comment the `@import "./theme.css"` line in `app.css`** (if you commented it in Task 2).

- [ ] **Step 3: Build + visual smoke test**

```bash
cd ~/endless-library/webapp && npm run build
```

Expected: PASS. Bundle size shrinks compared to v3 because v4 ships utilities on demand.

- [ ] **Step 4: Visual diff**

Rebuild the biblichor container so the new SPA dist gets served:

```bash
cd ~/endless-library
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
sleep 8
```

Open the dashboard (Library page in browser). Toggle dark mode (the theme toggle in the AppShell). Both light and dark should render with the new oklch palette — slightly different blues compared to v3 but the structure is identical.

- [ ] **Step 5: Commit**

```bash
git add webapp/src/styles/theme.css webapp/src/styles/app.css
git commit -m "Phase 6t.1: theme.css with oklch + tintable hue

BookOrbit's design-token scheme: --tint-h is a runtime variable
the user can change (Phase 6t.7 ships the picker). All shadcn
class colors (--color-background etc.) registered via @theme
inline so bg-background / text-foreground / etc. work as before
without a tailwind.config.js.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.1b — radix-vue → reka-ui migration

### Task 4: Swap the shadcn-vue primitive package

**Files:**
- Modify: `webapp/package.json`
- Modify: every file under `webapp/src/components/ui/` that imports from `'radix-vue'`

- [ ] **Step 1: Install reka-ui, remove radix-vue**

```bash
cd ~/endless-library/webapp
npm uninstall radix-vue
npm install reka-ui@^2.9.7
```

- [ ] **Step 2: Find every `radix-vue` import**

```bash
grep -rln "from 'radix-vue'" src/
```

Expected: a list of files under `src/components/ui/`. The import names map 1-to-1 to reka-ui's exports.

- [ ] **Step 3: Swap the import path in each file**

For every file the grep returned, replace `from 'radix-vue'` with `from 'reka-ui'`. You can do this with one command (validate `git diff` after):

```bash
cd ~/endless-library/webapp
grep -rl "from 'radix-vue'" src/ | xargs sed -i "s|from 'radix-vue'|from 'reka-ui'|g"
git diff src/components/ui/ | head -40
```

Expected: every `import { ... } from 'radix-vue'` is now `from 'reka-ui'`. No component name changes (reka-ui kept the names identical).

- [ ] **Step 4: Build**

```bash
npm run build
```

Expected: PASS. If a specific component name DID change in v2 (rare), the error tells you the missing export — google `reka-ui <name>` for the new name and update that one line.

- [ ] **Step 5: Run the SPA and verify components render**

```bash
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
```

Open `/queue` and check: dialog modal opens (book detail drawer), dropdowns work, toasts appear, tooltips show. If any one is broken, the import path didn't take effect for that primitive — re-run Step 3 against that file.

- [ ] **Step 6: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/src/components/ui/
git commit -m "Phase 6t.1b: radix-vue 1.9 -> reka-ui 2.9

reka-ui is the renamed-and-bumped successor to radix-vue.
Component names + APIs unchanged; only the import path
changes. Verified Dialog, Tooltip, Toast, Dropdown render
correctly post-swap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.2 — Responsive app shell

### Task 5: useTint composable + Pinia store

**Files:**
- Create: `webapp/src/composables/useTint.ts`

- [ ] **Step 1: Write the composable**

```ts
// webapp/src/composables/useTint.ts
// Phase 6t.2: write the --tint-h CSS variable on <html> so all
// oklch-keyed tokens respond. Persists across reloads in localStorage.

const STORAGE_KEY = 'biblichor.tint-h'

export function applyTint(hue: number): void {
  const safe = Math.max(0, Math.min(360, Math.round(hue)))
  document.documentElement.style.setProperty('--tint-h', String(safe))
  try {
    localStorage.setItem(STORAGE_KEY, String(safe))
  } catch {
    /* localStorage unavailable (private mode); silently no-op */
  }
}

export function readSavedTint(defaultHue = 265): number {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === null) return defaultHue
    const n = Number(v)
    return Number.isFinite(n) ? n : defaultHue
  } catch {
    return defaultHue
  }
}

export function useTint() {
  return { applyTint, readSavedTint }
}
```

- [ ] **Step 2: Apply on app boot**

In `webapp/src/main.ts`, add after the imports and before `.mount('#app')`:

```ts
import { applyTint, readSavedTint } from '@/composables/useTint'
applyTint(readSavedTint())
```

- [ ] **Step 3: Build + verify**

```bash
npm run build
```

Expected: PASS. In devtools, after page load, `getComputedStyle(document.documentElement).getPropertyValue('--tint-h')` returns `265`. Run `applyTint(20)` from devtools console — the page tints orange instantly.

- [ ] **Step 4: Commit**

```bash
git add webapp/src/composables/useTint.ts webapp/src/main.ts
git commit -m "Phase 6t.2: useTint composable writes --tint-h to <html>

Pure JS, no Pinia store yet (the hue is a single number; no need
for a store). localStorage persistence; defaults to BookOrbit's
blue (265). Settings page in Phase 6t.7 wires a picker UI to this.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: BottomNav + MobileHeader components

**Files:**
- Create: `webapp/src/components/BottomNav.vue`
- Create: `webapp/src/components/MobileHeader.vue`

- [ ] **Step 1: Write BottomNav**

```vue
<!-- webapp/src/components/BottomNav.vue -->
<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { Inbox, Database, Cpu, Server, Sliders, Clock, Settings as SettingsIcon, FileText, Library } from 'lucide-vue-next'

const items = [
  { to: '/queue',    label: 'Queue',    icon: Inbox },
  { to: '/library',  label: 'Library',  icon: Library },
  { to: '/sources',  label: 'Sources',  icon: Database },
  { to: '/scrapers', label: 'Scrapers', icon: Cpu },
  { to: '/mirrors',  label: 'Mirrors',  icon: Server },
  { to: '/scoring',  label: 'Scoring',  icon: Sliders },
  { to: '/schedule', label: 'Schedule', icon: Clock },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/logs',     label: 'Logs',     icon: FileText },
]
</script>

<template>
  <nav
    class="fixed inset-x-0 bottom-0 z-30 bg-card border-t border-border md:hidden"
    :style="{ paddingBottom: 'var(--safe-area-bottom)' }"
    aria-label="Primary"
  >
    <ul class="flex overflow-x-auto snap-x snap-mandatory scroll-pl-2 px-2 py-1.5">
      <li v-for="item in items" :key="item.to" class="snap-start shrink-0">
        <RouterLink
          :to="item.to"
          class="flex flex-col items-center justify-center w-16 h-12 rounded-lg
                 text-[10px] font-medium text-muted-foreground
                 hover:text-foreground"
          active-class="text-primary-foreground bg-primary"
        >
          <component :is="item.icon" class="w-5 h-5" />
          <span class="leading-tight mt-0.5">{{ item.label }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>
```

- [ ] **Step 2: Write MobileHeader**

```vue
<!-- webapp/src/components/MobileHeader.vue -->
<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import { Moon, Sun } from 'lucide-vue-next'

const route = useRoute()
const title = computed(() => {
  const titles: Record<string, string> = {
    '/queue': 'Queue',
    '/library': 'Library',
    '/sources': 'Sources',
    '/scrapers': 'Scrapers',
    '/mirrors': 'Mirrors',
    '/scoring': 'Scoring',
    '/schedule': 'Schedule',
    '/settings': 'Settings',
    '/logs': 'Logs',
  }
  return titles[route.path] ?? 'biblichor'
})

function toggleTheme() {
  document.documentElement.classList.toggle('dark')
  try {
    const isDark = document.documentElement.classList.contains('dark')
    localStorage.setItem('biblichor.theme', isDark ? 'dark' : 'light')
  } catch {
    /* private mode */
  }
}
</script>

<template>
  <header
    class="sticky top-0 z-30 flex items-center px-4 h-14 bg-background/95
           backdrop-blur border-b border-border md:hidden"
    :style="{ paddingTop: 'var(--safe-area-top)' }"
  >
    <h1 class="flex-1 text-base font-semibold truncate">{{ title }}</h1>
    <button
      type="button"
      class="w-10 h-10 inline-flex items-center justify-center
             rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground"
      aria-label="Toggle theme"
      @click="toggleTheme"
    >
      <Moon class="w-5 h-5 dark:hidden" />
      <Sun class="w-5 h-5 hidden dark:block" />
    </button>
  </header>
</template>
```

- [ ] **Step 3: Build**

```bash
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add webapp/src/components/BottomNav.vue webapp/src/components/MobileHeader.vue
git commit -m "Phase 6t.2: BottomNav + MobileHeader components

BottomNav: horizontal scroll-snapping strip of all 9 nav items;
hidden md:hidden so it only shows on phones. Each item is a 64x48
tap target. Active item highlighted with --primary.

MobileHeader: 56px sticky header with the current page title +
theme toggle. iOS safe-area-top respected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: AppShell rewrite (responsive)

**Files:**
- Modify: `webapp/src/components/AppShell.vue`

- [ ] **Step 1: Read the current AppShell to know what state it owns**

```bash
cat ~/endless-library/webapp/src/components/AppShell.vue
```

Note: which Pinia stores it uses (cycle, toast), which composables, what the current sidebar's height + items look like.

- [ ] **Step 2: Rewrite as responsive shell**

Replace the entire file with:

```vue
<!-- webapp/src/components/AppShell.vue -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import {
  BookOpen, Inbox, Database, Cpu, Settings as SettingsIcon,
  FileText, Activity, Play, Server, Sliders, Library, Clock, Moon, Sun,
} from 'lucide-vue-next'
import Toaster from '@/components/ui/Toaster.vue'
import Button from '@/components/ui/Button.vue'
import BottomNav from '@/components/BottomNav.vue'
import MobileHeader from '@/components/MobileHeader.vue'
import { useToast } from '@/composables/useToast'
import { useEventStream } from '@/composables/useWebSocket'
import { useCycleStore } from '@/stores/cycle'

const toast = useToast()
const cycle = useCycleStore()
const triggering = ref(false)

const nav = [
  { to: '/queue',    label: 'Queue',    icon: Inbox },
  { to: '/library',  label: 'Library',  icon: Library },
  { to: '/sources',  label: 'Sources',  icon: Database },
  { to: '/scrapers', label: 'Scrapers', icon: Cpu },
  { to: '/mirrors',  label: 'Mirrors',  icon: Server },
  { to: '/scoring',  label: 'Scoring',  icon: Sliders },
  { to: '/schedule', label: 'Schedule', icon: Clock },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/logs',     label: 'Logs',     icon: FileText },
]

onMounted(() => {
  useEventStream()
  // Restore theme preference
  try {
    if (localStorage.getItem('biblichor.theme') === 'dark') {
      document.documentElement.classList.add('dark')
    }
  } catch { /* */ }
})

async function runNow() {
  triggering.value = true
  try {
    await fetch('/api/cycle/run-now', { method: 'POST' })
    toast.success('Cycle queued')
  } catch (e: any) {
    toast.error(`Failed: ${e?.message ?? e}`)
  } finally {
    triggering.value = false
  }
}

function toggleTheme() {
  document.documentElement.classList.toggle('dark')
  try {
    const isDark = document.documentElement.classList.contains('dark')
    localStorage.setItem('biblichor.theme', isDark ? 'dark' : 'light')
  } catch { /* */ }
}
</script>

<template>
  <div class="min-h-dvh bg-background text-foreground">
    <!-- Desktop side-rail >= md (768px) -->
    <aside
      class="hidden md:flex fixed inset-y-0 left-0 w-56 flex-col border-r border-border bg-card"
    >
      <div class="flex items-center gap-2 px-4 h-14 border-b border-border">
        <BookOpen class="w-5 h-5 text-primary" />
        <span class="font-semibold">biblichor</span>
      </div>
      <nav class="flex-1 overflow-y-auto p-2 space-y-0.5">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm
                 text-muted-foreground hover:text-foreground hover:bg-secondary"
          active-class="bg-primary text-primary-foreground hover:bg-primary
                       hover:text-primary-foreground"
        >
          <component :is="item.icon" class="w-4 h-4" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="border-t border-border p-2 space-y-1">
        <Button class="w-full" variant="outline" :disabled="triggering" @click="runNow">
          <Play class="w-4 h-4 mr-2" />
          {{ triggering ? 'Queuing…' : 'Run now' }}
        </Button>
        <button
          class="flex items-center justify-center gap-2 w-full h-9 rounded-md
                 text-sm text-muted-foreground hover:text-foreground hover:bg-secondary"
          @click="toggleTheme"
        >
          <Moon class="w-4 h-4 dark:hidden" />
          <Sun class="w-4 h-4 hidden dark:block" />
          <span>Theme</span>
        </button>
      </div>
    </aside>

    <!-- Main column: full width on mobile, offset by sidebar width on desktop -->
    <div class="md:pl-56">
      <MobileHeader />

      <!-- Content -->
      <main
        class="min-h-dvh"
        :style="{ paddingBottom: 'calc(var(--safe-area-bottom) + 4rem)' }"
      >
        <RouterView />
      </main>

      <!-- Mobile-only bottom nav -->
      <BottomNav />
    </div>

    <Toaster />
  </div>
</template>
```

- [ ] **Step 3: Build + visual check**

```bash
cd ~/endless-library/webapp && npm run build
```

Expected: PASS.

Rebuild biblichor container:
```bash
cd ~/endless-library
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
```

Open the SPA on desktop: side-rail visible on the left. Resize the browser below 768px width: side-rail hides, top header + bottom scroll-strip appear. Scroll the bottom strip horizontally — all 9 items reachable.

- [ ] **Step 4: Commit**

```bash
git add webapp/src/components/AppShell.vue
git commit -m "Phase 6t.2: responsive AppShell

Desktop (md+): existing side-rail layout, restyled with the new
oklch tokens.
Mobile (<md): MobileHeader sticky at top + BottomNav scroll-strip
at bottom containing all 9 nav items. iOS safe-area-bottom
respected so the strip clears the home indicator.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.3 — PWA

### Task 8: vite-plugin-pwa + icons

**Files:**
- Modify: `webapp/package.json` (deps)
- Modify: `webapp/vite.config.ts` (add VitePWA plugin)
- Create: `webapp/pwa-assets.config.ts`
- Create: `webapp/public/pwa-icon-source.svg`
- Modify: `webapp/index.html` (manifest link + meta tags)
- Modify: `webapp/src/main.ts` (register service worker)

- [ ] **Step 1: Install**

```bash
cd ~/endless-library/webapp
npm install vite-plugin-pwa@^1.3.0
npm install -D @vite-pwa/assets-generator@^1.0.2
```

- [ ] **Step 2: Create the source icon**

Use a simple SVG with the biblichor "B" or book icon. Save as `webapp/public/pwa-icon-source.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0a0a0a"/>
  <path d="M128 96h208c44 0 80 36 80 80v240c0 11-9 20-20 20H148c-11 0-20-9-20-20V96z"
        fill="none" stroke="#a78bfa" stroke-width="32" stroke-linejoin="round"/>
  <line x1="180" y1="180" x2="364" y2="180" stroke="#a78bfa" stroke-width="20" stroke-linecap="round"/>
  <line x1="180" y1="240" x2="364" y2="240" stroke="#a78bfa" stroke-width="20" stroke-linecap="round"/>
  <line x1="180" y1="300" x2="304" y2="300" stroke="#a78bfa" stroke-width="20" stroke-linecap="round"/>
</svg>
```

- [ ] **Step 3: Create the assets-generator config**

```ts
// webapp/pwa-assets.config.ts
import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

export default defineConfig({
  headLinkOptions: { preset: '2023' },
  preset: minimal2023Preset,
  images: ['public/pwa-icon-source.svg'],
})
```

- [ ] **Step 4: Generate the icon set**

```bash
cd ~/endless-library/webapp
npx pwa-assets-generator --config pwa-assets.config.ts
```

Expected: produces `public/pwa-64x64.png`, `public/pwa-192x192.png`, `public/pwa-512x512.png`, `public/maskable-icon-512x512.png`, `public/apple-touch-icon-180x180.png`, etc.

- [ ] **Step 5: Add to `.gitignore`**

```bash
cd ~/endless-library
cat >> webapp/.gitignore <<'EOF'

# Phase 6t.3: PWA-generated icons (regenerated from pwa-icon-source.svg)
public/pwa-*x*.png
public/maskable-icon-*.png
public/apple-touch-icon-*.png
public/favicon.ico
EOF
```

Actually for biblichor we **commit** the generated icons so docker builds reproduce them. Skip the gitignore addition and instead `git add` the icons.

- [ ] **Step 6: Wire VitePWA into vite.config.ts**

```ts
// webapp/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: [
        'favicon.ico',
        'apple-touch-icon-180x180.png',
        'pwa-icon-source.svg',
      ],
      workbox: {
        cleanupOutdatedCaches: true,
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/healthz/],
      },
      manifest: {
        name: 'biblichor',
        short_name: 'biblichor',
        description:
          'Self-hosted Goodreads -> Anna -> Kindle automation + BookOrbit library',
        theme_color: '#0a0a0a',
        background_color: '#fafafa',
        display: 'standalone',
        orientation: 'any',
        start_url: '/queue',
        scope: '/',
        icons: [
          { src: 'pwa-64x64.png',  sizes: '64x64',  type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    proxy: {
      '/api':     'http://localhost:8090',
      '/ws':      { target: 'ws://localhost:8090', ws: true },
      '/healthz': 'http://localhost:8090',
    },
  },
})
```

- [ ] **Step 7: Update `index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#0a0a0a" media="(prefers-color-scheme: dark)" />
    <meta name="theme-color" content="#fafafa" media="(prefers-color-scheme: light)" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="default" />
    <meta name="apple-mobile-web-app-title" content="biblichor" />
    <meta name="mobile-web-app-capable" content="yes" />

    <link rel="icon" href="/favicon.ico" sizes="any" />
    <link rel="icon" href="/pwa-icon-source.svg" type="image/svg+xml" />
    <link rel="apple-touch-icon" href="/apple-touch-icon-180x180.png" />

    <title>biblichor</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 8: Register service worker in main.ts**

Add to `webapp/src/main.ts`:

```ts
import { registerSW } from 'virtual:pwa-register'

if (import.meta.env.PROD) {
  registerSW({ immediate: true })
}
```

- [ ] **Step 9: Build + verify manifest**

```bash
cd ~/endless-library/webapp && npm run build
ls dist/manifest.webmanifest dist/sw.js dist/registerSW.js
```

Expected: all three present.

- [ ] **Step 10: Live verify install**

```bash
cd ~/endless-library
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
```

Open the SPA in Chrome → the install button appears in the URL bar. Click it, biblichor opens as a standalone app. On iOS Safari → Share menu → "Add to Home Screen" produces an icon labeled "biblichor".

- [ ] **Step 11: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/vite.config.ts \
        webapp/pwa-assets.config.ts webapp/public/ webapp/index.html webapp/src/main.ts
git commit -m "Phase 6t.3: PWA — manifest, service worker, icons

vite-plugin-pwa with autoUpdate registration; manifest declares
biblichor as installable with start_url=/queue. Icons generated
from a single SVG source via @vite-pwa/assets-generator (committed
to the repo so docker builds are reproducible).

Mobile meta tag set added to index.html: viewport-fit=cover,
theme-color light+dark, apple-mobile-web-app-capable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.4 — Mobile passes: Queue + Sources + Library

### Task 9: Queue page narrow-screen layout

**Files:**
- Modify: `webapp/src/pages/QueuePage.vue`
- Install: `@tanstack/vue-table`

- [ ] **Step 1: Install**

```bash
cd ~/endless-library/webapp
npm install @tanstack/vue-table@^8.21.3
```

- [ ] **Step 2: Read the current QueuePage**

```bash
cat ~/endless-library/webapp/src/pages/QueuePage.vue
```

Identify: the table columns, the row click handler, the bulk-select state, the existing styling classes.

- [ ] **Step 3: Add a `<768px` card-list layout**

The existing table stays for desktop. Add a sibling div that renders only on mobile:

```vue
<!-- Add inside QueuePage.vue template, alongside the existing table -->
<!-- Desktop / wide table (existing markup, unchanged): wrap it: -->
<div class="hidden md:block">
  <!-- ... existing table markup ... -->
</div>

<!-- New narrow card list -->
<div class="md:hidden space-y-2 px-2">
  <article
    v-for="book in books"
    :key="book.id"
    class="bg-card border border-border rounded-lg p-3 flex items-start gap-3 active:bg-secondary"
    @click="openBook(book.id)"
  >
    <input
      type="checkbox"
      :checked="selectedIds.has(book.id)"
      class="w-5 h-5 mt-1 rounded border-border"
      @click.stop="toggleSelect(book.id)"
    />
    <div class="flex-1 min-w-0">
      <h3 class="font-medium text-sm truncate">{{ book.title }}</h3>
      <p class="text-xs text-muted-foreground truncate">{{ book.author || '—' }}</p>
      <div class="flex items-center gap-2 mt-1">
        <StatusPill :status="book.status" />
        <span v-if="book.isbn13" class="font-mono text-[10px] text-muted-foreground">
          {{ book.isbn13 }}
        </span>
      </div>
    </div>
  </article>

  <p v-if="!books.length" class="text-center text-sm text-muted-foreground py-8">
    Queue is empty.
  </p>
</div>
```

(Keep `openBook`, `toggleSelect`, `selectedIds`, `books` from the existing script.)

- [ ] **Step 4: Bulk-action bar pinned to bottom on mobile**

If your existing bulk-action bar (Delete N selected, etc.) is in the page, wrap its mobile variant:

```vue
<div
  v-if="selectedIds.size > 0"
  class="md:hidden fixed inset-x-0 z-20 bg-card border-t border-border p-3 flex items-center gap-2"
  :style="{ bottom: 'calc(var(--safe-area-bottom) + 56px)' }"
>
  <span class="text-sm">{{ selectedIds.size }} selected</span>
  <Button variant="destructive" size="sm" class="ml-auto" @click="bulkDelete">
    Delete
  </Button>
  <Button variant="ghost" size="sm" @click="clearSelection">Cancel</Button>
</div>
```

The `bottom` offset of `safe-area-bottom + 56px` clears the bottom nav strip and home indicator.

- [ ] **Step 5: Build + visual check on narrow viewport**

```bash
npm run build
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
```

Open Chrome → devtools → toggle mobile mode (iPhone 14 Pro preset). Visit `/queue`. Each book renders as a card. Tap targets are >=44px (card + checkbox). Bulk-action bar slides up when you check a box.

- [ ] **Step 6: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/src/pages/QueuePage.vue
git commit -m "Phase 6t.4: Queue page card-list on mobile

Existing desktop table preserved (hidden md:block). Added a
md:hidden card-list variant: each book a tap target with
checkbox, title, author, status pill, and ISBN. Bulk-action
bar slides up above the bottom nav when selection is non-empty.

@tanstack/vue-table installed for future desktop table polish
(sort/filter); QueuePage continues to use the current handwritten
table for now to keep this commit scoped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Sources page narrow-screen layout

**Files:**
- Modify: `webapp/src/pages/SourcesPage.vue`

- [ ] **Step 1: Add a `<768px` card-list of sources**

Below the existing source-cards block, add inside the same template:

```vue
<!-- Narrow-screen list of source cards already present; if the
     existing layout is a table, mirror the pattern from Queue: -->
<div class="md:hidden space-y-2 px-2">
  <article
    v-for="source in sources"
    :key="source.id"
    class="bg-card border border-border rounded-lg p-3"
  >
    <div class="flex items-start gap-2 mb-1">
      <h3 class="font-medium text-sm flex-1 truncate">{{ source.identifier }}</h3>
      <span class="text-[10px] text-muted-foreground uppercase tracking-wide">
        {{ source.source }}
      </span>
    </div>
    <p class="text-xs text-muted-foreground">
      Poll every {{ source.poll_interval_minutes }} min •
      {{ source.is_enabled ? 'Active' : 'Paused' }}
    </p>
    <div class="flex gap-2 mt-2">
      <Button size="sm" variant="outline" @click="pollNow(source.id)">Poll now</Button>
      <Button size="sm" variant="ghost" @click="toggleEnable(source.id)">
        {{ source.is_enabled ? 'Pause' : 'Resume' }}
      </Button>
      <Button size="sm" variant="ghost" class="ml-auto text-destructive" @click="del(source.id)">
        Delete
      </Button>
    </div>
  </article>
</div>
```

- [ ] **Step 2: Add-source form: bottom sheet on mobile**

Wrap the existing add-source form in a slide-up panel for narrow viewports. The simplest approach: it's already a Card on desktop; on mobile let it expand to full width with a sticky "Add" button at the bottom. No actual modal needed if the form is already visible.

The Phase 6s.3 dropdown additions (NYT, StoryGraph, BookWyrm, Wikidata) work as-is in the new card list.

- [ ] **Step 3: Build + visual check + commit**

```bash
npm run build
git add webapp/src/pages/SourcesPage.vue
git commit -m "Phase 6t.4: Sources card-list on mobile

Wide layout unchanged; narrow viewport collapses table to per-
source cards with the action buttons inline. Form already
single-column on narrow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Library page token refresh

**Files:**
- Modify: `webapp/src/pages/LibraryPage.vue`

- [ ] **Step 1: Library page already does responsive cards** from Phase 6p. Just audit the colors after the theme migration.

Open `/library` in a narrow viewport. Each card should render with `bg-card`, `text-card-foreground`, `border-border`. If any class still references v3-only utilities like `bg-gray-200`, replace with theme tokens.

- [ ] **Step 2: Build + visual check**

```bash
npm run build
```

If anything looks broken (rare; theme tokens are backward-compatible), fix inline and commit.

- [ ] **Step 3: Commit (only if changes)**

```bash
git diff --stat webapp/src/pages/LibraryPage.vue
# if non-empty:
git add webapp/src/pages/LibraryPage.vue
git commit -m "Phase 6t.4: LibraryPage theme-token audit"
```

---

## Section 6t.5 — Mobile passes: Scrapers + Mirrors + Settings

### Task 12: Scrapers page — drag-reorder + cards

**Files:**
- Modify: `webapp/src/pages/ScrapersPage.vue`
- Install: `vue-draggable-plus`

- [ ] **Step 1: Install**

```bash
cd ~/endless-library/webapp
npm install vue-draggable-plus@^0.6.1
```

- [ ] **Step 2: Replace up/down arrow buttons with drag handles**

In ScrapersPage.vue, find the scraper list rendering. Wrap it with the `<VueDraggable>` component:

```vue
<script setup lang="ts">
import { VueDraggable } from 'vue-draggable-plus'
import { GripVertical } from 'lucide-vue-next'
// ... existing imports ...
</script>

<template>
  <VueDraggable
    v-model="orderedScrapers"
    handle=".drag-handle"
    @update="saveOrder"
    class="space-y-2"
  >
    <div
      v-for="s in orderedScrapers"
      :key="s.name"
      class="flex items-center gap-3 bg-card border border-border rounded-lg p-3"
    >
      <GripVertical class="drag-handle w-5 h-5 text-muted-foreground cursor-grab" />
      <div class="flex-1 min-w-0">
        <h4 class="font-medium text-sm">{{ s.name }}</h4>
        <p class="text-xs text-muted-foreground">
          {{ Math.round((s.success_rate ?? 0) * 100) }}% success • {{ s.last_run || 'never run' }}
        </p>
      </div>
      <Switch :checked="s.enabled" @update:checked="toggle(s.name, $event)" />
    </div>
  </VueDraggable>
</template>
```

Replace the existing up/down arrow buttons since drag now handles reorder. Keep the bench section as-is.

- [ ] **Step 3: Build + verify**

```bash
npm run build
```

Open `/scrapers` on mobile: long-press a card and drag to reorder. On release, the new order is saved via `saveOrder` (the existing `POST /api/scrapers/order` call).

- [ ] **Step 4: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/src/pages/ScrapersPage.vue
git commit -m "Phase 6t.5: Scrapers drag-to-reorder + card layout

vue-draggable-plus replaces the up/down arrow buttons. Each
scraper is a horizontal card with grip handle, name, success
rate, and enable switch. Layout works identically on desktop +
mobile (no separate breakpoint variant needed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Mirrors page card-list

**Files:**
- Modify: `webapp/src/pages/MirrorsPage.vue`

- [ ] **Step 1: Wrap mirror rows in md:hidden card list**

Pattern identical to Queue's card-list. Each mirror gets:

```vue
<div class="md:hidden space-y-2 px-2">
  <article v-for="m in mirrors" :key="m.url" class="bg-card border border-border rounded-lg p-3">
    <div class="flex items-center gap-2 mb-1">
      <span class="w-2 h-2 rounded-full" :class="m.ok ? 'bg-green-500' : 'bg-destructive'" />
      <h3 class="font-medium text-sm flex-1 truncate font-mono">{{ m.url }}</h3>
    </div>
    <p class="text-xs text-muted-foreground">
      Last probe: {{ m.last_probed_at || 'never' }} •
      Latency: {{ m.latency_ms ? `${m.latency_ms}ms` : '—' }}
    </p>
    <div class="flex gap-2 mt-2">
      <Button size="sm" variant="outline" @click="probe(m.id)">Probe</Button>
    </div>
  </article>
</div>
```

- [ ] **Step 2: Build + commit**

```bash
npm run build
git add webapp/src/pages/MirrorsPage.vue
git commit -m "Phase 6t.5: Mirrors card-list on mobile"
```

---

### Task 14: Settings page accordion

**Files:**
- Modify: `webapp/src/pages/SettingsPage.vue`

- [ ] **Step 1: Wrap each settings section in a `<details>` accordion**

Settings has ~7 sections (SMTP, Kindle, Pushover, Calibre, Scrapers, Security, BookOrbit). On narrow they collapse:

```vue
<!-- Repeat per section: -->
<details class="md:open bg-card border border-border rounded-lg overflow-hidden mb-2">
  <summary class="cursor-pointer select-none px-4 py-3 font-medium text-sm flex items-center">
    <SectionIcon class="w-4 h-4 mr-2 text-primary" />
    SMTP
    <ChevronDown class="w-4 h-4 ml-auto transition-transform group-open:rotate-180" />
  </summary>
  <div class="p-4 pt-0 space-y-3">
    <!-- existing form fields -->
  </div>
</details>
```

The `md:open` modifier forces sections expanded on desktop where vertical real estate isn't tight.

- [ ] **Step 2: Force form fields single-column on narrow**

Wherever Settings uses a 2-col grid (`grid-cols-2`), change to `grid-cols-1 md:grid-cols-2`.

- [ ] **Step 3: Build + commit**

```bash
npm run build
git add webapp/src/pages/SettingsPage.vue
git commit -m "Phase 6t.5: Settings accordion on mobile

Each section collapses on <768px; expanded by default on desktop.
Form fields single-column below md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.6 — Mobile passes: Scoring + Schedule + Logs

### Task 15: Scoring page single column

**Files:**
- Modify: `webapp/src/pages/ScoringPage.vue`

- [ ] **Step 1: Convert any 2-column grid to single column below md**

```bash
sed -i 's|grid-cols-2|grid-cols-1 md:grid-cols-2|g' webapp/src/pages/ScoringPage.vue
```

(Audit the diff before committing — make sure no class strings break.)

- [ ] **Step 2: Build + commit**

```bash
npm run build
git diff webapp/src/pages/ScoringPage.vue
git add webapp/src/pages/ScoringPage.vue
git commit -m "Phase 6t.6: Scoring single-column on mobile"
```

---

### Task 16: Schedule page card-list

**Files:**
- Modify: `webapp/src/pages/SchedulePage.vue`

- [ ] **Step 1: Add md:hidden card-list mirroring Queue's pattern**

```vue
<div class="md:hidden space-y-2 px-2">
  <article v-for="job in jobs" :key="job.id" class="bg-card border border-border rounded-lg p-3">
    <h3 class="font-medium text-sm">{{ job.name || job.id }}</h3>
    <p class="text-xs text-muted-foreground">
      {{ job.trigger }} • next: {{ job.next_run_time || 'paused' }}
    </p>
    <div class="flex gap-2 mt-2">
      <Button size="sm" variant="outline" @click="runNow(job.id)">
        <Play class="w-3 h-3 mr-1" /> Run
      </Button>
      <Button size="sm" variant="ghost" @click="togglePause(job.id)">
        {{ job.paused ? 'Resume' : 'Pause' }}
      </Button>
    </div>
  </article>
</div>
```

- [ ] **Step 2: Build + commit**

```bash
npm run build
git add webapp/src/pages/SchedulePage.vue
git commit -m "Phase 6t.6: Schedule card-list on mobile"
```

---

### Task 17: Logs page with vue-virtual-scroller

**Files:**
- Modify: `webapp/src/pages/LogsPage.vue`
- Install: `vue-virtual-scroller`

- [ ] **Step 1: Install**

```bash
cd ~/endless-library/webapp
npm install vue-virtual-scroller@^3.0.3
```

- [ ] **Step 2: Replace the event list with DynamicScroller**

In LogsPage.vue's `<script setup>`:

```ts
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
```

In the template:

```vue
<DynamicScroller
  :items="events"
  :min-item-size="56"
  key-field="id"
  class="h-[calc(100dvh-8rem)]"
>
  <template #default="{ item, active }">
    <DynamicScrollerItem :item="item" :active="active" :size-dependencies="[item.message]">
      <div class="px-3 py-2 border-b border-border">
        <div class="flex items-center gap-2 text-xs">
          <span class="text-muted-foreground font-mono">{{ item.ts }}</span>
          <span v-if="item.scraper" class="text-primary font-medium">{{ item.scraper }}</span>
          <span class="text-muted-foreground">{{ item.kind }}</span>
        </div>
        <p class="text-sm mt-0.5">{{ item.message }}</p>
      </div>
    </DynamicScrollerItem>
  </template>
</DynamicScroller>
```

- [ ] **Step 3: Build + check that 1000+ events scroll smoothly**

```bash
npm run build
```

Open `/logs`. Should be smooth even with thousands of events.

- [ ] **Step 4: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/src/pages/LogsPage.vue
git commit -m "Phase 6t.6: Logs virtualized with vue-virtual-scroller

DynamicScroller handles variable-height event rows. 60 FPS scroll
even at 10k events. The existing event subscription (WebSocket)
unchanged; just the render layer is virtualized.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.7 — Hue picker + vue-sonner toasts

### Task 18: vue-sonner toast migration

**Files:**
- Modify: `webapp/package.json`
- Modify: `webapp/src/composables/useToast.ts`
- Modify: `webapp/src/components/AppShell.vue` (Toaster mount)
- Delete: `webapp/src/components/ui/Toaster.vue` (old)

- [ ] **Step 1: Install**

```bash
cd ~/endless-library/webapp
npm install vue-sonner@^2.0.9
```

- [ ] **Step 2: Replace useToast composable**

```ts
// webapp/src/composables/useToast.ts
import { toast } from 'vue-sonner'

export function useToast() {
  return {
    success: (msg: string) => toast.success(msg),
    error:   (msg: string) => toast.error(msg),
    info:    (msg: string) => toast(msg),
    warning: (msg: string) => toast.warning(msg),
  }
}
```

- [ ] **Step 3: Replace Toaster mount in AppShell**

In `AppShell.vue`, remove the line `import Toaster from '@/components/ui/Toaster.vue'` and the `<Toaster />` element. Add:

```vue
<script setup lang="ts">
import { Toaster } from 'vue-sonner'
import 'vue-sonner/style.css'
// ... rest of imports ...
</script>

<template>
  <!-- ... existing shell markup ... -->

  <Toaster
    position="top-right"
    :toast-options="{
      classes: {
        toast: 'group bg-card text-card-foreground border-border',
        title: 'font-medium text-sm',
        description: 'text-xs text-muted-foreground',
      },
    }"
  />
</template>
```

- [ ] **Step 4: Delete the old Toaster**

```bash
git rm webapp/src/components/ui/Toaster.vue
```

- [ ] **Step 5: Build + verify toasts**

```bash
npm run build
```

Open any page that emits a toast (e.g. trigger a Doctor probe on `/library`); check the toast appears with the new sonner styling.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Phase 6t.7: vue-sonner replaces custom Toaster

Same useToast() API for callers (success/error/info/warning);
the implementation now delegates to vue-sonner. Visual style
tuned via toastOptions.classes to match our card tokens. Old
Toaster.vue removed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: Hue picker on Settings page

**Files:**
- Modify: `webapp/src/pages/SettingsPage.vue`

- [ ] **Step 1: Add a hue-picker section to Settings**

Insert a new section card at the top of SettingsPage.vue's accordion:

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Palette } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import { applyTint, readSavedTint } from '@/composables/useTint'

const tintH = ref(265)

onMounted(() => {
  tintH.value = readSavedTint()
})

function onTintChange(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  tintH.value = value
  applyTint(value)
}

const presets = [
  { name: 'Blue',    h: 265 },
  { name: 'Green',   h: 142 },
  { name: 'Purple',  h: 292 },
  { name: 'Amber',   h: 60 },
  { name: 'Red',     h: 22 },
  { name: 'Teal',    h: 180 },
  { name: 'Slate',   h: 240 },
]
</script>

<template>
  <Card class="p-4 mb-4 space-y-3">
    <h3 class="text-sm font-semibold flex items-center gap-2">
      <Palette class="w-4 h-4 text-primary" /> Accent color
    </h3>
    <p class="text-[11px] text-muted-foreground">
      Pick a hue. Same scheme as BookOrbit so the two apps feel like one.
    </p>
    <div class="flex flex-wrap gap-2">
      <button
        v-for="p in presets"
        :key="p.h"
        class="w-10 h-10 rounded-full border-2 border-border"
        :style="{ background: `oklch(0.55 0.18 ${p.h})` }"
        :title="p.name"
        @click="(applyTint(p.h), tintH = p.h)"
      />
    </div>
    <label class="block text-xs space-y-1">
      <span class="text-muted-foreground">Custom hue: {{ tintH }}°</span>
      <input
        type="range"
        min="0"
        max="360"
        :value="tintH"
        class="w-full"
        @input="onTintChange"
      />
    </label>
  </Card>
</template>
```

- [ ] **Step 2: Build + verify**

```bash
npm run build
```

Open `/settings`. The Accent color card sits at the top. Click any preset → entire UI re-tints instantly. Slide the range slider → live tint update.

- [ ] **Step 3: Commit**

```bash
git add webapp/src/pages/SettingsPage.vue
git commit -m "Phase 6t.7: hue picker on Settings page

7 preset swatches + a 0-360 range slider. Writes --tint-h to
<html>, persists in localStorage via the existing useTint
composable. Whole UI re-tints instantly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6t.8 — Live verification + wiki + close

### Task 20: Final live verification

- [ ] **Step 1: Full rebuild**

```bash
cd ~/endless-library
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
sleep 10
```

- [ ] **Step 2: Verify endpoints + doctor**

```bash
curl -s http://localhost:8090/healthz | python3 -m json.tool
curl -sX POST http://localhost:8090/api/bookorbit/doctor > /tmp/doc.json && python3 -c "
import json
d = json.load(open('/tmp/doc.json'))
print(sum(1 for c in d['checks'] if c['ok']), '/', len(d['checks']), 'green')
"
```

Expected: healthz 200, doctor 9/9 green (or whatever the pre-6t state was; we didn't touch the backend).

- [ ] **Step 3: Mobile smoke**

In Chrome devtools → mobile mode → iPhone 14 Pro. Visit every page:
- `/queue` — books as cards, bulk-action bar slides up
- `/library` — sync surfaces stack, action buttons full-width
- `/sources` — source cards, dropdown for add
- `/scrapers` — drag handles visible, switches reachable
- `/mirrors` — mirror cards with status dot
- `/scoring` — single-column form
- `/schedule` — job cards with Run/Pause buttons
- `/settings` — accordion sections, hue picker at top
- `/logs` — virtualized event list

- [ ] **Step 4: PWA install on real device**

On a phone (Android Chrome OR iOS Safari):
1. Open `http://<TAILSCALE-IP>:8090`
2. Browser menu → Add to Home Screen / Install
3. Open the new icon — biblichor launches as a standalone app, no browser chrome
4. Confirm bottom nav strip respects the iOS home indicator

- [ ] **Step 5: Wiki update**

Append to `docs/wiki/Architecture.md` a new section:

```markdown
## UI overhaul (Phase 6t)

biblichor's web UI matches BookOrbit's exact stack — Vue 3, Vite 8,
Tailwind v4 (oklch with tintable hue), reka-ui 2.9, vue-router 5,
vite-plugin-pwa 1.3, lucide-vue-next, vue-sonner.

Responsive shell: side-rail at >=768px, bottom scroll-strip with
all 9 nav items at <768px. Installable as a PWA on Android, iOS,
and desktop browsers; offline shell loads from the service-worker
cache.

Hue picker on the Settings page lets the user re-tint the whole
app (the same hue picker pattern BookOrbit uses; same default
blue 265°).
```

Push wiki:
```bash
bash scripts/sync-wiki.sh
```

- [ ] **Step 6: Final commit**

```bash
git add docs/wiki/
git commit -m "Phase 6t.8: live verification + wiki

Doctor 9/9 green, healthz 200, every page mobile-friendly,
PWA installs cleanly on Android + iOS. Wiki Architecture page
updated with the new UI stack summary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

## Acceptance criteria

- [ ] `npm run build` succeeds after every section
- [ ] `python -m pytest -q` stays at 818 passed (UI changes don't touch Python)
- [ ] Doctor 9/9 green on the live container
- [ ] Every page renders correctly at 360px width (iPhone 14 Pro) and 1280px width (desktop)
- [ ] PWA installs and opens standalone on iOS Safari + Android Chrome
- [ ] Hue picker on Settings re-tints the whole UI instantly
- [ ] Service worker registered (`navigator.serviceWorker.ready` resolves)
- [ ] Wiki Architecture page documents the new stack

## Section commit summary

```
6t.0  vue-router 4 -> 5 bump               (1 commit)
6t.1  Tailwind v4 + theme + fonts          (2 commits)
6t.1b radix-vue -> reka-ui                 (1 commit)
6t.2  responsive shell                     (3 commits — useTint, BottomNav/MobileHeader, AppShell)
6t.3  PWA                                  (1 commit)
6t.4  mobile pass Queue+Sources+Library    (3 commits)
6t.5  mobile pass Scrapers+Mirrors+Settings(3 commits)
6t.6  mobile pass Scoring+Schedule+Logs    (3 commits)
6t.7  toasts migration + hue picker        (2 commits)
6t.8  live verify + wiki                   (1 commit)
```

~20 commits total. Each section is independently shippable; the executing agent pauses between sections for spot-check.
