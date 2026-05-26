<script setup lang="ts">
import { ref, watch } from 'vue'
import { Search, Loader2, Plus, CheckCircle2, AlertCircle, BookOpen } from 'lucide-vue-next'
import { api, ApiError } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'

type InLib = { id: number; status: string } | null

type Result = {
  md5: string
  title: string | null
  author: string | null
  language: string | null
  format: string | null
  filesize_bytes: number | null
  year: number | null
  publisher: string | null
  isbn13: string | null
  cover_url: string | null
  detail_url: string
  provider: string
  in_library: InLib
}

const query = ref('')
const results = ref<Result[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const lastQuery = ref('')          // what was actually queried (for the result header)
const queuing = ref<Set<string>>(new Set())   // md5s currently being POSTed
const justQueued = ref<Map<string, number>>(new Map())  // md5 -> book_id (recently added)
const toast = useToast()

// Debounce: 350ms after typing stops, run the search.
let debounceTimer: number | null = null
watch(query, (v) => {
  if (debounceTimer) window.clearTimeout(debounceTimer)
  const q = v.trim()
  if (!q) {
    results.value = []
    error.value = null
    lastQuery.value = ''
    return
  }
  debounceTimer = window.setTimeout(() => {
    runSearch(q)
  }, 350)
})

async function runSearch(q: string) {
  loading.value = true
  error.value = null
  try {
    const r = await api<{ query: string; count: number; results: Result[] }>(
      `/api/search?q=${encodeURIComponent(q)}&limit=30`,
    )
    results.value = r.results
    lastQuery.value = r.query
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : String(e)
    error.value = msg
    results.value = []
  } finally {
    loading.value = false
  }
}

async function addToQueue(r: Result) {
  if (queuing.value.has(r.md5)) return
  queuing.value.add(r.md5)
  try {
    const resp = await api<{ created: boolean; book_id: number; status: string; message?: string }>(
      '/api/books/from-search',
      {
        method: 'POST',
        body: JSON.stringify({
          md5: r.md5,
          title: r.title ?? '',
          author: r.author,
          isbn13: r.isbn13,
          language: r.language,
          format: r.format,
          filesize_bytes: r.filesize_bytes,
          year: r.year,
          publisher: r.publisher,
          detail_url: r.detail_url,
          provider: r.provider,
        }),
      },
    )
    justQueued.value.set(r.md5, resp.book_id)
    // Mutate the result row in place so the UI updates without a re-search
    const idx = results.value.findIndex((x) => x.md5 === r.md5)
    if (idx >= 0) {
      results.value[idx] = { ...results.value[idx], in_library: { id: resp.book_id, status: resp.status } }
    }
    if (resp.created) {
      toast.success(`Queued "${r.title}" (book #${resp.book_id})`)
    } else {
      toast.info(`Already tracked (book #${resp.book_id}, ${resp.status})`)
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : String(e)
    toast.error(`Add failed: ${msg}`)
  } finally {
    queuing.value.delete(r.md5)
  }
}

function fmtSize(b: number | null): string {
  if (!b) return ''
  const mb = b / 1024 / 1024
  if (mb >= 10) return `${mb.toFixed(0)} MB`
  if (mb >= 1) return `${mb.toFixed(1)} MB`
  return `${Math.round(b / 1024)} KB`
}

function statusTone(status: string): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'kindled' || status === 'sent') return 'success'
  if (status === 'queued' || status === 'searching' || status === 'downloading' || status === 'sending') return 'warning'
  if (status === 'failed' || status === 'skipped') return 'danger'
  if (status === 'needs_review') return 'warning'
  return 'default'
}
</script>

