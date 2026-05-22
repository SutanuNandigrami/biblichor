<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Cpu, Play, GripVertical, KeyRound, FlaskConical, Link2, Unlink } from "lucide-vue-next"
import { VueDraggable } from 'vue-draggable-plus'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

type ScrapersResp = {
  available: string[]; order: string[]; enabled: Record<string, boolean>
  success_rates_30d: Record<string, number>
  // Phase 6v.4
  ever_run: Record<string, boolean>
  last_run_at: Record<string, string | null>
  in_chain: Record<string, boolean>
  corpus_tags: Record<string, string[]>
}
type TestOutcome = {
  scraper: string; query: string; success: boolean
  duration_ms: number; candidates: number; matched_isbn: boolean; note: string
}
type BenchHistoryEntry = {
  ts: string; query: string; success: boolean
  duration_ms: number | null; http_code: number | null
}
type BenchHistoryResp = {
  history: Record<string, BenchHistoryEntry[]>
  success_rates_30d: Record<string, number>
}
const data = ref<ScrapersResp | null>(null)
const history = ref<Record<string, BenchHistoryEntry[]>>({})
const benchOutput = ref<string>('')
const benching = ref(false)
const benchJobId = ref<number | null>(null)
const benchProgress = ref<{ done: number; total: number }>({ done: 0, total: 0 })
const benchEventSrc = ref<EventSource | null>(null)

// Phase 6s.8 — Z-Library + cookies cards
const zlibEmail = ref('')
const zlibPassword = ref('')
const zlibBusy = ref(false)

// Phase 6w.5d — Mobilism credentials card
const mobiUser = ref('')
const mobiPass = ref('')
const mobiBusy = ref(false)
const cookieFile = ref<File | null>(null)
const cookieBusy = ref(false)
const cookieResult = ref('')

async function saveZlibCreds() {
  if (!zlibEmail.value || !zlibPassword.value) return
  zlibBusy.value = true
  try {
    await api('/api/scrapers/zlibrary/creds', {
      method: 'POST',
      body: JSON.stringify({ email: zlibEmail.value, password: zlibPassword.value }),
      headers: { 'Content-Type': 'application/json' },
    })
    zlibEmail.value = ''
    zlibPassword.value = ''
    toast.success('Z-Library credentials saved (encrypted)')
  } catch (e: any) {
    toast.error('Save failed: ' + (e?.message ?? e))
  } finally {
    zlibBusy.value = false
  }
}

async function clearZlibCreds() {
  if (!confirm('Clear stored Z-Library credentials?')) return
  await api('/api/scrapers/zlibrary/creds', { method: 'DELETE' })
  toast.success('Z-Library credentials cleared')
}

async function saveMobilismCreds() {
  if (!mobiUser.value || !mobiPass.value) return
  mobiBusy.value = true
  try {
    await api('/api/scrapers/mobilism/creds', {
      method: 'POST',
      body: JSON.stringify({ username: mobiUser.value, password: mobiPass.value }),
      headers: { 'Content-Type': 'application/json' },
    })
    mobiUser.value = ''
    mobiPass.value = ''
    toast.success('Mobilism credentials saved (encrypted)')
  } catch (e: any) {
    toast.error('Save failed: ' + (e?.message ?? e))
  } finally {
    mobiBusy.value = false
  }
}

async function clearMobilismCreds() {
  if (!confirm('Clear stored Mobilism credentials?')) return
  await api('/api/scrapers/mobilism/creds', { method: 'DELETE' })
  toast.success('Mobilism credentials cleared')
}

async function testMobilismCreds() {
  if (!mobiUser.value || !mobiPass.value) return
  mobiBusy.value = true
  try {
    await api('/api/scrapers/mobilism/test-creds', {
      method: 'POST',
      body: JSON.stringify({ username: mobiUser.value, password: mobiPass.value }),
      headers: { 'Content-Type': 'application/json' },
    })
    toast.success('Mobilism login successful')
  } catch (e: any) {
    toast.error('Login failed: ' + (e?.message ?? e))
  } finally {
    mobiBusy.value = false
  }
}

function pickCookieFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0] ?? null
  cookieFile.value = f
}

