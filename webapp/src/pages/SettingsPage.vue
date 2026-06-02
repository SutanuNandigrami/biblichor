<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Save, Mail, Bell, AlertTriangle, ExternalLink, Cookie, Sliders, ChevronDown, Palette, ArrowUpCircle, ShieldCheck, RotateCw, Smartphone, X } from 'lucide-vue-next'
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
  loadUpgradeStatus()
  loadStkStatus()
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
// ===== Phase 6v.1+6v.2: BookOrbit upgrade =====

type UpgradeCheckResult = { name: string; ok: boolean; detail: string }
type UpgradeStatus = {
  current: string | null
  latest: string | null
  update_available: boolean
  last_checked_at: string
  docker_socket_available: boolean
  release_notes: string
  release_url: string
  has_pending_preflight: boolean
  preflight_target: string | null
  preflight_expires_at: number | null
}
type PreflightReport = {
  target_version: string
  ok: boolean
  token: string
  expires_at: number
  checks: UpgradeCheckResult[]
}
type ApplyStep = {
  name: string; status: string; detail: string
  started_at: string; finished_at: string
}
type ApplyResult = {
  target_version: string
  success: boolean
  rolled_back: boolean
  backup_path: string | null
  final_version: string | null
  steps: ApplyStep[]
}

const upgrade = reactive({
  status: null as UpgradeStatus | null,
  preflight: null as PreflightReport | null,
  applyResult: null as ApplyResult | null,
  loadingStatus: false,
  runningPreflight: false,
  applying: false,
  showChangelog: false,
})

async function loadUpgradeStatus() {
  upgrade.loadingStatus = true
  try {
    upgrade.status = await api<UpgradeStatus>('/api/bookorbit/upgrade/status')
  } catch (e: any) {
    toast.error('Could not check for BookOrbit updates: ' + (e?.message ?? e))
  } finally {
    upgrade.loadingStatus = false
  }
}

async function runPreflight() {
  if (!upgrade.status?.latest) return
  upgrade.runningPreflight = true
  upgrade.preflight = null
  upgrade.applyResult = null
  try {
    upgrade.preflight = await api<PreflightReport>(
      '/api/bookorbit/upgrade/preflight',
      {
        method: 'POST',
        body: JSON.stringify({ target_version: upgrade.status.latest.replace(/^v/, '') }),
        headers: { 'Content-Type': 'application/json' },
      },
    )
    if (upgrade.preflight.ok) {
      toast.success(`Preflight OK — token valid for 15 min`)
    } else {
      const failed = upgrade.preflight.checks.filter((c) => !c.ok).map((c) => c.name).join(', ')
      toast.error(`Preflight failed: ${failed}`)
    }
    await loadUpgradeStatus()
  } catch (e: any) {
    toast.error('Preflight error: ' + (e?.message ?? e))
  } finally {
    upgrade.runningPreflight = false
  }
}

async function applyUpgrade() {
  if (!upgrade.preflight?.ok) return
  if (!confirm(
    `Apply BookOrbit upgrade to ${upgrade.preflight.target_version}?\n\n` +
    `This will:\n` +
    `  1. Take a pg_dump backup (kept under /data/backups/)\n` +
    `  2. Recreate the bookorbit container at the new tag\n` +
    `  3. Wait for /health and verify the version\n\n` +
    `If anything fails, the upgrade rolls back to the current image.\n` +
    `BookOrbit will be unavailable for ~30-60 seconds during the swap.`,
  )) return

  upgrade.applying = true
  upgrade.applyResult = null
  try {
    upgrade.applyResult = await api<ApplyResult>(
      '/api/bookorbit/upgrade/apply',
      {
        method: 'POST',
        body: JSON.stringify({
          target_version: upgrade.preflight.target_version,
          token: upgrade.preflight.token,
        }),
        headers: { 'Content-Type': 'application/json' },
      },
    )
    if (upgrade.applyResult.success) {
      toast.success(`BookOrbit upgraded to ${upgrade.applyResult.final_version}`)
      // Preflight token is consumed; reset.
      upgrade.preflight = null
    } else if (upgrade.applyResult.rolled_back) {
      toast.error('Upgrade failed and was rolled back. Backup is intact at ' + upgrade.applyResult.backup_path)
    } else {
      toast.error('Upgrade failed (no rollback). Inspect the steps below.')
    }
    await loadUpgradeStatus()
  } catch (e: any) {
    toast.error('Apply error: ' + (e?.message ?? e))
  } finally {
    upgrade.applying = false
  }
}

