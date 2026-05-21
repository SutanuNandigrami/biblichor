<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Sliders, RotateCcw, Save, Eye } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

type Weights = {
  isbn_match: number
  title_weight: number
  author_weight: number
  format_bonus: Record<string, number>
  language_bonus: number
  filesize_min_bytes: number
  filesize_max_bytes: number
  scan_penalty: number
  audio_keywords: string[]
}

type Candidate = {
  id: number; md5: string | null; title: string | null
  format: string | null; filesize_bytes: number | null
  language: string | null; isbn_match: boolean
  score: number; components: Record<string, number>
  is_hard_skip: boolean; skip_reason: string | null
}

type Book = {
  id: number; title: string; author: string | null; status: string
}

const weights = ref<Weights | null>(null)
const saved = ref<Weights | null>(null)
const previewBookId = ref<number | null>(null)
const previewCandidates = ref<Candidate[]>([])
const previewBook = ref<{ id: number; title: string; isbn13: string | null; author: string | null } | null>(null)
const allBooks = ref<Book[]>([])
const saving = ref(false)
const previewing = ref(false)
const toast = useToast()

const COMPONENT_LABELS: Record<string, string> = {
  isbn_match: 'ISBN match',
  title_similarity: 'Title similarity',
  author_similarity: 'Author similarity',
  format_bonus: 'Format bonus',
  language_bonus: 'Language bonus',
  filesize_penalty: 'Filesize',
  scan_penalty: 'Scan penalty',
}

async function loadWeights() {
  const w = await api<Weights>('/api/scoring')
  weights.value = JSON.parse(JSON.stringify(w))
  saved.value = JSON.parse(JSON.stringify(w))
}

async function loadBooks() {
  const r = await api<{ books: Book[] }>('/api/books?limit=200')
  // Prefer books that have candidates (anything past 'queued')
  allBooks.value = r.books.filter((b) => b.status !== 'queued')
  if (!previewBookId.value && allBooks.value.length) {
    previewBookId.value = allBooks.value[0].id
  }
}

onMounted(async () => {
  await Promise.all([loadWeights(), loadBooks()])
  refreshPreview()
})

async function refreshPreview() {
  if (!previewBookId.value || !weights.value) return
  previewing.value = true
  try {
    const r = await api<{ book: any; candidates: Candidate[]; weights: any }>(
      '/api/scoring/preview',
      { method: 'POST', body: JSON.stringify({
        book_id: previewBookId.value, weights: weights.value,
      })},
    )
    previewBook.value = r.book
    previewCandidates.value = r.candidates
  } catch (e: any) { toast.error('Preview failed', String(e?.message ?? e)) }
  finally { previewing.value = false }
}

watch(weights, () => { refreshPreview() }, { deep: true })

async function saveWeights() {
  if (!weights.value) return
  saving.value = true
  try {
    await api('/api/scoring', { method: 'POST', body: JSON.stringify(weights.value) })
    saved.value = JSON.parse(JSON.stringify(weights.value))
    toast.success('Scoring weights saved')
  } catch (e: any) { toast.error('Save failed', String(e?.message ?? e)) }
  finally { saving.value = false }
}

async function resetDefaults() {
  if (!confirm('Reset all weights to shipped defaults?')) return
  try {
    const r = await api<{ scoring: Weights }>('/api/scoring/reset', { method: 'POST' })
    weights.value = JSON.parse(JSON.stringify(r.scoring))
    saved.value = JSON.parse(JSON.stringify(r.scoring))
    toast.success('Reset to defaults')
    refreshPreview()
  } catch (e: any) { toast.error('Reset failed', String(e?.message ?? e)) }
}

const dirty = () => weights.value && saved.value
  && JSON.stringify(weights.value) !== JSON.stringify(saved.value)

