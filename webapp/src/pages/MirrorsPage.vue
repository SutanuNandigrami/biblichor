<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Server, Plus, RefreshCw, Trash2, Power, Activity } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Drawer from '@/components/ui/Drawer.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

type Mirror = {
  id: number
  kind: string
  url: string
  label: string | null
  enabled: boolean
  consecutive_failures: number
  last_probed_at: string | null
  last_ok_at: string | null
  last_status: number | null
  last_latency_ms: number | null
  last_error: string | null
}

type Effective = {
  configured: string[]
  wiki: string[]
  merged: string[]
  last_refresh_at: string | null
  next_refresh_at: string | null
  refresh_interval_hours: number
}

const mirrors = ref<Mirror[]>([])
const effective = ref<Effective | null>(null)
const probing = ref(false)
const refreshingMirrors = ref(false)
const addOpen = ref(false)
const newKind = ref<'annas' | 'welib' | 'libgen'>('annas')
const newUrl = ref('')
const newLabel = ref('')
const toast = useToast()

async function load() {
  mirrors.value = (await api<{ mirrors: Mirror[] }>('/api/mirrors')).mirrors
  try { effective.value = await api<Effective>('/api/mirrors/effective') }
  catch { effective.value = null }
}

async function refreshWikiNow() {
  refreshingMirrors.value = true
  try {
    await api('/api/schedule/jobs/mirrors_refresh/run', { method: 'POST' })
    toast.success('Mirror refresh queued', 'Wikipedia fetch fires on next scheduler tick')
    // Give the job a moment to run, then reload
    setTimeout(load, 4000)
  } catch (e: any) { toast.error('Refresh failed', String(e?.message ?? e)) }
  finally { refreshingMirrors.value = false }
}

function relTime(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso).getTime() - Date.now()
  const ad = Math.abs(d)
  if (ad < 60_000) return d < 0 ? 'just now' : 'in <1m'
  if (ad < 3_600_000) {
    const m = Math.round(ad / 60_000)
    return d < 0 ? `${m}m ago` : `in ${m}m`
  }
  const h = Math.round(ad / 3_600_000)
  return d < 0 ? `${h}h ago` : `in ${h}h`
}
onMounted(load)

async function probeAll() {
  probing.value = true
  try {
    await api('/api/mirrors/probe-all', { method: 'POST' })
    await load()
    const ok = mirrors.value.filter((m) => m.last_status !== null && m.last_status < 400).length
    toast.success('Probe complete', `${ok}/${mirrors.value.length} mirrors healthy`)
  } catch (e: any) { toast.error('Probe failed', String(e?.message ?? e)) }
  finally { probing.value = false }
}

async function probeOne(m: Mirror) {
  try {
    const r = await api<{ ok: boolean; status: number; latency_ms: number; error: string | null }>(
      `/api/mirrors/${m.id}/probe`, { method: 'POST' },
    )
    if (r.ok) toast.success(m.url, `${r.status} in ${r.latency_ms} ms`)
    else toast.error(m.url, r.error || `HTTP ${r.status}`)
    await load()
  } catch (e: any) { toast.error('Probe failed', String(e?.message ?? e)) }
}

async function toggle(m: Mirror) {
  try { await api(`/api/mirrors/${m.id}/toggle`, { method: 'POST' }); await load() }
  catch (e: any) { toast.error('Toggle failed', String(e?.message ?? e)) }
}

async function del(m: Mirror) {
  if (!confirm(`Delete ${m.url}?`)) return
  try { await api(`/api/mirrors/${m.id}/delete`, { method: 'POST' }); await load(); toast.success('Removed') }
  catch (e: any) { toast.error('Delete failed', String(e?.message ?? e)) }
}

async function add() {
  if (!newUrl.value.trim()) return
  try {
    await api('/api/mirrors', { method: 'POST',
      body: JSON.stringify({ kind: newKind.value, url: newUrl.value.trim(),
                             label: newLabel.value || null })})
    addOpen.value = false; newUrl.value = ''; newLabel.value = ''
    await load()
    toast.success('Mirror added')
  } catch (e: any) { toast.error('Add failed', String(e?.message ?? e)) }
}

function latencyColor(ms: number | null): 'success' | 'warning' | 'danger' | 'muted' {
  if (ms === null) return 'muted'
  if (ms < 500) return 'success'
  if (ms < 1500) return 'warning'
  return 'danger'
}
</script>