function stepIcon(status: string): string {
  switch (status) {
    case 'ok': return '✓'
    case 'failed': return '✗'
    case 'running': return '⟳'
    case 'skipped': return '–'
    default: return '•'
  }
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
// Phase STK 11: Kindle Browser Upload state ---------------------------------
const stkStatus = ref<{
  configured: boolean
  customer_id?: string
  registered_at?: string
  default_destination?: string
  default_destination_sn?: string
  amazon_domain?: string
}>({ configured: false })

const AMAZON_DOMAINS: Array<{ value: string; label: string }> = [
  { value: 'amazon.com',    label: 'United States (amazon.com)' },
  { value: 'amazon.in',     label: 'India (amazon.in)' },
  { value: 'amazon.co.uk',  label: 'United Kingdom (amazon.co.uk)' },
  { value: 'amazon.de',     label: 'Germany (amazon.de)' },
  { value: 'amazon.fr',     label: 'France (amazon.fr)' },
  { value: 'amazon.it',     label: 'Italy (amazon.it)' },
  { value: 'amazon.es',     label: 'Spain (amazon.es)' },
  { value: 'amazon.co.jp',  label: 'Japan (amazon.co.jp)' },
  { value: 'amazon.com.au', label: 'Australia (amazon.com.au)' },
  { value: 'amazon.ca',     label: 'Canada (amazon.ca)' },
  { value: 'amazon.com.br', label: 'Brazil (amazon.com.br)' },
  { value: 'amazon.com.mx', label: 'Mexico (amazon.com.mx)' },
]

const stkAmazonDomain = ref<string>('amazon.com')

const stkQuota = ref<{
  configured: boolean
  sent_24h?: number
  cap?: number
  remaining?: number
  exhausted?: boolean
}>({ configured: false })

const showStkModal = ref(false)
const stkModalStep = ref<'authorize' | 'paste' | 'devices' | 'manual' | 'done'>('authorize')
const stkManualSn = ref<string>('')
const stkManualName = ref<string>('')
// Build a regional Manage Your Content & Devices link from the stored
// amazon_domain (so amazon.in users don't get pointed at amazon.com).
const stkManageDevicesUrl = computed(() => {
  const dom = stkStatus.value.amazon_domain || stkAmazonDomain.value || 'amazon.com'
  // amazon.in serves it at /hz/mycd (no /myx suffix); amazon.com works with either.
  // /hz/mycd works on every regional Amazon, so prefer it.
  return `https://www.${dom}/hz/mycd`
})
const stkAuthorizeUrl = ref<string>('')
const stkRedirectUrl = ref<string>('')
const stkDevices = ref<Array<{ device_serial_number: string; device_type?: string; device_name: string }>>([])
const stkSelectedSn = ref<string>('')
const stkLoading = ref<boolean>(false)
const stkError = ref<string>('')

async function loadStkStatus(): Promise<void> {
  try {
    const r1 = await api<any>('/api/kindle-stk/status')
    stkStatus.value = r1
    if (r1.amazon_domain) stkAmazonDomain.value = r1.amazon_domain
    try {
      const r2 = await api<any>('/healthz')
      if (r2.stk) stkQuota.value = r2.stk
    } catch { /* healthz stk block optional */ }
  } catch (e) {
    console.warn('stk status load failed', e)
  }
}

async function setAmazonDomain(domain: string): Promise<void> {
  stkAmazonDomain.value = domain
  try {
    await api<any>('/api/kindle-stk/region', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amazon_domain: domain }),
    })
  } catch (e: any) {
    console.warn('stk region set failed', e)
  }
}

