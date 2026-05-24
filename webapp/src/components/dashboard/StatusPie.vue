<script setup lang="ts">
/**
 * StatusPie.vue — Donut chart showing current book status breakdown.
 * Uses Chart.js via vue-chartjs.
 */
import { computed } from "vue"
import { Doughnut } from "vue-chartjs"
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
} from "chart.js"

ChartJS.register(ArcElement, Tooltip, Legend)

const props = defineProps<{
  counts: Record<string, number>
}>()

const STATUS_COLORS: Record<string, string> = {
  kindled:      "#22c55e",  // green-500
  sent:         "#86efac",  // green-300
  queued:       "#eab308",  // yellow-500
  needs_review: "#f97316",  // orange-500
  failed:       "#ef4444",  // red-500
  skipped:      "#94a3b8",  // slate-400
}

const DEFAULT_COLOR = "#cbd5e1"  // slate-300

const chartData = computed(() => {
  const entries = Object.entries(props.counts).filter(([, v]) => v > 0)
  if (entries.length === 0) {
    return {
      labels: ["No data"],
      datasets: [{ data: [1], backgroundColor: [DEFAULT_COLOR], borderWidth: 0 }],
    }
  }
  return {
    labels: entries.map(([k]) => k),
    datasets: [
      {
        data: entries.map(([, v]) => v),
        backgroundColor: entries.map(([k]) => STATUS_COLORS[k] ?? DEFAULT_COLOR),
        borderWidth: 2,
        borderColor: "transparent",
        hoverBorderColor: "transparent",
      },
    ],
  }
})

const total = computed(() => Object.values(props.counts).reduce((a, b) => a + b, 0))

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  cutout: "65%",
  plugins: {
    legend: {
      position: "bottom" as const,
      labels: {
        boxWidth: 12,
        padding: 10,
        font: { size: 11 },
      },
    },
    tooltip: {
      callbacks: {
        label: (ctx: any) => {
          const val = ctx.parsed
          const pct = total.value > 0 ? Math.round((val / total.value) * 100) : 0
          return ` ${ctx.label}: ${val} (${pct}%)`
        },
      },
    },
  },
}
</script>

<template>
  <div class="relative h-full flex flex-col items-center justify-center">
    <Doughnut :data="chartData" :options="chartOptions" class="max-h-56" />
    <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-center pointer-events-none">
      <div class="text-2xl font-bold tabular-nums">{{ total.toLocaleString() }}</div>
      <div class="text-xs text-muted-foreground">total</div>
    </div>
  </div>
</template>
