<!-- webapp/src/components/AppShell.vue
     Phase 6t.2: responsive shell.
     Desktop (>= md / 768px): existing left side-rail layout, restyled.
     Mobile (< md): MobileHeader sticky top + BottomNav scroll-strip bottom. -->
<script setup lang="ts">
import { onMounted, ref } from "vue"
import { RouterLink } from "vue-router"
import {
  BookOpen,
  Inbox,
  Database,
  Cpu,
  Settings as SettingsIcon,
  FileText,
  Sparkles,
  Play,
  Activity,
  Server,
  Sliders,
  Library as LibraryIcon,
  Clock,
  BarChart3,
  Moon,
  Sun,
} from "lucide-vue-next"
import { Toaster } from 'vue-sonner'
import 'vue-sonner/style.css'
import Button from "@/components/ui/Button.vue"
import BottomNav from "@/components/BottomNav.vue"
import MobileHeader from "@/components/MobileHeader.vue"
import { useToast } from "@/composables/useToast"
import { useEventStream } from "@/composables/useWebSocket"
import { useCycleStore } from "@/stores/cycle"
import { api } from "@/composables/useApi"

type SmtpQuota = { sent_24h: number; cap: number; remaining: number; exhausted: boolean }
const smtpQuota = ref<SmtpQuota | null>(null)
async function refreshSmtpQuota() {
  try {
    const h = await api<{ smtp?: SmtpQuota }>("/healthz")
    if (h && typeof h.smtp === "object") smtpQuota.value = h.smtp as SmtpQuota
  } catch {
    /* healthz down -> nothing to show */
  }
}

const toast = useToast()
const cycle = useCycleStore()
const triggering = ref(false)

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { to: "/queue",    label: "Queue",    icon: Inbox },
  { to: "/library",  label: "Library",  icon: LibraryIcon },
  { to: "/sources",  label: "Sources",  icon: Database },
  { to: "/scrapers", label: "Scrapers", icon: Cpu },
  { to: "/mirrors",  label: "Mirrors",  icon: Server },
  { to: "/scoring",  label: "Scoring",  icon: Sliders },
  { to: "/schedule", label: "Schedule", icon: Clock },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/logs",     label: "Logs",     icon: FileText },
  { to: "/setup",    label: "Setup",    icon: Sparkles },
]

const { connected } = useEventStream((msg) => {
  if (msg.type === "cycle") {
    cycle.applyStatus(msg.data)
  }
})

