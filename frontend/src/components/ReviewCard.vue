<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../api'
import { diffSegments } from '../diff'
import { type DocumentSummary, useLoad } from '../session'
import LoadState from './LoadState.vue'
import Stamp from './Stamp.vue'

type Review = {
  document_id: string
  pending_version_id: string
  serving_version_id: string | null
  author_id: string
  title: string
  diff: string[]
}

const props = defineProps<{
  doc: DocumentSummary
  base: string
  workspaceId: string
  decide: (action: () => Promise<unknown>, done: string) => Promise<void>
}>()

const path = computed(() => `${props.base}/documents/${props.doc.id}`)
const review = useLoad<Review>(() => `${path.value}/review`)
const segments = computed(() => diffSegments(review.data?.diff ?? []).filter((s) => s.kind !== 'file'))
const reason = ref('')

function approve(r: Review) {
  return props.decide(
    () => api(`${path.value}/approve`, 'POST', { version_id: r.pending_version_id }),
    `Approved "${r.title}"; it is now the serving version.`,
  )
}
function reject(r: Review) {
  return props.decide(
    () => api(`${path.value}/reject`, 'POST', { version_id: r.pending_version_id, reason: reason.value || null }),
    `Rejected "${r.title}"; the approved version keeps serving.`,
  )
}
</script>

<template>
  <section class="sheet" :aria-labelledby="`review-${doc.id}`">
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-rule px-5 py-4">
      <div class="min-w-0">
        <h2 :id="`review-${doc.id}`">
          <RouterLink :to="`/w/${workspaceId}/documents/${doc.id}`">{{ review.data?.title ?? doc.title }}</RouterLink>
        </h2>
        <p class="mt-0.5 text-sm text-graphite">
          By {{ doc.author_name }}. {{ doc.serving_version_id ? 'Replaces the approved version.' : 'First version.' }}
        </p>
      </div>
      <Stamp kind="draft">Awaiting approval</Stamp>
    </header>

    <div class="px-5 py-4">
      <LoadState :load="review" :empty="!review.data?.diff.length" empty-text="No text changes." />
      <div
        v-if="segments.length"
        class="overflow-x-auto rounded-[3px] border border-rule bg-white py-2 text-[0.8125rem] leading-relaxed"
        role="group"
        aria-label="Changes against the approved version"
      >
        <template v-for="(s, i) in segments" :key="i">
          <pre v-if="s.kind === 'hunk'" class="my-1 bg-film px-4 py-0.5 text-muted">{{ s.text }}</pre>
          <div v-else-if="s.kind === 'add'" class="cloud mx-1"><pre class="px-1.5 text-ink">{{ s.text }}</pre></div>
          <pre v-else-if="s.kind === 'del'" class="diff-del px-4">{{ s.text }}</pre>
          <pre v-else class="px-4 text-graphite">{{ s.text }}</pre>
        </template>
      </div>
      <p class="mt-2 text-xs text-muted">
        Added lines are clouded, removed lines are struck through. Invisible characters show as \u escapes.
      </p>
    </div>

    <footer v-if="review.data" class="flex flex-wrap items-end gap-3 border-t border-rule bg-film/60 px-5 py-4">
      <button type="button" class="btn btn-primary" @click="approve(review.data)">Approve</button>
      <label class="field min-w-60 flex-1">
        Rejection reason (optional)
        <input v-model="reason" class="input" maxlength="500" />
      </label>
      <button type="button" class="btn btn-danger" @click="reject(review.data)">Reject</button>
    </footer>
  </section>
</template>