async function uploadCookies() {
  if (!cookieFile.value) return
  cookieBusy.value = true
  cookieResult.value = ''
  try {
    const form = new FormData()
    form.append('file', cookieFile.value)
    const r = await fetch('/api/scrapers/cookies', { method: 'POST', body: form })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const body: any = await r.json()
    const domains: string[] = body.domains ?? []
    cookieResult.value = `Saved cookies for ${domains.length} domain(s): ${domains.join(', ')}`
    cookieFile.value = null
  } catch (e: any) {
    toast.error('Cookie upload failed: ' + (e?.message ?? e))
  } finally {
    cookieBusy.value = false
  }
}
const toast = useToast()

async function load() {
  data.value = await api<ScrapersResp>('/api/scrapers')
  try {
    const r = await api<BenchHistoryResp>('/api/bench/history?limit=7')
    history.value = r.history
  } catch { history.value = {} }
}
onMounted(load)

async function toggle(name: string) {
  try { await api(`/api/scrapers/${name}/toggle`, { method: 'POST' }); await load() }
  catch (e: any) { toast.error('Toggle failed', String(e?.message ?? e)) }
}

// Two-way bound array for VueDraggable.
const orderModel = computed<string[]>({
  get: () => data.value?.order ?? [],
  set: (next) => { if (data.value) data.value.order = next },
})

async function saveOrder() {
  if (!data.value) return
  try {
    await api('/api/scrapers/order', {
      method: 'POST',
      body: JSON.stringify({ order: data.value.order }),
    })
  } catch (e: any) {
    toast.error('Reorder failed', String(e?.message ?? e))
    await load()
  }
}

const unusedScrapers = computed<string[]>(() => {
  if (!data.value) return []
  return data.value.available.filter((n) => !data.value!.order.includes(n))
})

// Phase 6v.4: per-scraper "Test now" state. Keyed by scraper name so each
// row has its own spinner + result.
const testState = reactive<Record<string, {
  busy: boolean
  outcome: TestOutcome | null
  error: string | null
}>>({})

function testStateFor(name: string) {
  if (!testState[name]) testState[name] = { busy: false, outcome: null, error: null }
  return testState[name]
}

async function testNow(name: string) {
  const s = testStateFor(name)
  s.busy = true
  s.outcome = null
  s.error = null
  try {
    const r = await api<{ outcome: TestOutcome }>(
      `/api/scrapers/${name}/test_now`,
      { method: 'POST' },
    )
    s.outcome = r.outcome
    if (r.outcome.success) toast.success(`${name}: OK (${r.outcome.duration_ms}ms)`)
    else toast.error(`${name}: ${r.outcome.note || 'no candidates'}`)
    // Reload to refresh ever_run + success_rates_30d.
    await load()
  } catch (e: any) {
    s.error = e?.message ?? String(e)
    toast.error(`${name} test failed: ${s.error}`)
  } finally {
    s.busy = false
  }
}

async function bench(mode: 'quick' | 'full') {
  benching.value = true
  benchOutput.value = `Starting ${mode} bench…`
  benchProgress.value = { done: 0, total: 0 }
  try {
    const r = await api<{ job_id: number }>(
      `/api/bench/run?mode=${mode}`, { method: 'POST' },
    )
    benchJobId.value = r.job_id
    _streamJob(r.job_id)
  } catch (e: any) {
    benchOutput.value = `FAILED: ${e?.message ?? e}`
    toast.error('Bench failed', String(e?.message ?? e))
    benching.value = false
  }
}

function _streamJob(jobId: number) {
  const es = new EventSource(`/api/bench/jobs/${jobId}/stream`)
  benchEventSrc.value = es
  es.addEventListener('progress', (ev: MessageEvent) => {
    const p = JSON.parse(ev.data) as { done: number; total: number }
    benchProgress.value = p
    benchOutput.value = `Running… ${p.done}/${p.total}`
  })
  for (const term of ['done', 'cancelled', 'failed', 'gone']) {
    es.addEventListener(term, async (ev: MessageEvent) => {
      benching.value = false
      es.close()
      if (term === 'done') {
        const r = await api<{ summary_json: string | null }>(`/api/bench/jobs/${jobId}`)
        benchOutput.value = r.summary_json
          ? _formatSummary(JSON.parse(r.summary_json))
          : 'done (no outcomes)'
        toast.success(`Bench complete`)
        await load()
      } else {
        benchOutput.value = `${term}: ${ev.data}`
        toast.error(`Bench ${term}`)
      }
    })
  }
  es.onerror = () => {
    es.close()
    _pollJob(jobId)
  }
}

