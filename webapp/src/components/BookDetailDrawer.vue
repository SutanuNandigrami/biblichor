<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '@/composables/useApi'
import Drawer from '@/components/ui/Drawer.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import StatusPill from '@/components/StatusPill.vue'
import Card from '@/components/ui/Card.vue'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ id: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const toast = useToast()

type Book = {
  id: number; title: string; author: string | null; isbn13: string | null
  status: string; format: string | null; attempts: number
  file_path: string | null; last_error: string | null; updated_at: string
  source: string; sent_at: string | null; md5: string | null
}
type Candidate = {
  id: number; provider: string; md5: string | null; title: string | null
  format: string | null; filesize_bytes: number | null; language: string | null
  score: number | null; year: number | null; edition_hints: string | null
}
type Event = {
  id: number; ts: string; kind: string; scraper: string | null; message: string
  meta: Record<string, any>
}

const book = ref<Book | null>(null)
const candidates = ref<Candidate[]>([])
const events = ref<Event[]>([])

async function load() {
  const r = await api<{ book: Book; candidates: Candidate[]; events: Event[] }>(`/api/books/${props.id}`)
  book.value = r.book
  candidates.value = r.candidates
  events.value = r.events
}
onMounted(load)
watch(() => props.id, load)

async function retry() {
  await api(`/api/books/${props.id}/retry`, { method: 'POST' })
  toast.success('Re-queued')
  await load()
}
async function del() {
  if (!confirm('Delete this book?')) return
  await api(`/api/books/${props.id}/delete`, { method: 'POST' })
  toast.success('Deleted')
  emit('close')
}
async function pick(c: Candidate) {
  await api(`/api/books/${props.id}/pick/${c.id}`, { method: 'POST' })
  toast.success(`Picked ${c.title?.slice(0, 40) ?? c.md5}`)
  await load()
}
function size(b?: number | null) {
  if (!b) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <Drawer :open="true" :title="book?.title ?? 'Loading…'" @close="emit('close')">
    <div v-if="book" class="space-y-6">
      <section>
        <div class="flex items-center gap-2 mb-2">
          <StatusPill :status="book.status" />
          <Badge v-if="book.format" variant="muted">{{ book.format }}</Badge>
          <Badge variant="muted">{{ book.source }}</Badge>
        </div>
        <p class="text-muted-foreground">{{ book.author ?? 'Unknown author' }}</p>
        <dl class="grid grid-cols-2 gap-y-1 mt-3 text-xs">
          <dt class="text-muted-foreground">ISBN-13</dt><dd>{{ book.isbn13 ?? '—' }}</dd>
          <dt class="text-muted-foreground">Attempts</dt><dd>{{ book.attempts }}</dd>
          <dt class="text-muted-foreground">File</dt><dd class="truncate">{{ book.file_path ?? '—' }}</dd>
          <dt class="text-muted-foreground">MD5</dt><dd class="font-mono text-[10px]">{{ book.md5 ?? '—' }}</dd>
          <dt class="text-muted-foreground">Updated</dt><dd>{{ book.updated_at }}</dd>
          <dt class="text-muted-foreground" v-if="book.sent_at">Sent</dt>
          <dd v-if="book.sent_at">{{ book.sent_at }}</dd>
        </dl>
        <div v-if="book.last_error" class="mt-3 p-2 rounded bg-amber-500/10 text-amber-200 text-xs border border-amber-500/30">
          {{ book.last_error }}
        </div>
        <div class="mt-4 flex gap-2">
          <Button variant="subtle" size="sm" @click="retry">Retry</Button>
          <Button variant="ghost" size="sm" @click="del">Delete</Button>
        </div>
      </section>

      <section>
        <h3 class="text-sm font-semibold mb-2">Candidates</h3>
        <Card v-if="candidates.length" class="overflow-hidden">
          <table class="w-full text-xs">
            <thead class="text-muted-foreground">
              <tr class="text-left">
                <th class="px-3 py-2">Score</th>
                <th class="px-3 py-2">Title</th>
                <th class="px-3 py-2">Fmt</th>
                <th class="px-3 py-2">Size</th>
                <th class="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in candidates" :key="c.id" class="border-t border-border/40">
                <td class="px-3 py-2 font-mono">{{ c.score?.toFixed(1) ?? '—' }}</td>
                <td class="px-3 py-2 truncate max-w-xs">{{ c.title ?? c.md5 }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ c.format ?? '—' }}</td>
                <td class="px-3 py-2 text-muted-foreground">{{ size(c.filesize_bytes) }}</td>
                <td class="px-3 py-2 text-right">
                  <Button size="sm" variant="outline" @click="pick(c)">Pick</Button>
                </td>
              </tr>
            </tbody>
          </table>
        </Card>
        <p v-else class="text-xs text-muted-foreground">No candidates yet. Try retrying.</p>
      </section>

      <section>
        <h3 class="text-sm font-semibold mb-2">Events</h3>
        <ul class="space-y-1 text-xs">
          <li v-for="e in events" :key="e.id" class="flex gap-2">
            <span class="text-muted-foreground">{{ e.ts }}</span>
            <Badge variant="info">{{ e.kind }}<span v-if="e.scraper"> / {{ e.scraper }}</span></Badge>
            <span class="flex-1 text-foreground">{{ e.message }}</span>
          </li>
          <li v-if="!events.length" class="text-muted-foreground">No events.</li>
        </ul>
      </section>
    </div>
  </Drawer>
</template>
