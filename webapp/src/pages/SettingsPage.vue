<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Save, Mail, Bell, AlertTriangle, ExternalLink, Cookie, Sliders, ChevronDown } from 'lucide-vue-next'
import { useMediaQuery } from '@vueuse/core'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

const isDesktop = useMediaQuery('(min-width: 768px)')

const form = reactive({
  poll_interval_minutes: 60,
  max_attempts: 5,
  auto_pick_threshold: 50,
  auto_pick_gap: 5,
  log_level: 'INFO',
  kindle_recipient: '',
  smtp_host: 'smtp.gmail.com',
  smtp_port: 587,
  smtp_user: '',
  smtp_password: '',
  pushover_enabled: false,
  pushover_user_key: '',
  pushover_app_token: '',
  welib_auth_cookie: '',
})
const saving = ref(false)
const testingSmtp = ref(false)
const testingPushover = ref(false)
const smtpResult = ref<{ ok: boolean; msg: string } | null>(null)
const pushoverResult = ref<{ ok: boolean; msg: string } | null>(null)
const toast = useToast()

async function load() {
  const cfg = await api<any>('/api/settings')
  form.poll_interval_minutes = cfg.general.poll_interval_minutes
  form.max_attempts          = cfg.general.max_attempts
  form.auto_pick_threshold   = cfg.general.auto_pick_threshold
  form.auto_pick_gap         = cfg.general.auto_pick_gap
  form.log_level             = cfg.general.log_level
  form.kindle_recipient      = cfg.kindle.recipient
  form.smtp_host             = cfg.smtp.host
  form.smtp_port             = cfg.smtp.port
  form.smtp_user             = cfg.smtp.user
  form.smtp_password         = ''      // never display existing
  form.pushover_enabled      = cfg.pushover.enabled
  form.pushover_user_key     = ''
  form.pushover_app_token    = ''
  form.welib_auth_cookie     = ''
}
onMounted(load)

async function save() {
  saving.value = true
  try {
    const body: any = { ...form }
    // Don't overwrite secrets if the user didn't type a new value
    if (!body.smtp_password) delete body.smtp_password
    if (!body.pushover_user_key) delete body.pushover_user_key
    if (!body.pushover_app_token) delete body.pushover_app_token
    // welib_auth_cookie: empty string sent through means "clear it", undefined skipped
    if (body.welib_auth_cookie === '') delete body.welib_auth_cookie
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) })
    toast.success('Settings saved')
    form.smtp_password = ''
  } catch (e: any) { toast.error('Save failed', String(e?.message ?? e)) }
  finally { saving.value = false }
}

async function testSmtp() {
  testingSmtp.value = true
  smtpResult.value = null
  try {
    const r = await api<{ ok: boolean; error?: string; recipient?: string; response?: string }>('/api/settings/test-smtp', { method: 'POST' })
    smtpResult.value = r.ok
      ? { ok: true, msg: `Sent to ${r.recipient} — ${r.response}` }
      : { ok: false, msg: r.error ?? 'unknown error' }
  } catch (e: any) {
    smtpResult.value = { ok: false, msg: String(e?.message ?? e) }
  } finally { testingSmtp.value = false }
}
async function testPushover() {
  testingPushover.value = true
  pushoverResult.value = null
  try {
    const r = await api<{ ok: boolean; error?: string }>('/api/settings/test-pushover', { method: 'POST' })
    pushoverResult.value = r.ok ? { ok: true, msg: 'Sent — check your device' } : { ok: false, msg: r.error ?? 'failed' }
  } catch (e: any) {
    pushoverResult.value = { ok: false, msg: String(e?.message ?? e) }
  } finally { testingPushover.value = false }
}
</script>