async function openStkSetup(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  showStkModal.value = true
  stkRedirectUrl.value = ''   // never carry over a stale paste between opens
  try {
    // If we're already configured, jump straight to the device-picker
    // step instead of starting a fresh OAuth dance (which would
    // overwrite the stored code_verifier and break a later complete
    // call from a separately-issued authorize URL). This is the
    // "Change device" flow.
    const status = await api<{ configured: boolean }>('/api/kindle-stk/status')
    if (status.configured) {
      try {
        const devs = await api<{ devices: any[] }>('/api/kindle-stk/devices')
        stkDevices.value = devs.devices || []
        // Preserve the user's current default so the radio is pre-selected.
        stkSelectedSn.value = (status as any).default_destination_sn
          || _pickRecommendedDevice(stkDevices.value)
          || (stkDevices.value[0]?.device_serial_number ?? '')
        stkModalStep.value = 'devices'
      } catch (e: any) {
        // Amazon's GetListOfOwnedDevices endpoint has been intermittently
        // 503-ing for hours (CloudFront stale-cached error). Fall back to
        // manual device serial entry — the user looks it up on amazon.com
        // and pastes it in. Send/upload endpoints work even when this list
        // call doesn't.
        const msg = e?.message || String(e)
        if (/503|temporarily unavailable|Amazon STK/i.test(msg)) {
          stkManualSn.value = (status as any).default_destination_sn || ''
          stkManualName.value = (status as any).default_destination_name || ''
          stkModalStep.value = 'manual'
        } else {
          throw e
        }
      }
      return
    }
    // Fresh setup: start a new OAuth flow.
    stkModalStep.value = 'authorize'
    const r = await api<{ authorize_url: string }>('/api/kindle-stk/oauth/start', { method: 'POST' })
    stkAuthorizeUrl.value = r.authorize_url
    stkModalStep.value = 'paste'
  } catch (e: any) {
    stkError.value = e?.message || String(e)
  } finally {
    stkLoading.value = false
  }
}


function _pickRecommendedDevice(devices: Array<any>): string {
  // Prefer Kindle for Web by name match (it's cloud-only, no auto-push).
  // Then any device whose capabilities flag a web target. Fallback to
  // first.
  const byName = devices.find(d =>
    typeof d.device_name === 'string'
    && d.device_name.toLowerCase().includes('web'))
  if (byName) return byName.device_serial_number
  const byType = devices.find(d => d.device_type === 'FionaWebApp')
  if (byType) return byType.device_serial_number
  return devices[0]?.device_serial_number ?? ''
}

async function completeStkOauth(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  try {
    const r = await api<any>('/api/kindle-stk/oauth/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirect_url: stkRedirectUrl.value.trim() }),
    })
    if (r && r.detail) {
      stkError.value = r.detail
      stkLoading.value = false
      return
    }
    try {
      const devs = await api<{ devices: Array<{ device_serial_number: string; device_type?: string; device_name: string }> }>('/api/kindle-stk/devices')
      stkDevices.value = devs.devices || []
      // Pre-select Kindle for Web by name (primary), then by device_type if present
      const webDev = stkDevices.value.find(
        d => d.device_name.toLowerCase().includes('web') || (d.device_type && d.device_type === 'FionaWebApp')
      )
      stkSelectedSn.value = webDev?.device_serial_number || stkDevices.value[0]?.device_serial_number || ''
      stkModalStep.value = 'devices'
    } catch (e: any) {
      // Same 503 fallback as openStkSetup — manual device entry.
      const msg = e?.message || String(e)
      if (/503|temporarily unavailable|Amazon STK/i.test(msg)) {
        stkModalStep.value = 'manual'
      } else {
        throw e
      }
    }
  } catch (e: any) {
    const msg = e?.message || String(e)
    // Auto-recover from "verifier missing" — the PKCE session expired
    // (cert wiped between Start and Complete, or stale paste UI). Get
    // a fresh verifier + authorize URL so the next Amazon click works.
    if (/code_verifier|start_oauth must run/i.test(msg)) {
      try {
        const fresh = await api<{ authorize_url: string }>('/api/kindle-stk/oauth/start', { method: 'POST' })
        stkAuthorizeUrl.value = fresh.authorize_url
        stkRedirectUrl.value = ''
        stkError.value = 'Your authorization session expired. Click "Open Amazon authorize page" below again, sign in, then paste the new redirect URL.'
        stkModalStep.value = 'paste'
      } catch (e2: any) {
        stkError.value = 'Could not refresh authorization session: ' + (e2?.message ?? e2)
      }
    } else {
      stkError.value = msg
    }
  } finally {
    stkLoading.value = false
  }
}

