<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Badge from '@/components/ui/Badge.vue'
import Card from '@/components/ui/Card.vue'
import { api } from '@/composables/useApi'
import { useEventStream } from '@/composables/useWebSocket'

type Event = { id: number; ts: string; kind: string; scraper: string | null; message: string; book_id: number | null; meta: Record<string, any> }
const events = ref<Event[]>([])
const kindFilter = ref('')

async function load() {
  const r = await api<{ events: Event[] }>('/api/events?limit=500')
  events.value = r.events
}
onMounted(load)

// Prepend live events as they arrive
useEventStream((msg) => {
  if (msg.type === 'event') {
    if (events.value[0]?.id === msg.data.id) return
    events.value = [msg.data as Event, ...events.value].slice(0, 500)
  }
})

const filtered = computed(() => kindFilter.value ? events.value.filter((e) => e.kind === kindFilter.value) : events.value)
const kinds = computed(() => Array.from(new Set(events.value.map((e) => e.kind))).sort())
</script>

<template>
  <div class="p-6 space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
      <Badge variant="muted">{{ filtered.length }}</Badge>
      <div class="flex-1"></div>
      <select v-model="kindFilter" class="h-9 px-3 rounded-md border border-input bg-background text-sm">
        <option value="">All kinds</option>
        <option v-for="k in kinds" :key="k">{{ k }}</option>
      </select>
    </div>
    <Card class="overflow-hidden">
      <ul class="text-xs">
        <li v-for="e in filtered" :key="e.id" class="px-4 py-2 border-b border-border/40 last:border-0 flex gap-2">
          <span class="text-muted-foreground font-mono">{{ e.ts }}</span>
          <Badge variant="info">{{ e.kind }}<span v-if="e.scraper"> / {{ e.scraper }}</span></Badge>
          <RouterLink v-if="e.book_id" :to="`/book/${e.book_id}`" class="text-primary underline">#{{ e.book_id }}</RouterLink>
          <span class="flex-1 truncate">{{ e.message }}</span>
        </li>
        <li v-if="!filtered.length" class="px-4 py-12 text-center text-muted-foreground">No events.</li>
      </ul>
    </Card>
  </div>
</template>
