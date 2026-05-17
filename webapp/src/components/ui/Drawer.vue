<script setup lang="ts">
import { X } from 'lucide-vue-next'
const props = defineProps<{ open: boolean; title?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>
<template>
  <teleport to="body">
    <transition
      enter-active-class="transition-opacity duration-150"
      enter-from-class="opacity-0" leave-active-class="transition-opacity duration-150"
      leave-to-class="opacity-0">
      <div v-if="props.open" class="fixed inset-0 bg-black/60 z-40" @click="emit('close')" />
    </transition>
    <transition
      enter-active-class="transition-transform duration-200"
      enter-from-class="translate-x-full" leave-active-class="transition-transform duration-200"
      leave-to-class="translate-x-full">
      <aside v-if="props.open"
        class="fixed top-0 right-0 h-screen w-full sm:w-[640px] bg-card border-l border-border z-50 overflow-y-auto">
        <header class="sticky top-0 bg-card border-b border-border h-14 px-4 flex items-center justify-between z-10">
          <h2 class="font-semibold">{{ title }}</h2>
          <button class="p-1 rounded hover:bg-accent" @click="emit('close')"><X class="w-4 h-4" /></button>
        </header>
        <div class="p-4">
          <slot />
        </div>
      </aside>
    </transition>
  </teleport>
</template>
