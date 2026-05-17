import { reactive } from 'vue'

export type Toast = {
  id: number
  kind: 'success' | 'error' | 'info'
  title: string
  message?: string
}

const state = reactive<{ items: Toast[] }>({ items: [] })
let _id = 0

export function useToast() {
  function push(t: Omit<Toast, 'id'>) {
    const item: Toast = { ...t, id: ++_id }
    state.items.push(item)
    setTimeout(() => dismiss(item.id), 5000)
  }
  function dismiss(id: number) {
    state.items = state.items.filter((x) => x.id !== id)
  }
  return { state, push, dismiss,
    success: (title: string, message?: string) => push({ kind: 'success', title, message }),
    error:   (title: string, message?: string) => push({ kind: 'error',   title, message }),
    info:    (title: string, message?: string) => push({ kind: 'info',    title, message }),
  }
}
