<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Database, Plus, RefreshCw, Trash2, Power } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Drawer from '@/components/ui/Drawer.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

type Source = { id: number; source: string; identifier: string; enabled: boolean; poll_interval_minutes: number; last_polled_at: string | null; token: string | null }
type SourceType = 'goodreads' | 'goodreads_listopia' | 'goodreads_series' | 'hardcover' | 'nyt' | 'storygraph' | 'bookwyrm' | 'wikidata' | 'kindlebangla'

const TYPE_OPTIONS: { value: SourceType; label: string }[] = [
  { value: 'goodreads',          label: 'Goodreads shelf' },
  { value: 'goodreads_listopia', label: 'Listopia' },
  { value: 'goodreads_series',   label: 'Series' },
  { value: 'hardcover',          label: 'Hardcover' },
  { value: 'nyt',                label: 'NYT Best Sellers' },
  { value: 'storygraph',         label: 'StoryGraph' },
  { value: 'bookwyrm',           label: 'BookWyrm (Fediverse)' },
  { value: 'wikidata',           label: 'Wikidata follow author' },
  { value: 'kindlebangla',       label: 'KindleBangla (whole catalog)' },
]

const sources = ref<Source[]>([])
const addOpen = ref(false)
const newType = ref<SourceType>('goodreads')
const newId = ref('')
const newToken = ref('')
const toast = useToast()

async function load() {
  sources.value = (await api<{ sources: Source[] }>('/api/sources')).sources
}
onMounted(load)

async function add() {
  if (!newId.value.trim()) return
  try {
    await api('/api/sources', { method: 'POST', body: JSON.stringify({
      source: newType.value, identifier: newId.value, token: newToken.value || null,
    })})
    addOpen.value = false; newId.value = ''; newToken.value = ''
    await load()
    toast.success('Source added')
  } catch (e: any) { toast.error('Add failed', String(e?.message ?? e)) }
}
async function poll(s: Source) {
  try {
    const r = await api<{ ok: boolean; added: number }>(`/api/sources/${s.id}/poll`, { method: 'POST' })
    toast.success('Polled', `${r.added} new books`)
    await load()
  } catch (e: any) { toast.error('Poll failed', String(e?.message ?? e)) }
}
async function toggle(s: Source) {
  try { await api(`/api/sources/${s.id}/toggle`, { method: 'POST' }); await load() }
  catch (e: any) { toast.error('Toggle failed', String(e?.message ?? e)) }
}
async function del(s: Source) {
  if (!confirm('Delete this source?')) return
  try { await api(`/api/sources/${s.id}/delete`, { method: 'POST' }); await load(); toast.success('Deleted') }
  catch (e: any) { toast.error('Delete failed', String(e?.message ?? e)) }
}
</script>

