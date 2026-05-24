<script setup lang="ts">
/**
 * SourceFunnelBars.vue — Horizontal stacked bar chart, one row per source.
 * Shows discovered → downloaded → sent funnel stages.
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

export interface SourceFunnelEntry {
  source: string
  discovered: number
  downloaded: number
  sent: number
}

const props = defineProps<{
  funnel: SourceFunnelEntry[]
}>()

// Only show top 10 sources by discovered count, sorted desc
const topSources = computed(() =>
  [...props.funnel]
    .sort((a, b) => b.discovered - a.discovered)
    .slice(0, 10)
)

const chartData = computed(() => ({
  labels: topSources.value.map((s) => s.source),
  datasets: [
    {
      label: "Sent",
      data: topSources.value.map((s) => s.sent),
      backgroundColor: "#22c55e",   // green-500
      borderRadius: 2,
    },
    {
      label: "Downloaded (not sent)",
      data: topSources.value.map((s) => Math.max(0, s.downloaded - s.sent)),
      backgroundColor: "#60a5fa",   // blue-400
      borderRadius: 2,
    },
    {
      label: "Queued (not downloaded)",
      data: topSources.value.map((s) => Math.max(0, s.discovered - s.downloaded)),
      backgroundColor: "#e2e8f0",   // slate-200
      borderRadius: 2,
    },
  ],
}))

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  indexAxis: "y" as const,
  plugins: {
    legend: {
      position: "bottom" as const,
      labels: { boxWidth: 12, padding: 8, font: { size: 10 } },
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
      ticks: { font: { size: 10 } },
      grid: { display: false },
    },
  },
}))

// Dynamic height: at least 120px, 36px per source row
const chartHeight = computed(() => Math.max(120, topSources.value.length * 36 + 60))
</script>

<template>
  <div v-if="funnel.length === 0" class="flex items-center justify-center h-full text-muted-foreground text-sm">
    No sources yet
  </div>
  <div v-else :style="{ height: chartHeight + 'px' }">
    <Bar :data="chartData" :options="chartOptions" class="w-full h-full" />
  </div>
</template>
