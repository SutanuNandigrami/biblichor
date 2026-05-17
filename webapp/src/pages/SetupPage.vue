<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { CheckCircle2, XCircle, ExternalLink, Activity } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import { api } from '@/composables/useApi'

type Setup = {
  sources_count: number
  smtp_configured: boolean
  kindle_recipient: string
  smtp_user: string
  calibre_present: boolean
  calibre_version: string
  last_smtp_probe: string | null
}
const data = ref<Setup | null>(null)
const probing = ref(false)

async function load() { data.value = await api<Setup>('/api/setup') }
onMounted(load)

async function probe() {
  probing.value = true
  try {
    await api('/api/setup/probe-smtp', { method: 'POST' })
    await load()
  } finally { probing.value = false }
}
</script>

<template>
  <div class="p-6 space-y-4 max-w-3xl" v-if="data">
    <h1 class="text-2xl font-semibold tracking-tight">Setup</h1>
    <p class="text-sm text-muted-foreground">Quick checklist of what's wired up.</p>

    <Card class="p-4 flex items-start gap-3">
      <component :is="data.sources_count > 0 ? CheckCircle2 : XCircle"
        :class="data.sources_count > 0 ? 'text-emerald-400' : 'text-red-400'" class="w-5 h-5 mt-0.5" />
      <div class="flex-1">
        <div class="font-medium">Sources</div>
        <div class="text-sm text-muted-foreground">
          {{ data.sources_count }} configured.
          <RouterLink to="/sources" class="text-primary underline">Manage</RouterLink>
        </div>
      </div>
    </Card>

    <Card class="p-4 flex items-start gap-3">
      <component :is="data.smtp_configured ? CheckCircle2 : XCircle"
        :class="data.smtp_configured ? 'text-emerald-400' : 'text-red-400'" class="w-5 h-5 mt-0.5" />
      <div class="flex-1">
        <div class="font-medium">SMTP credentials</div>
        <div class="text-sm text-muted-foreground">
          {{ data.smtp_configured ? `Configured for ${data.smtp_user}` : 'SMTP user + password required.' }}
          <RouterLink to="/settings" class="text-primary underline">Edit</RouterLink>
        </div>
      </div>
    </Card>

    <Card class="p-4 flex items-start gap-3">
      <component :is="data.kindle_recipient ? CheckCircle2 : XCircle"
        :class="data.kindle_recipient ? 'text-emerald-400' : 'text-red-400'" class="w-5 h-5 mt-0.5" />
      <div class="flex-1">
        <div class="font-medium">Kindle recipient</div>
        <div class="text-sm text-muted-foreground">
          <code v-if="data.kindle_recipient">{{ data.kindle_recipient }}</code>
          <span v-else>Not set.</span>
        </div>
      </div>
    </Card>

    <Card class="p-4 flex items-start gap-3 border-amber-500/30" v-if="data.smtp_user">
      <ExternalLink class="w-5 h-5 mt-0.5 text-amber-400" />
      <div class="flex-1">
        <div class="font-medium text-amber-200">Whitelist at Amazon</div>
        <div class="text-sm text-muted-foreground">
          Add <code class="bg-secondary px-2 py-0.5 rounded text-amber-200">{{ data.smtp_user }}</code>
          to your
          <a href="https://www.amazon.com/myk" target="_blank" class="text-primary underline inline-flex items-center gap-1">
            Approved Personal Document E-mail List <ExternalLink class="w-3 h-3" />
          </a>.
        </div>
      </div>
    </Card>

    <Card class="p-4 flex items-start gap-3">
      <CheckCircle2 v-if="data.calibre_present" class="w-5 h-5 mt-0.5 text-emerald-400" />
      <XCircle v-else class="w-5 h-5 mt-0.5 text-red-400" />
      <div class="flex-1">
        <div class="font-medium">Calibre</div>
        <div class="text-sm text-muted-foreground">
          {{ data.calibre_present ? data.calibre_version : 'Not installed (apt install calibre)' }}
        </div>
      </div>
    </Card>

    <Card class="p-4 flex items-start gap-3">
      <Activity class="w-5 h-5 mt-0.5 text-muted-foreground" />
      <div class="flex-1">
        <div class="font-medium">Outbound SMTP reachability</div>
        <div class="text-sm text-muted-foreground">
          <code v-if="data.last_smtp_probe">{{ data.last_smtp_probe }}</code>
          <span v-else>not probed yet</span>
        </div>
      </div>
      <Button :loading="probing" variant="outline" size="sm" @click="probe">Probe</Button>
    </Card>
  </div>
</template>
