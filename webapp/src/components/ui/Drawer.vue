<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

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

const width = ref<number>(DEFAULT_PX)
const dragging = ref(false)

onMounted(() => {
  const stored = Number(localStorage.getItem(STORAGE_KEY))
  if (stored && stored >= MIN_PX && stored < window.innerWidth) {
    width.value = stored
  }
})

const maxPx = () => Math.round(window.innerWidth * 0.95)

const style = computed(() => {
  // Below sm breakpoint we let CSS handle full-width via class;
  // above it we apply the user-chosen pixel width.
  if (typeof window === 'undefined' || window.innerWidth < 640) return {}
  return { width: width.value + 'px' }
})

function _onMove(e: MouseEvent | TouchEvent) {
  const x = 'touches' in e ? e.touches[0].clientX : e.clientX
  // drawer is right-anchored, so width = viewport_width - x
  const next = Math.max(MIN_PX, Math.min(maxPx(), window.innerWidth - x))
  width.value = next
}
function _onUp() {
  if (!dragging.value) return
  dragging.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  localStorage.setItem(STORAGE_KEY, String(width.value))
  window.removeEventListener('mousemove', _onMove)
  window.removeEventListener('mouseup', _onUp)
  window.removeEventListener('touchmove', _onMove)
  window.removeEventListener('touchend', _onUp)
}
function startDrag(e: MouseEvent | TouchEvent) {
  if (window.innerWidth < 640) return  // full-width on mobile, no drag
  dragging.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', _onMove)
  window.addEventListener('mouseup', _onUp)
  window.addEventListener('touchmove', _onMove, { passive: true })
  window.addEventListener('touchend', _onUp)
  e.preventDefault()
}
onBeforeUnmount(_onUp)
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
          'fixed top-0 right-0 h-screen w-full bg-card border-l border-border z-50 overflow-y-auto',
          dragging ? 'select-none' : '',
        ]">
        <!-- Drag handle: hidden on mobile (full-width drawer there) -->
        <div
          class="hidden sm:block absolute top-0 left-0 h-full w-1.5 -ml-0.5 cursor-col-resize group z-[60]"
          :class="dragging ? 'bg-primary/60' : 'hover:bg-primary/40'"
          aria-label="Drag to resize"
          @mousedown="startDrag"
          @touchstart="startDrag"
        >
          <!-- Hover/drag visual indicator: a slim vertical bar -->
          <div
            class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-12 rounded-full transition-colors"
            :class="dragging ? 'bg-primary' : 'bg-border group-hover:bg-primary'"
          />
        </div>

        <header class="sticky top-0 bg-card border-b border-border h-14 px-4 flex items-center justify-between z-10">
          <h2 class="font-semibold">{{ title }}</h2>
          <button class="p-1 rounded hover:bg-accent" @click="emit('close')"><X class="w-4 h-4" /></button>
        </header>
        <div class="p-4">
          <slot />
        </div>
      </aside>
    </transition>
  </teleport>
</template>
