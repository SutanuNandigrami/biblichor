<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
type ScoreBreakdown = {
  components?: Record<string, number>
  is_hard_skip?: boolean
  skip_reason?: string | null
  error?: string
}
type Candidate = {
  id: number; provider: string; md5: string | null; title: string | null
  format: string | null; filesize_bytes: number | null; language: string | null
  score: number | null; year: number | null; edition_hints: string | null
  mirror: string | null; detail_url: string | null
  score_breakdown?: ScoreBreakdown
}
type Event = {
  id: number; ts: string; kind: string; scraper: string | null; message: string
  meta: Record<string, any>
}

const book = ref<Book | null>(null)
const candidates = ref<Candidate[]>([])
const events = ref<Event[]>([])
const expandedRow = ref<number | null>(null)
function toggleRow(id: number) {
  expandedRow.value = expandedRow.value === id ? null : id
}
function fmtComponent(name: string, value: number): { label: string; weight: number } {
  // Display names mapping for the breakdown rows
  const labels: Record<string, string> = {
    isbn_match: 'ISBN13 match',
    isbn13_matched: 'ISBN13 matched (flag)',
    title_similarity: 'Title similarity (weighted)',
    title_similarity_raw: 'Title similarity (raw 0-1)',
    author_similarity: 'Author similarity',
    format_bonus: 'Format bonus',
    language_bonus: 'Language bonus',
    filesize_penalty: 'Filesize penalty',
    filesize_bonus: 'Filesize bonus',
    scan_penalty: 'Scan/OCR penalty',
    audio_penalty: 'Audio penalty',
    derivative_penalty: 'Derivative-content penalty',
  }
  return { label: labels[name] ?? name, weight: value }
}
const scrapeTrace = computed(() =>
  events.value
    .filter((e) => e.scraper && (e.kind === 'scrape' || e.kind === 'error'))
    .slice(0, 15)
)

async function load() {
  const r = await api<{ book: Book; candidates: Candidate[]; events: Event[] }>(`/api/books/${props.id}`)
  book.value = r.book
  candidates.value = r.candidates
  events.value = r.events
}
onMounted(load)
watch(() => props.id, load)