async function _pollJob(jobId: number) {
  while (true) {
    const r = await api<any>(`/api/bench/jobs/${jobId}`)
    benchProgress.value = { done: r.progress_done, total: r.progress_total }
    if (r.status !== 'running') {
      benching.value = false
      benchOutput.value = r.status === 'done' && r.summary_json
        ? _formatSummary(JSON.parse(r.summary_json))
        : `${r.status}`
      await load()
      return
    }
    await new Promise(res => setTimeout(res, 2000))
  }
}

function _formatSummary(outcomes: any[]): string {
  if (!outcomes || outcomes.length === 0) return '(no outcomes)'
  const by: Record<string, { p: number; f: number }> = {}
  for (const o of outcomes) {
    by[o.scraper] = by[o.scraper] || { p: 0, f: 0 }
    if (o.success) by[o.scraper].p++; else by[o.scraper].f++
  }
  const lines = ['| Scraper | Pass | Fail |', '|---|---|---|']
  for (const [name, s] of Object.entries(by)) lines.push(`| ${name} | ${s.p} | ${s.f} |`)
  return lines.join('\n')
}

async function cancelBench() {
  if (!benchJobId.value) return
  await api(`/api/bench/jobs/${benchJobId.value}/cancel`, { method: 'POST' })
  toast.success('Cancel requested')
}
</script>

