<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<!-- Snippets mark matches with [[ ]]; split them into text and <mark>s, never HTML (KTD11). -->
<template>
  <template v-for="(part, i) in text.split(/\[\[(.*?)\]\]/s)" :key="i">
    <mark v-if="i % 2">{{ part }}</mark>
    <template v-else>{{ part }}</template>
  </template>
</template>