<template>
  <div class="p-6 space-y-6 max-w-4xl">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold tracking-tight">Settings</h1>
      <div class="flex-1"></div>
      <Button :loading="saving" @click="save"><Save class="w-4 h-4" /> Save all</Button>
    </div>

    <Card class="p-5" v-if="form.smtp_user">
      <div class="flex gap-3">
        <AlertTriangle class="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
        <div class="space-y-2">
          <p class="text-sm font-medium text-amber-200">Amazon Kindle whitelist (one-time)</p>
          <p class="text-sm text-muted-foreground">
            Amazon only accepts attachments from senders on your
            <a class="text-primary underline inline-flex items-center gap-1" target="_blank" href="https://www.amazon.com/myk">
              Approved Personal Document E-mail List
              <ExternalLink class="w-3 h-3" />
            </a>.
            Add this address there:
          </p>
          <code class="inline-block bg-secondary px-3 py-1 rounded text-amber-200">{{ form.smtp_user }}</code>
        </div>
      </div>
    </Card>

    <details :open="isDesktop" class="settings-section group bg-card border border-border rounded-lg overflow-hidden">
      <summary class="cursor-pointer md:cursor-default select-none px-5 py-3 font-semibold flex items-center gap-2 text-sm">
        <Mail class="w-4 h-4 text-primary" />
        Kindle delivery (SMTP)
        <ChevronDown class="w-4 h-4 ml-auto md:hidden transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-5 pt-0 space-y-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="sm:col-span-2">
            <label class="text-xs text-muted-foreground mb-1 block">Kindle recipient</label>
            <Input v-model="form.kindle_recipient" placeholder="yourname@kindle.com" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">SMTP host</label>
            <Input v-model="form.smtp_host" placeholder="smtp.gmail.com" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Port</label>
            <Input v-model="form.smtp_port" type="number" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">SMTP user (this is what you whitelist at Amazon)</label>
            <Input v-model="form.smtp_user" placeholder="you@gmail.com" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Password (leave blank to keep existing)</label>
            <Input v-model="form.smtp_password" type="password" placeholder="(unchanged)" />
            <p class="text-xs text-muted-foreground mt-1">Spaces in Gmail app passwords are auto-stripped.</p>
          </div>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <Button variant="outline" :loading="testingSmtp" @click="testSmtp">Send test email</Button>
          <Badge v-if="smtpResult" :variant="smtpResult.ok ? 'success' : 'danger'">
            {{ smtpResult.ok ? '✓' : '✗' }} {{ smtpResult.msg }}
          </Badge>
        </div>
      </div>
    </details>

    <details :open="isDesktop" class="settings-section group bg-card border border-border rounded-lg overflow-hidden">
      <summary class="cursor-pointer md:cursor-default select-none px-5 py-3 font-semibold flex items-center gap-2 text-sm">
        <Bell class="w-4 h-4 text-primary" />
        Pushover
        <ChevronDown class="w-4 h-4 ml-auto md:hidden transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-5 pt-0 space-y-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="sm:col-span-2 flex items-center gap-2">
            <input type="checkbox" v-model="form.pushover_enabled" id="po" class="rounded" />
            <label for="po" class="text-sm">Enabled</label>
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">User key</label>
            <Input v-model="form.pushover_user_key" placeholder="(unchanged)" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">App token</label>
            <Input v-model="form.pushover_app_token" placeholder="(unchanged)" />
          </div>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <Button variant="outline" :loading="testingPushover" @click="testPushover">Send test push</Button>
          <Badge v-if="pushoverResult" :variant="pushoverResult.ok ? 'success' : 'danger'">
            {{ pushoverResult.ok ? '✓' : '✗' }} {{ pushoverResult.msg }}
          </Badge>
        </div>
      </div>
    </details>

    <details :open="isDesktop" class="settings-section group bg-card border border-border rounded-lg overflow-hidden">
      <summary class="cursor-pointer md:cursor-default select-none px-5 py-3 font-semibold flex items-center gap-2 text-sm">
        <Cookie class="w-4 h-4 text-primary" />
        Welib auth cookie (optional)
        <ChevronDown class="w-4 h-4 ml-auto md:hidden transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-5 pt-0 space-y-3">
        <p class="text-xs text-muted-foreground">
          Paste the literal <code>Cookie:</code> header value from your browser
          after signing in to <code>welib.org</code> — open devtools → Application → Cookies,
          select all rows, "Copy with name=value;…". With this set, welib uses
          <code>/fast_download/</code> instead of the slow-countdown anonymous path.
          Stored only in <code>.env</code>, never in <code>config.yaml</code>.
        </p>
        <Input
          v-model="form.welib_auth_cookie"
          placeholder="(unchanged) e.g. session_id=abc; user_id=42"
        />
      </div>
    </details>

    <details :open="isDesktop" class="settings-section group bg-card border border-border rounded-lg overflow-hidden">
      <summary class="cursor-pointer md:cursor-default select-none px-5 py-3 font-semibold flex items-center gap-2 text-sm">
        <Sliders class="w-4 h-4 text-primary" />
        General
        <ChevronDown class="w-4 h-4 ml-auto md:hidden transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-5 pt-0">
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Poll interval (minutes)</label>
            <Input v-model="form.poll_interval_minutes" type="number" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Max attempts per book</label>
            <Input v-model="form.max_attempts" type="number" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Log level</label>
            <select v-model="form.log_level" class="h-9 w-full px-3 rounded-md border border-input bg-background text-sm">
              <option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Auto-pick threshold</label>
            <Input v-model="form.auto_pick_threshold" type="number" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Auto-pick min gap</label>
            <Input v-model="form.auto_pick_gap" type="number" />
          </div>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.settings-section > summary { list-style: none; }
.settings-section > summary::-webkit-details-marker { display: none; }
</style>
