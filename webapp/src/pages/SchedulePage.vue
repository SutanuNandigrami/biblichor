<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Clock, Play, Pause, Zap, Settings as Cog } from 'lucide-vue-next'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Drawer from '@/components/ui/Drawer.vue'

type Job = {
  id: string
  name: string
  trigger: string
  next_run_at: string | null
  paused: boolean
}

const jobs = ref<Job[]>([])
const schedulerRunning = ref(false)
const editing = ref<Job | null>(null)
const editMinutes = ref<string>('')
const editHours = ref<string>('')
const editCronHour = ref<string>('')
const toast = useToast()
let timer: number | null = null

async function load() {
  const r = await api<{ jobs: Job[]; scheduler_running: boolean }>('/api/schedule/jobs')
  jobs.value = r.jobs
  schedulerRunning.value = r.scheduler_running
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 5000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })

async function pause(j: Job) {
  try { await api(`/api/schedule/jobs/${j.id}/pause`, { method: 'POST' }); toast.success(`Paused ${j.name}`); await load() }
  catch (e: any) { toast.error('Pause failed', String(e?.message ?? e)) }
}
async function resume(j: Job) {
  try { await api(`/api/schedule/jobs/${j.id}/resume`, { method: 'POST' }); toast.success(`Resumed ${j.name}`); await load() }
  catch (e: any) { toast.error('Resume failed', String(e?.message ?? e)) }
}
async function runNow(j: Job) {
  try { await api(`/api/schedule/jobs/${j.id}/run`, { method: 'POST' }); toast.success(`${j.name} queued to run now`); await load() }
  catch (e: any) { toast.error('Run-now failed', String(e?.message ?? e)) }
}

function openEdit(j: Job) {
  editing.value = j
  editMinutes.value = ''
  editHours.value = ''
  editCronHour.value = ''
}
async function saveReschedule() {
  if (!editing.value) return
  const body: Record<string, number> = {}
  const ch = editCronHour.value.trim()
  const h = editHours.value.trim()
  const m = editMinutes.value.trim()
  if (ch !== '') body.cron_hour = Number(ch)
  else if (h !== '') body.hours = Number(h)
  else if (m !== '') body.minutes = Number(m)
  else { toast.error('Set one field', 'Enter minutes, hours, or cron hour'); return }
  try {
    await api(`/api/schedule/jobs/${editing.value.id}/reschedule`, {
      method: 'POST', body: JSON.stringify(body),
    })
    toast.success('Rescheduled')
    editing.value = null
    await load()
  } catch (e: any) { toast.error('Reschedule failed', String(e?.message ?? e)) }
}

function fmtNext(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = Date.now()
  const ms = d.getTime() - now
  if (ms < 0) return 'overdue'
  const s = Math.round(ms / 1000)
  if (s < 60) return `in ${s}s`
  if (s < 3600) return `in ${Math.round(s / 60)}m`
  return `in ${Math.round(s / 3600)}h`
}
</script>

<template>
  <div class="p-6 space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Schedule</h1>
      <Badge :variant="schedulerRunning ? 'success' : 'muted'">
        {{ schedulerRunning ? 'running' : 'stopped' }}
      </Badge>
      <Badge variant="muted">{{ jobs.length }} jobs</Badge>
    </div>

    <Card v-if="!jobs.length" class="p-10 text-center">
      <Clock class="w-8 h-8 mx-auto mb-3 opacity-50" />
      <p class="text-muted-foreground">No jobs registered.</p>
    </Card>

    <Card v-else class="overflow-hidden">
      <table class="w-full text-sm">
        <thead class="text-xs text-muted-foreground border-b border-border">
          <tr class="text-left">
            <th class="px-4 py-3">Job</th>
            <th class="px-4 py-3">Schedule</th>
            <th class="px-4 py-3">Next run</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.id" class="border-b border-border/60 last:border-0">
            <td class="px-4 py-3">
              <div class="font-medium">{{ j.name }}</div>
              <div class="text-xs text-muted-foreground font-mono">{{ j.id }}</div>
            </td>
            <td class="px-4 py-3 text-muted-foreground">{{ j.trigger }}</td>
            <td class="px-4 py-3 text-muted-foreground text-xs">
              <div>{{ j.next_run_at ? new Date(j.next_run_at).toLocaleString() : '—' }}</div>
              <div class="opacity-70">{{ fmtNext(j.next_run_at) }}</div>
            </td>
            <td class="px-4 py-3">
              <Badge :variant="j.paused ? 'warning' : 'success'">{{ j.paused ? 'paused' : 'active' }}</Badge>
            </td>
            <td class="px-4 py-3 text-right space-x-1">
              <Button size="sm" variant="outline" @click="runNow(j)" title="Trigger now">
                <Zap class="w-3.5 h-3.5" /> Run now
              </Button>
              <Button v-if="!j.paused" size="sm" variant="ghost" @click="pause(j)" title="Pause">
                <Pause class="w-3.5 h-3.5" />
              </Button>
              <Button v-else size="sm" variant="ghost" @click="resume(j)" title="Resume">
                <Play class="w-3.5 h-3.5" />
              </Button>
              <Button size="sm" variant="ghost" @click="openEdit(j)" title="Reschedule">
                <Cog class="w-3.5 h-3.5" />
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <p class="text-xs text-muted-foreground">
      Reschedule applies immediately and persists for the lifetime of the process. To survive a restart, also update <code>poll_interval_minutes</code> / <code>daily_summary_hour_utc</code> in Settings.
    </p>

    <Drawer :open="editing !== null" :title="editing ? `Reschedule: ${editing.name}` : ''" @close="editing = null">
      <div class="space-y-4" v-if="editing">
        <p class="text-xs text-muted-foreground">
          Set <em>one</em> of the fields below. Cron hour (0-23, UTC) for daily jobs; minutes/hours for interval jobs.
        </p>
        <div>
          <label class="text-xs text-muted-foreground">Interval — minutes</label>
          <Input v-model="editMinutes" placeholder="60" />
        </div>
        <div>
          <label class="text-xs text-muted-foreground">Interval — hours</label>
          <Input v-model="editHours" placeholder="6" />
        </div>
        <div>
          <label class="text-xs text-muted-foreground">Cron — daily UTC hour (0-23)</label>
          <Input v-model="editCronHour" placeholder="7" />
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <Button variant="ghost" @click="editing = null">Cancel</Button>
          <Button @click="saveReschedule">Save</Button>
        </div>
      </div>
    </Drawer>
  </div>
</template>
