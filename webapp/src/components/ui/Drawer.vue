<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ open: boolean; title?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

// ── Resizable width persistence ──────────────────────────────
// User can drag the left edge of the drawer to resize. Width is
// clamped to [320, 95vw] and persisted to localStorage so it
// survives reloads. On screens below sm (640px) the drawer goes
// full-width and the handle is hidden — dragging is desktop-only.
const STORAGE_KEY = 'biblichor.drawer.width'
const DEFAULT_PX = 640
const MIN_PX = 320
const SM_BREAKPOINT = 640
const ARROW_STEP_PX = 32

const width = ref<number>(DEFAULT_PX)
const dragging = ref(false)
// Track viewport width reactively so the breakpoint check and the
// 95vw max clamp update when the user resizes the window.
const viewportWidth = ref<number>(
  typeof window !== 'undefined' ? window.innerWidth : DEFAULT_PX * 2,
)

function _safeReadStorage(): number | null {
  // localStorage.getItem() can throw when storage is disabled (Safari
  // private browsing pre-15, certain sandboxes). Treat any failure as
  // "no stored value".
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return null
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}
function _safeWriteStorage(value: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    /* private mode / disabled — silently skip */
  }
}

function _clamp(n: number): number {
  const maxPx = Math.round(viewportWidth.value * 0.95)
  return Math.max(MIN_PX, Math.min(maxPx, n))
}

function _onResize() {
  viewportWidth.value = window.innerWidth
  // Re-clamp the current width so it never exceeds the new 95vw bound.
  width.value = _clamp(width.value)
}

onMounted(() => {
  const stored = _safeReadStorage()
  if (stored !== null && stored >= MIN_PX) {
    width.value = _clamp(stored)
  }
  window.addEventListener('resize', _onResize)
})

const style = computed(() => {
  // Below sm breakpoint we let CSS handle full-width via class;
  // above it we apply the user-chosen pixel width.
  if (viewportWidth.value < SM_BREAKPOINT) return {}
  return { width: width.value + 'px' }
})

function _onMove(e: MouseEvent | TouchEvent) {
  // Touch events: guard against an empty touches list (touchend fires
  // with touches=[]); also fall back to changedTouches when touches is
  // empty mid-gesture on some browsers.
  let x: number
  if ('touches' in e) {
    const t = e.touches[0] ?? (e as TouchEvent).changedTouches?.[0]
    if (!t) return
    x = t.clientX
    // Prevent the page from scrolling/zooming during a drag gesture on
    // touch devices. Only fires when the listener is non-passive (we
    // re-register touchmove as non-passive in startDrag).
    if (e.cancelable) e.preventDefault()
  } else {
    x = (e as MouseEvent).clientX
  }
  // drawer is right-anchored, so width = viewport_width - x
  width.value = _clamp(viewportWidth.value - x)
}
function _onUp() {
  if (!dragging.value) return
  dragging.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  _safeWriteStorage(width.value)
  window.removeEventListener('mousemove', _onMove)
  window.removeEventListener('mouseup', _onUp)
  window.removeEventListener('touchmove', _onMove)
  window.removeEventListener('touchend', _onUp)
  window.removeEventListener('touchcancel', _onUp)
}
function startDrag(e: MouseEvent | TouchEvent) {
  if (viewportWidth.value < SM_BREAKPOINT) return  // full-width on mobile, no drag
  dragging.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', _onMove)
  window.addEventListener('mouseup', _onUp)
  // touchmove must NOT be passive — we call preventDefault() inside
  // _onMove to stop the page from scrolling during the drag. The
  // {passive:false} contract is required by spec for preventDefault
  // to take effect.
  window.addEventListener('touchmove', _onMove, { passive: false })
  window.addEventListener('touchend', _onUp)
  window.addEventListener('touchcancel', _onUp)
  e.preventDefault()
}

// Keyboard a11y: the handle is a focusable element with role="separator".
// Left/Right arrow keys nudge the width; Home/End jump to clamp bounds.
function onHandleKey(e: KeyboardEvent) {
  if (viewportWidth.value < SM_BREAKPOINT) return
  let next = width.value
  switch (e.key) {
    case 'ArrowLeft':
      // Left grows the drawer (handle is on left, drawer extends right)
      next = _clamp(width.value + ARROW_STEP_PX)
      break
    case 'ArrowRight':
      next = _clamp(width.value - ARROW_STEP_PX)
      break
    case 'Home':
      next = MIN_PX
      break
    case 'End':
      next = _clamp(Number.MAX_SAFE_INTEGER)
      break
    default:
      return
  }
  width.value = next
  _safeWriteStorage(next)
  e.preventDefault()
}

// If the drawer is closed while a drag is in flight, _onUp would
// never fire and listeners + body styles would leak. Watch for it.
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) _onUp()
  },
)

onBeforeUnmount(() => {
  _onUp()
  window.removeEventListener('resize', _onResize)
})
</script>
<template>
  <teleport to="body">
    <transition
      enter-active-class="transition-opacity duration-150"
      enter-from-class="opacity-0" leave-active-class="transition-opacity duration-150"
      leave-to-class="opacity-0">
      <div v-if="props.open" class="fixed inset-0 bg-black/60 z-40" @click="emit('close')" />
    </transition>
    <transition
      enter-active-class="transition-transform duration-200"
      enter-from-class="translate-x-full" leave-active-class="transition-transform duration-200"
      leave-to-class="translate-x-full">
      <aside v-if="props.open"
        :style="style"
        :class="[
          'fixed top-0 right-0 h-screen w-full bg-card border-l border-border z-50 flex',
          dragging ? 'select-none' : '',
        ]">
        <!-- Drag handle: full viewport-height because it sits OUTSIDE the
             scrollable content area. Hidden on mobile (drawer is full-width).
             Focusable via Tab, resizable via Left/Right arrows + Home/End. -->
        <div
          class="hidden sm:flex shrink-0 w-1.5 h-screen cursor-col-resize group relative z-[60] touch-none focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="dragging ? 'bg-primary/60' : 'hover:bg-primary/40'"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel: drag, or press Left/Right arrow keys"
          aria-valuemin="320"
          :aria-valuenow="Math.round(width)"
          :aria-valuemax="Math.round(viewportWidth * 0.95)"
          tabindex="0"
          @mousedown="startDrag"
          @touchstart="startDrag"
          @keydown="onHandleKey"
        >
          <!-- Visual indicator: slim vertical bar centered on the handle -->
          <div
            class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-12 rounded-full transition-colors pointer-events-none"
            :class="dragging ? 'bg-primary' : 'bg-border group-hover:bg-primary'"
          />
        </div>

        <!-- Scrollable content column. Sibling to the handle so scrolling
             inside the drawer doesn't move the drag bar. -->
        <div class="flex-1 min-w-0 h-screen overflow-y-auto">
          <header class="sticky top-0 bg-card border-b border-border h-14 px-4 flex items-center justify-between z-10">
            <h2 class="font-semibold">{{ title }}</h2>
            <button class="p-1 rounded hover:bg-accent" @click="emit('close')"><X class="w-4 h-4" /></button>
          </header>
          <div class="p-4">
            <slot />
          </div>
        </div>
      </aside>
    </transition>
  </teleport>
</template>
