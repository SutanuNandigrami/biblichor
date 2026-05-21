<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Search, BookOpen, Plus, Trash2 } from 'lucide-vue-next'
import { api } from '@/composables/useApi'
import { useEventStream } from '@/composables/useWebSocket'
import { useToast } from '@/composables/useToast'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import StatusPill from '@/components/StatusPill.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Card from '@/components/ui/Card.vue'

type Book = {
  id: number; title: string; author: string | null
  source: string; status: string; format: string | null
  attempts: number; updated_at: string; last_error: string | null
  isbn13: string | null; file_path: string | null
}

const books = ref<Book[]>([])
const total = ref(0)
const query = ref('')
const statusFilter = ref('')
const addOpen = ref(false)
const addTitle = ref(''); const addAuthor = ref(''); const addIsbn = ref('')
const detailId = ref<number | null>(null)
const selected = ref<Set<number>>(new Set())
const toast = useToast()

async function fetchBooks() {
  const params = new URLSearchParams()
  if (query.value) params.set('q', query.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  const r = await api<{ books: Book[]; total: number }>('/api/books?' + params)
  books.value = r.books
  total.value = r.total
  const visible = new Set(r.books.map((b) => b.id))
  selected.value = new Set([...selected.value].filter((id) => visible.has(id)))
}
onMounted(fetchBooks)
watch([query, statusFilter], () => { selected.value.clear(); fetchBooks() })

useEventStream((msg) => {
  if (msg.type === 'event' && (msg.data.kind === 'state_change' || msg.data.kind === 'send')) {
    fetchBooks().catch(() => {})
  }
})

const statuses = computed(() => Array.from(new Set(books.value.map((b) => b.status))).sort())
const allVisibleSelected = computed(() =>
  books.value.length > 0 && books.value.every((b) => selected.value.has(b.id)),
)
const someSelected = computed(() => selected.value.size > 0)

function toggleOne(id: number) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  selected.value = next
}
function toggleAllVisible() {
  if (allVisibleSelected.value) {
    selected.value = new Set()
  } else {
    selected.value = new Set(books.value.map((b) => b.id))
  }
}

async function add() {
  if (!addTitle.value.trim()) return
  try {
    await api<{ id: number }>('/api/books', {
      method: 'POST',
      body: JSON.stringify({ title: addTitle.value, author: addAuthor.value || null, isbn13: addIsbn.value || null }),
    })
    addTitle.value = addAuthor.value = addIsbn.value = ''
    addOpen.value = false
    await fetchBooks()
    toast.success('Book added')
  } catch (e: any) { toast.error('Add failed', String(e?.message ?? e)) }
}

async function retry(b: Book) {
  try {
    await api(`/api/books/${b.id}/retry`, { method: 'POST' })
    toast.success(`Re-queued: ${b.title}`)
    await fetchBooks()
  } catch (e: any) { toast.error('Retry failed', String(e?.message ?? e)) }
}

async function del(b: Book) {
  if (!confirm(`Delete "${b.title}"?`)) return
  try {
    await api(`/api/books/${b.id}/delete`, { method: 'POST' })
    toast.success('Deleted')
    await fetchBooks()
  } catch (e: any) { toast.error('Delete failed', String(e?.message ?? e)) }
}

async function bulkDelete(opts: { hard: boolean }) {
  const ids = [...selected.value]
  if (!ids.length) return
  const label = opts.hard ? `Hard-delete ${ids.length} books? (cannot undo)` : `Delete ${ids.length} selected books?`
  if (!confirm(label)) return
  try {
    const r = await api<{ deleted: number; hard: boolean }>('/api/books/bulk_delete', {
      method: 'POST',
      body: JSON.stringify({ ids, hard: opts.hard }),
    })
    toast.success(`${r.deleted} ${opts.hard ? 'removed' : 'deleted'}`)
    selected.value = new Set()
    await fetchBooks()
  } catch (e: any) { toast.error('Bulk delete failed', String(e?.message ?? e)) }
}

