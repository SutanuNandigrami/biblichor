/**
 * useDashboardStream.ts
 * Composable: fetches initial dashboard snapshot, then opens an SSE stream
 * for live updates every ~3s. Cleans up EventSource on unmount.
 *
 * window_hours can change at runtime via setWindowHours — composable
 * re-fetches the snapshot and reopens the stream against the new window.
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

export interface Throughput {
  window_hours: number
  bucket_minutes: number
  series: ThroughputSeries[]
}

export interface MethodBreakdown {
  window_hours: number
  stk: number
  smtp: number
}

export interface SourceFunnelEntry {
  source: string
  discovered: number
  downloaded: number
  sent: number
}

export interface Kpis {
  queue_depth: number
  in_flight: number
  today_sent: number
  recent_failures: number
}

export interface RecentEvent {
  ts: string
  kind: string
  scraper: string
  message: string
  book_id: number | null
  book_title: string | null
}

export interface StageStats {
  count: number
  p50: number | null
  p90: number | null
  p99: number | null
}

export interface StageTimings {
  window_hours: number
  search_to_downloaded_seconds: StageStats
  downloaded_to_sent_seconds: StageStats
  search_to_sent_seconds: StageStats
}

export interface DashboardSnapshot {
  ts: string
  window_hours: number
  kpis: Kpis
  status_counts: Record<string, number>
  throughput: Throughput
  method_breakdown: MethodBreakdown
  source_funnel: SourceFunnelEntry[]
  recent_events: RecentEvent[]
  stage_timings: StageTimings
  // back-compat fields (unused by new SPA but kept by backend)
  throughput_24h?: Throughput
  method_breakdown_24h?: Record<string, number>
}

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "error"

const EMPTY_THROUGHPUT: Throughput = { window_hours: 24, bucket_minutes: 5, series: [] }
const EMPTY_METHOD: MethodBreakdown = { window_hours: 24, stk: 0, smtp: 0 }
const EMPTY_KPIS: Kpis = { queue_depth: 0, in_flight: 0, today_sent: 0, recent_failures: 0 }
const EMPTY_STAGE_STATS: StageStats = { count: 0, p50: null, p90: null, p99: null }
const EMPTY_STAGE_TIMINGS: StageTimings = {
  window_hours: 168,
  search_to_downloaded_seconds: EMPTY_STAGE_STATS,
  downloaded_to_sent_seconds: EMPTY_STAGE_STATS,
  search_to_sent_seconds: EMPTY_STAGE_STATS,
}

// ──────────────────────────────────────────────────────────
// Composable
// ──────────────────────────────────────────────────────────

export function useDashboardStream(initialWindowHours = 24) {
  const windowHours = ref<number>(initialWindowHours)
  const statusCounts = ref<Record<string, number>>({})
  const throughput = ref<Throughput>(EMPTY_THROUGHPUT)
  const methodBreakdown = ref<MethodBreakdown>(EMPTY_METHOD)
  const sourceFunnel = ref<SourceFunnelEntry[]>([])
  const kpis = ref<Kpis>(EMPTY_KPIS)
  const recentEvents = ref<RecentEvent[]>([])
  const stageTimings = ref<StageTimings>(EMPTY_STAGE_TIMINGS)
  const lastUpdate = ref<string | null>(null)
  const connectionState = ref<ConnectionState>("connecting")

  let es: EventSource | null = null

  function _applySnapshot(snap: DashboardSnapshot) {
    statusCounts.value = snap.status_counts ?? {}
    throughput.value = snap.throughput ?? snap.throughput_24h ?? EMPTY_THROUGHPUT
    methodBreakdown.value = snap.method_breakdown ?? EMPTY_METHOD
    sourceFunnel.value = snap.source_funnel ?? []
    kpis.value = snap.kpis ?? EMPTY_KPIS
    recentEvents.value = snap.recent_events ?? []
    stageTimings.value = snap.stage_timings ?? EMPTY_STAGE_TIMINGS
    lastUpdate.value = snap.ts
  }

  function _openStream() {
    if (es) {
      es.close()
      es = null
    }
    connectionState.value = "connecting"
    es = new EventSource(`/api/dashboard/stream?window_hours=${windowHours.value}`)

    es.onopen = () => {
      connectionState.value = "connected"
    }
    es.onmessage = (evt) => {
      connectionState.value = "connected"
      try {
        _applySnapshot(JSON.parse(evt.data))
      } catch {
        /* malformed frame — skip */
      }
    }
    es.onerror = () => {
      connectionState.value = "reconnecting"
    }
  }

  async function _fetchSnapshot() {
    try {
      const snap = await api<DashboardSnapshot>(
        `/api/dashboard/snapshot?window_hours=${windowHours.value}`,
      )
      _applySnapshot(snap)
    } catch {
      /* network error — SSE will catch up */
    }
  }

  async function setWindowHours(h: number) {
    if (h === windowHours.value) return
    windowHours.value = h
    await _fetchSnapshot()
    _openStream()
  }

  onMounted(async () => {
    await _fetchSnapshot()
    _openStream()
  })

  onUnmounted(() => {
    if (es) {
      es.close()
      es = null
    }
  })

  return {
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
  }
}
