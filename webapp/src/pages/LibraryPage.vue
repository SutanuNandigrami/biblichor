<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ExternalLink,
  BookOpen,
  Tablet,
  ChartLine,
  Copy,
  Globe,
  Smartphone,
  RotateCw,
  Stethoscope,
  KeyRound,
  CheckCircle2,
  XCircle,
} from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import { api } from '@/composables/useApi'
import { useToast } from '@/composables/useToast'

interface BookOrbitUrls {
  dashboard: string
  opds_catalog: string
  kobo_sync_root: string
  koreader_sync: string
  statistics: string
  reader_base: string
  base: string
}

interface BookOrbitStatus {
  enabled: boolean
  setup_needed: boolean
  has_creds: boolean
  library_id: string | null
  library_root: string
  library_root_exists: boolean
  url: string
  health_ok: boolean
  last_check_error: string | null
}

interface DoctorCheck {
  name: string
  ok: boolean
  detail: string
}

const toast = useToast()
const urls = ref<BookOrbitUrls | null>(null)
const status = ref<BookOrbitStatus | null>(null)
const loading = ref(true)

const showWizard = ref(false)
const wizardBusy = ref(false)
const wizardForm = ref({
  admin_username: 'admin',
  admin_email: '',
  admin_name: 'Admin',
  admin_password: '',
  setup_token: '',
  library_root: '/library',
})

const showCreds = ref(false)
const credsBusy = ref(false)
const credsForm = ref({ admin_username: 'admin', admin_password: '' })

const showChangePw = ref(false)
const changePwBusy = ref(false)
const changePwForm = ref({ new_password: '', confirm_password: '' })

const doctorBusy = ref(false)
const doctorReport = ref<{ ok: boolean; checks: DoctorCheck[] } | null>(null)

const scanBusy = ref(false)
const recreateBusy = ref(false)

