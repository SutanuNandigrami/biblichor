<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Save, Mail, Bell, AlertTriangle, ExternalLink, Cookie, Sliders, ChevronDown, Palette } from 'lucide-vue-next'
import { useMediaQuery } from '@vueuse/core'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'
import { applyTint, readSavedTint } from '@/composables/useTint'

const isDesktop = useMediaQuery('(min-width: 768px)')

const tintH = ref(265)
const tintPresets = [
  { name: 'Blue',    h: 265 },
  { name: 'Indigo',  h: 240 },
  { name: 'Purple',  h: 292 },
  { name: 'Pink',    h: 340 },
  { name: 'Red',     h: 22 },
  { name: 'Amber',   h: 60 },
  { name: 'Green',   h: 142 },
  { name: 'Teal',    h: 180 },
]

function setTint(h: number) {
  tintH.value = h
  applyTint(h)
}
function onTintInput(e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  setTint(v)
}

const form = reactive({
  poll_interval_minutes: 60,
  max_attempts: 5,
  auto_pick_threshold: 50,
  auto_pick_gap: 5,
  log_level: 'INFO',
  kindle_recipient: '',
  smtp_host: 'smtp.gmail.com',
  smtp_port: 587,
  smtp_starttls: true,
  smtp_user: '',
  smtp_password: '',
  smtp_daily_cap: 80,
  smtp_max_attachment_mb: 24,
  pushover_enabled: false,
  pushover_user_key: '',
  pushover_app_token: '',
  welib_auth_cookie: '',
})

// Phase 6u.6: SMTP provider presets. Picking one fills host / port /
// starttls / daily_cap / max_attachment_mb so the user only types
// their own user + password. Caps are the documented free-tier numbers
// at provider time of writing — user can override afterwards.
type SmtpProvider = {
  id: string
  label: string
  host: string
  port: number
  starttls: boolean
  daily_cap: number
  max_attachment_mb: number
  hint: string
}
const SMTP_PRESETS: SmtpProvider[] = [
  {
    id: 'gmail',
    label: 'Gmail (free, ~100/day)',
    host: 'smtp.gmail.com',
    port: 587,
    starttls: true,
    daily_cap: 80,
    max_attachment_mb: 24,
    hint:
      'Free Gmail caps outbound at ~100/day. Use a Google account ' +
      'app-password, not your login password. Workspace users can raise ' +
      'daily_cap to ~1800.',
  },
  {
    id: 'gmail_workspace',
    label: 'Google Workspace (~2000/day)',
    host: 'smtp.gmail.com',
    port: 587,
    starttls: true,
    daily_cap: 1800,
    max_attachment_mb: 24,
    hint:
      'Workspace SMTP cap is ~2000 messages/day per account. Sender ' +
      'address must still be on Amazon Kindle Approved List.',
  },
  {
    id: 'brevo',
    label: 'Brevo (300/day free)',
    host: 'smtp-relay.brevo.com',
    port: 587,
    starttls: true,
    daily_cap: 280,
    max_attachment_mb: 10,
    hint:
      'Sign up at brevo.com (no card). SMTP user/key under SMTP & API → ' +
      'SMTP. Verify your sender address before first send. 300/day free, ' +
      '10 MB attachment cap (raise if you upgrade).',
  },
  {
    id: 'sendgrid',
    label: 'SendGrid (100/day free)',
    host: 'smtp.sendgrid.net',
    port: 587,
    starttls: true,
    daily_cap: 95,
    max_attachment_mb: 30,
    hint:
      "Free tier 100/day forever. User is literally 'apikey'; password " +
      'is the API key you create at sendgrid.com.',
  },
  {
    id: 'aws_ses',
    label: 'AWS SES (200/day sandbox)',
    host: 'email-smtp.us-east-1.amazonaws.com',
    port: 587,
    starttls: true,
    daily_cap: 190,
    max_attachment_mb: 40,
    hint:
      'Create SMTP credentials in the SES console. Sandbox accounts must ' +
      'verify recipient emails. Out-of-sandbox is a free 24h approval ' +
      'process.',
  },
  {
    id: 'mailgun',
    label: 'Mailgun (sandbox)',
    host: 'smtp.mailgun.org',
    port: 587,
    starttls: true,
    daily_cap: 100,
    max_attachment_mb: 25,
    hint:
      'Sandbox domain: 5/day to 5 verified recipients. Add a real domain ' +
      'with SPF/DKIM to unlock higher caps. Pay-as-you-go after.',
  },
  {
    id: 'custom',
    label: 'Custom SMTP',
    host: '',
    port: 587,
    starttls: true,
    daily_cap: 0,
    max_attachment_mb: 24,
    hint:
      'Set daily_cap to 0 to disable the gate entirely (use this for a ' +
      'self-hosted Postfix or Workspace relay).',
  },
]

