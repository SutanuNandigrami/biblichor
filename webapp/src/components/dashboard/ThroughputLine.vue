<script setup lang="ts">
/**
 * ThroughputLine.vue — Line chart: sends per bucket over the last 24h.
 * Two series: STK (purple/primary) and SMTP (green).
 */
import { computed } from "vue"
import { Line } from "vue-chartjs"
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Legend,
  Filler,
} from "chart.js"
import "chartjs-adapter-date-fns"

ChartJS.register(LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend, Filler)

interface ThroughputPoint { t: string; v: number }
interface ThroughputSeries { name: string; points: ThroughputPoint[] }

const props = defineProps<{
  series: ThroughputSeries[]
  bucketMinutes?: number
}>()

const chartData = computed(() => {
  const stkSeries = props.series.find((s) => s.name === "stk")
  const smtpSeries = props.series.find((s) => s.name === "smtp")

  const toDataset = (s: ThroughputSeries | undefined, label: string, color: string) => ({
    label,
    data: (s?.points ?? []).map((p) => ({ x: new Date(p.t).getTime(), y: p.v })),
    borderColor: color,
    backgroundColor: color + "22",
    borderWidth: 2,
    pointRadius: 0,
    fill: true,
    tension: 0.3,
  })

  return {
    datasets: [
      toDataset(stkSeries, "STK", "#a855f7"),    // purple-500
      toDataset(smtpSeries, "SMTP", "#22c55e"),   // green-500
    ],
  }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index" as const, intersect: false },
  plugins: {
    legend: {
      position: "bottom" as const,
      labels: { boxWidth: 12, padding: 10, font: { size: 11 } },
    },
    tooltip: {
      callbacks: {
        title: (items: any[]) => {
          if (!items.length) return ""
          return new Date(items[0].parsed.x).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })
        },
      },
    },
  },
  scales: {
    x: {
      type: "time" as const,
      time: {
        unit: "hour" as const,
        displayFormats: { hour: "HH:mm" },
      },
      ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, font: { size: 10 } },
      grid: { display: false },
    },
    y: {
      beginAtZero: true,
      ticks: { precision: 0, font: { size: 10 } },
      grid: { color: "rgba(128,128,128,0.1)" },
    },
  },
}))
</script>

<template>
  <div class="h-full">
    <Line :data="chartData" :options="chartOptions" class="h-full" />
  </div>
</template>