async function saveStkDestinationManual(): Promise<void> {
  // Workaround path: Amazon's GetListOfOwnedDevices is unreachable, so the
  // user pastes their device serial directly from amazon.com/hz/mycd/myx.
  stkLoading.value = true
  stkError.value = ''
  try {
    await api<any>('/api/kindle-stk/default-destination/manual', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_sn: stkManualSn.value.trim(),
        device_name: stkManualName.value.trim(),
      }),
    })
    showStkModal.value = false
    await loadStkStatus()
    toast.success('Device saved')
  } catch (e: any) {
    stkError.value = e?.message || String(e)
  } finally {
    stkLoading.value = false
  }
}

async function saveStkDestination(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  try {
    await api<any>('/api/kindle-stk/default-destination', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_sn: stkSelectedSn.value }),
    })
    showStkModal.value = false
    await loadStkStatus()
    toast.success('Kindle Browser Upload configured')
  } catch (e: any) {
    stkError.value = e?.message || String(e)
  } finally {
    stkLoading.value = false
  }
}

async function disconnectStk(): Promise<void> {
  if (!confirm('Disconnect Amazon and wipe stored credentials?')) return
  try {
    await api<any>('/api/kindle-stk/connection', { method: 'DELETE' })
    await loadStkStatus()
    toast.success('Kindle Browser Upload disconnected')
  } catch (e: any) {
    toast.error('Disconnect failed: ' + (e?.message ?? e))
  }
}

