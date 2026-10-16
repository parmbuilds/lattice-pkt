<script setup lang="ts">
import { computed } from 'vue'
import { ApiError, errorText, NOT_FOUND } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import ReviewCard from '../components/ReviewCard.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { type DocumentSummary, useAction, useLoad, useWorkspace } from '../session'

const w = useWorkspace()
const docs = useLoad<{ documents: DocumentSummary[] }>(() => (w.manager ? `${w.base}/documents` : null))
const { message, run } = useAction()
const pending = computed(() => docs.data?.documents.filter((d) => d.pending_version_id) ?? [])

// Server 409 details (services/documents.py, services/intake.py) mapped to what to do next.
const conflicts: Record<string, string> = {
  'This draft is still being prepared for search. Try again shortly.':
    'Refused: this draft is still being prepared for search. Approve it once it shows Ready.',
  "This draft's extraction receipt has flags. Review it and acknowledge that receipt.":
    'Refused: the extraction receipt has flags. Review them and tick the acknowledgement before approving.',
}
const staleRefusal = (e: unknown) =>
  e instanceof ApiError && e.status === 409
    ? (conflicts[e.message] ??
      'Refused: this draft was replaced or already decided. The current pending drafts have been reloaded.')
    : errorText(e)

// Reload after every decision, refused or not, so a replaced draft is fetched again.
async function decide(action: () => Promise<unknown>, done: string) {
  await run(action, done, staleRefusal)
  docs.reload()
}
</script>

<template>
  <PageHeader
    title="Approvals"
    lede="Approving a draft makes that exact version the one search and agents retrieve. Until then, the approved version keeps serving."
  />
  <!-- Reviewing is for owners and admins; a member's own drafts are on the Documents page. -->
  <StatusMessage v-if="!w.manager" :text="NOT_FOUND" />
  <template v-else>
    <StatusMessage :text="message" />
    <LoadState :load="docs" :empty="!pending.length" empty-text="No drafts are waiting for approval." />
    <p v-if="pending.length" class="mb-3 text-sm text-graphite">
      {{ pending.length }} {{ pending.length === 1 ? 'draft' : 'drafts' }} waiting
    </p>
    <div class="grid gap-6">
      <!-- Keyed by the draft id: a replaced draft remounts and loads its own review. -->
      <ReviewCard
        v-for="d in pending"
        :key="`${d.id}:${d.pending_version_id}`"
        :doc="d"
        :base="w.base"
        :workspace-id="w.workspaceId"
        :decide="decide"
      />
    </div>
  </template>
</template>
