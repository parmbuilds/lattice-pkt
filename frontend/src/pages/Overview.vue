<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, errorText } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Snippet from '../components/Snippet.vue'
import { type DocumentSummary, useLoad, useWorkspace, when } from '../session'

type Result = {
  version_id: string
  document_id: string
  title: string
  snippet: string
  approved_at: string
  approved_by: string | null
  spans: { start: number; end: number }[]
}
type Searched = { occurred_at: string; source: 'search' | 'ask' | 'agent'; token_name: string | null; query: string; count: number }
type Turn = { q: string; results?: Result[]; error?: string }

const SOURCE = { search: 'Search', ask: 'Ask', agent: 'Agent' }
const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

const w = useWorkspace()
const docs = useLoad<{ documents: DocumentSummary[] }>(() => `${w.base}/documents`)
const invites = useLoad<{ invites: unknown[] }>(() => (w.manager ? `${w.base}/invites` : null))
const history = useLoad<{ searches: Searched[] }>(() => `${w.base}/search/history`)

// Counted from the list the API already filtered for this person, so nothing hidden is ever counted.
const counts = computed(() => {
  const list = docs.data?.documents ?? []
  return {
    approved: list.filter((d) => d.serving_version_id && !d.withdrawn).length,
    drafts: list.filter((d) => d.pending_version_id).length,
    withdrawn: list.filter((d) => d.withdrawn).length,
    yours: list.filter((d) => d.author_id === w.userId).length,
  }
})
const yourDrafts = computed(() => docs.data?.documents.filter((d) => d.pending_version_id && d.author_id === w.userId) ?? [])
const openInvites = computed(() => invites.data?.invites.length ?? 0)
const nothingWaits = computed(() => (w.manager ? !counts.value.drafts && !openInvites.value : !yourDrafts.value.length))

const question = ref('')
const turns = ref<Turn[]>([])
const asking = ref(false)

async function ask(q = question.value.trim()) {
  if (!q || asking.value) return
  asking.value = true
  question.value = ''
  const i = turns.value.push({ q }) - 1
  try {
    const params = new URLSearchParams({ q, via: 'ask' })
    turns.value[i].results = (await api<{ results: Result[] }>(`${w.base}/search?${params}`)).results
  } catch (e) {
    turns.value[i].error = errorText(e)
  }
  asking.value = false
  history.reload()
}

// Enter asks and Shift+Enter adds a line, as in chat; an IME composition's Enter only confirms the text.
function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  e.preventDefault()
  void ask()
}
</script>