async function sendStkTest(): Promise<void> {
  try {
    await api<any>('/api/kindle-stk/test-send', { method: 'POST' })
    toast.success('Test send queued — check your Kindle library in a minute.')
  } catch (e: any) {
    toast.error('Test failed: ' + (e?.message ?? e))
  }
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

    <!-- Phase 6v.1+6v.2: BookOrbit upgrade card -->
    <Card class="p-5 space-y-4">
      <div class="flex items-center gap-2">
        <ArrowUpCircle class="w-5 h-5 text-primary" />
        <h2 class="font-semibold text-sm">BookOrbit upgrade</h2>
        <div class="flex-1"></div>
        <Button size="sm" variant="outline" :loading="upgrade.loadingStatus" @click="loadUpgradeStatus">
          <RotateCw class="w-4 h-4" /> Check for updates
        </Button>
      </div>

      <p v-if="!upgrade.status" class="text-xs text-muted-foreground">
        Loading…
      </p>

      <template v-else-if="upgrade.status">
        <!-- Version line -->
        <div class="flex items-center gap-3 flex-wrap text-sm">
          <span class="text-muted-foreground">Current:</span>
          <code class="font-mono">{{ upgrade.status.current ?? 'unknown' }}</code>
          <span class="text-muted-foreground">→</span>
          <span class="text-muted-foreground">Latest:</span>
          <code class="font-mono">{{ upgrade.status.latest ?? 'unknown' }}</code>
          <Badge v-if="upgrade.status.update_available" variant="warning">update available</Badge>
          <Badge v-else variant="success">up to date</Badge>
        </div>

        <!-- Docker socket warning -->
        <div v-if="!upgrade.status.docker_socket_available"
             class="flex gap-3 p-3 rounded bg-amber-500/10 border border-amber-500/30 text-xs">
          <AlertTriangle class="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <p class="text-amber-200 font-medium">Docker socket not reachable</p>
            <p class="text-muted-foreground mt-1">
              biblichor can't talk to the host Docker daemon — the Apply button is
              disabled. Add the docker.sock bind-mount + group_add lines in
              <code class="font-mono">deploy/compose.yml</code> (and set
              <code class="font-mono">DOCKER_GID</code> in <code>.env</code>),
              then re-run <code>docker compose up -d --build biblichor</code>.
            </p>
          </div>
        </div>

        <!-- Changelog toggle -->
        <details v-if="upgrade.status.release_notes" class="text-xs"
                 :open="upgrade.showChangelog"
                 @toggle="(e) => upgrade.showChangelog = (e.target as HTMLDetailsElement).open">
          <summary class="cursor-pointer text-primary hover:underline select-none">
            Release notes for {{ upgrade.status.latest }}
          </summary>
          <pre class="mt-2 bg-secondary p-3 rounded whitespace-pre-wrap font-mono text-[11px] max-h-72 overflow-y-auto">{{ upgrade.status.release_notes }}</pre>
          <a v-if="upgrade.status.release_url" :href="upgrade.status.release_url"
             target="_blank" class="text-primary text-[11px] inline-flex items-center gap-1 mt-1">
            View on GitHub <ExternalLink class="w-3 h-3" />
          </a>
        </details>

        <!-- Action buttons -->
        <div v-if="upgrade.status.update_available && upgrade.status.docker_socket_available"
             class="flex gap-2 flex-wrap">
          <Button :loading="upgrade.runningPreflight" :disabled="upgrade.applying" @click="runPreflight">
            <ShieldCheck class="w-4 h-4" /> Run preflight
          </Button>
          <Button variant="destructive"
                  :loading="upgrade.applying"
                  :disabled="!upgrade.preflight?.ok || upgrade.runningPreflight"
                  @click="applyUpgrade">
            <ArrowUpCircle class="w-4 h-4" /> Apply upgrade
          </Button>
          <span v-if="upgrade.preflight?.ok" class="text-[11px] text-muted-foreground self-center">
            Preflight valid until {{ new Date((upgrade.preflight.expires_at ?? 0) * 1000).toLocaleTimeString() }}
          </span>
        </div>

        <!-- Preflight result -->
        <div v-if="upgrade.preflight" class="space-y-1">
          <p class="text-xs font-medium" :class="upgrade.preflight.ok ? 'text-emerald-400' : 'text-red-400'">
            Preflight {{ upgrade.preflight.ok ? 'PASSED' : 'FAILED' }} for target {{ upgrade.preflight.target_version }}
          </p>
          <ul class="text-[11px] font-mono space-y-0.5">
            <li v-for="c in upgrade.preflight.checks" :key="c.name"
                :class="c.ok ? 'text-emerald-500' : 'text-red-500'">
              {{ c.ok ? '✓' : '✗' }} {{ c.name }}<span v-if="c.detail" class="opacity-70"> — {{ c.detail }}</span>
            </li>
          </ul>
        </div>

        <!-- Apply progress / result -->
        <div v-if="upgrade.applyResult" class="space-y-1">
          <p class="text-xs font-medium"
             :class="upgrade.applyResult.success ? 'text-emerald-400' : 'text-red-400'">
            Apply {{ upgrade.applyResult.success ? 'SUCCEEDED' : (upgrade.applyResult.rolled_back ? 'FAILED + ROLLED BACK' : 'FAILED') }}
            <span v-if="upgrade.applyResult.final_version"> — running {{ upgrade.applyResult.final_version }}</span>
          </p>
          <p v-if="upgrade.applyResult.backup_path" class="text-[11px] text-muted-foreground font-mono">
            Backup: {{ upgrade.applyResult.backup_path }}
          </p>
          <ul class="text-[11px] font-mono space-y-0.5">
            <li v-for="s in upgrade.applyResult.steps" :key="s.name + s.started_at"
                :class="{
                  'text-emerald-500': s.status === 'ok',
                  'text-red-500': s.status === 'failed',
                  'text-muted-foreground': s.status === 'skipped' || s.status === 'running',
                }">
              {{ stepIcon(s.status) }} {{ s.name }}<span v-if="s.detail" class="opacity-70"> — {{ s.detail }}</span>
            </li>
          </ul>
        </div>
      </template>
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

    <!-- Phase STK 11: Kindle Browser Upload Card -->
    <details :open="isDesktop" class="settings-section group bg-card border border-border rounded-lg overflow-hidden">
      <summary class="cursor-pointer md:cursor-default select-none px-5 py-3 font-semibold flex items-center gap-2 text-sm">
        <Smartphone class="w-4 h-4 text-primary" />
        Kindle Browser Upload
        <Badge v-if="stkStatus.configured" variant="success" class="ml-1">connected</Badge>
        <span class="ml-1 text-[11px] text-emerald-600 font-normal">(recommended — bypasses SMTP cap)</span>
        <ChevronDown class="w-4 h-4 ml-auto md:hidden transition-transform group-open:rotate-180" />
      </summary>
      <div class="p-5 pt-0 space-y-4">
        <!-- Not yet configured -->
        <div v-if="!stkStatus.configured" class="space-y-3">
          <p class="text-sm text-muted-foreground">
            Send via Amazon’s web upload — bypasses SMTP’s ~80/day cap,
            supports files up to 200 MB, no Gmail dependency.
          </p>
          <div class="flex flex-col gap-1">
            <div class="flex items-center gap-2">
              <label class="text-sm font-medium whitespace-nowrap">Amazon region:</label>
              <select
                :value="stkAmazonDomain"
                @change="setAmazonDomain(($event.target as HTMLSelectElement).value)"
                class="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option v-for="d in AMAZON_DOMAINS" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
            </div>
            <p class="text-xs text-muted-foreground">Used for account region; auth flow always uses amazon.com</p>
          </div>
          <Button @click="openStkSetup" :loading="stkLoading">
            <Smartphone class="w-4 h-4" /> Set up Amazon
          </Button>
        </div>
        <!-- Configured -->
        <div v-else class="space-y-3">
          <p class="text-sm">
            Connected as <strong>{{ stkStatus.customer_id }}</strong>
            <span v-if="stkStatus.registered_at" class="text-muted-foreground"> · since {{ stkStatus.registered_at.slice(0, 10) }}</span>
            <span v-if="stkStatus.amazon_domain && stkStatus.amazon_domain !== 'amazon.com'" class="text-muted-foreground"> · {{ stkStatus.amazon_domain }}</span>
          </p>
          <p class="text-sm">
            Default destination: <strong>{{ stkStatus.default_destination || 'none' }}</strong>
          </p>
          <p v-if="stkQuota.configured" class="text-sm"
             :class="stkQuota.exhausted ? 'text-red-500' : 'text-muted-foreground'">
            Sent today: {{ stkQuota.sent_24h }} / {{ stkQuota.cap }}
            <span v-if="stkQuota.exhausted"> — daily cap reached, biblichor will fall back to SMTP</span>
          </p>
          <div class="flex gap-2 flex-wrap">
            <Button variant="outline" @click="openStkSetup">Change device</Button>
            <Button variant="outline" @click="sendStkTest">Send test</Button>
            <Button variant="destructive" @click="disconnectStk">Disconnect</Button>
          </div>
        </div>
      </div>
    </details>

    <!-- Phase STK 11: setup modal -->
    <div v-if="showStkModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div class="bg-card rounded-lg border border-border shadow-xl max-w-lg w-full p-6 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-lg font-semibold">Connect to Amazon</h3>
          <button class="text-muted-foreground hover:text-foreground" @click="showStkModal = false">
            <X class="w-5 h-5" />
          </button>
        </div>

        <div v-if="stkError" class="rounded bg-destructive/10 border border-destructive/30 text-destructive p-3 text-sm">
          {{ stkError }}
        </div>

        <!-- Step: authorize + paste URL -->
        <div v-if="stkModalStep === 'authorize' || stkModalStep === 'paste'" class="space-y-5">
          <div>
            <p class="text-sm mb-3">
              <strong>Step 1:</strong> Click below to open Amazon’s authorize page.
              Sign in and click "Allow".
            </p>
            <a :href="stkAuthorizeUrl" target="_blank" rel="noopener"
               class="inline-flex items-center gap-2 h-9 px-4 text-sm rounded-md font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
              <ExternalLink class="w-4 h-4" /> Open Amazon authorize page
            </a>
          </div>
          <div class="space-y-2">
            <p class="text-sm">
              <strong>Step 2:</strong> After clicking Allow, copy the full URL from your
              address bar and paste here:
            </p>
            <Input
              v-model="stkRedirectUrl"
              placeholder="https://www.amazon.com/ap/maplanding?openid..."
            />
            <div class="flex gap-2 mt-3">
              <Button :disabled="!stkRedirectUrl || stkLoading" :loading="stkLoading" @click="completeStkOauth">
                Connect
              </Button>
              <Button variant="outline" @click="showStkModal = false">Cancel</Button>
            </div>
          </div>
        </div>

        <!-- Step: device picker -->
        <div v-else-if="stkModalStep === 'devices'" class="space-y-4">
          <Badge variant="success">✓ Connected to Amazon</Badge>
          <p class="text-sm">Pick the default delivery target:</p>
          <div class="space-y-2">
            <label
              v-for="d in stkDevices"
              :key="d.device_serial_number"
              class="flex items-center gap-2 text-sm cursor-pointer"
            >
              <input type="radio" :value="d.device_serial_number" v-model="stkSelectedSn" class="accent-primary" />
              <span>{{ d.device_name }}</span>
              <Badge
                v-if="d.device_name.toLowerCase().includes('web') || (d.device_type && d.device_type === 'FionaWebApp')"
                variant="success"
                class="text-[10px]"
              >recommended</Badge>
            </label>
          </div>
          <p class="text-xs text-muted-foreground">
            Every book lands in your Personal Documents library regardless of device.
            Per-device auto-download is managed at amazon.com/mycontent.
          </p>
          <div class="flex gap-2">
            <Button :disabled="!stkSelectedSn || stkLoading" :loading="stkLoading" @click="saveStkDestination">
              Save
            </Button>
            <Button variant="outline" @click="showStkModal = false">Cancel</Button>
          </div>
        </div>

        <!-- Step: manual device entry (fallback when Amazon's device list endpoint is down) -->
        <div v-else-if="stkModalStep === 'manual'" class="space-y-4">
          <Badge variant="success">✓ Connected to Amazon</Badge>
          <div class="rounded bg-amber-500/10 border border-amber-500/30 text-amber-200 p-3 text-sm space-y-2">
            <p><strong>Amazon's device-list service is unavailable right now.</strong></p>
            <p class="text-xs">Send-to-Kindle uploads still work — biblichor just can't fetch your device list automatically. Enter your Kindle's serial number manually below.</p>
          </div>
          <ol class="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
            <li>
              Open <a :href="stkManageDevicesUrl" target="_blank" rel="noopener" class="text-primary underline">{{ stkManageDevicesUrl.replace(/^https?:\/\//, '') }}</a> → <strong>Devices</strong> tab
            </li>
            <li>Click your Kindle row (or "Kindle for Web") to expand its details</li>
            <li>Copy the <strong>Serial Number</strong> — it's a 16-char alphanumeric string. For Kindle for Web it may be labelled "Device serial number" inside the expanded row.</li>
          </ol>
          <div class="space-y-2">
            <label class="text-sm font-medium">Device serial number</label>
            <Input v-model="stkManualSn" placeholder="e.g. G090G10551070LE3" />
          </div>
          <div class="space-y-2">
            <label class="text-sm font-medium">Display name <span class="text-xs text-muted-foreground">(optional)</span></label>
            <Input v-model="stkManualName" placeholder="e.g. Kindle for Web" />
          </div>
          <div class="flex gap-2">
            <Button :disabled="!stkManualSn.trim() || stkLoading" :loading="stkLoading" @click="saveStkDestinationManual">
              Save device
            </Button>
            <Button variant="outline" @click="showStkModal = false">Cancel</Button>
          </div>
        </div>
      </div>
    </div>

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
