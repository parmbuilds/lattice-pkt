<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, errorText, postStream } from '../api'
import AiAnswer, { type AiPassage, type AiSentence, type AiTurn } from '../components/AiAnswer.vue'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Snippet from '../components/Snippet.vue'
import { type DocumentSummary, useAction, useLoad, useWorkspace, when } from '../session'
import type { AiSettingsView } from './AiSettings.vue'

type Result = {
  version_id: string
  document_id: string
  title: string
  snippet: string
  approved_at: string
  approved_by: string | null
  spans: { start: number; end: number }[]
}
type Searched = {
  occurred_at: string
  source: 'search' | 'ask' | 'agent'
  conversation_id: string | null
  token_name: string | null
  query: string
  count: number
}
type Turn = { q: string; results?: Result[]; error?: string }
type Conversation = { id: string; title: string; created_at: string; last_turn_at: string }
type SavedTurn = { question: string; sentences: AiSentence[]; outcome: string; passages: AiPassage[] }
type OpenedConversation = Conversation & { turns: SavedTurn[]; left_out: number; summary_dropped: boolean }

const NOT_COVERED = "Your documents don't cover this"
const STOPPED = 'Stopped. This answer was not saved.'

const SOURCE = { search: 'Search', ask: 'Ask', agent: 'Agent' }
const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

const w = useWorkspace()
const docs = useLoad<{ documents: DocumentSummary[] }>(() => `${w.base}/documents`)
const invites = useLoad<{ invites: unknown[] }>(() => (w.manager ? `${w.base}/invites` : null))
const history = useLoad<{ searches: Searched[] }>(() => `${w.base}/search/history`)
const ai = useLoad<AiSettingsView>(() => `${w.base}/ai/settings`)
const conversations = useLoad<{ conversations: Conversation[] }>(() => `${w.base}/conversations`)
const aiOn = computed(() => !!ai.data?.enabled)
const aiLabel = computed(() => {
  const s = ai.data
  const p = s?.providers.find((x) => x.id === s.provider_id)
  return `${p?.models.find((m) => m.id === s?.model)?.label ?? s?.model} from ${p?.label ?? s?.provider_id}`
})

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
// The open conversation; with AI off it is read-only (R31).
const open = ref<{ id: string; title: string; notices: string[] } | null>(null)
const aiTurns = ref<AiTurn[]>([])
const answering = ref<AbortController | null>(null)
const { message: conversationError, run } = useAction()

function guardNotices(leftOut: number, summaryDropped: boolean) {
  const notices = []
  if (leftOut)
    notices.push(
      `${plural(leftOut, 'earlier turn')} ${leftOut === 1 ? 'is' : 'are'} left out because a document ${leftOut === 1 ? 'it' : 'they'} used is no longer available to you.`,
    )
  if (summaryDropped) notices.push('Earlier context was dropped because a document it drew on is no longer available to you')
  return notices
}

async function answer(q: string) {
  asking.value = true
  question.value = ''
  const t = aiTurns.value[aiTurns.value.push({ q, sentences: [], notices: [], streaming: true }) - 1]
  const controller = (answering.value = new AbortController())
  try {
    if (!open.value) {
      const c = await api<Conversation>(`${w.base}/conversations`, 'POST', { title: q.slice(0, 200) })
      open.value = { id: c.id, title: c.title, notices: [] }
    }
    const events = postStream(`${w.base}/conversations/${open.value.id}/turns`, { question: q }, controller.signal)
    for await (const { event, data } of events) {
      if (event === 'passages') Object.assign(t, { passages: data.passages, notices: guardNotices(data.left_out, data.summary_dropped) })
      else if (event === 'sentence') t.sentences.push(data)
      else if (event !== 'done') {
        // not_covered, unavailable and discarded notices replace the sentences; a discarded turn's passages may be unreadable now (R29).
        Object.assign(t, { notice: data.notice }, event === 'discarded' ? { passages: [] } : {})
      }
    }
  } catch (e) {
    if (controller.signal.aborted) t.notice = STOPPED
    else t.error = errorText(e)
  }
  t.streaming = false
  answering.value = null
  asking.value = false
  history.reload()
  conversations.reload()
}

const reopen = (id: string) =>
  run(async () => {
    const c = await api<OpenedConversation>(`${w.base}/conversations/${id}`)
    open.value = { id: c.id, title: c.title, notices: guardNotices(c.left_out, c.summary_dropped) }
    aiTurns.value = c.turns.map((t) => ({
      q: t.question,
      sentences: t.sentences,
      notice: t.outcome === 'not_covered' ? NOT_COVERED : undefined,
      passages: t.passages,
      notices: [],
    }))
  })

function close() {
  open.value = null
  aiTurns.value = []
}