<template>
  <PageHeader
    title="Overview"
    :lede="`What you can read in ${w.workspaceName}, what waits on you, and what you and your agents asked.`"
  >
    <RouterLink :to="`/w/${w.workspaceId}/documents`" class="btn btn-primary no-underline">Go to documents</RouterLink>
  </PageHeader>

  <section aria-label="Waiting on you" class="sheet mb-4 flex flex-wrap items-baseline gap-x-5 gap-y-1 px-3.5 py-2.5">
    <span class="caps text-graphite">Waiting on you</span>
    <template v-if="docs.data">
      <template v-if="w.manager">
        <RouterLink v-if="counts.drafts" :to="`/w/${w.workspaceId}/approvals`">{{ plural(counts.drafts, 'draft') }} to review</RouterLink>
        <span v-if="openInvites">
          <RouterLink :to="`/w/${w.workspaceId}/members`">{{ plural(openInvites, 'invite link') }}</RouterLink>
          <span class="text-graphite"> not used yet</span>
        </span>
      </template>
      <template v-else>
        <span v-for="d in yourDrafts.slice(0, 3)" :key="d.id">
          <RouterLink :to="`/w/${w.workspaceId}/documents/${d.id}`">{{ d.title }}</RouterLink>
          <span class="text-graphite"> is waiting for an admin to approve it</span>
        </span>
      </template>
      <span v-if="nothingWaits" class="text-graphite">Nothing waits on you.</span>
    </template>
  </section>

  <LoadState :load="docs" :empty="false" empty-text="" />
  <dl v-if="docs.data" class="titleblock mb-6 sm:grid-cols-4" aria-label="Documents you can read">
    <div>
      <dt>Approved · serving</dt>
      <dd><span class="mr-1.5 text-[1.0625rem] font-semibold">{{ counts.approved }}</span><span class="font-normal text-graphite">search and agents</span></dd>
    </div>
    <div>
      <dt>Drafts</dt>
      <dd><span class="mr-1.5 text-[1.0625rem] font-semibold">{{ counts.drafts }}</span><span class="font-normal text-graphite">not serving yet</span></dd>
    </div>
    <div>
      <dt>Withdrawn</dt>
      <dd><span class="mr-1.5 text-[1.0625rem] font-semibold">{{ counts.withdrawn }}</span><span class="font-normal text-graphite">serve nothing</span></dd>
    </div>
    <div>
      <dt>Written by you</dt>
      <dd><span class="mr-1.5 text-[1.0625rem] font-semibold">{{ counts.yours }}</span></dd>
    </div>
  </dl>

  <div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
    <section class="sheet" aria-labelledby="ask-h">
      <h2 id="ask-h" class="border-b border-rule px-3.5 py-3">Ask Lattice</h2>
      <div aria-live="polite" class="grid gap-4" :class="{ 'p-3.5': turns.length }">
        <template v-for="(t, i) in turns" :key="i">
          <p class="ml-auto max-w-xl rounded-[4px] bg-ink px-3.5 py-2 text-sheet">{{ t.q }}</p>
          <p v-if="t.error" role="alert" class="alert">{{ t.error }}</p>
          <p v-else-if="!t.results" class="text-muted">Searching…</p>
          <p v-else-if="!t.results.length" class="rounded-[4px] border border-dashed border-rule-strong px-3.5 py-3 text-graphite">
            No approved passage you can read matches. Try other words, or
            <RouterLink :to="`/w/${w.workspaceId}/search`">search documents</RouterLink>.
          </p>
          <div v-else class="grid gap-2">
            <p class="text-sm text-graphite">Found in {{ plural(t.results.length, 'approved document') }} you can read</p>
            <article v-for="r in t.results" :key="r.version_id" class="sheet px-3.5 py-3">
              <header class="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <RouterLink :to="`/w/${w.workspaceId}/documents/${r.document_id}`" class="font-semibold">{{ r.title }}</RouterLink>
                <span class="stamp stamp-approved">Approved</span>
              </header>
              <blockquote class="max-w-[70ch] border-l border-rule-strong pl-3"><Snippet :text="r.snippet" /></blockquote>
              <p class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-graphite">
                <span>{{ when(r.approved_at) }}{{ r.approved_by ? ` by ${r.approved_by}` : '' }}</span>
                <code v-if="r.spans.length" class="text-xs">chars {{ r.spans[0].start }}–{{ r.spans[0].end }}</code>
              </p>
            </article>
          </div>
        </template>
      </div>
      <form class="grid gap-2 p-3.5" :class="{ 'border-t border-rule': turns.length }" @submit.prevent="ask()">
        <label class="sr-only" for="ask-input">Ask Lattice</label>
        <textarea
          id="ask-input"
          v-model="question"
          rows="3"
          class="input min-h-24 resize-y font-sans text-base"
          placeholder="Ask a question, e.g. how do we fail over the payment gateway?"
          required
          @keydown.enter.exact="onEnter"
        />
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm text-graphite">Answers quote approved documents. Nothing is generated.</p>
          <button type="submit" class="btn btn-primary min-h-10 px-6" :disabled="asking">Ask</button>
        </div>
      </form>
    </section>

    <section class="sheet" aria-labelledby="history-h">
      <div class="border-b border-rule px-3.5 py-3">
        <h2 id="history-h">Your last 10 queries</h2>
        <p class="text-sm text-graphite">From Search, Ask and your agents. Select one to ask it again.</p>
      </div>
      <div class="px-3.5"><LoadState :load="history" :empty="!history.data?.searches.length" empty-text="" /></div>
      <p v-if="history.data && !history.data.searches.length" class="px-3.5 py-4 text-graphite">
        <strong class="block font-semibold text-ink">No queries yet.</strong>
        Your searches, Ask questions and your agents' queries show here, newest first, so you can run them again.
      </p>
      <ul v-else-if="history.data" class="divide-y divide-rule">
        <li v-for="(s, i) in history.data.searches" :key="i">
          <button
            type="button"
            class="grid w-full gap-0.5 px-3.5 py-2 text-left hover:bg-film/60 disabled:cursor-not-allowed"
            :disabled="asking || !s.query"
            :aria-label="`Ask again: ${s.query}`"
            @click="ask(s.query)"
          >
            <span class="font-medium break-words">{{ s.query || '(empty query)' }}</span>
            <span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-graphite">
              <span class="stamp stamp-plain">{{ SOURCE[s.source] }}</span>
              <code v-if="s.token_name" class="text-xs">{{ s.token_name }}</code>
              <time :datetime="s.occurred_at">{{ when(s.occurred_at) }}</time>
              <span :class="{ 'text-muted': !s.count }">{{ s.count ? plural(s.count, 'match', 'matches') : 'No matches' }}</span>
            </span>
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
