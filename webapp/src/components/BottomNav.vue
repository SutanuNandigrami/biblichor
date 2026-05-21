<!-- webapp/src/components/BottomNav.vue
     Phase 6t.2: scroll-snapping bottom nav with all 9 routes.
     Only renders on <768px (md:hidden). Respects iOS home indicator. -->
<script setup lang="ts">
import { RouterLink } from "vue-router"
import {
  Inbox,
  Database,
  Cpu,
  Server,
  Sliders,
  Clock,
  Settings as SettingsIcon,
  FileText,
  Library,
} from "lucide-vue-next"

const items = [
  { to: "/queue",    label: "Queue",    icon: Inbox },
  { to: "/library",  label: "Library",  icon: Library },
  { to: "/sources",  label: "Sources",  icon: Database },
  { to: "/scrapers", label: "Scrapers", icon: Cpu },
  { to: "/mirrors",  label: "Mirrors",  icon: Server },
  { to: "/scoring",  label: "Scoring",  icon: Sliders },
  { to: "/schedule", label: "Schedule", icon: Clock },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/logs",     label: "Logs",     icon: FileText },
]
</script>

<template>
  <nav
    class="fixed inset-x-0 bottom-0 z-30 bg-card border-t border-border md:hidden"
    :style="{ paddingBottom: 'var(--safe-area-bottom)' }"
    aria-label="Primary"
  >
    <ul class="flex overflow-x-auto snap-x snap-mandatory scroll-pl-2 px-2 py-1.5 gap-1">
      <li v-for="item in items" :key="item.to" class="snap-start shrink-0">
        <RouterLink
          :to="item.to"
          class="flex flex-col items-center justify-center w-16 h-12 rounded-lg
                 text-[10px] font-medium text-muted-foreground
                 hover:text-foreground transition-colors"
          active-class="text-primary-foreground bg-primary"
        >
          <component :is="item.icon" class="w-5 h-5" />
          <span class="leading-tight mt-0.5">{{ item.label }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>
