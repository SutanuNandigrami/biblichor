<script setup lang="ts">
import { ref } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'

const url = '/library/'
const reloadKey = ref(0)
function reload() { reloadKey.value++ }
function openExternal() {
  window.open(url, '_blank', 'noopener')
}
</script>

<template>
  <div class="h-full flex flex-col">
    <div class="flex items-center gap-3 px-6 py-3 border-b border-border bg-card/30">
      <h1 class="text-base font-semibold">Library</h1>
      <span class="text-xs text-muted-foreground">
        Calibre-Web embedded — proxied through this dashboard
      </span>
      <div class="flex-1"></div>
      <Button size="sm" variant="outline" @click="reload">Reload</Button>
      <Button size="sm" variant="ghost" @click="openExternal">
        <ExternalLink class="w-4 h-4" /> Open standalone
      </Button>
    </div>
    <div class="flex-1 min-h-0 bg-background">
      <iframe
        :key="reloadKey"
        :src="url"
        class="w-full h-full border-0"
        title="Calibre-Web Library"
        allow="fullscreen"
        sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads allow-modals"
      />
    </div>
  </div>
</template>
