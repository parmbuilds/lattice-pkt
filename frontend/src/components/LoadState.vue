<script setup lang="ts">
import type { Loaded } from '../session'

defineProps<{ load: Loaded<unknown>; empty: boolean; emptyText: string }>()
</script>

<template>
  <p v-if="load.loading && !load.data" class="py-6 text-muted">Loading…</p>
  <p v-else-if="load.error" role="alert" class="alert">{{ load.error }}</p>
  <p v-else-if="load.data && empty" class="py-6 text-muted">{{ emptyText }}</p>
</template>