<template>
  <div class="p-6 space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Mirrors</h1>
      <Badge variant="muted">{{ mirrors.length }}</Badge>
      <div class="flex-1"></div>
      <Button variant="outline" :loading="probing" @click="probeAll">
        <Activity class="w-4 h-4" /> Probe all
      </Button>
      <Button variant="subtle" @click="addOpen = true">
        <Plus class="w-4 h-4" /> Add mirror
      </Button>
    </div>
    <p class="text-sm text-muted-foreground">
      Anna's Archive + Welib + LibGen mirrors. Auto-disable after 5 consecutive probe failures.
      Pipeline picks healthy ones at search time.
    </p>

    <Card v-if="effective" class="p-4 space-y-3">
      <div class="flex items-center gap-2">
        <Activity class="w-4 h-4 text-primary" />
        <h2 class="font-semibold">Effective Anna's Archive mirrors</h2>
        <span class="text-xs text-muted-foreground">— what scrapers actually use right now</span>
        <div class="flex-1"></div>
        <Button size="sm" variant="outline" :loading="refreshingMirrors" @click="refreshWikiNow">
          <RefreshCw class="w-3.5 h-3.5" /> Refresh from Wikipedia now
        </Button>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <div>
          <div class="text-xs text-muted-foreground mb-1">Merged ({{ effective.merged.length }})</div>
          <ul class="space-y-1">
            <li v-for="m in effective.merged" :key="m" class="font-mono text-xs flex items-center gap-2">
              <span>{{ m }}</span>
              <Badge v-if="effective.configured.includes(m)" variant="info">config</Badge>
              <Badge v-else variant="success">wiki</Badge>
            </li>
          </ul>
        </div>
        <div class="space-y-2">
          <div>
            <div class="text-xs text-muted-foreground">Last Wikipedia refresh</div>
            <div class="text-sm">
              {{ effective.last_refresh_at ? new Date(effective.last_refresh_at).toLocaleString() : 'never' }}
              <span class="text-xs text-muted-foreground">({{ relTime(effective.last_refresh_at) }})</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">Next refresh</div>
            <div class="text-sm">
              {{ effective.next_refresh_at ? new Date(effective.next_refresh_at).toLocaleString() : '—' }}
              <span class="text-xs text-muted-foreground">({{ relTime(effective.next_refresh_at) }})</span>
            </div>
          </div>
          <div class="text-xs text-muted-foreground">
            Interval: every {{ effective.refresh_interval_hours }}h
            (change on the <a class="underline" href="/schedule">Schedule</a> page)
          </div>
        </div>
      </div>
    </Card>

    <Card class="overflow-hidden">
      <table class="w-full text-sm">
        <thead class="text-xs text-muted-foreground border-b border-border">
          <tr class="text-left">
            <th class="px-4 py-3">Kind</th>
            <th class="px-4 py-3">URL</th>
            <th class="px-4 py-3">State</th>
            <th class="px-4 py-3">Last status</th>
            <th class="px-4 py-3">Latency</th>
            <th class="px-4 py-3">Failures</th>
            <th class="px-4 py-3">Last probed</th>
            <th class="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in mirrors" :key="m.id" class="border-b border-border/60 last:border-0">
            <td class="px-4 py-3"><Badge variant="info">{{ m.kind }}</Badge></td>
            <td class="px-4 py-3">
              <div class="font-mono text-xs">{{ m.url }}</div>
              <div v-if="m.label" class="text-xs text-muted-foreground">{{ m.label }}</div>
              <div v-if="m.last_error" class="text-xs text-red-400 truncate max-w-md">{{ m.last_error }}</div>
            </td>
            <td class="px-4 py-3">
              <Badge :variant="m.enabled ? 'success' : 'muted'">
                {{ m.enabled ? 'enabled' : 'disabled' }}
              </Badge>
            </td>
            <td class="px-4 py-3">
              <span v-if="m.last_status !== null"
                :class="m.last_status < 400 ? 'text-emerald-400' : 'text-red-400'"
                class="font-mono">{{ m.last_status }}</span>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-4 py-3">
              <Badge v-if="m.last_latency_ms !== null" :variant="latencyColor(m.last_latency_ms)">
                {{ m.last_latency_ms }} ms
              </Badge>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-4 py-3">
              <span :class="m.consecutive_failures > 0 ? 'text-amber-400' : 'text-muted-foreground'">
                {{ m.consecutive_failures }}
              </span>
            </td>
            <td class="px-4 py-3 text-muted-foreground text-xs">{{ m.last_probed_at ?? 'never' }}</td>
            <td class="px-4 py-3 text-right space-x-1">
              <Button size="sm" variant="outline" @click="probeOne(m)"><RefreshCw class="w-3.5 h-3.5" /></Button>
              <Button size="sm" variant="ghost" @click="toggle(m)"><Power class="w-3.5 h-3.5" /></Button>
              <Button size="sm" variant="ghost" @click="del(m)"><Trash2 class="w-3.5 h-3.5" /></Button>
            </td>
          </tr>
          <tr v-if="!mirrors.length">
            <td colspan="8" class="px-4 py-10 text-center text-muted-foreground">
              <Server class="w-8 h-8 mx-auto mb-3 opacity-50" />
              No mirrors yet.
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <Drawer :open="addOpen" title="Add a mirror" @close="addOpen = false">
      <div class="space-y-3">
        <div>
          <label class="text-xs text-muted-foreground">Kind</label>
          <div class="flex gap-2 mt-1">
            <Button size="sm" :variant="newKind === 'annas' ? 'default' : 'outline'" @click="newKind = 'annas'">Annas</Button>
            <Button size="sm" :variant="newKind === 'welib' ? 'default' : 'outline'" @click="newKind = 'welib'">Welib</Button>
            <Button size="sm" :variant="newKind === 'libgen' ? 'default' : 'outline'" @click="newKind = 'libgen'">LibGen</Button>
          </div>
        </div>
        <div>
          <label class="text-xs text-muted-foreground">URL (root)</label>
          <Input v-model="newUrl" placeholder="https://annas-archive.xyz" />
        </div>
        <div>
          <label class="text-xs text-muted-foreground">Label (optional)</label>
          <Input v-model="newLabel" placeholder="My private mirror" />
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <Button variant="ghost" @click="addOpen = false">Cancel</Button>
          <Button @click="add">Add</Button>
        </div>
      </div>
    </Drawer>
  </div>
</template>
