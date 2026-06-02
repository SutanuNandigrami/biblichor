<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { Search, Loader2, Plus, CheckCircle2, AlertCircle, BookOpen, ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { api, ApiError } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'

type InLib = { id: number; status: string } | null

type SearchResponse = {
  query: string
  lang: string
  count: number
  results: Result[]
  sources_used: string[]
  sources_skipped: { name: string; reason: string }[]
  page?: number
  has_next?: boolean
}

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

// Minimum query length before we hit the API. One- and two-letter queries
// match millions of Anna's results, take ~10s, and aren't actually useful
// for finding a book. Three chars is the sweet spot where most queries
// narrow enough to be fast (~1s).
const MIN_QUERY_LEN = 3
// Debounce window. Longer than typing-pause but short enough to feel live.
const DEBOUNCE_MS = 500
// Cache last N (query, lang, page) tuples in memory so paging back and
// re-running the same query is instant. Keyed by `${lang}|${q}|p${page}`.
const CACHE_LIMIT = 60
// Pagination: how many results per page, and max page depth.
const PAGE_SIZE = 25
const MAX_PAGE = 10

const query = ref('')
const lang = ref<string>('all')  // 'all' = use cfg default (no override)
const sourcesUsed = ref<string[]>([])
const sourcesSkipped = ref<{ name: string; reason: string }[]>([])
const LANG_OPTIONS = [
  { value: 'all', label: 'All languages' },
  { value: 'en', label: 'English' },
  { value: 'bn', label: 'Bengali' },
  { value: 'hi', label: 'Hindi' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
] as const
const results = ref<Result[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const lastQuery = ref('')
const queuing = ref<Set<string>>(new Set())
const toast = useToast()
// Pagination state.
const currentPage = ref(1)
// Per-(q,lang,page) record of whether the server said "more pages exist".
// Used to disable Next at the depth boundary even if cache replay is showing.
const hasNextByKey = new Map<string, boolean>()
const canGoPrev = computed(() => currentPage.value > 1)
const canGoNext = computed(() => {
  if (currentPage.value >= MAX_PAGE) return false
  const k = `${lang.value}|${query.value.trim().toLowerCase()}|p${currentPage.value}`
  return hasNextByKey.get(k) === true
})

// AbortController for the in-flight request. When the user types another
// letter, we cancel the previous fetch so its (now-stale) response can't
// land after a newer one and overwrite the UI. This is the single biggest
// fix to the "letter-by-letter lag" the user reported.
let inflightCtrl: AbortController | null = null

// In-memory cache. Keyed by normalized query string. Insertion-ordered Map
// lets us drop the oldest entry when we exceed CACHE_LIMIT.
const cache = new Map<string, Result[]>()

let debounceTimer: number | null = null
// Also re-run when lang changes (fires immediately, no debounce — the
// user explicitly picked the language). Reset page to 1.
watch(lang, () => {
  currentPage.value = 1
  const q = query.value.trim()
  if (q.length >= MIN_QUERY_LEN) runSearch(q)
})
watch(query, (v) => {
  if (debounceTimer) window.clearTimeout(debounceTimer)

  // Any query edit resets paging.
  currentPage.value = 1

  const q = v.trim()

  // Empty input: clear results, cancel any in-flight, reset.
  if (!q) {
    if (inflightCtrl) inflightCtrl.abort()
    inflightCtrl = null
    results.value = []
    error.value = null
    lastQuery.value = ''
    loading.value = false
    return
  }

  // Under the minimum: don't even queue a fetch. Show a hint instead.
  if (q.length < MIN_QUERY_LEN) {
    if (inflightCtrl) inflightCtrl.abort()
    inflightCtrl = null
    results.value = []
    error.value = null
    lastQuery.value = ''
    loading.value = false
    return
  }

  // Cache hit (page 1 of this q+lang): render instantly, no network.
  const key = `${lang.value}|${q.toLowerCase()}|p1`
  const cached = cache.get(key)
  if (cached) {
    if (inflightCtrl) inflightCtrl.abort()
    inflightCtrl = null
    results.value = cached
    lastQuery.value = q
    error.value = null
    loading.value = false
    return
  }

  debounceTimer = window.setTimeout(() => {
    runSearch(q)
  }, DEBOUNCE_MS)
})

async function runSearch(q: string, page: number = currentPage.value) {
  // Cancel any earlier in-flight request. Critical for keystroke-by-keystroke
  // typing: without this, a slow request for "ed" returning AFTER "eddie"
  // would clobber the eddie results with two-letter junk.
  if (inflightCtrl) inflightCtrl.abort()
  const ctrl = new AbortController()
  inflightCtrl = ctrl

  loading.value = true
  error.value = null
  try {
    const r = await api<SearchResponse>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${PAGE_SIZE}&page=${page}` + (lang.value && lang.value !== 'all' ? `&lang=${encodeURIComponent(lang.value)}` : ''),
      { signal: ctrl.signal },
    )
    // Ignore if a newer request started while we were waiting.
    if (ctrl.signal.aborted) return
    results.value = r.results
    lastQuery.value = r.query
    sourcesUsed.value = r.sources_used || []
    sourcesSkipped.value = r.sources_skipped || []
    // Cache by (lang, query, page). LRU-ish eviction.
    const key = `${lang.value}|${q.toLowerCase()}|p${page}`
    cache.set(key, r.results)
    hasNextByKey.set(key, Boolean(r.has_next))
    if (cache.size > CACHE_LIMIT) {
      const oldest = cache.keys().next().value
      if (oldest !== undefined) {
        cache.delete(oldest)
        hasNextByKey.delete(oldest)
      }
    }
  } catch (e: unknown) {
    // Aborted requests are not errors — we cancelled them on purpose.
    if (e instanceof DOMException && e.name === 'AbortError') return
    const isAbort = (e as { name?: string } | null)?.name === 'AbortError'
    if (isAbort) return
    const msg = e instanceof ApiError ? e.message : String(e)
    if (ctrl.signal.aborted) return
    error.value = msg
    results.value = []
  } finally {
    if (inflightCtrl === ctrl) {
      loading.value = false
      inflightCtrl = null
    }
  }
}

function goToPage(target: number) {
  if (target < 1 || target > MAX_PAGE) return
  const q = query.value.trim()
  if (q.length < MIN_QUERY_LEN) return
  currentPage.value = target
  const key = `${lang.value}|${q.toLowerCase()}|p${target}`
  const cached = cache.get(key)
  if (cached) {
    // Instant: previously fetched page, render from cache.
    if (inflightCtrl) inflightCtrl.abort()
    inflightCtrl = null
    results.value = cached
    lastQuery.value = q
    error.value = null
    loading.value = false
    return
  }
  // Not cached: fetch this page from the server.
  runSearch(q, target)
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
    const idx = results.value.findIndex((x) => x.md5 === r.md5)
    if (idx >= 0) {
      results.value[idx] = { ...results.value[idx], in_library: { id: resp.book_id, status: resp.status } }
    }
    // Update cache entry too so backtracking to this query shows the new state.
    if (lastQuery.value) {
      const key = `${lang.value}|${lastQuery.value.toLowerCase()}|p${currentPage.value}`
      if (cache.has(key)) cache.set(key, [...results.value])
    }
    if (resp.created) toast.success(`Queued "${r.title}" (book #${resp.book_id})`)
    else toast.info(`Already tracked (book #${resp.book_id}, ${resp.status})`)
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

    <div class="flex gap-2 mb-4 items-stretch">
      <div class="relative flex-1">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <Input
          v-model="query"
          type="search"
          placeholder="title, author, isbn..."
          class="pl-9 h-11 text-base w-full"
          autofocus
        />
        <Loader2 v-if="loading" class="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-muted-foreground" />
      </div>
      <select
        v-model="lang"
        class="h-11 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label="Filter by language"
      >
        <option v-for="opt in LANG_OPTIONS" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <div v-if="error" class="mb-4 p-3 rounded-md border border-destructive/30 bg-destructive/5 text-sm text-destructive flex items-start gap-2">
      <AlertCircle class="w-4 h-4 mt-0.5 shrink-0" />
      <span>{{ error }}</span>
    </div>

    <div
      v-if="query.trim().length > 0 && query.trim().length < MIN_QUERY_LEN"
      class="mb-3 text-sm text-muted-foreground"
    >
      Type {{ MIN_QUERY_LEN - query.trim().length }} more {{ MIN_QUERY_LEN - query.trim().length === 1 ? 'character' : 'characters' }} to search…
    </div>

    <div v-else-if="lastQuery && !loading" class="mb-3 text-sm text-muted-foreground space-y-1">
      <div>
        {{ results.length }} {{ results.length === 1 ? 'result' : 'results' }} for "<span class="text-foreground">{{ lastQuery }}</span>"
      </div>
      <div v-if="sourcesUsed.length" class="text-xs opacity-70">
        searched: <span class="font-medium">{{ sourcesUsed.join(', ') }}</span>
        <template v-if="sourcesSkipped.length">
          · skipped: <span class="font-medium">{{ sourcesSkipped.map(s => s.name).join(', ') }}</span>
        </template>
      </div>
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

    <nav
      v-if="lastQuery && (canGoPrev || canGoNext)"
      class="mt-6 flex items-center justify-center gap-3"
      aria-label="Search pagination"
    >
      <Button
        size="sm"
        variant="outline"
        :disabled="!canGoPrev || loading"
        @click="goToPage(currentPage - 1)"
      >
        <ChevronLeft class="w-3.5 h-3.5 mr-1" />
        Previous
      </Button>
      <span class="text-sm text-muted-foreground tabular-nums">
        Page {{ currentPage }}<span v-if="!canGoNext"> (end)</span>
      </span>
      <Button
        size="sm"
        variant="outline"
        :disabled="!canGoNext || loading"
        @click="goToPage(currentPage + 1)"
      >
        Next
        <ChevronRight class="w-3.5 h-3.5 ml-1" />
      </Button>
    </nav>
  </div>
</template>
