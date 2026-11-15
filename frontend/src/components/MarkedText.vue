<script setup lang="ts">
import { computed } from 'vue'

// Text with the server's match offsets wrapped in <mark>. Rendered as text, never HTML (KTD11).
const props = defineProps<{ text: string; marks: [number, number][] }>()

const parts = computed(() => {
  const out: { text: string; marked: boolean }[] = []
  let at = 0
  for (const [start, end] of [...props.marks].sort((a, b) => a[0] - b[0])) {
    if (start > at) out.push({ text: props.text.slice(at, start), marked: false })
    out.push({ text: props.text.slice(start, end), marked: true })
    at = end
  }
  if (at < props.text.length) out.push({ text: props.text.slice(at), marked: false })
  return out
})
</script>

<template>
  <template v-for="(part, i) in parts" :key="i">
    <mark v-if="part.marked">{{ part.text }}</mark>
    <template v-else>{{ part.text }}</template>
  </template>
</template>