function size(b: number | null) {
  if (!b) return '—'
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="p-6 space-y-4" v-if="weights">
    <div class="flex items-center gap-3">
      <Sliders class="w-5 h-5 text-primary" />
      <h1 class="text-2xl font-semibold tracking-tight">Scoring</h1>
      <Badge v-if="dirty()" variant="warning">unsaved</Badge>
      <div class="flex-1"></div>
      <Button variant="ghost" @click="resetDefaults"><RotateCcw class="w-4 h-4" /> Reset</Button>
      <Button :loading="saving" :disabled="!dirty()" @click="saveWeights"><Save class="w-4 h-4" /> Save</Button>
    </div>
    <p class="text-sm text-muted-foreground">
      Adjust how candidates are ranked. Sliders update the preview below instantly — no commit
      until you hit Save. Maximum score is ~100; auto-pick threshold lives in Settings.
    </p>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card class="p-5 lg:col-span-2">
        <h2 class="text-sm font-semibold mb-4">Weights</h2>
        <div class="space-y-5">
          <div v-for="key in ['isbn_match', 'title_weight', 'author_weight', 'language_bonus', 'scan_penalty']" :key="key">
            <div class="flex justify-between items-center mb-1">
              <label class="text-sm">{{ COMPONENT_LABELS[key] || key }}</label>
              <span class="font-mono text-sm">{{ (weights as any)[key] }}</span>
            </div>
            <input type="range" min="0" max="50" step="1"
              v-model.number="(weights as any)[key]"
              class="w-full accent-primary" />
          </div>

          <div>
            <label class="text-sm font-semibold mb-2 block">Format bonus</label>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div v-for="fmt in ['epub', 'azw3', 'mobi', 'pdf']" :key="fmt">
                <div class="flex justify-between text-xs">
                  <span class="font-mono">{{ fmt }}</span>
                  <span class="font-mono">{{ weights.format_bonus[fmt] ?? 0 }}</span>
                </div>
                <input type="range" min="0" max="15" step="1"
                  v-model.number="weights.format_bonus[fmt]"
                  class="w-full accent-primary" />
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label class="text-xs text-muted-foreground">filesize_min_bytes</label>
              <Input v-model.number="weights.filesize_min_bytes" type="number" />
              <p class="text-xs text-muted-foreground mt-1">~200 KB recommended</p>
            </div>
            <div>
              <label class="text-xs text-muted-foreground">filesize_max_bytes</label>
              <Input v-model.number="weights.filesize_max_bytes" type="number" />
              <p class="text-xs text-muted-foreground mt-1">~80 MB recommended (Amazon cap)</p>
            </div>
          </div>

          <div>
            <label class="text-xs text-muted-foreground">Audio hard-skip keywords (comma-separated, score=0)</label>
            <Input
              :model-value="weights.audio_keywords.join(', ')"
              @update:model-value="weights.audio_keywords = String($event).split(',').map((s) => s.trim()).filter(Boolean)"
            />
          </div>
        </div>
      </Card>

      <Card class="p-5">
        <div class="flex items-center gap-2 mb-3">
          <Eye class="w-4 h-4 text-primary" />
          <h2 class="text-sm font-semibold">Live preview</h2>
        </div>
        <p class="text-xs text-muted-foreground mb-3">
          Pick a book that has candidates already scored. Sliders re-rank instantly.
        </p>
        <select v-model.number="previewBookId" @change="refreshPreview"
          class="w-full h-9 px-3 rounded-md border border-input bg-background text-sm mb-3">
          <option v-for="b in allBooks" :key="b.id" :value="b.id">#{{ b.id }} {{ b.title }}</option>
          <option v-if="!allBooks.length" :value="null">No scored books yet</option>
        </select>

        <div v-if="previewBook" class="mb-3 text-xs text-muted-foreground">
          <div>{{ previewBook.title }}</div>
          <div>author: {{ previewBook.author ?? '—' }} · isbn: {{ previewBook.isbn13 ?? '—' }}</div>
        </div>

        <ul class="space-y-2">
          <li v-for="(c, idx) in previewCandidates" :key="c.id"
              class="border border-border rounded p-2 text-xs"
              :class="{ 'border-emerald-500/50': idx === 0 && !c.is_hard_skip }">
            <div class="flex justify-between items-center">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-muted-foreground font-mono">#{{ idx + 1 }}</span>
                <span class="font-mono">{{ c.score.toFixed(1) }}</span>
                <span class="truncate">{{ c.title ?? c.md5 }}</span>
              </div>
              <Badge v-if="c.is_hard_skip" variant="danger">skip: {{ c.skip_reason }}</Badge>
              <Badge v-else-if="c.isbn_match" variant="success">ISBN ✓</Badge>
              <Badge v-else variant="muted">{{ c.format ?? '?' }}</Badge>
            </div>
            <div class="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
              <span v-for="(v, k) in c.components" :key="k" v-show="v !== 0">
                {{ COMPONENT_LABELS[k] || k }}: <span :class="v > 0 ? 'text-emerald-300' : 'text-red-300'">{{ v > 0 ? '+' : '' }}{{ v.toFixed(1) }}</span>
              </span>
              <span class="font-mono">size: {{ size(c.filesize_bytes) }}</span>
            </div>
          </li>
          <li v-if="previewing" class="text-xs text-muted-foreground italic">scoring…</li>
          <li v-if="!previewCandidates.length && !previewing" class="text-xs text-muted-foreground italic">
            No candidates for this book yet. Run the pipeline on it first.
          </li>
        </ul>
      </Card>
    </div>
  </div>
</template>
