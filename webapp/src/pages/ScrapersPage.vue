<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Cpu, Play, GripVertical, KeyRound } from "lucide-vue-next"
import { VueDraggable } from 'vue-draggable-plus'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

type ScrapersResp = {
  available: string[]; order: string[]; enabled: Record<string, boolean>
  success_rates_30d: Record<string, number>
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

// Phase 6s.8 — Z-Library + cookies cards
const zlibEmail = ref('')
const zlibPassword = ref('')
const zlibBusy = ref(false)
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

async function bench(mode: 'quick' | 'full') {
  benching.value = true
  benchOutput.value = `Running ${mode} bench…\n(may take ${mode === 'quick' ? '~2' : '~15'} min)`
  try {
    const r = await api<{ outcomes: any[]; table: string }>(`/api/bench/run?mode=${mode}`, { method: 'POST' })
    benchOutput.value = r.table
    toast.success(`Bench complete (${mode})`)
    await load()
  } catch (e: any) {
    benchOutput.value = `FAILED: ${e?.message ?? e}`
    toast.error('Bench failed', String(e?.message ?? e))
  } finally { benching.value = false }
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
        class="flex items-center gap-3 bg-card border border-border rounded-lg p-3"
      >
        <GripVertical class="drag-handle w-5 h-5 text-muted-foreground cursor-grab shrink-0 touch-none" />
        <span class="text-xs text-muted-foreground font-mono w-5 text-center shrink-0">{{ idx + 1 }}</span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h4 class="font-mono text-sm break-all">{{ name }}</h4>
            <Badge :variant="data.enabled[name] ? 'success' : 'muted'">
              {{ data.enabled[name] ? 'enabled' : 'disabled' }}
            </Badge>
          </div>
          <p class="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-2 flex-wrap">
            <span>{{ ((data.success_rates_30d[name] ?? 0) * 100).toFixed(0) }}% success (30d)</span>
            <span v-if="(history[name] ?? []).length" class="inline-flex items-center gap-0.5">
              <span v-for="(e, i) in (history[name] ?? [])" :key="i"
                :title="`${e.query} — ${e.success ? 'OK' : 'fail'}${e.duration_ms ? ' (' + e.duration_ms + 'ms)' : ''}`"
                :class="['inline-block w-2 h-2 rounded-full',
                        e.success ? 'bg-emerald-500' : 'bg-red-500']"></span>
            </span>
          </p>
        </div>
        <Button size="sm" variant="outline" class="shrink-0" @click="toggle(name)">
          {{ data.enabled[name] ? 'Disable' : 'Enable' }}
        </Button>
      </div>
    </VueDraggable>

    <div v-if="unusedScrapers.length" class="space-y-2">
      <p class="text-xs text-muted-foreground px-1">Unused — enable to add to the chain.</p>
      <div v-for="name in unusedScrapers" :key="name"
           class="flex items-center gap-3 bg-card border border-border rounded-lg p-3 opacity-60">
        <span class="w-5 text-center text-muted-foreground shrink-0">—</span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h4 class="font-mono text-sm break-all">{{ name }}</h4>
            <Badge variant="muted">unused</Badge>
          </div>
          <p class="text-[11px] text-muted-foreground">
            {{ ((data.success_rates_30d[name] ?? 0) * 100).toFixed(0) }}% success (30d)
          </p>
        </div>
        <Button size="sm" variant="outline" class="shrink-0" @click="toggle(name)">Enable</Button>
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
      <div class="flex gap-2 mb-3">
        <Button :loading="benching" @click="bench('quick')"><Play class="w-4 h-4" /> Quick (3 queries)</Button>
        <Button :loading="benching" variant="outline" @click="bench('full')">Full (10 queries)</Button>
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
