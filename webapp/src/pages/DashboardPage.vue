<script setup lang="ts">
/**
 * DashboardPage.vue — Live /dashboard page with 4 charts.
 * SSE-connected via useDashboardStream composable.
 */
import { computed } from "vue"
import { useDashboardStream } from "@/composables/useDashboardStream"
import StatusPie from "@/components/dashboard/StatusPie.vue"
import ThroughputLine from "@/components/dashboard/ThroughputLine.vue"
import MethodBreakdownBar from "@/components/dashboard/MethodBreakdownBar.vue"
import SourceFunnelBars from "@/components/dashboard/SourceFunnelBars.vue"

const {
  statusCounts,
  throughput24h,
  methodBreakdown24h,
  sourceFunnel,
  lastUpdate,
  connectionState,
} = useDashboardStream()

const isLive = computed(() => connectionState.value === "connected")

const lastUpdateStr = computed(() => {
  if (!lastUpdate.value) return null
  try {
    return new Date(lastUpdate.value).toLocaleTimeString()
  } catch {
    return lastUpdate.value
  }
})
</script>

<template>
  <div class="p-4 space-y-4">
    <!-- Page header -->
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold tracking-tight">Dashboard</h1>
      <div class="flex items-center gap-2 text-sm">
        <span
          :class="[
            'w-2.5 h-2.5 rounded-full',
            isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-500',
          ]"
        />
        <span :class="isLive ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'">
          {{ isLive ? "Live" : "Reconnecting…" }}
        </span>
        <span v-if="lastUpdateStr" class="text-muted-foreground text-xs">
          updated {{ lastUpdateStr }}
        </span>
      </div>
    </div>

    <!-- 2×2 Grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">

      <!-- 1. Status Pie -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Book Status</h2>
        <div class="h-64">
          <StatusPie :counts="statusCounts" />
        </div>
      </div>

      <!-- 2. Throughput Line -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Throughput — last 24h</h2>
        <div class="h-64">
          <ThroughputLine
            :series="throughput24h.series"
            :bucket-minutes="throughput24h.bucket_minutes"
          />
        </div>
      </div>

      <!-- 3. Method Breakdown -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Delivery method — last 24h</h2>
        <div class="h-64">
          <MethodBreakdownBar :breakdown="methodBreakdown24h" />
        </div>
      </div>

      <!-- 4. Source Funnel (scrollable if many sources) -->
      <div class="bg-card border border-border rounded-xl p-4 shadow-sm overflow-auto">
        <h2 class="text-sm font-medium text-muted-foreground mb-3">Per-source funnel</h2>
        <SourceFunnelBars :funnel="sourceFunnel" />
      </div>

    </div>
  </div>
</template>
