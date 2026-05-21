<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
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
  <div class="p-6 pb-24 md:pb-6 space-y-4 h-[100dvh] flex flex-col">
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
      <Badge variant="muted">{{ filtered.length }}</Badge>
      <div class="flex-1"></div>
      <select v-model="kindFilter" class="h-9 px-3 rounded-md border border-input bg-background text-sm">
        <option value="">All kinds</option>
        <option v-for="k in kinds" :key="k">{{ k }}</option>
      </select>
    </div>
    <Card class="overflow-hidden flex-1 min-h-[200px]">
      <DynamicScroller
        v-if="filtered.length"
        :items="filtered"
        :min-item-size="44"
        key-field="id"
        class="h-full"
      >
        <template #default="{ item, active }">
          <DynamicScrollerItem
            :item="item"
            :active="active"
            :size-dependencies="[item.message]"
          >
            <div class="px-4 py-2 border-b border-border/40 text-xs flex flex-col md:flex-row gap-1 md:gap-2 md:items-center">
              <span class="text-muted-foreground font-mono shrink-0">{{ item.ts }}</span>
              <div class="flex items-center gap-2 flex-wrap">
                <Badge variant="info">{{ item.kind }}<span v-if="item.scraper"> / {{ item.scraper }}</span></Badge>
                <RouterLink v-if="item.book_id" :to="`/book/${item.book_id}`" class="text-primary underline">#{{ item.book_id }}</RouterLink>
              </div>
              <span class="flex-1 break-words md:truncate">{{ item.message }}</span>
            </div>
          </DynamicScrollerItem>
        </template>
      </DynamicScroller>
      <p v-else class="px-4 py-12 text-center text-muted-foreground text-xs">No events.</p>
    </Card>
  </div>
</template>
