<script setup lang="ts">
/**
 * KpiCards.vue — Top-row summary tiles for the dashboard.
 * Shows queue depth, in-flight, today's sends, recent failures.
 */
import { Inbox, Activity, Send, AlertTriangle } from "lucide-vue-next"
import type { Kpis } from "@/composables/useDashboardStream"

defineProps<{ kpis: Kpis }>()
</script>

<template>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
      <div class="flex items-center gap-2 text-xs text-muted-foreground mb-1">
        <Inbox class="w-3.5 h-3.5" />
        <span>Queue</span>
      </div>
      <div class="text-2xl font-bold tabular-nums">{{ kpis.queue_depth.toLocaleString() }}</div>
      <div class="text-[10px] text-muted-foreground mt-1">waiting to process</div>
    </div>

    <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
      <div class="flex items-center gap-2 text-xs text-muted-foreground mb-1">
        <Activity class="w-3.5 h-3.5" />
        <span>In flight</span>
      </div>
      <div class="text-2xl font-bold tabular-nums">
        {{ kpis.in_flight.toLocaleString() }}
        <span v-if="kpis.in_flight > 0" class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 align-middle ml-1 animate-pulse" />
      </div>
      <div class="text-[10px] text-muted-foreground mt-1">
        searching / downloading / sending
        <span
          v-if="(kpis.oldest_in_flight_minutes ?? 0) > 0"
          class="ml-1 tabular-nums"
          :class="(kpis.oldest_in_flight_minutes ?? 0) > 25 ? 'text-amber-600 dark:text-amber-400 font-medium' : ''"
          :title="`Oldest in-flight book has been mid-pipeline for ${kpis.oldest_in_flight_minutes} min. Anything above the 30 min zombie threshold means the tick itself is hung.`"
        >
          · oldest {{ kpis.oldest_in_flight_minutes }}m
        </span>
      </div>
    </div>

    <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
      <div class="flex items-center gap-2 text-xs text-muted-foreground mb-1">
        <Send class="w-3.5 h-3.5" />
        <span>Sent today</span>
      </div>
      <div class="text-2xl font-bold tabular-nums text-emerald-600 dark:text-emerald-400">
        {{ kpis.today_sent.toLocaleString() }}
      </div>
      <div class="text-[10px] text-muted-foreground mt-1">since 00:00 UTC</div>
    </div>

    <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
      <div class="flex items-center gap-2 text-xs text-muted-foreground mb-1">
        <AlertTriangle class="w-3.5 h-3.5" />
        <span>Failures (24h)</span>
      </div>
      <div
        class="text-2xl font-bold tabular-nums"
        :class="kpis.recent_failures > 0 ? 'text-amber-600 dark:text-amber-400' : ''"
      >
        {{ kpis.recent_failures.toLocaleString() }}
      </div>
      <div class="text-[10px] text-muted-foreground mt-1">
        failed or needs review
        <span
          v-if="(kpis.dedups_24h ?? 0) > 0"
          class="ml-1 text-sky-600 dark:text-sky-400 tabular-nums"
          :title="`${kpis.dedups_24h} books skipped because their md5 already existed in another row`"
        >
          · {{ kpis.dedups_24h }} deduped
        </span>
      </div>
    </div>
  </div>
</template>