async function bulkDeleteByStatus(opts: { hard: boolean }) {
  if (!statusFilter.value) {
    toast.error('Pick a status filter first', 'Set a status to scope the delete')
    return
  }
  const verb = opts.hard ? 'hard-remove' : 'delete'
  if (!confirm(`${verb.toUpperCase()} every book with status="${statusFilter.value}"?`)) return
  try {
    const r = await api<{ deleted: number }>('/api/books/bulk_delete', {
      method: 'POST',
      body: JSON.stringify({ status: statusFilter.value, hard: opts.hard }),
    })
    toast.success(`${r.deleted} ${opts.hard ? 'removed' : 'deleted'}`)
    await fetchBooks()
  } catch (e: any) { toast.error('Bulk delete failed', String(e?.message ?? e)) }
}
</script>

<template>
  <div class="p-6 space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Queue</h1>
      <Badge variant="muted">{{ total }} books</Badge>
      <div class="flex-1"></div>
      <Button variant="subtle" size="sm" @click="addOpen = true">
        <Plus class="w-4 h-4" /> Add book
      </Button>
    </div>

    <div class="flex gap-2 items-center">
      <div class="relative flex-1 max-w-md">
        <Search class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="query" placeholder="Search title or author" class="pl-9" />
      </div>
      <select v-model="statusFilter"
        class="h-9 px-3 rounded-md border border-input bg-background text-sm">
        <option value="">All statuses</option>
        <option v-for="s in statuses" :key="s">{{ s }}</option>
      </select>
      <Button
        v-if="statusFilter"
        size="sm" variant="outline"
        @click="bulkDeleteByStatus({ hard: false })">
        <Trash2 class="w-3.5 h-3.5" /> Clear {{ statusFilter }}
      </Button>
      <Button
        v-if="statusFilter"
        size="sm" variant="ghost"
        class="text-destructive"
        @click="bulkDeleteByStatus({ hard: true })">
        Hard
      </Button>
    </div>

    <div v-if="someSelected" class="flex items-center gap-3 px-4 py-2 rounded-md bg-accent/50 border border-border">
      <span class="text-sm font-medium">{{ selected.size }} selected</span>
      <div class="flex-1"></div>
      <Button size="sm" variant="outline" @click="bulkDelete({ hard: false })">
        <Trash2 class="w-3.5 h-3.5" /> Delete selected
      </Button>
      <Button size="sm" variant="ghost" class="text-destructive" @click="bulkDelete({ hard: true })">
        Hard delete
      </Button>
      <Button size="sm" variant="ghost" @click="selected = new Set()">Clear</Button>
    </div>

    <!-- Desktop table (md+) -->
    <Card class="overflow-hidden hidden md:block">
      <table class="w-full text-sm">
        <thead class="text-xs text-muted-foreground border-b border-border">
          <tr class="text-left">
            <th class="px-4 py-3 w-8">
              <input type="checkbox" :checked="allVisibleSelected" @change="toggleAllVisible"
                     class="rounded border-input" />
            </th>
            <th class="px-4 py-3">Title</th>
            <th class="px-4 py-3">Author</th>
            <th class="px-4 py-3">Source</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3">Format</th>
            <th class="px-4 py-3">Attempts</th>
            <th class="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in books" :key="b.id"
              :class="['border-b border-border/60 last:border-0 hover:bg-accent/40 cursor-pointer',
                       selected.has(b.id) ? 'bg-accent/30' : '']"
              @click="detailId = b.id">
            <td class="px-4 py-3" @click.stop>
              <input type="checkbox" :checked="selected.has(b.id)" @change="toggleOne(b.id)"
                     class="rounded border-input" />
            </td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <BookOpen class="w-4 h-4 text-muted-foreground" />
                <span class="font-medium">{{ b.title }}</span>
              </div>
            </td>
            <td class="px-4 py-3 text-muted-foreground">{{ b.author ?? '—' }}</td>
            <td class="px-4 py-3 text-muted-foreground text-xs">{{ b.source }}</td>
            <td class="px-4 py-3"><StatusPill :status="b.status" /></td>
            <td class="px-4 py-3 text-muted-foreground">{{ b.format ?? '—' }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ b.attempts }}</td>
            <td class="px-4 py-3 text-right space-x-1" @click.stop>
              <Button size="sm" variant="outline" @click="retry(b)">Retry</Button>
              <Button size="sm" variant="ghost" @click="del(b)">Delete</Button>
            </td>
          </tr>
          <tr v-if="!books.length">
            <td colspan="8" class="px-4 py-12 text-center text-muted-foreground">
              <BookOpen class="w-8 h-8 mx-auto mb-3 opacity-50" />
              Nothing in the queue yet. Add a book or configure a source.
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <!-- Mobile card list (<md) -->
    <div class="md:hidden space-y-2">
      <article
        v-for="b in books"
        :key="b.id"
        :class="[
          'bg-card border border-border rounded-lg p-3 active:bg-accent/40',
          selected.has(b.id) ? 'ring-2 ring-primary' : '',
        ]"
        @click="detailId = b.id"
      >
        <div class="flex items-start gap-3">
          <input
            type="checkbox"
            class="w-5 h-5 mt-1 rounded border-input shrink-0"
            :checked="selected.has(b.id)"
            @click.stop
            @change="toggleOne(b.id)"
          />
          <div class="flex-1 min-w-0">
            <h3 class="font-medium text-sm break-words">{{ b.title }}</h3>
            <p class="text-xs text-muted-foreground break-words">{{ b.author ?? '—' }}</p>
            <div class="flex flex-wrap items-center gap-1.5 mt-1.5">
              <StatusPill :status="b.status" />
              <span class="text-[10px] text-muted-foreground uppercase tracking-wide">
                {{ b.source }}
              </span>
              <span v-if="b.format" class="text-[10px] text-muted-foreground">
                {{ b.format }}
              </span>
              <span v-if="b.attempts > 0" class="text-[10px] text-muted-foreground">
                {{ b.attempts }} attempt{{ b.attempts > 1 ? 's' : '' }}
              </span>
            </div>
          </div>
        </div>
        <div class="flex gap-2 mt-2.5" @click.stop>
          <Button size="sm" variant="outline" class="flex-1" @click="retry(b)">Retry</Button>
          <Button size="sm" variant="ghost" class="text-destructive" @click="del(b)">Delete</Button>
        </div>
      </article>
      <p v-if="!books.length" class="text-center text-sm text-muted-foreground py-8">
        Nothing in the queue yet. Add a book or configure a source.
      </p>
    </div>

    <Drawer :open="addOpen" title="Add a book manually" @close="addOpen = false">
      <div class="space-y-3">
        <div>
          <label class="text-xs text-muted-foreground">Title</label>
          <Input v-model="addTitle" placeholder="The Pragmatic Programmer" />
        </div>
        <div>
          <label class="text-xs text-muted-foreground">Author (optional)</label>
          <Input v-model="addAuthor" placeholder="Hunt, Thomas" />
        </div>
        <div>
          <label class="text-xs text-muted-foreground">ISBN-13 (optional, dramatically improves match accuracy)</label>
          <Input v-model="addIsbn" placeholder="9780135957059" />
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <Button variant="ghost" @click="addOpen = false">Cancel</Button>
          <Button @click="add">Add</Button>
        </div>
      </div>
    </Drawer>

    <BookDetailDrawer v-if="detailId !== null" :id="detailId" @close="detailId = null; fetchBooks()" />
  </div>
</template>

<script lang="ts">
import BookDetailDrawer from '@/components/BookDetailDrawer.vue'
export default { components: { BookDetailDrawer } }
</script>
