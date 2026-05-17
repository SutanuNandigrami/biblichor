<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { BookOpen, Inbox, Database, Cpu, Settings as SettingsIcon, FileText, Sparkles, Play, Activity, Server, Sliders, Library as LibraryIcon } from 'lucide-vue-next'
import Toaster from '@/components/ui/Toaster.vue'
import Button from '@/components/ui/Button.vue'
import { useToast } from '@/composables/useToast'
import { useEventStream } from '@/composables/useWebSocket'
import { useCycleStore } from '@/stores/cycle'

const toast = useToast()
const cycle = useCycleStore()
const triggering = ref(false)

const nav = [
  { to: '/queue',    label: 'Queue',    icon: Inbox },
  { to: '/sources',  label: 'Sources',  icon: Database },
  { to: '/scrapers', label: 'Scrapers', icon: Cpu },
  { to: '/mirrors',  label: 'Mirrors',  icon: Server },
  { to: '/scoring',  label: 'Scoring',  icon: Sliders },
  { to: '/lib',       label: 'Library',  icon: LibraryIcon },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/logs',     label: 'Logs',     icon: FileText },
  { to: '/setup',    label: 'Setup',    icon: Sparkles },
]

const { connected } = useEventStream((msg) => {
  if (msg.type === 'cycle') {
    cycle.applyStatus(msg.data)
  }
  // 'event' frames flow into stores subscribed per-page
})

onMounted(() => { cycle.refresh().catch(() => {}) })

async function onRun() {
  triggering.value = true
  try {
    await cycle.runNow()
    toast.success('Cycle started', 'Polling sources and processing queue…')
  } catch (e: any) {
    if (e?.status === 409) toast.info('Already running', 'A cycle is in progress')
    else toast.error('Run failed', String(e?.message ?? e))
  } finally {
    triggering.value = false
  }
}
</script>

<template>
  <Toaster />
  <div class="min-h-screen flex">
    <aside class="w-60 shrink-0 border-r border-border bg-card flex flex-col">
      <div class="h-14 flex items-center gap-2 px-4 border-b border-border">
        <BookOpen class="w-5 h-5 text-primary" />
        <span class="font-semibold tracking-tight">endless-library</span>
      </div>
      <nav class="flex-1 p-2 space-y-1">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          class="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          active-class="!bg-primary/10 !text-foreground">
          <component :is="item.icon" class="w-4 h-4" />
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="p-3 flex items-center gap-2 text-xs text-muted-foreground">
        <span :class="['w-2 h-2 rounded-full', connected ? 'bg-emerald-400' : 'bg-amber-500']" />
        {{ connected ? 'live' : 'reconnecting…' }}
      </div>
    </aside>

    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-14 border-b border-border bg-card/50 backdrop-blur sticky top-0 z-30 flex items-center px-4 gap-3">
        <div class="flex-1"></div>
        <div class="flex items-center gap-2 text-xs text-muted-foreground" v-if="cycle.running">
          <Activity class="w-3.5 h-3.5 animate-pulse text-primary" />
          running…
        </div>
        <div class="flex items-center gap-2 text-xs text-muted-foreground" v-else-if="cycle.lastTally">
          last cycle:
          <span v-for="(v, k) in cycle.lastTally" :key="k" class="ml-1 text-foreground">{{ k }}={{ v }}</span>
        </div>
        <Button :loading="triggering || cycle.running" :disabled="cycle.running" @click="onRun">
          <Play class="w-4 h-4" />
          Run now
        </Button>
      </header>
      <main class="flex-1 overflow-y-auto"><slot /></main>
    </div>
  </div>
</template>
