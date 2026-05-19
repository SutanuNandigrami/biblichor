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

const toast = useToast()
const urls = ref<BookOrbitUrls | null>(null)
const enabled = ref<boolean>(false)
const loading = ref<boolean>(true)

onMounted(async () => {
  try {
    const settings = await api<any>('/api/settings')
    enabled.value = !!settings?.bookorbit?.enabled
    urls.value = settings?.bookorbit?.urls ?? null
  } finally {
    loading.value = false
  }
})

const bookOrbitUrl = computed(() => {
  if (urls.value?.base) return urls.value.base
  const proto = window.location.protocol
  const host = window.location.hostname
  return `${proto}//${host}:3000`
})

function openBookOrbit() {
  window.open(bookOrbitUrl.value, '_blank', 'noopener')
}

function open(url: string) {
  window.open(url, '_blank', 'noopener')
}

async function copy(text: string, label: string) {
  await navigator.clipboard.writeText(text)
  toast.success(`${label} URL copied`)
}
</script>

<template>
  <div class="h-full overflow-y-auto px-6 py-8">
    <div class="max-w-3xl mx-auto space-y-6">
      <header class="text-center space-y-2">
        <BookOpen class="w-14 h-14 mx-auto text-primary" />
        <h1 class="text-2xl font-semibold">Library</h1>
        <p class="text-sm text-muted-foreground max-w-xl mx-auto leading-relaxed">
          Your library lives in <strong>BookOrbit</strong> — a dedicated reader with built-in EPUB/PDF readers,
          Kobo auto-push, KOReader two-way progress, OPDS feed, and reading statistics. biblichor stays focused
          on getting books; BookOrbit owns the reading experience.
        </p>
      </header>

      <Card v-if="loading" class="p-8 text-center text-sm text-muted-foreground">
        Loading library URLs…
      </Card>

      <template v-else-if="urls">
        <!-- Primary action -->
        <Card class="p-5 space-y-3">
          <div class="flex items-center gap-3">
            <BookOpen class="w-5 h-5 text-primary" />
            <h2 class="font-semibold text-base flex-1">Open BookOrbit</h2>
            <Button size="lg" @click="openBookOrbit">
              <ExternalLink class="w-4 h-4 mr-2" />
              Launch
            </Button>
          </div>
          <p class="text-xs text-muted-foreground font-mono break-all">{{ urls.dashboard }}</p>
          <p v-if="!enabled" class="text-[11px] text-amber-500 dark:text-amber-400">
            Pipeline integration is <strong>disabled</strong> — books won’t auto-land here until you run
            <code class="font-mono">biblichor bookorbit-setup</code>.
          </p>
        </Card>

        <!-- E-reader sync surfaces -->
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
              <Button size="sm" variant="ghost" @click="open(urls.opds_catalog)">
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
              <Button size="sm" variant="ghost" @click="open(urls.dashboard + '/settings/kobo')">
                <ExternalLink class="w-4 h-4" />
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              Set up Kobo auto-push via BookOrbit's Settings → Kobo. Each device gets its own sync token; once
              registered, new books drop into your Kobo automatically over Wi-Fi.
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
              KOReader speaks OPDS for browsing + sync. In KOReader: <strong>OPDS Catalog → Add Catalog</strong>.
              Use the OPDS URL above; KOReader will sync reading progress two-way through the same channel.
            </p>
          </Card>

          <Card class="p-4 space-y-2">
            <div class="flex items-center gap-3">
              <ChartLine class="w-5 h-5 text-primary" />
              <h4 class="font-medium flex-1">Reading statistics</h4>
              <Button size="sm" variant="ghost" @click="open(urls.statistics)">
                <ExternalLink class="w-4 h-4" />
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground">
              Heatmaps, streaks, pages-per-day, time-spent. Surfaces what you've actually read versus what
              biblichor has just delivered.
            </p>
          </Card>
        </div>

        <!-- First-run + advanced -->
        <details class="text-xs text-muted-foreground rounded-md border border-border p-3">
          <summary class="cursor-pointer hover:text-foreground select-none">
            First-run / advanced setup
          </summary>
          <div class="mt-3 space-y-2 leading-relaxed">
            <p>
              <code class="font-mono">biblichor bookorbit-setup --admin-email you@example.com</code>
              creates the admin account + watched library, then flips
              <code class="font-mono">bookorbit.enabled</code> in <code>config.yaml</code>.
            </p>
            <p>
              <code class="font-mono">biblichor migrate-to-bookorbit</code> walks an existing
              <code>data/calibre-library/</code> and copies every book file into the BookOrbit library.
            </p>
            <p>
              <code class="font-mono">biblichor bookorbit-doctor</code> probes BookOrbit's API before/after
              upgrades to catch drift in the endpoints biblichor relies on.
            </p>
          </div>
        </details>
      </template>
    </div>
  </div>
</template>