const smtpProviderId = ref<string>('gmail')
const currentProvider = computed<SmtpProvider | null>(() =>
  SMTP_PRESETS.find((p) => p.id === smtpProviderId.value) ?? null,
)
function applyProviderPreset(id: string) {
  const p = SMTP_PRESETS.find((x) => x.id === id)
  if (!p) return
  smtpProviderId.value = id
  if (id === 'custom') return // don't overwrite anything the user already entered
  form.smtp_host = p.host
  form.smtp_port = p.port
  form.smtp_starttls = p.starttls
  form.smtp_daily_cap = p.daily_cap
  form.smtp_max_attachment_mb = p.max_attachment_mb
}
function detectProviderFromHost(host: string): string {
  const m = SMTP_PRESETS.find((p) => p.host && p.host === host)
  return m?.id ?? 'custom'
}
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
  form.smtp_starttls         = cfg.smtp.starttls ?? true
  form.smtp_user             = cfg.smtp.user
  form.smtp_password         = ''      // never display existing
  form.smtp_daily_cap        = cfg.smtp.daily_cap ?? 80
  form.smtp_max_attachment_mb = cfg.smtp.max_attachment_mb ?? 24
  smtpProviderId.value       = detectProviderFromHost(cfg.smtp.host)
  form.pushover_enabled      = cfg.pushover.enabled
  form.pushover_user_key     = ''
  form.pushover_app_token    = ''
  form.welib_auth_cookie     = ''
}
onMounted(() => {
  load()
  tintH.value = readSavedTint()
})

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

    <Card class="p-5 space-y-3">
      <h2 class="font-semibold flex items-center gap-2 text-sm">
        <Palette class="w-4 h-4 text-primary" /> Accent color
      </h2>
      <p class="text-xs text-muted-foreground">
        Pick a hue. Same palette as BookOrbit so the two apps feel like one. The
        whole UI re-tints instantly and your choice is remembered in this browser.
      </p>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="p in tintPresets"
          :key="p.h"
          type="button"
          class="w-9 h-9 rounded-full border-2 transition-transform hover:scale-110"
          :class="tintH === p.h ? 'border-foreground' : 'border-border'"
          :style="{ background: `oklch(0.6 0.18 ${p.h})` }"
          :title="`${p.name} (${p.h}°)`"
          @click="setTint(p.h)"
        />
      </div>
      <label class="block text-xs space-y-1">
        <span class="text-muted-foreground">Custom hue: {{ tintH }}°</span>
        <input
          type="range"
          min="0"
          max="360"
          :value="tintH"
          class="w-full accent-primary"
          @input="onTintInput"
        />
      </label>
    </Card>

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
        <!-- Phase 6u.6: provider preset switch -->
        <div>
          <label class="text-xs text-muted-foreground mb-1 block">SMTP provider</label>
          <select
            class="h-9 w-full px-3 rounded-md border border-input bg-background text-sm"
            :value="smtpProviderId"
            @change="applyProviderPreset(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="p in SMTP_PRESETS" :key="p.id" :value="p.id">{{ p.label }}</option>
          </select>
          <p v-if="currentProvider" class="text-[11px] text-muted-foreground mt-1 leading-snug">
            {{ currentProvider.hint }}
          </p>
        </div>

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
            <Input v-model.number="form.smtp_port" type="number" />
          </div>
          <div class="sm:col-span-2 flex items-center gap-2">
            <input
              id="smtp-starttls"
              type="checkbox"
              v-model="form.smtp_starttls"
              class="rounded border-input"
            />
            <label for="smtp-starttls" class="text-sm">Use STARTTLS (recommended on port 587)</label>
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">SMTP user (this is what you whitelist at Amazon)</label>
            <Input v-model="form.smtp_user" placeholder="you@gmail.com" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">Password / API key (leave blank to keep existing)</label>
            <Input v-model="form.smtp_password" type="password" placeholder="(unchanged)" />
            <p class="text-xs text-muted-foreground mt-1">
              Spaces in Gmail app passwords are auto-stripped. For SendGrid the
              user is literally <code>apikey</code>; for SES use the SMTP creds
              from the SES console (not your AWS access key).
            </p>
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">
              Daily send cap (0 disables)
            </label>
            <Input v-model.number="form.smtp_daily_cap" type="number" min="0" />
            <p class="text-[11px] text-muted-foreground mt-1">
              Pipeline defers sends past this cap to the next cycle.
            </p>
          </div>
          <div>
            <label class="text-xs text-muted-foreground mb-1 block">
              Max attachment MB (MIME-encoded)
            </label>
            <Input v-model.number="form.smtp_max_attachment_mb" type="number" min="1" />
            <p class="text-[11px] text-muted-foreground mt-1">
              Gmail outbound: 25 (use 24). SES: 40. SendGrid: 30.
            </p>
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
