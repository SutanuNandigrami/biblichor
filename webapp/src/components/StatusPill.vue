<script setup lang="ts">
import Badge from '@/components/ui/Badge.vue'

const props = defineProps<{ status: string }>()
const map: Record<string, 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted'> = {
  queued: 'info', searching: 'info', downloading: 'info', converting: 'info', sending: 'info',
  sent: 'success', needs_review: 'warning', failed: 'danger', skipped: 'muted',
}
</script>
<template>
  <Badge :variant="(map[props.status] as any) ?? 'default'">{{ props.status }}</Badge>
</template>
