<script setup lang="ts">
/**
 * RecentActivity.vue — Last 30 user-meaningful events: state changes,
 * sends, downloads, errors. Click an event with a book_id to open the
 * book detail drawer (via emit).
 */
import { computed } from "vue"
import { AlertCircle, CheckCircle2, Send, Download, ArrowRight, Inbox } from "lucide-vue-next"
import type { RecentEvent } from "@/composables/useDashboardStream"

const props = defineProps<{ events: RecentEvent[] }>()
const emit = defineEmits<{ (e: "select", bookId: number): void }>()

const items = computed(() => props.events.slice(0, 30))

function iconFor(kind: string) {
  if (kind === "error") return AlertCircle
  if (kind === "send-stk" || kind === "send") return Send
  if (kind === "download") return Download
  if (kind === "oversize-routed-stk") return ArrowRight
  if (kind === "state_change") return CheckCircle2
  return Inbox
}

function colorFor(kind: string, message: string): string {
  if (kind === "error" || /failed|error/i.test(message)) return "text-red-500"
  if (kind === "send-stk" || kind === "send") return "text-emerald-500"
  if (kind === "download") return "text-blue-500"
  return "text-muted-foreground"
}

function timeAgo(ts: string): string {
  try {
    const t = new Date(ts.replace(" ", "T") + (ts.endsWith("Z") ? "" : "Z"))
    const sec = Math.floor((Date.now() - t.getTime()) / 1000)
    if (sec < 60) return `${sec}s ago`
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
    return `${Math.floor(sec / 86400)}d ago`
  } catch {
    return ts
  }
}
</script>

<template>
  <div class="space-y-1.5">
    <div
      v-if="items.length === 0"
      class="text-center text-xs text-muted-foreground py-8"
    >
      No recent activity yet.
    </div>
    <button
      v-for="(e, i) in items"
      :key="i"
      class="w-full text-left flex items-start gap-2.5 px-2 py-1.5 rounded hover:bg-accent/40 transition-colors group"
      :disabled="!e.book_id"
      @click="e.book_id && emit('select', e.book_id)"
    >
      <component :is="iconFor(e.kind)" :class="['w-3.5 h-3.5 mt-0.5 shrink-0', colorFor(e.kind, e.message)]" />
      <div class="flex-1 min-w-0">
        <div class="text-xs flex items-baseline gap-2">
          <span v-if="e.book_title" class="font-medium truncate">{{ e.book_title }}</span>
          <span v-else class="text-muted-foreground italic">(no book)</span>
          <span class="text-[10px] text-muted-foreground shrink-0 ml-auto">{{ timeAgo(e.ts) }}</span>
        </div>
        <div class="text-[11px] text-muted-foreground line-clamp-1">
          <span v-if="e.scraper" class="text-foreground/70 mr-1">{{ e.scraper }}</span>
          {{ e.message }}
        </div>
      </div>
    </button>
  </div>
</template>
