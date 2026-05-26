<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown } from 'lucide-vue-next'
import Badge from '@/components/ui/Badge.vue'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import { api } from '@/composables/useApi'
import { useEventStream } from '@/composables/useWebSocket'

type Event = {
  id: number
  ts: string
  kind: string
  scraper: string | null
  message: string
  book_id: number | null
  meta: Record<string, any>
}

// All events held in MEMORY in oldest -> newest order. The /api/events
// endpoint returns newest-first; we reverse on initial load so the list
// reads top -> bottom = old -> new (tail -f semantics). New events from
// the SSE stream append to the end.
const events = ref<Event[]>([])
const kindFilter = ref('')
const limit = ref<100 | 500 | 2000>(500)
const autoScroll = ref(true)        // off if user scrolls up to read history
const scrollerEl = ref<HTMLElement | null>(null)

async function load() {
  const r = await api<{ events: Event[] }>(`/api/events?limit=${limit.value}`)
  // API gives newest-first; reverse so newest sits at the bottom.
  events.value = [...r.events].reverse()
  await nextTick()
  scrollToBottom()
}

watch(limit, load)
onMounted(load)

useEventStream((msg) => {
  if (msg.type !== 'event') return
  const ev = msg.data as Event
  // De-dup: skip if we already have this id (the SSE stream + load() race
  // can re-emit the most recent event).
  if (events.value.length && events.value[events.value.length - 1].id === ev.id) return
  events.value.push(ev)
  // Cap the list at `limit` so memory + DOM don't grow without bound.
  if (events.value.length > limit.value) {
    events.value = events.value.slice(events.value.length - limit.value)
  }
  if (autoScroll.value) nextTick(() => scrollToBottom())
})

function scrollToBottom() {
  const el = scrollerEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = scrollerEl.value
  if (!el) return
  // If user is within 80px of the bottom, keep auto-scroll on. Otherwise
  // they probably scrolled up to read; pause auto-scroll until they jump
  // back to "live".
  const nearBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 80
  autoScroll.value = nearBottom
}

function jumpToLive() {
  autoScroll.value = true
  nextTick(() => scrollToBottom())
}

const filtered = computed(() =>
  kindFilter.value
    ? events.value.filter((e) => e.kind === kindFilter.value)
    : events.value,
)
const kinds = computed(() =>
  Array.from(new Set(events.value.map((e) => e.kind))).sort(),
)

function kindTone(kind: string): 'success' | 'warning' | 'danger' | 'info' | 'muted' {
  if (kind === 'error') return 'danger'
  if (kind.startsWith('send-') || kind === 'bookorbit' || kind === 'convert') return 'success'
  if (kind === 'state_change' || kind === 'scrape') return 'info'
  if (kind === 'download' || kind === 'compress') return 'warning'
  return 'muted'
}
</script>

<template>
  <div class="p-4 md:p-6 pb-24 md:pb-6 space-y-3 h-[100dvh] flex flex-col">
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
      <Badge variant="muted">{{ filtered.length }}</Badge>
      <Badge v-if="autoScroll" variant="success">live</Badge>
      <Badge v-else variant="warning">paused</Badge>
      <div class="flex-1"></div>
      <select
        v-model="kindFilter"
        class="h-9 px-3 rounded-md border border-input bg-background text-sm"
      >
        <option value="">All kinds</option>
        <option v-for="k in kinds" :key="k">{{ k }}</option>
      </select>
      <select
        v-model.number="limit"
        class="h-9 px-3 rounded-md border border-input bg-background text-sm"
        aria-label="Number of log entries to keep"
      >
        <option :value="100">Last 100</option>
        <option :value="500">Last 500</option>
        <option :value="2000">Last 2000</option>
      </select>
    </div>

    <Card class="overflow-hidden flex-1 min-h-[200px] relative">
      <div
        ref="scrollerEl"
        class="h-full overflow-y-auto"
        @scroll.passive="onScroll"
      >
        <div v-if="!filtered.length" class="px-4 py-12 text-center text-muted-foreground text-xs">
          No events.
        </div>
        <div v-else>
          <div
            v-for="item in filtered"
            :key="item.id"
            class="px-4 py-2 border-b border-border/40 text-xs flex flex-col md:flex-row gap-1 md:gap-2 md:items-start"
          >
            <span class="text-muted-foreground font-mono shrink-0">{{ item.ts }}</span>
            <div class="flex items-center gap-2 flex-wrap shrink-0">
              <Badge :variant="kindTone(item.kind)">
                {{ item.kind }}<span v-if="item.scraper"> / {{ item.scraper }}</span>
              </Badge>
              <RouterLink
                v-if="item.book_id"
                :to="`/book/${item.book_id}`"
                class="text-primary underline"
              >
                #{{ item.book_id }}
              </RouterLink>
            </div>
            <span class="flex-1 break-words font-mono whitespace-pre-wrap">{{ item.message }}</span>
          </div>
        </div>
      </div>

      <!-- "Jump to live" floating button when user has scrolled up -->
      <Button
        v-if="!autoScroll && filtered.length"
        size="sm"
        variant="default"
        class="absolute bottom-3 right-3 shadow-lg"
        @click="jumpToLive"
      >
        <ChevronDown class="w-3.5 h-3.5 mr-1.5" />
        Jump to live
      </Button>
    </Card>
  </div>
</template>