async function refresh() {
  loading.value = true
  try {
    const [settings, st] = await Promise.all([
      api<any>('/api/settings'),
      api<BookOrbitStatus>('/api/bookorbit/status'),
    ])
    urls.value = settings?.bookorbit?.urls ?? null
    status.value = st
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

const bookOrbitUrl = computed(() => urls.value?.base ?? '')

function openBookOrbit() {
  if (bookOrbitUrl.value) window.open(bookOrbitUrl.value, '_blank', 'noopener')
}
function openLink(url: string) {
  window.open(url, '_blank', 'noopener')
}
async function copy(text: string, label: string) {
  await navigator.clipboard.writeText(text)
  toast.success(label + ' URL copied')
}

async function openWizard() {
  try {
    const tok = await api<{ token: string }>('/api/bookorbit/setup-token', { method: 'POST' })
    wizardForm.value.setup_token = tok.token
  } catch (e) {
    // non-fatal
  }
  showWizard.value = true
}

async function submitWizard() {
  wizardBusy.value = true
  try {
    await api('/api/bookorbit/setup', {
      method: 'POST',
      body: JSON.stringify(wizardForm.value),
      headers: { 'Content-Type': 'application/json' },
    })
    toast.success('BookOrbit setup complete')
    showWizard.value = false
    await refresh()
  } catch (e: any) {
    toast.error('Setup failed: ' + (e?.message ?? e))
  } finally {
    wizardBusy.value = false
  }
}

async function submitCreds() {
  credsBusy.value = true
  try {
    await api('/api/bookorbit/creds', {
      method: 'POST',
      body: JSON.stringify(credsForm.value),
      headers: { 'Content-Type': 'application/json' },
    })
    toast.success('Credentials updated')
    showCreds.value = false
    credsForm.value.admin_password = ''
    await refresh()
  } catch (e: any) {
    toast.error('Update failed: ' + (e?.message ?? e))
  } finally {
    credsBusy.value = false
  }
}

async function clearCreds() {
  if (!confirm('Clear stored BookOrbit credentials? Scan + authenticated Doctor checks will stop working until you re-enter them.'))
    return
  await api('/api/bookorbit/creds', { method: 'DELETE' })
  toast.success('Credentials cleared')
  await refresh()
}

async function runDoctor() {
  doctorBusy.value = true
  doctorReport.value = null
  try {
    doctorReport.value = await api('/api/bookorbit/doctor', { method: 'POST' })
  } catch (e: any) {
    toast.error('Doctor failed: ' + (e?.message ?? e))
  } finally {
    doctorBusy.value = false
  }
}

async function runScan() {
  scanBusy.value = true
  try {
    await api('/api/bookorbit/scan', { method: 'POST' })
    toast.success('Scan triggered')
  } catch (e: any) {
    toast.error('Scan failed: ' + (e?.message ?? e))
  } finally {
    scanBusy.value = false
  }
}

async function runRecreateLibrary() {
  recreateBusy.value = true
  try {
    const out: any = await api('/api/bookorbit/recreate-library', { method: 'POST' })
    toast.success(out.created ? `Library created (id=${out.library_id})` : `Library OK (id=${out.library_id})`)
    await refresh()
  } catch (e: any) {
    toast.error('Recreate failed: ' + (e?.message ?? e))
  } finally {
    recreateBusy.value = false
  }
}

async function submitChangePassword() {
  if (changePwForm.value.new_password !== changePwForm.value.confirm_password) {
    toast.error('New password and confirmation do not match')
    return
  }
  if (changePwForm.value.new_password.length < 8) {
    toast.error('Password must be at least 8 characters')
    return
  }
  changePwBusy.value = true
  try {
    await api('/api/bookorbit/admin/change-password', {
      method: 'POST',
      body: JSON.stringify({ new_password: changePwForm.value.new_password }),
      headers: { 'Content-Type': 'application/json' },
    })
    toast.success('BookOrbit admin password changed')
    showChangePw.value = false
    changePwForm.value = { new_password: '', confirm_password: '' }
    await refresh()
  } catch (e: any) {
    toast.error('Change failed: ' + (e?.message ?? e))
  } finally {
    changePwBusy.value = false
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto px-6 py-8">
    <div class="max-w-3xl mx-auto space-y-6">
      <header class="text-center space-y-2">
        <BookOpen class="w-14 h-14 mx-auto text-primary" />
        <h1 class="text-2xl font-semibold">Library</h1>
        <p class="text-sm text-muted-foreground max-w-xl mx-auto leading-relaxed">
          Your library lives in <strong>BookOrbit</strong>. biblichor stays focused on getting books;
          BookOrbit owns reading, Kobo/KOReader sync, OPDS, and statistics.
        </p>
      </header>

      <Card v-if="loading" class="p-8 text-center text-sm text-muted-foreground">
        Loading library status...
      </Card>

      <Card v-else-if="status?.enabled && status.setup_needed && !showWizard"
            class="p-5 space-y-3 border-amber-500/40">
        <div class="flex items-center gap-3">
          <KeyRound class="w-5 h-5 text-amber-500" />
          <h2 class="font-semibold text-base flex-1">First-run setup needed</h2>
          <Button size="lg" @click="openWizard">Set up BookOrbit</Button>
        </div>
        <p class="text-xs text-muted-foreground">
          BookOrbit is running but no admin account exists yet. Create one to unlock the library, OPDS,
          Kobo/KOReader sync, and reading statistics.
        </p>
      </Card>

      <Card v-if="showWizard" class="p-5 space-y-4">
        <h2 class="font-semibold text-base flex items-center gap-2">
          <KeyRound class="w-5 h-5 text-primary" /> BookOrbit setup (first run only)
        </h2>
        <p class="text-[11px] text-muted-foreground">
          Creates the BookOrbit admin account + the biblichor watched library.
          Only works if BookOrbit reports <code>needsSetup=true</code> (i.e. no admin yet).
          If BookOrbit is already set up, use <strong>Change password</strong> to rotate the admin
          password, or <strong>Stored creds</strong> to tell biblichor the existing one.
        </p>
        <div class="grid grid-cols-2 gap-3">
          <label class="text-xs space-y-1">
            <span class="text-muted-foreground">Admin username</span>
            <input v-model="wizardForm.admin_username"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
          </label>
          <label class="text-xs space-y-1">
            <span class="text-muted-foreground">Display name</span>
            <input v-model="wizardForm.admin_name"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
          </label>
          <label class="text-xs space-y-1 col-span-2">
            <span class="text-muted-foreground">Email</span>
            <input v-model="wizardForm.admin_email" type="email" autocomplete="email"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
          </label>
          <label class="text-xs space-y-1 col-span-2">
            <span class="text-muted-foreground">Password</span>
            <input v-model="wizardForm.admin_password" type="password" autocomplete="new-password"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
          </label>
          <label class="text-xs space-y-1 col-span-2">
            <span class="text-muted-foreground">Library root (container path)</span>
            <input v-model="wizardForm.library_root"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm font-mono" />
            <span class="text-[10px] text-muted-foreground">
              Inside docker: <code>/library</code>. Native install: the host directory.
            </span>
          </label>
          <label class="text-xs space-y-1 col-span-2">
            <span class="text-muted-foreground">Setup token</span>
            <input v-model="wizardForm.setup_token"
              class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm font-mono" />
            <span class="text-[10px] text-muted-foreground">
              Auto-generated. Must match <code>BOOKORBIT_SETUP_TOKEN</code> if set in <code>.env</code>.
            </span>
          </label>
        </div>
        <div class="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" :disabled="wizardBusy" @click="showWizard = false">Cancel</Button>
          <Button size="sm" :disabled="wizardBusy || !wizardForm.admin_password || !wizardForm.admin_email"
            @click="submitWizard">
            {{ wizardBusy ? 'Setting up...' : 'Create admin + library' }}
          </Button>
        </div>
      </Card>

      <template v-else-if="urls && status">
        <Card class="p-5 space-y-3">
          <div class="flex items-center gap-3">
            <BookOpen class="w-5 h-5 text-primary" />
            <h2 class="font-semibold text-base flex-1">Open BookOrbit</h2>
            <Button size="lg" @click="openBookOrbit">
              <ExternalLink class="w-4 h-4 mr-2" /> Launch
            </Button>
          </div>
          <p class="text-xs text-muted-foreground font-mono break-all">{{ urls.dashboard }}</p>
          <p v-if="!status.enabled" class="text-[11px] text-amber-500 dark:text-amber-400">
            Pipeline integration is <strong>disabled</strong>. Set
            <code class="font-mono">bookorbit.enabled = true</code> in config.yaml to enable auto-drop.
          </p>
        </Card>

        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Button variant="outline" :disabled="scanBusy" @click="runScan">
            <RotateCw class="w-4 h-4 mr-2" :class="scanBusy ? 'animate-spin' : ''" />
            {{ scanBusy ? 'Scanning...' : 'Scan now' }}
          </Button>
          <Button variant="outline" :disabled="doctorBusy" @click="runDoctor">
            <Stethoscope class="w-4 h-4 mr-2" />
            {{ doctorBusy ? 'Probing...' : 'Run doctor' }}
          </Button>
          <Button variant="outline" @click="showChangePw = !showChangePw">
            <KeyRound class="w-4 h-4 mr-2" />
            Change password
          </Button>
          <Button variant="outline" @click="showCreds = !showCreds">
            <KeyRound class="w-4 h-4 mr-2" />
            {{ status.has_creds ? 'Stored creds' : 'Store creds' }}
          </Button>
        </div>

        <details class="text-[11px] text-muted-foreground -mt-2">
          <summary class="cursor-pointer hover:text-foreground select-none">
            Recovery actions (advanced)
          </summary>
          <div class="mt-3 space-y-2 pl-3 border-l-2 border-border">
            <p>
              <strong>Library missing in BookOrbit?</strong>
              If you deleted the watched library in BookOrbit's own UI, or its <code class="font-mono">library_id</code>
              has drifted from <code class="font-mono">config.yaml</code>, biblichor can recreate it using your stored
              credentials.
            </p>
            <Button variant="ghost" size="sm" :disabled="recreateBusy || !status.has_creds" @click="runRecreateLibrary">
              <KeyRound class="w-4 h-4 mr-2" />
              {{ recreateBusy ? 'Working...' : 'Recreate watched library' }}
            </Button>
            <p v-if="!status.has_creds" class="text-[10px] text-amber-500">
              Requires stored credentials. Save them under "Stored creds" first.
            </p>
          </div>
        </details>

        <Card v-if="doctorReport" class="p-4 space-y-2">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <Stethoscope class="w-4 h-4" />
            Doctor -
            <span :class="doctorReport.ok ? 'text-emerald-500' : 'text-rose-500'">
              {{ doctorReport.ok ? 'all checks passed' : 'failures found' }}
            </span>
          </h3>
          <ul class="space-y-1 text-xs">
            <li v-for="c in doctorReport.checks" :key="c.name" class="flex items-start gap-2">
              <CheckCircle2 v-if="c.ok" class="w-4 h-4 mt-0.5 text-emerald-500 flex-shrink-0" />
              <XCircle v-else class="w-4 h-4 mt-0.5 text-rose-500 flex-shrink-0" />
              <span class="font-mono text-[11px]">{{ c.name }}</span>
              <span class="text-muted-foreground flex-1">{{ c.detail }}</span>
            </li>
          </ul>
        </Card>

        <Card v-if="showChangePw" class="p-4 space-y-3 border-primary/40">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <KeyRound class="w-4 h-4 text-primary" /> Change BookOrbit admin password
          </h3>
          <p class="text-[11px] text-muted-foreground">
            Pick a password you'll remember. biblichor handles the authentication with BookOrbit
            for you — you only need to type the new password below.
          </p>
          <div class="space-y-3">
            <label class="text-xs space-y-1 block">
              <span class="text-muted-foreground">New password (8+ characters)</span>
              <input v-model="changePwForm.new_password" type="password" autocomplete="new-password"
                class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
            </label>
            <label class="text-xs space-y-1 block">
              <span class="text-muted-foreground">Confirm new password</span>
              <input v-model="changePwForm.confirm_password" type="password" autocomplete="new-password"
                class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
            </label>
          </div>
          <div class="flex gap-2 justify-end">
            <Button variant="ghost" size="sm" :disabled="changePwBusy" @click="showChangePw = false">Cancel</Button>
            <Button size="sm"
              :disabled="changePwBusy || !changePwForm.new_password || changePwForm.new_password !== changePwForm.confirm_password"
              @click="submitChangePassword">
              {{ changePwBusy ? 'Changing...' : 'Change password' }}
            </Button>
          </div>
        </Card>

        <Card v-if="showCreds" class="p-4 space-y-3">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <KeyRound class="w-4 h-4 text-primary" /> Stored BookOrbit credentials
          </h3>
          <p class="text-[11px] text-muted-foreground">
            What biblichor uses to authenticate with BookOrbit for Scan and Doctor checks.
            Stored encrypted in <code class="font-mono">library.db</code>.
            <strong>This does NOT change BookOrbit's password</strong> — it just tells biblichor what password
            to use. To actually rotate the password, use <strong>Change password</strong> above.
          </p>
          <div class="grid grid-cols-2 gap-3">
            <label class="text-xs space-y-1">
              <span class="text-muted-foreground">Admin username</span>
              <input v-model="credsForm.admin_username"
                class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
            </label>
            <label class="text-xs space-y-1">
              <span class="text-muted-foreground">Admin password (must match BookOrbit's current)</span>
              <input v-model="credsForm.admin_password" type="password" autocomplete="off"
                class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
            </label>
          </div>
          <div class="flex gap-2 justify-end">
            <Button v-if="status.has_creds" variant="ghost" size="sm" @click="clearCreds">Clear stored creds</Button>
            <Button size="sm" :disabled="credsBusy || !credsForm.admin_password" @click="submitCreds">
              {{ credsBusy ? 'Saving...' : 'Save' }}
            </Button>
          </div>
        </Card>

        <div class="space-y-3">
          <h3 class="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            E-reader sync
          </h3>

          <Card class="p-4 space-y-2">
            <div class="flex items-center gap-3">
              <Globe class="w-5 h-5 text-primary" />
              <h4 class="font-medium flex-1">OPDS catalog</h4>
              <Button size="sm" variant="outline" @click="copy(urls.opds_catalog, 'OPDS')">
                <Copy class="w-4 h-4 mr-1.5" /> Copy
              </Button>
              <Button size="sm" variant="ghost" @click="openLink(urls.opds_catalog)">
                <ExternalLink class="w-4 h-4" />
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              Point any OPDS-compatible reader at this URL: KOReader, Thorium, Moon+ Reader, Marvin, Aldiko.
              First use prompts for credentials; create an OPDS password in BookOrbit's Account settings.
            </p>
            <p class="font-mono text-[11px] break-all bg-muted/40 px-2 py-1.5 rounded">
              {{ urls.opds_catalog }}
            </p>
          </Card>

          <Card class="p-4 space-y-2">
            <div class="flex items-center gap-3">
              <Tablet class="w-5 h-5 text-primary" />
              <h4 class="font-medium flex-1">Kobo sync</h4>
              <Button size="sm" variant="ghost" @click="openLink(urls.dashboard + '/settings/kobo')">
                <ExternalLink class="w-4 h-4" />
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              Set up Kobo auto-push via BookOrbit's Settings > Kobo. Each device gets its own sync token.
            </p>
            <p class="font-mono text-[11px] break-all bg-muted/40 px-2 py-1.5 rounded">
              {{ urls.kobo_sync_root }}/&lt;deviceToken&gt;
            </p>
          </Card>

          <Card class="p-4 space-y-2">
            <div class="flex items-center gap-3">
              <Smartphone class="w-5 h-5 text-primary" />
              <h4 class="font-medium flex-1">KOReader sync</h4>
              <Button size="sm" variant="outline" @click="copy(urls.koreader_sync, 'KOReader OPDS')">
                <Copy class="w-4 h-4 mr-1.5" /> Copy
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              KOReader speaks OPDS for browsing + sync. In KOReader:
              <strong>OPDS Catalog > Add Catalog</strong>. Use the OPDS URL above.
            </p>
          </Card>

          <Card class="p-4 space-y-2">
            <div class="flex items-center gap-3">
              <ChartLine class="w-5 h-5 text-primary" />
              <h4 class="font-medium flex-1">Reading statistics</h4>
              <Button size="sm" variant="ghost" @click="openLink(urls.statistics)">
                <ExternalLink class="w-4 h-4" />
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              Heatmaps, streaks, pages-per-day, time-spent.
            </p>
          </Card>
        </div>
      </template>
    </div>
  </div>
</template>
