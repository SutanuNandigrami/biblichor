<script setup lang="ts">
/**
 * DashboardPage.vue — Live /dashboard with KPI tiles, time-windowed charts,
 * recent activity feed, stage-timing percentiles.
 * SSE-connected via useDashboardStream composable.
 */
import { computed, ref } from "vue"
import { useDashboardStream } from "@/composables/useDashboardStream"
import StatusPie from "@/components/dashboard/StatusPie.vue"
import ThroughputLine from "@/components/dashboard/ThroughputLine.vue"
import MethodBreakdownBar from "@/components/dashboard/MethodBreakdownBar.vue"
import SourceFunnelBars from "@/components/dashboard/SourceFunnelBars.vue"
import KpiCards from "@/components/dashboard/KpiCards.vue"
import RecentActivity from "@/components/dashboard/RecentActivity.vue"
import StageTimings from "@/components/dashboard/StageTimings.vue"
import BookDetailDrawer from "@/components/BookDetailDrawer.vue"

const {
  windowHours,
  statusCounts,
  throughput,
  methodBreakdown,
  sourceFunnel,
  kpis,
  recentEvents,
  stageTimings,
  lastUpdate,
  connectionState,
  setWindowHours,
} = useDashboardStream(24)

const isLive = computed(() => connectionState.value === "connected")

const lastUpdateStr = computed(() => {
  if (!lastUpdate.value) return null
  try {
    return new Date(lastUpdate.value).toLocaleTimeString()
  } catch {
    return lastUpdate.value
  }
})

// Throughput is "empty" when every series sums to zero — show placeholder.
const throughputIsEmpty = computed(() => {
  return throughput.value.series.every(s => s.points.every(p => p.v === 0))
})
const methodIsEmpty = computed(() => {
  return (methodBreakdown.value.stk + methodBreakdown.value.smtp) === 0
})

const windowLabel = computed(() => {
  if (windowHours.value === 24) return "last 24h"
  if (windowHours.value === 168) return "last 7 days"
  return "last 30 days"
})

const selectedBookId = ref<number | null>(null)
</script>

<template>
  <div class="p-4 space-y-4">
    <!-- Page header -->
    <div class="flex flex-wrap items-center gap-3 justify-between">
      <h1 class="text-xl font-semibold tracking-tight">Dashboard</h1>
      <div class="flex items-center gap-3 text-sm">
        <!-- Window selector -->
        <div class="inline-flex rounded-md border border-border overflow-hidden text-xs">
          <button
            v-for="opt in [
              { h: 24,  label: '24h' },
              { h: 168, label: '7d'  },
              { h: 720, label: '30d' },
            ]"
            :key="opt.h"
            type="button"
            :class="[
              'px-3 py-1.5 transition-colors',
              windowHours === opt.h
                ? 'bg-primary text-primary-foreground'
                : 'hover:bg-accent/40 text-muted-foreground',
            ]"
            @click="setWindowHours(opt.h)"
          >
            {{ opt.label }}
          </button>
        </div>
        <!-- Live indicator -->
        <div class="flex items-center gap-1.5">
          <span
            :class="[
              'w-2 h-2 rounded-full',
              isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-500',
            ]"
          />
          <span :class="isLive ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'">
            {{ isLive ? 'Live' : 'Reconnecting…' }}
          </span>
          <span v-if="lastUpdateStr" class="text-muted-foreground text-xs ml-1">
            {{ lastUpdateStr }}
          </span>
        </div>
      </div>
    </div>

    <!-- KPI tiles row -->
    <KpiCards :kpis="kpis" />

    <!-- Charts grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- 1. Status Pie (all-time) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Book status (all time)</h2>
        <div class="h-64">
          <StatusPie :counts="statusCounts" />
        </div>
      </div>

      <!-- 2. Throughput Line (windowed) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">
          Throughput — {{ windowLabel }}
        </h2>
        <div class="h-64 relative">
          <ThroughputLine
            :series="throughput.series"
            :bucket-minutes="throughput.bucket_minutes"
          />
          <div
            v-if="throughputIsEmpty"
            class="absolute inset-0 flex items-center justify-center pointer-events-none bg-background/60 backdrop-blur-[1px]"
          >
            <div class="text-center px-4">
              <div class="text-sm font-medium text-muted-foreground">No deliveries in {{ windowLabel }}</div>
              <div class="text-xs text-muted-foreground/70 mt-1">
                Pick a longer window above or queue a book to see activity here.
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 3. Method Breakdown (windowed) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">
          Delivery method — {{ windowLabel }}
        </h2>
        <div class="h-64 relative">
          <MethodBreakdownBar :breakdown="methodBreakdown" />
          <div
            v-if="methodIsEmpty"
            class="absolute inset-0 flex items-center justify-center pointer-events-none bg-background/60 backdrop-blur-[1px]"
          >
            <div class="text-center px-4">
              <div class="text-sm font-medium text-muted-foreground">Nothing kindled in {{ windowLabel }}</div>
              <div class="text-xs text-muted-foreground/70 mt-1">
                The STK/SMTP split shows up once books start being sent.
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 4. Source funnel (all-time, scrollable) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm overflow-auto">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Per-source funnel</h2>
        <SourceFunnelBars :funnel="sourceFunnel" />
      </div>

      <!-- 5. Stage timings (last 7d) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">
          Stage latency — last 7 days (p50 / p90 / p99)
        </h2>
        <StageTimings :stats="stageTimings" />
      </div>

      <!-- 6. Recent activity -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm overflow-hidden flex flex-col">
        <h2 class="text-sm font-medium text-muted-foreground mb-2">Recent activity</h2>
        <div class="overflow-y-auto max-h-[28rem] -mx-2">
          <RecentActivity :events="recentEvents" @select="selectedBookId = $event" />
        </div>
      </div>
    </div>

    <BookDetailDrawer
      v-if="selectedBookId"
      :id="selectedBookId"
      @close="selectedBookId = null"
    />
  </div>
</template>