<template>
  <div class="p-6 space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Sources</h1>
      <Badge variant="muted">{{ sources.length }}</Badge>
      <div class="flex-1"></div>
      <Button variant="subtle" size="sm" @click="addOpen = true">
        <Plus class="w-4 h-4" /> Add source
      </Button>
    </div>

    <!-- Desktop table (md+) -->
    <Card v-if="sources.length" class="overflow-hidden hidden md:block">
      <table class="w-full text-sm">
        <thead class="text-xs text-muted-foreground border-b border-border">
          <tr class="text-left">
            <th class="px-4 py-3">Type</th>
            <th class="px-4 py-3">Identifier</th>
            <th class="px-4 py-3">Enabled</th>
            <th class="px-4 py-3">Last poll</th>
            <th class="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sources" :key="s.id" class="border-b border-border/60 last:border-0">
            <td class="px-4 py-3"><Badge variant="info">{{ s.source }}</Badge></td>
            <td class="px-4 py-3 font-mono text-xs">{{ s.identifier }}</td>
            <td class="px-4 py-3">
              <Badge :variant="s.enabled ? 'success' : 'muted'">{{ s.enabled ? 'on' : 'off' }}</Badge>
            </td>
            <td class="px-4 py-3 text-muted-foreground text-xs">{{ s.last_polled_at ?? 'never' }}</td>
            <td class="px-4 py-3 text-right space-x-1">
              <Button size="sm" variant="outline" @click="poll(s)"><RefreshCw class="w-3.5 h-3.5" /> Poll</Button>
              <Button size="sm" variant="ghost" @click="toggle(s)"><Power class="w-3.5 h-3.5" /></Button>
              <Button size="sm" variant="ghost" @click="del(s)"><Trash2 class="w-3.5 h-3.5" /></Button>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <!-- Mobile card list (<md) -->
    <div v-if="sources.length" class="md:hidden space-y-2">
      <article
        v-for="src in sources"
        :key="src.id"
        class="bg-card border border-border rounded-lg p-3 space-y-2"
      >
        <div class="flex items-center gap-2 flex-wrap">
          <Badge variant="info">{{ src.source }}</Badge>
          <Badge :variant="src.enabled ? 'success' : 'muted'">{{ src.enabled ? 'on' : 'off' }}</Badge>
        </div>
        <p class="font-mono text-xs break-all">{{ src.identifier }}</p>
        <p class="text-[11px] text-muted-foreground">
          Last poll: {{ src.last_polled_at ?? 'never' }} | every {{ src.poll_interval_minutes }} min
        </p>
        <div class="flex gap-2 pt-1">
          <Button size="sm" variant="outline" class="flex-1" @click="poll(src)">
            <RefreshCw class="w-3.5 h-3.5 mr-1" /> Poll
          </Button>
          <Button size="sm" variant="ghost" @click="toggle(src)"><Power class="w-3.5 h-3.5" /></Button>
          <Button size="sm" variant="ghost" class="text-destructive" @click="del(src)">
            <Trash2 class="w-3.5 h-3.5" />
          </Button>
        </div>
      </article>
    </div>

    <Card v-if="!sources.length" class="p-10 text-center">
      <Database class="w-8 h-8 mx-auto mb-3 opacity-50" />
      <p class="text-muted-foreground">No sources configured. Add a Goodreads shelf, Listopia list, series, or Hardcover list.</p>
    </Card>

    <Drawer :open="addOpen" title="Add a source" @close="addOpen = false">
      <div class="space-y-4">
        <div>
          <label class="text-xs text-muted-foreground">Type</label>
          <div class="flex flex-wrap gap-2 mt-1">
            <Button
              v-for="opt in TYPE_OPTIONS" :key="opt.value"
              :variant="newType === opt.value ? 'default' : 'outline'"
              size="sm" @click="newType = opt.value">
              {{ opt.label }}
            </Button>
          </div>
        </div>

        <div v-if="newType === 'goodreads'">
          <label class="text-xs text-muted-foreground">Identifier: <code>userid:shelf</code></label>
          <Input v-model="newId" placeholder="69278726:to-read" />
          <p class="text-xs text-muted-foreground mt-1">
            Find your user ID in any bookshelf URL:
            <code>goodreads.com/review/list/<b>USERID</b>-name?shelf=to-read</code>
          </p>
        </div>

        <div v-else-if="newType === 'goodreads_listopia'">
          <label class="text-xs text-muted-foreground">List URL, path, or ID</label>
          <Input v-model="newId" placeholder="https://www.goodreads.com/list/show/112351.Best_YA_Romance" />
          <p class="text-xs text-muted-foreground mt-1">
            Paste the URL from <code>goodreads.com/list/show/<b>ID</b>.Name</code>, the path <code>/list/show/ID.Name</code>, or just the ID. Polls the whole list and queues every book.
          </p>
        </div>

        <div v-else-if="newType === 'goodreads_series'">
          <label class="text-xs text-muted-foreground">Series URL, path, or ID</label>
          <Input v-model="newId" placeholder="https://www.goodreads.com/series/40395-mistborn" />
          <p class="text-xs text-muted-foreground mt-1">
            Paste the URL from <code>goodreads.com/series/<b>ID</b>-name</code>, the path, or just the ID. Only main-series books are queued (novellas like book 1.5 are skipped).
          </p>
        </div>

        <div v-else-if="newType === 'hardcover'">
          <label class="text-xs text-muted-foreground">Identifier (use <code>me</code>)</label>
          <Input v-model="newId" placeholder="me" />
          <label class="text-xs text-muted-foreground mt-3 block">API token</label>
          <Input v-model="newToken" placeholder="hardcover bearer token" />
          <p class="text-xs text-muted-foreground mt-1">
            Generate at
            <a class="underline text-primary" href="https://hardcover.app/account/api" target="_blank">hardcover.app/account/api</a>.
          </p>
        </div>
        <div v-else-if="newType === 'nyt'">
          <label class="block text-xs text-muted-foreground mb-1">NYT list slug</label>
          <Input v-model="newId" placeholder="hardcover-fiction" />
          <p class="text-[11px] text-muted-foreground mt-1">
            E.g. <code>hardcover-fiction</code>, <code>combined-print-and-e-book-nonfiction</code>.
            Token = your NYT API key from <a class="underline text-primary" href="https://developer.nytimes.com" target="_blank">developer.nytimes.com</a>.
          </p>
          <label class="block text-xs text-muted-foreground mt-2 mb-1">NYT API key</label>
          <Input v-model="newToken" placeholder="NYT API key" />
        </div>
        <div v-else-if="newType === 'storygraph'">
          <label class="block text-xs text-muted-foreground mb-1">StoryGraph username</label>
          <Input v-model="newId" placeholder="testuser" />
          <p class="text-[11px] text-muted-foreground mt-1">
            Profile must be public. Polls <code>/to-read</code> and <code>/currently-reading</code> daily.
          </p>
        </div>
        <div v-else-if="newType === 'bookwyrm'">
          <label class="block text-xs text-muted-foreground mb-1">BookWyrm instance + username</label>
          <Input v-model="newId" placeholder="bookwyrm.social:alice" />
          <p class="text-[11px] text-muted-foreground mt-1">
            Format: <code>&lt;instance-host&gt;:&lt;username&gt;</code>. Uses the public ActivityPub outbox; no token.
          </p>
        </div>
        <div v-else-if="newType === 'wikidata'">
          <label class="block text-xs text-muted-foreground mb-1">Wikidata Q-ID</label>
          <Input v-model="newId" placeholder="Q36322" />
          <p class="text-[11px] text-muted-foreground mt-1">
            E.g. <code>Q36322</code> for Jane Austen, <code>Q5950</code> for Charles Dickens.
            Returns every literary work by the author via SPARQL.
          </p>
        </div>
        <div v-else-if="newType === 'kindlebangla'">
          <label class="block text-xs text-muted-foreground mb-1">Scope</label>
          <Input v-model="newId" placeholder="full" />
          <p class="text-[11px] text-muted-foreground mt-1">
            Use <code>full</code> for the whole kindlebangla.com catalog, or
            <code>category:&lt;slug&gt;</code> to limit (e.g. <code>category:উপন্যাস</code>).
            First poll walks every category — can queue thousands of books and
            run for ~1–2 hours. Subsequent polls only re-walk to dedup.
            Each book is auto-picked and sent to Kindle + BookOrbit.
          </p>
        </div>

        <div class="flex justify-end gap-2 pt-2">
          <Button variant="ghost" @click="addOpen = false">Cancel</Button>
          <Button @click="add">Add</Button>
        </div>
      </div>
    </Drawer>
  </div>
</template>
