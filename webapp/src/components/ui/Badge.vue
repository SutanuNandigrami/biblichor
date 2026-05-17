<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
const props = defineProps<{ variant?: 'default' | 'success' | 'warning' | 'danger' | 'muted' | 'info' }>()
const cls = computed(() => cn(
  'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
  {
    default: 'bg-secondary text-secondary-foreground ring-border',
    success: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
    warning: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
    danger:  'bg-red-500/10  text-red-300  ring-red-500/30',
    info:    'bg-blue-500/10 text-blue-300 ring-blue-500/30',
    muted:   'bg-muted text-muted-foreground ring-border',
  }[props.variant ?? 'default'],
))
</script>
<template><span :class="cls"><slot /></span></template>