const remove = (c: Conversation) =>
  window.confirm(`Delete the conversation "${c.title}"? Its questions and answers are deleted for good.`) &&
  run(() => api(`${w.base}/conversations/${c.id}`, 'DELETE')).then((ok) => {
    if (!ok) return
    if (open.value?.id === c.id) close()
    conversations.reload()
    history.reload()
  })

async function ask(q = question.value.trim()) {
  if (!q || asking.value) return
  if (aiOn.value) return answer(q)
  close()
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
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-3.5 py-3">
        <div class="min-w-0">
          <h2 id="ask-h">Ask Lattice</h2>
          <p v-if="open" class="text-sm break-words text-graphite">{{ open.title }}</p>
        </div>
        <button v-if="open" type="button" class="btn" :disabled="asking" @click="close">
          {{ aiOn ? 'New conversation' : 'Close conversation' }}
        </button>
      </div>
      <p v-if="conversationError" role="alert" class="alert m-3.5">{{ conversationError }}</p>
      <div v-if="open || aiOn" aria-live="polite" class="grid gap-4" :class="{ 'p-3.5': open || aiTurns.length }">
        <p v-for="n in open?.notices" :key="n" class="text-sm text-graphite">{{ n }}</p>
        <AiAnswer v-for="(t, i) in aiTurns" :key="i" :turn="t" :workspace-id="w.workspaceId" />
      </div>
      <div v-else aria-live="polite" class="grid gap-4" :class="{ 'p-3.5': turns.length }">
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
      <p v-if="open && !aiOn" class="border-t border-rule p-3.5 text-graphite">
        AI answers are off for this workspace, so this conversation is read-only
      </p>
      <form
        v-else
        class="grid gap-2 p-3.5"
        :class="{ 'border-t border-rule': turns.length || open || aiTurns.length }"
        @submit.prevent="ask()"
      >
        <label class="sr-only" for="ask-input">Ask Lattice</label>
        <textarea
          id="ask-input"
          v-model="question"
          rows="3"
          class="input min-h-24 resize-y font-sans text-base"
          placeholder="Ask a question, e.g. how do we fail over the payment gateway?"
          required
          :disabled="!!answering"
          @keydown.enter.exact="onEnter"
        />
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p v-if="aiOn" class="text-sm text-graphite">
            Written by {{ aiLabel }} from approved passages you can read. Sentences they don't support are marked.
          </p>
          <p v-else class="text-sm text-graphite">Answers quote approved documents. Nothing is generated.</p>
          <div class="flex gap-2">
            <button v-if="answering" type="button" class="btn min-h-10" @click="answering.abort()">Stop</button>
            <button type="submit" class="btn btn-primary min-h-10 px-6" :disabled="asking">{{ answering ? 'Answering…' : 'Ask' }}</button>
          </div>
        </div>
      </form>
    </section>

    <div class="grid gap-4">
    <section v-if="aiOn || conversations.data?.conversations.length" class="sheet" aria-labelledby="conversations-h">
      <div class="border-b border-rule px-3.5 py-3">
        <h2 id="conversations-h">Your conversations</h2>
        <p v-if="ai.data" class="text-sm text-graphite">Kept {{ plural(ai.data.retention_days, 'day') }} after their last question.</p>
      </div>
      <div class="px-3.5"><LoadState :load="conversations" :empty="false" empty-text="" /></div>
      <p v-if="conversations.data && !conversations.data.conversations.length" class="px-3.5 py-4 text-graphite">
        No saved conversations yet. Ask a question to start one.
      </p>
      <ul v-else-if="conversations.data" class="divide-y divide-rule">
        <li v-for="c in conversations.data.conversations" :key="c.id" class="flex items-center gap-2 px-3.5 py-2" :class="{ 'bg-film/60': open?.id === c.id }">
          <button
            type="button"
            class="grid min-w-0 flex-1 gap-0.5 text-left disabled:cursor-not-allowed"
            :disabled="asking"
            :aria-label="`Open conversation: ${c.title}`"
            :aria-current="open?.id === c.id ? 'true' : undefined"
            @click="reopen(c.id)"
          >
            <span class="font-medium break-words">{{ c.title }}</span>
            <time class="text-sm text-graphite" :datetime="c.last_turn_at">{{ when(c.last_turn_at) }}</time>
          </button>
          <button type="button" class="btn btn-danger min-h-7" :disabled="asking" :aria-label="`Delete conversation: ${c.title}`" @click="remove(c)">
            Delete
          </button>
        </li>
      </ul>
    </section>

    <section class="sheet" aria-labelledby="history-h">
      <div class="border-b border-rule px-3.5 py-3">
        <h2 id="history-h">Your last 10 queries</h2>
        <p class="text-sm text-graphite">From Search, Ask and your agents. Select one to ask it again or reopen its conversation.</p>
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
            :aria-label="s.conversation_id ? `Reopen conversation: ${s.query}` : `Ask again: ${s.query}`"
            @click="s.conversation_id ? reopen(s.conversation_id) : ask(s.query)"
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
  </div>
</template>
