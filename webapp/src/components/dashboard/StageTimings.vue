<script setup lang="ts">
/**
 * StageTimings.vue — Pipeline stage latency percentiles (last 7d).
 * Three stages: search→downloaded, downloaded→sent, search→sent (end-to-end).
 * Renders as a small table with sparkline-style bars for p50/p90/p99.
 */
import { computed } from "vue"
import type { StageTimings } from "@/composables/useDashboardStream"

const props = defineProps<{ stats: StageTimings }>()

const rows = computed(() => [
  { label: "Search → Downloaded", stats: props.stats.search_to_downloaded_seconds, color: "bg-blue-500" },
  { label: "Downloaded → Sent",   stats: props.stats.downloaded_to_sent_seconds,   color: "bg-emerald-500" },
  { label: "End to end",          stats: props.stats.search_to_sent_seconds,       color: "bg-purple-500" },
])

const hasData = computed(() => rows.value.some(r => r.stats.count > 0))

function fmt(sec: number | null): string {
  if (sec === null || sec === undefined) return "—"
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${(sec / 60).toFixed(1)}m`
  return `${(sec / 3600).toFixed(1)}h`
}

// p99 across all rows = scale for bars
const maxP99 = computed(() => {
  const xs = rows.value.map(r => r.stats.p99 ?? 0)
  return Math.max(1, ...xs)
})
function widthPct(v: number | null): string {
  if (!v) return "0%"
  return `${Math.min(100, (v / maxP99.value) * 100).toFixed(1)}%`
}
</script>

<template>
  <div>
    <div v-if="!hasData" class="text-center text-xs text-muted-foreground py-8">
      No completed deliveries in the last 7 days.
    </div>
    <div v-else class="space-y-3">
      <div class="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 gap-y-1 text-[10px] text-muted-foreground uppercase tracking-wider px-1">
        <div>Stage</div>
        <div class="text-right">p50</div>
        <div class="text-right">p90</div>
        <div class="text-right">p99</div>
      </div>
      <div v-for="(r, i) in rows" :key="i" class="space-y-1">
        <div class="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 text-xs items-baseline">
          <div class="font-medium">{{ r.label }}</div>
          <div class="text-right tabular-nums">{{ fmt(r.stats.p50) }}</div>
          <div class="text-right tabular-nums">{{ fmt(r.stats.p90) }}</div>
          <div class="text-right tabular-nums">{{ fmt(r.stats.p99) }}</div>
        </div>
        <div class="h-1.5 bg-muted/40 rounded-full overflow-hidden flex">
          <div :class="[r.color, 'opacity-100']" :style="{ width: widthPct(r.stats.p50) }" />
          <div :class="[r.color, 'opacity-60']" :style="{ width: widthPct((r.stats.p90 ?? 0) - (r.stats.p50 ?? 0)) }" />
          <div :class="[r.color, 'opacity-30']" :style="{ width: widthPct((r.stats.p99 ?? 0) - (r.stats.p90 ?? 0)) }" />
        </div>
        <div class="text-[10px] text-muted-foreground">n={{ r.stats.count }}</div>
      </div>
    </div>
  </div>
</template>
