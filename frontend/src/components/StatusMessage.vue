<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<!-- Always rendered so screen readers announce changes to it. -->
<template>
  <p class="status" role="status" aria-live="polite">{{ text }}</p>
</template>
