import { onBeforeUnmount, onMounted, ref } from 'vue'

/** Auto-reconnecting WebSocket. Calls `onMessage` for each parsed JSON frame. */
export function useEventStream(onMessage: (msg: { type: string; data: any }) => void) {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let backoff = 1_000
  let stopped = false

  function connect() {
    if (stopped) return
    const url =
      (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'
    ws = new WebSocket(url)
    ws.onopen = () => {
      connected.value = true
      backoff = 1_000
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        onMessage(msg)
      } catch (e) {
        console.warn('ws parse error', e)
      }
    }
    ws.onclose = () => {
      connected.value = false
      ws = null
      if (stopped) return
      setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, 30_000)
    }
    ws.onerror = () => ws?.close()
  }

  onMounted(connect)
  onBeforeUnmount(() => {
    stopped = true
    ws?.close()
  })

  return { connected }
}
