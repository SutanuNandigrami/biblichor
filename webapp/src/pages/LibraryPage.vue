<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ExternalLink, BookOpen } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { api } from '@/composables/useApi'

const configured = ref<string | null>(null)

onMounted(async () => {
  try {
    const settings = await api<any>('/api/settings')
    const u = settings?.bookorbit?.url
    if (typeof u === 'string' && u.length > 0) {
      configured.value = u
    }
  } catch {
    // best-effort; fall back to runtime guess below
  }
})

const bookOrbitUrl = computed(() => {
  if (configured.value) return configured.value
  // Fallback: same host, default port 3000
  const proto = window.location.protocol
  const host = window.location.hostname
  return `${proto}//${host}:3000`
})

function openBookOrbit() {
  window.open(bookOrbitUrl.value, '_blank', 'noopener')
}
</script>

<template>
  <div class="h-full flex flex-col items-center justify-center px-6 py-12">
    <div class="max-w-md w-full text-center space-y-6">
      <BookOpen class="w-16 h-16 mx-auto text-primary" />
      <h1 class="text-2xl font-semibold">Library</h1>
      <p class="text-sm text-muted-foreground leading-relaxed">
        Your library lives in <strong>BookOrbit</strong> — a dedicated reader
        with Kobo sync, KOReader two-way progress, OPDS, and built-in EPUB/PDF
        readers. biblichor stays focused on getting books; BookOrbit owns
        the reading experience.
      </p>
      <div class="space-y-2">
        <Button size="lg" class="w-full" @click="openBookOrbit">
          <ExternalLink class="w-4 h-4 mr-2" />
          Open BookOrbit
        </Button>
        <p class="text-xs text-muted-foreground font-mono">{{ bookOrbitUrl }}</p>
        <p v-if="!configured" class="text-[11px] text-muted-foreground italic">
          (default port — set <code class="font-mono">BOOKORBIT_URL</code> in .env to override)
        </p>
      </div>
      <details class="text-xs text-muted-foreground text-left">
        <summary class="cursor-pointer hover:text-foreground">First-run setup</summary>
        <pre class="mt-2 bg-muted/50 p-3 rounded text-[11px] leading-snug">biblichor bookorbit-setup --admin-email you@example.com</pre>
        <p class="mt-2">
          Creates the admin account + watched library + flips
          <code class="font-mono">bookorbit.enabled</code> in your
          <code class="font-mono">config.yaml</code>. Then run
          <code class="font-mono">migrate-to-bookorbit</code> to import an
          existing Calibre library.
        </p>
      </details>
    </div>
  </div>
</template>