onMounted(() => {
  cycle.refresh().catch(() => {})
  // Restore theme preference
  try {
    if (localStorage.getItem("biblichor.theme") === "dark") {
      document.documentElement.classList.add("dark")
    }
  } catch {
    /* private mode */
  }
  refreshSmtpQuota()
  // Refresh once a minute so the user sees the counter climb during a backfill
  setInterval(refreshSmtpQuota, 60_000)
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

async function onRun() {
  triggering.value = true
  try {
    await cycle.runNow()
    toast.success("Cycle started", "Polling sources and processing queue…")
  } catch (e: any) {
    if (e?.status === 409) toast.info("Already running", "A cycle is in progress")
    else toast.error("Run failed", String(e?.message ?? e))
  } finally {
    triggering.value = false
  }
}
</script>

<template>
  <Toaster
    position="top-right"
    rich-colors
    close-button
    :toast-options="{
      classes: {
        toast: 'bg-card text-card-foreground border border-border shadow-lg',
        title: 'font-medium text-sm',
        description: 'text-xs text-muted-foreground',
      },
    }"
  />
  <div class="min-h-dvh bg-background text-foreground">
    <!-- Desktop side-rail (>= md) -->
    <aside
      class="hidden md:flex fixed inset-y-0 left-0 w-60 flex-col
             border-r border-border bg-card z-20"
    >
      <div class="h-14 flex items-center gap-2 px-4 border-b border-border">
        <BookOpen class="w-5 h-5 text-primary" />
        <span class="font-semibold tracking-tight">biblichor</span>
      </div>
      <nav class="flex-1 p-2 space-y-0.5 overflow-y-auto">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm
                 text-muted-foreground hover:bg-accent hover:text-foreground
                 transition-colors"
          active-class="!bg-primary/10 !text-foreground"
        >
          <component :is="item.icon" class="w-4 h-4" />
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="p-3 flex items-center gap-2 text-xs text-muted-foreground border-t border-border">
        <span
          :class="[
            'w-2 h-2 rounded-full',
            connected ? 'bg-emerald-400' : 'bg-amber-500',
          ]"
        />
        {{ connected ? "live" : "reconnecting…" }}
      </div>
    </aside>

    <!-- Mobile-only header -->
    <MobileHeader />

    <!-- Main column: full-width on mobile, offset by sidebar width on desktop -->
    <div class="md:pl-60">
      <!-- Desktop top header strip with Run-now button -->
      <header
        class="hidden md:flex h-14 border-b border-border bg-card/50 backdrop-blur
               sticky top-0 z-10 items-center px-4 gap-3"
      >
        <div class="flex-1"></div>
        <div
          v-if="cycle.running"
          class="flex items-center gap-2 text-xs text-muted-foreground"
        >
          <Activity class="w-3.5 h-3.5 animate-pulse text-primary" />
          running…
        </div>
        <div
          v-else-if="cycle.lastTally"
          class="flex items-center gap-2 text-xs text-muted-foreground"
        >
          last cycle:
          <span
            v-for="(v, k) in cycle.lastTally"
            :key="k"
            class="ml-1 text-foreground"
          >{{ k }}={{ v }}</span>
        </div>
        <span
          v-if="smtpQuota && smtpQuota.cap > 0"
          class="text-xs font-mono px-2 py-1 rounded border"
          :class="
            smtpQuota.exhausted
              ? 'border-destructive/40 text-destructive'
              : smtpQuota.remaining <= Math.max(5, smtpQuota.cap / 10)
                ? 'border-amber-500/40 text-amber-500'
                : 'border-border text-muted-foreground'
          "
          :title="`SMTP: ${smtpQuota.sent_24h} sent in last 24h, cap ${smtpQuota.cap}. ` +
                  (smtpQuota.exhausted ? 'Pipeline is deferring sends.' : 'New books queue freely.')"
        >
          📧 {{ smtpQuota.sent_24h }}/{{ smtpQuota.cap }}
        </span>
        <button
          type="button"
          class="w-9 h-9 inline-flex items-center justify-center
                 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground"
          aria-label="Toggle theme"
          @click="toggleTheme"
        >
          <Moon class="w-4 h-4 dark:hidden" />
          <Sun class="w-4 h-4 hidden dark:block" />
        </button>
        <Button
          :loading="triggering || cycle.running"
          :disabled="cycle.running"
          @click="onRun"
        >
          <Play class="w-4 h-4" />
          Run now
        </Button>
      </header>

      <main
        class="min-h-dvh"
        :style="{
          paddingBottom: 'calc(var(--safe-area-bottom) + 4rem)',
        }"
      >
        <slot />
      </main>

      <!-- Mobile floating Run-now button (above the bottom nav) -->
      <button
        type="button"
        class="md:hidden fixed right-4 z-30 w-14 h-14 rounded-full
               bg-primary text-primary-foreground shadow-lg
               flex items-center justify-center
               disabled:opacity-60"
        :style="{ bottom: 'calc(var(--safe-area-bottom) + 4.5rem)' }"
        :disabled="triggering || cycle.running"
        aria-label="Run cycle now"
        @click="onRun"
      >
        <Activity v-if="cycle.running" class="w-6 h-6 animate-pulse" />
        <Play v-else class="w-6 h-6" />
      </button>

      <!-- Mobile bottom nav -->
      <BottomNav />
    </div>
  </div>
</template>