<template>
  <div class="p-6 space-y-4" v-if="data">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Scrapers</h1>
      <div class="flex-1"></div>
    </div>
    <p class="text-sm text-muted-foreground">Strategy order = priority. Pipeline tries each enabled strategy in turn.</p>

    <VueDraggable
      v-model="orderModel"
      handle=".drag-handle"
      :animation="180"
      class="space-y-2"
      @update="saveOrder"
    >
      <div
        v-for="(name, idx) in orderModel"
        :key="name"
        class="flex items-start gap-3 bg-card border border-border rounded-lg p-3"
      >
        <GripVertical class="drag-handle w-5 h-5 text-muted-foreground cursor-grab shrink-0 touch-none mt-0.5" />
        <span class="text-xs text-muted-foreground font-mono w-5 text-center shrink-0 mt-0.5">{{ idx + 1 }}</span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h4 class="font-mono text-sm break-all">{{ name }}</h4>
            <Badge :variant="data.enabled[name] ? 'success' : 'muted'">
              {{ data.enabled[name] ? 'enabled' : 'disabled' }}
            </Badge>
            <!-- Phase 6v.4: in-chain is the difference between "the toggle says
                 on" and "the pipeline will actually try it" -->
            <Badge :variant="data.in_chain[name] ? 'success' : 'muted'"
                   :title="data.in_chain[name]
                     ? 'In the pipeline chain: requests will be routed here'
                     : 'NOT in the chain — toggling enabled alone is not enough; reorder to add'">
              <Link2 v-if="data.in_chain[name]" class="inline-block w-3 h-3 mr-0.5" />
              <Unlink v-else class="inline-block w-3 h-3 mr-0.5" />
              {{ data.in_chain[name] ? 'in chain' : 'not in chain' }}
            </Badge>
            <Badge v-if="(data.corpus_tags[name] ?? []).length" variant="muted"
                   :title="`Benched only on queries tagged: ${(data.corpus_tags[name] ?? []).join(', ')}`">
              corpus: {{ (data.corpus_tags[name] ?? []).join(',') }}
            </Badge>
          </div>
          <p class="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-2 flex-wrap">
            <!-- Never-tested vs broken: 0% on a scraper that's never been
                 benched means nothing; show that explicitly. -->
            <Badge v-if="!data.ever_run[name]" variant="muted"
                   title="No bench outcome recorded yet — click Test now to find out if it works.">
              never tested
            </Badge>
            <span v-else>
              {{ ((data.success_rates_30d[name] ?? 0) * 100).toFixed(0) }}% success (30d)
            </span>
            <span v-if="(history[name] ?? []).length" class="inline-flex items-center gap-0.5">
              <span v-for="(e, i) in (history[name] ?? [])" :key="i"
                :title="`${e.query} — ${e.success ? 'OK' : 'fail'}${e.duration_ms ? ' (' + e.duration_ms + 'ms)' : ''}`"
                :class="['inline-block w-2 h-2 rounded-full',
                        e.success ? 'bg-emerald-500' : 'bg-red-500']"></span>
            </span>
          </p>
          <!-- Phase 6v.4: inline test result -->
          <div v-if="testStateFor(name).outcome || testStateFor(name).error"
               class="mt-2 text-[11px] font-mono break-all"
               :class="testStateFor(name).outcome?.success
                       ? 'text-emerald-500' : 'text-red-500'">
            <template v-if="testStateFor(name).outcome">
              <span>{{ testStateFor(name).outcome!.success ? '✓' : '✗' }}</span>
              <span class="ml-1">{{ testStateFor(name).outcome!.query }}</span>
              <span class="ml-1 opacity-70">
                {{ testStateFor(name).outcome!.candidates }} cand,
                {{ testStateFor(name).outcome!.duration_ms }}ms
              </span>
              <span v-if="testStateFor(name).outcome!.note" class="ml-1 opacity-70">
                — {{ testStateFor(name).outcome!.note }}
              </span>
            </template>
            <template v-else-if="testStateFor(name).error">
              ✗ {{ testStateFor(name).error }}
            </template>
          </div>
        </div>
        <div class="flex flex-col gap-1 shrink-0">
          <Button size="sm" variant="outline" :loading="testStateFor(name).busy" @click="testNow(name)">
            <FlaskConical class="w-4 h-4" /> Test now
          </Button>
          <Button size="sm" variant="ghost" @click="toggle(name)">
            {{ data.enabled[name] ? 'Disable' : 'Enable' }}
          </Button>
        </div>
      </div>
    </VueDraggable>

    <div v-if="unusedScrapers.length" class="space-y-2">
      <p class="text-xs text-muted-foreground px-1">Unused — enable to add to the chain.</p>
      <div v-for="name in unusedScrapers" :key="name"
           class="flex items-start gap-3 bg-card border border-border rounded-lg p-3 opacity-60">
        <span class="w-5 text-center text-muted-foreground shrink-0 mt-0.5">—</span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h4 class="font-mono text-sm break-all">{{ name }}</h4>
            <Badge variant="muted">unused</Badge>
            <Badge v-if="(data.corpus_tags[name] ?? []).length" variant="muted"
                   :title="`Specialised — only sees queries tagged: ${(data.corpus_tags[name] ?? []).join(', ')}`">
              corpus: {{ (data.corpus_tags[name] ?? []).join(',') }}
            </Badge>
          </div>
          <p class="text-[11px] text-muted-foreground mt-0.5">
            <Badge v-if="!data.ever_run[name]" variant="muted">never tested</Badge>
            <span v-else>
              {{ ((data.success_rates_30d[name] ?? 0) * 100).toFixed(0) }}% success (30d)
            </span>
          </p>
          <div v-if="testStateFor(name).outcome || testStateFor(name).error"
               class="mt-2 text-[11px] font-mono break-all"
               :class="testStateFor(name).outcome?.success
                       ? 'text-emerald-500' : 'text-red-500'">
            <template v-if="testStateFor(name).outcome">
              {{ testStateFor(name).outcome!.success ? '✓' : '✗' }}
              {{ testStateFor(name).outcome!.query }}
              ({{ testStateFor(name).outcome!.candidates }} cand,
              {{ testStateFor(name).outcome!.duration_ms }}ms)
              <span v-if="testStateFor(name).outcome!.note">
                — {{ testStateFor(name).outcome!.note }}
              </span>
            </template>
            <template v-else>✗ {{ testStateFor(name).error }}</template>
          </div>
        </div>
        <div class="flex flex-col gap-1 shrink-0">
          <Button size="sm" variant="outline" :loading="testStateFor(name).busy" @click="testNow(name)">
            <FlaskConical class="w-4 h-4" /> Test now
          </Button>
          <Button size="sm" variant="ghost" @click="toggle(name)">Enable</Button>
        </div>
      </div>
    </div>

    <Card class="p-4">
      <div class="flex items-center gap-2 mb-3">
        <Cpu class="w-4 h-4 text-primary" />
        <h2 class="font-semibold">Benchmark</h2>
      </div>
      <p class="text-xs text-muted-foreground mb-3">
        Runs a curated set of queries against each enabled strategy to measure real-world success.
      </p>
      <div class="flex gap-2 mb-3 items-center flex-wrap">
        <Button :loading="benching" @click="bench('quick')"><Play class="w-4 h-4" /> Quick</Button>
        <Button :loading="benching" variant="outline" @click="bench('full')">Full</Button>
        <Button v-if="benching" variant="ghost" size="sm" @click="cancelBench">Cancel</Button>
        <span v-if="benching && benchProgress.total > 0" class="text-xs text-muted-foreground font-mono">
          {{ benchProgress.done }}/{{ benchProgress.total }}
        </span>
      </div>
      <pre v-if="benchOutput" class="bg-secondary p-3 rounded text-xs whitespace-pre-wrap overflow-x-auto">{{ benchOutput }}</pre>
    </Card>

    <Card class="p-4 space-y-3">
      <h2 class="font-semibold flex items-center gap-2 text-sm">
        <KeyRound class="w-4 h-4 text-primary" /> Z-Library credentials (optional)
      </h2>
      <p class="text-[11px] text-muted-foreground leading-relaxed">
        Save Z-Library email + password to enable the <code class="font-mono">zlib_singlelogin</code>
        scraper. biblichor logs into <code>singlelogin.re</code>, captures the per-user personal
        domain, and caches it for ~30 days. Stored encrypted in <code class="font-mono">library.db</code>;
        only used when zlib_singlelogin is in the scraper chain.
      </p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Z-Library email</span>
          <input v-model="zlibEmail" type="email" autocomplete="email"
            class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Z-Library password</span>
          <input v-model="zlibPassword" type="password" autocomplete="off"
            class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
        </label>
      </div>
      <div class="flex gap-2 justify-end">
        <Button variant="ghost" size="sm" @click="clearZlibCreds">Clear stored creds</Button>
        <Button size="sm" :disabled="zlibBusy || !zlibEmail || !zlibPassword" @click="saveZlibCreds">
          {{ zlibBusy ? 'Saving...' : 'Save' }}
        </Button>
      </div>
    </Card>

    <Card class="p-4 space-y-3">
      <h2 class="font-semibold flex items-center gap-2 text-sm">
        <KeyRound class="w-4 h-4 text-primary" /> Mobilism credentials (optional)
      </h2>
      <p class="text-[11px] text-muted-foreground leading-relaxed">
        Save Mobilism forum username + password to enable the
        <code class="font-mono">mobilism_books</code> scraper. biblichor logs in via phpBB,
        caches the session for 24 h, and extracts Mediafire links from the English Books subforum.
        Stored encrypted in <code class="font-mono">library.db</code>.
      </p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Mobilism username</span>
          <input v-model="mobiUser" type="text" autocomplete="username"
            class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Mobilism password</span>
          <input v-model="mobiPass" type="password" autocomplete="off"
            class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
        </label>
      </div>
      <div class="flex gap-2 justify-end">
        <Button variant="ghost" size="sm" @click="clearMobilismCreds">Clear stored creds</Button>
        <Button size="sm" variant="outline" :disabled="mobiBusy || !mobiUser || !mobiPass" @click="testMobilismCreds">
          {{ mobiBusy ? 'Testing...' : 'Test login' }}
        </Button>
        <Button size="sm" :disabled="mobiBusy || !mobiUser || !mobiPass" @click="saveMobilismCreds">
          {{ mobiBusy ? 'Saving...' : 'Save' }}
        </Button>
      </div>
    </Card>

    <Card class="p-4 space-y-3">
      <h2 class="font-semibold flex items-center gap-2 text-sm">
        <KeyRound class="w-4 h-4 text-primary" /> Browser cookies (advanced)
      </h2>
      <p class="text-[11px] text-muted-foreground leading-relaxed">
        Upload a Netscape-format <code class="font-mono">cookies.txt</code> file (export via the
        Cookie Editor browser extension or yt-dlp's <code>--cookies-from-browser</code>).
        Cookies are stored per-domain in the encrypted secrets store and reused by scrapers
        for that domain — useful for sites that solve Cloudflare Turnstile interactively in your
        browser but block our headless requests.
      </p>
      <input type="file" accept=".txt,text/plain" @change="pickCookieFile"
        class="text-xs" />
      <div class="flex gap-2 justify-end">
        <Button size="sm" :disabled="cookieBusy || !cookieFile" @click="uploadCookies">
          {{ cookieBusy ? 'Uploading...' : 'Upload' }}
        </Button>
      </div>
      <p v-if="cookieResult" class="text-[11px] text-emerald-500 font-mono">{{ cookieResult }}</p>
    </Card>
  </div>
</template>