<template>
  <div class="max-w-4xl mx-auto p-4 md:p-6">
    <div class="mb-4">
      <h1 class="text-2xl font-semibold tracking-tight mb-1">Search Anna's Archive</h1>
      <p class="text-sm text-muted-foreground">
        Find books and add them to the queue. Already-tracked books are flagged so you don't add them twice.
      </p>
    </div>

    <div class="relative mb-4">
      <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
      <Input
        v-model="query"
        type="search"
        placeholder="title, author, isbn..."
        class="pl-9 h-11 text-base"
        autofocus
      />
      <Loader2 v-if="loading" class="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-muted-foreground" />
    </div>

    <div v-if="error" class="mb-4 p-3 rounded-md border border-destructive/30 bg-destructive/5 text-sm text-destructive flex items-start gap-2">
      <AlertCircle class="w-4 h-4 mt-0.5 shrink-0" />
      <span>{{ error }}</span>
    </div>

    <div v-if="lastQuery && !loading" class="mb-3 text-sm text-muted-foreground">
      {{ results.length }} {{ results.length === 1 ? 'result' : 'results' }} for "<span class="text-foreground">{{ lastQuery }}</span>"
    </div>

    <div v-if="!query.trim() && results.length === 0 && !loading" class="text-center py-16 text-muted-foreground">
      <Search class="w-12 h-12 mx-auto mb-3 opacity-30" />
      <p>Start typing to search</p>
    </div>

    <div v-else class="space-y-3">
      <article
        v-for="r in results"
        :key="r.md5"
        class="flex gap-4 p-4 rounded-lg border border-border bg-card hover:bg-accent/30 transition-colors"
      >
        <!-- Cover -->
        <div class="w-20 md:w-24 shrink-0 aspect-[2/3] rounded overflow-hidden bg-muted flex items-center justify-center">
          <img
            v-if="r.cover_url"
            :src="r.cover_url"
            :alt="r.title ?? ''"
            class="w-full h-full object-cover"
            loading="lazy"
            referrerpolicy="no-referrer"
            @error="($event.target as HTMLImageElement).style.display = 'none'"
          />
          <BookOpen v-else class="w-8 h-8 text-muted-foreground/40" />
        </div>

        <!-- Meta -->
        <div class="flex-1 min-w-0">
          <h3 class="font-medium leading-snug line-clamp-2 mb-1">
            {{ r.title || '(untitled)' }}
          </h3>
          <p v-if="r.author" class="text-sm text-muted-foreground line-clamp-1 mb-2">
            {{ r.author }}
          </p>
          <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span v-if="r.year">{{ r.year }}</span>
            <span v-if="r.format" class="uppercase">{{ r.format }}</span>
            <span v-if="r.filesize_bytes">{{ fmtSize(r.filesize_bytes) }}</span>
            <span v-if="r.language">{{ r.language }}</span>
            <span v-if="r.isbn13" class="font-mono">{{ r.isbn13 }}</span>
            <span class="opacity-60">·</span>
            <span>{{ r.provider }}</span>
          </div>

          <div v-if="r.in_library" class="mt-2 flex items-center gap-2 text-xs">
            <Badge :variant="statusTone(r.in_library.status)">
              <CheckCircle2 v-if="statusTone(r.in_library.status) === 'success'" class="w-3 h-3 mr-1" />
              In library: #{{ r.in_library.id }} ({{ r.in_library.status }})
            </Badge>
          </div>
        </div>

        <!-- Action -->
        <div class="shrink-0 self-start">
          <Button
            v-if="!r.in_library"
            size="sm"
            :disabled="queuing.has(r.md5)"
            @click="addToQueue(r)"
          >
            <Loader2 v-if="queuing.has(r.md5)" class="w-3.5 h-3.5 mr-1.5 animate-spin" />
            <Plus v-else class="w-3.5 h-3.5 mr-1.5" />
            Queue
          </Button>
          <Button v-else size="sm" variant="ghost" disabled>
            <CheckCircle2 class="w-3.5 h-3.5 mr-1.5" />
            Added
          </Button>
        </div>
      </article>
    </div>
  </div>
</template>
