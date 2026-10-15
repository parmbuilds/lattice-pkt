<script setup lang="ts">
defineProps<{ title: string; lede?: string }>()
</script>

<template>
  <header class="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-rule pb-4">
    <div class="min-w-0 max-w-3xl">
      <h1>{{ title }}</h1>
      <p v-if="lede" class="mt-1 text-graphite">{{ lede }}</p>
    </div>
    <div v-if="$slots.default" class="flex flex-wrap items-center gap-2"><slot /></div>
  </header>
</template>