async function retryDownload() {
  const r: any = await api(`/api/books/${props.id}/retry-download`, { method: 'POST' })
  toast.success(r?.mode === 'redownload' ? 'Retrying download (same pick)' : 'Re-queued')
  await load()
}
async function research() {
  if (!confirm('Re-search will clear the current picked candidate and search again from scratch. Continue?')) return
  await api(`/api/books/${props.id}/retry`, { method: 'POST' })
  toast.success('Re-queued (full re-search)')
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
          <Button variant="subtle" size="sm" @click="retryDownload">Retry download</Button>
          <Button variant="ghost" size="sm" @click="research" title="Clear pick and search again from scratch">Re-search</Button>
          <Button variant="ghost" size="sm" @click="del">Delete</Button>
        </div>
      </section>

      <section>
        <h3 class="text-sm font-semibold mb-2">Search trace</h3>
        <p class="text-xs text-muted-foreground mb-2">
          Scrapers try in order; the first one to return candidates wins. Within a scraper,
          mirror rotation is failover-only (other mirrors only get tried if the first fails).
        </p>
        <Card class="overflow-hidden mb-4 hidden md:block">
          <table class="w-full text-xs">
            <tbody>
              <tr v-for="ev in scrapeTrace" :key="ev.id" class="border-t border-border/40">
                <td class="px-3 py-2 text-muted-foreground w-20">{{ ev.ts.slice(11,19) }}</td>
                <td class="px-3 py-2 font-mono">{{ ev.scraper ?? '—' }}</td>
                <td class="px-3 py-2">{{ ev.message }}</td>
              </tr>
              <tr v-if="!scrapeTrace.length">
                <td class="px-3 py-2 text-muted-foreground" colspan="3">No scraper events yet.</td>
              </tr>
            </tbody>
          </table>
        </Card>
        <div class="md:hidden space-y-1.5 mb-4">
          <article v-for="ev in scrapeTrace" :key="ev.id"
                   class="bg-card border border-border rounded-lg p-2.5 text-xs">
            <div class="flex items-center gap-2 mb-0.5">
              <span class="font-mono text-[10px] text-muted-foreground">{{ ev.ts.slice(11,19) }}</span>
              <Badge variant="info">{{ ev.scraper ?? '—' }}</Badge>
            </div>
            <p class="break-words">{{ ev.message }}</p>
          </article>
          <p v-if="!scrapeTrace.length" class="text-xs text-muted-foreground px-1">No scraper events yet.</p>
        </div>
        <h3 class="text-sm font-semibold mb-2">Candidates</h3>
        <Card v-if="candidates.length" class="overflow-hidden hidden md:block">
          <table class="w-full text-xs">
            <thead class="text-muted-foreground">
              <tr class="text-left">
                <th class="px-3 py-2">Score</th>
                <th class="px-3 py-2">Source</th>
                <th class="px-3 py-2">Title</th>
                <th class="px-3 py-2">Fmt</th>
                <th class="px-3 py-2">Size</th>
                <th class="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="c in candidates" :key="c.id">
                <tr class="border-t border-border/40 hover:bg-accent/30 cursor-pointer"
                    @click="toggleRow(c.id)">
                  <td class="px-3 py-2 font-mono">
                    <span class="mr-1 text-muted-foreground">{{ expandedRow === c.id ? '▾' : '▸' }}</span>
                    {{ c.score?.toFixed(1) ?? '—' }}
                  </td>
                  <td class="px-3 py-2 text-xs">
                    <div class="font-medium">{{ c.provider }}</div>
                    <a v-if="c.detail_url" :href="c.detail_url" target="_blank" rel="noopener" @click.stop
                       class="text-muted-foreground hover:text-primary underline-offset-2 hover:underline truncate inline-block max-w-[14ch]">{{ c.mirror ?? c.detail_url }}</a>
                    <span v-else class="text-muted-foreground">{{ c.mirror ?? '—' }}</span>
                  </td>
                  <td class="px-3 py-2 truncate max-w-xs">{{ c.title ?? c.md5 }}</td>
                  <td class="px-3 py-2 text-muted-foreground">{{ c.format ?? '—' }}</td>
                  <td class="px-3 py-2 text-muted-foreground">{{ size(c.filesize_bytes) }}</td>
                  <td class="px-3 py-2 text-right" @click.stop>
                    <Button size="sm" variant="outline" @click="pick(c)">Pick</Button>
                  </td>
                </tr>
                <tr v-if="expandedRow === c.id" class="bg-muted/40 border-t border-border/40">
                  <td :colspan="6" class="px-4 py-3">
                    <div class="text-xs text-muted-foreground mb-2">Score breakdown</div>
                    <div v-if="c.score_breakdown?.error" class="text-xs text-destructive font-mono">
                      {{ c.score_breakdown.error }}
                    </div>
                    <div v-else-if="c.score_breakdown?.is_hard_skip" class="text-xs">
                      <Badge variant="danger">Hard-skipped</Badge>
                      <span class="ml-2 text-muted-foreground">{{ c.score_breakdown.skip_reason }}</span>
                    </div>
                    <table v-else-if="c.score_breakdown?.components" class="w-full text-xs">
                      <tbody>
                        <tr v-for="(value, key) in c.score_breakdown.components" :key="key"
                            class="border-b border-border/20 last:border-0">
                          <td class="py-1 pr-3 text-muted-foreground">{{ fmtComponent(String(key), value).label }}</td>
                          <td class="py-1 text-right font-mono"
                              :class="value < 0 ? 'text-destructive' : value > 0 ? 'text-emerald-500' : 'text-muted-foreground'">
                            {{ value >= 0 ? '+' : '' }}{{ value.toFixed(2) }}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="text-xs text-muted-foreground">No breakdown available.</div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </Card>
        <div v-if="candidates.length" class="md:hidden space-y-2">
          <article v-for="c in candidates" :key="c.id"
                   class="bg-card border border-border rounded-lg p-3 active:bg-accent/40">
            <button class="w-full text-left" @click="toggleRow(c.id)">
              <div class="flex items-start gap-2 mb-1.5">
                <div class="font-mono text-sm font-semibold tabular-nums">
                  <span class="mr-0.5 text-muted-foreground">{{ expandedRow === c.id ? '▾' : '▸' }}</span>
                  {{ c.score?.toFixed(1) ?? '—' }}
                </div>
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-medium break-words">{{ c.title ?? c.md5 }}</p>
                  <p class="text-[11px] text-muted-foreground font-medium">{{ c.provider }}</p>
                  <a v-if="c.detail_url" :href="c.detail_url" target="_blank" rel="noopener" @click.stop
                     class="text-[11px] text-muted-foreground hover:text-primary underline-offset-2 hover:underline break-all">{{ c.mirror ?? c.detail_url }}</a>
                  <span v-else-if="c.mirror" class="text-[11px] text-muted-foreground break-all">{{ c.mirror }}</span>
                </div>
              </div>
              <div class="flex flex-wrap items-center gap-1.5">
                <Badge v-if="c.format" variant="muted">{{ c.format }}</Badge>
                <Badge variant="muted">{{ size(c.filesize_bytes) }}</Badge>
              </div>
            </button>
            <div class="flex justify-end mt-2">
              <Button size="sm" variant="outline" @click.stop="pick(c)">Pick</Button>
            </div>
            <div v-if="expandedRow === c.id" class="mt-3 pt-3 border-t border-border/60">
              <div class="text-xs text-muted-foreground mb-2">Score breakdown</div>
              <div v-if="c.score_breakdown?.error" class="text-xs text-destructive font-mono break-words">
                {{ c.score_breakdown.error }}
              </div>
              <div v-else-if="c.score_breakdown?.is_hard_skip" class="text-xs">
                <Badge variant="danger">Hard-skipped</Badge>
                <span class="ml-2 text-muted-foreground break-words">{{ c.score_breakdown.skip_reason }}</span>
              </div>
              <ul v-else-if="c.score_breakdown?.components" class="text-xs space-y-1">
                <li v-for="(value, key) in c.score_breakdown.components" :key="key"
                    class="flex justify-between gap-3">
                  <span class="text-muted-foreground">{{ fmtComponent(String(key), value).label }}</span>
                  <span class="font-mono"
                        :class="value < 0 ? 'text-destructive' : value > 0 ? 'text-emerald-500' : 'text-muted-foreground'">
                    {{ value >= 0 ? '+' : '' }}{{ value.toFixed(2) }}
                  </span>
                </li>
              </ul>
              <div v-else class="text-xs text-muted-foreground">No breakdown available.</div>
            </div>
          </article>
        </div>
        <p v-if="!candidates.length" class="text-xs text-muted-foreground">No candidates yet. Try retrying.</p>
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
