<!-- webapp/src/components/MobileHeader.vue
     Phase 6t.2: 56px sticky header for narrow viewports with the current
     page title + theme toggle. iOS safe-area-top respected. -->
<script setup lang="ts">
import { useRoute } from "vue-router"
import { computed } from "vue"
import { Moon, Sun, BookOpen } from "lucide-vue-next"

const route = useRoute()

const TITLES: Record<string, string> = {
  "/queue": "Queue",
  "/library": "Library",
  "/sources": "Sources",
  "/scrapers": "Scrapers",
  "/mirrors": "Mirrors",
  "/scoring": "Scoring",
  "/schedule": "Schedule",
  "/settings": "Settings",
  "/logs": "Logs",
  "/setup": "Setup",
}

const title = computed(() => {
  if (route.path.startsWith("/book/")) return "Book"
  return TITLES[route.path] ?? "biblichor"
})

function toggleTheme() {
  document.documentElement.classList.toggle("dark")
  try {
    const isDark = document.documentElement.classList.contains("dark")
    localStorage.setItem("biblichor.theme", isDark ? "dark" : "light")
  } catch {
    /* private mode */
  }
}
</script>

<template>
  <header
    class="sticky top-0 z-30 flex items-center px-4 h-14
           bg-background/95 backdrop-blur border-b border-border md:hidden"
    :style="{ paddingTop: 'var(--safe-area-top)' }"
  >
    <BookOpen class="w-5 h-5 text-primary mr-2" />
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
