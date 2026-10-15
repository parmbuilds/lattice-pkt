<script setup lang="ts">
import { NOT_FOUND } from '../api'
import TopBar from '../components/TopBar.vue'
</script>

<template>
  <TopBar />
  <main class="px-5 py-10 md:px-10">
    <p role="alert" class="alert inline-block">{{ NOT_FOUND }}</p>
    <p class="mt-4"><RouterLink to="/">Back to your workspaces</RouterLink></p>
  </main>
</template>
