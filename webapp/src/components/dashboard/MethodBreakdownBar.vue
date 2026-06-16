<script setup lang="ts">
/**
 * MethodBreakdownBar.vue — Stacked bar showing STK vs SMTP delivery totals
 * for the active dashboard window (24h / 7d / 30d).
 */
import { computed } from "vue"
import { Bar } from "vue-chartjs"
import {
  Chart as ChartJS,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
} from "chart.js"

ChartJS.register(BarElement, CategoryScale, LinearScale, Tooltip, Legend)

// `breakdown` may carry a `window_hours` field from the new windowed
// snapshot, or be a plain {stk, smtp} dict for back-compat. Both forms
// render correctly; only the chart label changes.
const props = defineProps<{
  breakdown: Record<string, number> & { window_hours?: number }
}>()

const stk = computed(() => Number(props.breakdown["stk"] ?? 0))
const smtp = computed(() => Number(props.breakdown["smtp"] ?? 0))
const total = computed(() => stk.value + smtp.value)
const windowLabel = computed(() => {
  const h = props.breakdown.window_hours
  if (h === 168) return "Last 7 days"
  if (h === 720) return "Last 30 days"
  return "Last 24h"
})

const chartData = computed(() => ({
  labels: [windowLabel.value],
  datasets: [
    {
      label: "STK",
      data: [stk.value],
      backgroundColor: "#a855f7",  // purple-500
      borderRadius: 4,
    },
    {
      label: "SMTP",
      data: [smtp.value],
      backgroundColor: "#22c55e",  // green-500
      borderRadius: 4,
    },
  ],
}))

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  indexAxis: "y" as const,
  plugins: {
    legend: {
      position: "bottom" as const,
      labels: { boxWidth: 12, padding: 10, font: { size: 11 } },
    },
    tooltip: {
      callbacks: {
        label: (ctx: any) => ` ${ctx.dataset.label}: ${ctx.parsed.x}`,
      },
    },
  },
  scales: {
    x: {
      stacked: true,
      beginAtZero: true,
      ticks: { precision: 0, font: { size: 10 } },
      grid: { color: "rgba(128,128,128,0.1)" },
    },
    y: {
      stacked: true,
      grid: { display: false },
    },
  },
}
</script>

<template>
  <div class="h-full flex flex-col">
    <div class="flex gap-6 mb-3 text-sm">
      <div class="flex flex-col items-center">
        <span class="text-2xl font-bold tabular-nums">{{ stk.toLocaleString() }}</span>
        <span class="text-xs text-purple-500 font-medium">STK</span>
      </div>
      <div class="flex flex-col items-center">
        <span class="text-2xl font-bold tabular-nums">{{ smtp.toLocaleString() }}</span>
        <span class="text-xs text-green-500 font-medium">SMTP</span>
      </div>
      <div class="flex flex-col items-center">
        <span class="text-2xl font-bold tabular-nums">{{ total.toLocaleString() }}</span>
        <span class="text-xs text-muted-foreground">total</span>
      </div>
    </div>
    <div class="flex-1 min-h-0">
      <Bar :data="chartData" :options="chartOptions" class="h-full" />
    </div>
  </div>
</template>
