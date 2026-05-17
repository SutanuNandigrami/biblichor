import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/composables/useApi'

export const useCycleStore = defineStore('cycle', () => {
  const running = ref(false)
  const lastTally = ref<Record<string, any> | null>(null)

  async function runNow() {
    const r = await api<{ ok: boolean; running: boolean }>('/api/cycle/run-now', { method: 'POST' })
    running.value = !!r.running
    return r
  }
  function applyStatus(p: { running: boolean; last_tally: Record<string, any> | null }) {
    running.value = p.running
    lastTally.value = p.last_tally
  }
  async function refresh() {
    const r = await api<{ running: boolean; last_tally: any }>('/api/cycle/status')
    applyStatus(r)
  }
  return { running, lastTally, runNow, applyStatus, refresh }
})
