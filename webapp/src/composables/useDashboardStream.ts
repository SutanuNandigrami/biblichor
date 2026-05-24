/**
 * useDashboardStream.ts
 * Composable: fetches initial dashboard snapshot, then opens an SSE stream
 * for live updates every ~3s. Cleans up EventSource on unmount.
 */
import { ref, onMounted, onUnmounted } from "vue"
import { api } from "@/composables/useApi"

// ──────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────

export interface ThroughputPoint {
  t: string
  v: number
}

export interface ThroughputSeries {
  name: string
  points: ThroughputPoint[]
}

export interface Throughput24h {
  bucket_minutes: number
  series: ThroughputSeries[]
}

export interface SourceFunnelEntry {
  source: string
  discovered: number
  downloaded: number
  sent: number
}

export interface DashboardSnapshot {
  ts: string
  status_counts: Record<string, number>
  throughput_24h: Throughput24h
  method_breakdown_24h: Record<string, number>
  source_funnel: SourceFunnelEntry[]
}

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "error"

// ──────────────────────────────────────────────────────────
// Composable
// ──────────────────────────────────────────────────────────

export function useDashboardStream() {
  const statusCounts = ref<Record<string, number>>({})
  const throughput24h = ref<Throughput24h>({ bucket_minutes: 5, series: [] })
  const methodBreakdown24h = ref<Record<string, number>>({ stk: 0, smtp: 0 })
  const sourceFunnel = ref<SourceFunnelEntry[]>([])
  const lastUpdate = ref<string | null>(null)
  const connectionState = ref<ConnectionState>("connecting")

  let es: EventSource | null = null

  function _applySnapshot(snap: DashboardSnapshot) {
    statusCounts.value = snap.status_counts ?? {}
    throughput24h.value = snap.throughput_24h ?? { bucket_minutes: 5, series: [] }
    methodBreakdown24h.value = snap.method_breakdown_24h ?? { stk: 0, smtp: 0 }
    sourceFunnel.value = snap.source_funnel ?? []
    lastUpdate.value = snap.ts
  }

  function _openStream() {
    if (es) {
      es.close()
      es = null
    }
    connectionState.value = "connecting"
    es = new EventSource("/api/dashboard/stream")

    es.onopen = () => {
      connectionState.value = "connected"
    }

    es.onmessage = (evt) => {
      connectionState.value = "connected"
      try {
        const snap: DashboardSnapshot = JSON.parse(evt.data)
        _applySnapshot(snap)
      } catch {
        /* malformed frame — skip */
      }
    }

    es.onerror = () => {
      connectionState.value = "reconnecting"
      // Browser auto-reconnects SSE; we just reflect the state
    }
  }

  onMounted(async () => {
    // 1. Fetch initial snapshot so charts render immediately
    try {
      const snap = await api<DashboardSnapshot>("/api/dashboard/snapshot")
      _applySnapshot(snap)
    } catch {
      /* network error on mount — SSE will catch up */
    }
    // 2. Open live stream
    _openStream()
  })

  onUnmounted(() => {
    if (es) {
      es.close()
      es = null
    }
  })

  return {
    statusCounts,
    throughput24h,
    methodBreakdown24h,
    sourceFunnel,
    lastUpdate,
    connectionState,
  }
}
