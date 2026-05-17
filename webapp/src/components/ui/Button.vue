<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { Loader2 } from 'lucide-vue-next'

const props = defineProps<{
  variant?: 'default' | 'outline' | 'destructive' | 'ghost' | 'subtle'
  size?: 'sm' | 'md' | 'lg' | 'icon'
  loading?: boolean
  disabled?: boolean
}>()
const cls = computed(() => cn(
  'inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
  'disabled:opacity-50 disabled:pointer-events-none',
  {
    default:    'bg-primary text-primary-foreground hover:bg-primary/90',
    outline:    'border border-border bg-transparent hover:bg-accent',
    destructive:'bg-destructive text-destructive-foreground hover:bg-destructive/90',
    ghost:      'hover:bg-accent',
    subtle:     'bg-secondary text-secondary-foreground hover:bg-secondary/80',
  }[props.variant ?? 'default'],
  {
    sm:   'h-8 px-3 text-xs',
    md:   'h-9 px-4 text-sm',
    lg:   'h-10 px-6 text-sm',
    icon: 'h-9 w-9',
  }[props.size ?? 'md'],
))
</script>
<template>
  <button :class="cls" :disabled="disabled || loading">
    <Loader2 v-if="loading" class="w-4 h-4 animate-spin" />
    <slot />
  </button>
</template>
