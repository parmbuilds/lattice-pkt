<script setup lang="ts">
import { me } from './session'
</script>

<template>
  <p v-if="me === undefined" class="p-10 text-muted">Loading…</p>
  <RouterView v-else />
</template>
