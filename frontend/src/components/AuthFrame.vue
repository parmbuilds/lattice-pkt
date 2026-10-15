<script setup lang="ts">
import Wordmark from './Wordmark.vue'

const facts = [
  ['Approved only', 'Search and agents retrieve approved versions, never drafts.'],
  ['Exact version', 'An approval covers one immutable version of a document.'],
  ['On the record', 'Every decision and every denial is written to the audit trail.'],
]
</script>

<template>
  <div class="grid min-h-dvh md:grid-cols-[minmax(0,1fr)_minmax(0,32rem)]">
    <aside class="flex flex-col justify-between gap-10 bg-ink px-6 py-8 text-rail-text md:px-12 md:py-12">
      <Wordmark />
      <div class="max-w-md">
        <p class="text-[2rem] font-semibold leading-[1.15] tracking-[-0.02em] md:text-[2.5rem]">
          Connected knowledge for AI agents.
        </p>
        <dl class="mt-8 hidden border-[1.5px] border-rail-muted/60 md:block">
          <div v-for="[term, text] in facts" :key="term" class="border-b border-rail-muted/40 px-4 py-3 last:border-b-0">
            <dt class="caps text-rail-muted">{{ term }}</dt>
            <dd class="mt-0.5 text-rail-text">{{ text }}</dd>
          </div>
        </dl>
      </div>
      <p class="hidden text-sm text-rail-muted md:block">Governed knowledge store · MCP</p>
    </aside>
    <main class="flex items-center justify-center px-5 py-10 md:px-12">
      <div class="w-full max-w-sm"><slot /></div>
    </main>
  </div>
</template>
