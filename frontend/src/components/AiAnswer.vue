<script setup lang="ts">
import { when } from '../session'

export type AiPassage = {
  id?: string // "P1"… from the stream; a reopened turn's passages are listed in the same order
  document_id: string
  version_id: string
  title: string
  start: number
  end: number
  text: string
  approved_by?: string | null
  approved_at?: string | null
}
export type AiSentence = { text: string; markers: string[]; supported: boolean }
export type AiTurn = {
  q: string
  sentences: AiSentence[]
  passages?: AiPassage[]
  notices: string[] // left-out turns and dropped context; counts only, never a document (R15)
  notice?: string // replaces the sentences: not covered, not saved, unavailable or stopped
  error?: string
  streaming?: boolean
}

defineProps<{ turn: AiTurn; workspaceId: string }>()
</script>

<!-- Model and document text render as text only, never HTML. -->
<template>
  <p class="ml-auto max-w-xl rounded-[4px] bg-ink px-3.5 py-2 text-sheet">{{ turn.q }}</p>
  <p v-for="n in turn.notices" :key="n" class="text-sm text-graphite">{{ n }}</p>
  <p v-if="turn.error" role="alert" class="alert">{{ turn.error }}</p>
  <p v-else-if="turn.notice" class="rounded-[4px] border border-dashed border-rule-strong px-3.5 py-3 text-graphite">{{ turn.notice }}</p>
  <p v-else-if="turn.streaming && !turn.sentences.length" class="text-muted">{{ turn.passages ? 'Writing…' : 'Searching…' }}</p>
  <ul v-else class="grid max-w-[70ch] gap-1.5">
    <li v-for="(s, i) in turn.sentences" :key="i" :class="{ 'text-graphite': !s.supported }">
      {{ s.text }}
      <code v-for="m in s.markers" :key="m" class="ml-1 text-xs text-graphite">{{ m }}</code>
      <span v-if="!s.supported" class="stamp stamp-needs-ack ml-1">Not supported by your documents</span>
    </li>
  </ul>
  <div v-if="turn.passages?.length && !turn.error" class="grid gap-2">
    <p class="text-sm text-graphite">Passages sent with your question</p>
    <article v-for="(p, n) in turn.passages" :key="`${p.version_id}-${p.start}`" class="sheet px-3.5 py-3">
      <header class="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <code class="text-xs text-graphite">{{ p.id ?? `P${n + 1}` }}</code>
        <RouterLink :to="`/w/${workspaceId}/documents/${p.document_id}`" class="font-semibold">{{ p.title }}</RouterLink>
        <span class="stamp stamp-approved">Approved</span>
      </header>
      <blockquote class="max-w-[70ch] border-l border-rule-strong pl-3">{{ p.text }}</blockquote>
      <p class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-graphite">
        <span v-if="p.approved_at">{{ when(p.approved_at) }}{{ p.approved_by ? ` by ${p.approved_by}` : '' }}</span>
        <code class="text-xs">chars {{ p.start }}–{{ p.end }}</code>
      </p>
    </article>
  </div>
</template>
