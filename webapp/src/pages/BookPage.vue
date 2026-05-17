<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import BookDetailDrawer from '@/components/BookDetailDrawer.vue'
const route = useRoute()
const router = useRouter()
</script>
<template>
  <BookDetailDrawer v-if="route.params.id" :id="Number(route.params.id)" @close="router.push('/queue')" />
</template>
