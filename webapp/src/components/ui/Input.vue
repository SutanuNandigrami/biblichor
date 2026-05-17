<script setup lang="ts">
import { cn } from '@/lib/utils'
defineProps<{ modelValue?: string | number; placeholder?: string; type?: string }>()
defineEmits(['update:modelValue'])
</script>
<template>
  <input
    :type="type ?? 'text'"
    :value="modelValue"
    :placeholder="placeholder"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    :class="cn(
      'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm',
      'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      'disabled:opacity-50',
    )"
  />
</template>
