<script setup lang="ts">
import { ref } from 'vue'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Snippet from '../components/Snippet.vue'
import { me, useLoad, useWorkspace, when } from '../session'

type Result = {
  version_id: string
  document_id: string
  title: string
  rank: number
  snippet: string
  approved_at: string
  approved_by: string | null
  spans: { start: number; end: number; sha256: string }[]
  retrieval: 'lexical' | 'hybrid'
}

const modes = { lexical: 'Keyword search', hybrid: 'Keyword and meaning search' }

const w = useWorkspace()
const query = ref('')
const submitted = ref<string | null>(null)
const results = useLoad<{ results: Result[] }>(() =>
  submitted.value === null ? null : `${w.base}/search?${new URLSearchParams({ q: submitted.value })}`,
)

function submit() {
  if (query.value === submitted.value) results.reload()
  submitted.value = query.value
}
</script>

<template>
  <PageHeader
    title="Search"
    :lede="`Searches the approved documents ${me?.user.name ?? 'you'} can retrieve. Agents using your token get exactly these results.`"
  />
  <form role="search" class="mb-6 flex max-w-3xl items-end gap-2" @submit.prevent="submit">
    <label class="field flex-1">
      Search approved documents
      <input v-model="query" class="input min-h-10 text-base" type="search" required />
    </label>
    <button type="submit" class="btn btn-primary min-h-10 px-5">Search</button>
  </form>

  <div aria-live="polite" class="max-w-3xl">
    <LoadState
      v-if="submitted !== null"
      :load="results"
      :empty="!results.data?.results.length"
      empty-text="No approved documents you can retrieve match."
    />
    <template v-if="results.data?.results.length">
      <p class="mb-2 text-sm text-graphite">
        {{ results.data.results.length }} {{ results.data.results.length === 1 ? 'result' : 'results' }} for “{{ submitted }}” ·
        {{ modes[results.data.results[0].retrieval] }}
      </p>
      <ol class="sheet divide-y divide-rule">
        <li v-for="r in results.data.results" :key="r.version_id" class="px-5 py-4">
          <RouterLink :to="`/w/${w.workspaceId}/documents/${r.document_id}`" class="font-medium">{{ r.title }}</RouterLink>
          <p class="mt-1 max-w-[75ch] text-graphite"><Snippet :text="r.snippet" /></p>
          <p class="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
            <span class="stamp stamp-approved">Approved</span>
            <span>{{ when(r.approved_at) }}{{ r.approved_by ? ` by ${r.approved_by}` : '' }}</span>
            <span v-if="r.spans.length">
              · {{ r.spans.length }} evidence {{ r.spans.length === 1 ? 'span' : 'spans' }}, from character {{ r.spans[0].start }}
            </span>
          </p>
        </li>
      </ol>
    </template>
  </div>
</template>
