<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { diffSegments } from '../diff'
import { type Derivation, derivationStamp, type DocumentSummary, useLoad } from '../session'
import LoadState from './LoadState.vue'
import Stamp from './Stamp.vue'

type Review = {
  document_id: string
  pending_version_id: string
  serving_version_id: string | null
  author_id: string
  title: string
  diff: string[]
  receipt: {
    flags: { kind: string; location: string; count: number }[]
    notes?: string[]
    sha256: string
    hidden_excerpts: { kind: string; location: string; text: string }[]
  } | null
  intake: { filename: string; detected_type: string; parser: string } | null
  derivation: Derivation
}

const props = defineProps<{
  doc: DocumentSummary
  base: string
  workspaceId: string
  decide: (action: () => Promise<unknown>, done: string) => Promise<void>
}>()

const path = computed(() => `${props.base}/documents/${props.doc.id}`)
const review = useLoad<Review>(() => `${path.value}/review`)
// The worker readies drafts in the background: check again until this one is ready (R11),
// and keep checking through a failed request so one network blip does not end the wait.
let waiting = false
watch(
  () => [review.data, review.error] as const,
  ([data, error], _old, onCleanup) => {
    if (data) waiting = data.derivation.status !== 'ready'
    if (!waiting || (!data && !error)) return
    const timer = setTimeout(review.reload, 2000)
    onCleanup(() => clearTimeout(timer))
  },
)
const segments = computed(() => diffSegments(review.data?.diff ?? []).filter((s) => s.kind !== 'file'))
const reason = ref('')
const acknowledged = ref(false)
const flagged = computed(() => !!review.data?.receipt?.flags.length)
const blocker = computed(() => {
  if (review.data?.derivation.status !== 'ready') return 'Approve unlocks once the draft is ready for search.'
  if (flagged.value && !acknowledged.value) return 'Acknowledge the receipt flags to approve.'
  return ''
})

function approve(r: Review) {
  const body = { version_id: r.pending_version_id, acknowledged_receipt: r.receipt?.sha256 ?? null }
  return props.decide(
    () => api(`${path.value}/approve`, 'POST', body),
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
      <span class="flex flex-wrap gap-1">
        <Stamp kind="draft">Awaiting approval</Stamp>
        <Stamp v-if="review.data" :kind="derivationStamp(review.data.derivation).kind">
          {{ derivationStamp(review.data.derivation).label }}
        </Stamp>
      </span>
    </header>

    <section v-if="review.data?.receipt" class="grid gap-3 border-b border-rule px-5 py-4" aria-label="Extraction receipt">
      <p class="flex flex-wrap items-center gap-2 text-sm text-graphite">
        <Stamp v-if="flagged" kind="needs-ack">Needs acknowledgement</Stamp>
        <span v-if="review.data.intake">
          Uploaded from <code>{{ review.data.intake.filename }}</code> ({{ review.data.intake.detected_type }}, read by
          {{ review.data.intake.parser }})
        </span>
      </p>
      <ul v-if="review.data.receipt.flags.length" class="list-disc pl-5 text-sm">
        <li v-for="(f, i) in review.data.receipt.flags" :key="i">{{ f.kind }} at {{ f.location }} ({{ f.count }})</li>
      </ul>
      <ul v-if="review.data.receipt.notes?.length" class="text-sm text-muted">
        <li v-for="n in review.data.receipt.notes" :key="n">Note: {{ n }}</li>
      </ul>
      <div v-if="review.data.receipt.hidden_excerpts.length">
        <h3 class="caps text-graphite">Hidden content left out of the draft</h3>
        <div v-for="(h, i) in review.data.receipt.hidden_excerpts" :key="i" class="mt-2">
          <p class="text-xs text-muted">{{ h.kind }} at {{ h.location }}</p>
          <pre class="overflow-x-auto rounded-[3px] border border-rule bg-white px-3 py-2 text-[0.8125rem]">{{ h.text }}</pre>
        </div>
      </div>
      <label v-if="flagged" class="flex items-center gap-2 text-sm font-medium">
        <input v-model="acknowledged" type="checkbox" />
        I reviewed these flags and the hidden content.
      </label>
    </section>

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
      <button type="button" class="btn btn-primary" :disabled="!!blocker" @click="approve(review.data)">Approve</button>
      <p v-if="blocker" class="self-center text-sm text-graphite">{{ blocker }}</p>
      <label class="field min-w-60 flex-1">
        Rejection reason (optional)
        <input v-model="reason" class="input" maxlength="500" />
      </label>
      <button type="button" class="btn btn-danger" @click="reject(review.data)">Reject</button>
    </footer>
  </section>
</template>
