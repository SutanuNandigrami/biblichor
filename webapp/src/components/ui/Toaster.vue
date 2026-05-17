<script setup lang="ts">
import { useToast } from '@/composables/useToast'
import { CheckCircle2, XCircle, Info } from 'lucide-vue-next'
const { state, dismiss } = useToast()
const icons = { success: CheckCircle2, error: XCircle, info: Info }
const cls = { success: 'border-emerald-500/40', error: 'border-red-500/40', info: 'border-border' }
</script>
<template>
  <div class="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 max-w-sm">
    <transition-group enter-active-class="transition-all duration-200"
      enter-from-class="opacity-0 translate-x-4" leave-active-class="transition-all duration-150"
      leave-to-class="opacity-0">
      <div v-for="t in state.items" :key="t.id"
        :class="['flex items-start gap-3 p-3 rounded-md border bg-card shadow-lg', cls[t.kind]]">
        <component :is="icons[t.kind]" class="w-5 h-5 mt-0.5"
          :class="{ 'text-emerald-400': t.kind === 'success', 'text-red-400': t.kind === 'error', 'text-blue-400': t.kind === 'info' }" />
        <div class="flex-1 min-w-0">
          <div class="text-sm font-medium">{{ t.title }}</div>
          <div v-if="t.message" class="text-xs text-muted-foreground mt-0.5">{{ t.message }}</div>
        </div>
        <button class="text-muted-foreground hover:text-foreground" @click="dismiss(t.id)">×</button>
      </div>
    </transition-group>
  </div>
</template>
