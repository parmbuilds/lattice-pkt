<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Stamp from '../components/Stamp.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { type DocumentSummary, documentStamps, shortId, useAction, useLoad, useWorkspace } from '../session'

const w = useWorkspace()
const router = useRouter()
const docs = useLoad<{ documents: DocumentSummary[] }>(() => `${w.base}/documents`)
const { message, run } = useAction()
const adding = ref(false)
const title = ref('')
const body = ref('')

const filters = {
  all: () => true,
  approved: (d: DocumentSummary) => !!d.serving_version_id && !d.withdrawn,
  pending: (d: DocumentSummary) => !!d.pending_version_id && !d.withdrawn,
  withdrawn: (d: DocumentSummary) => d.withdrawn,
}
const filterLabels: [keyof typeof filters, string][] = [
  ['all', 'All'],
  ['approved', 'Approved'],
  ['pending', 'Awaiting approval'],
  ['withdrawn', 'Withdrawn'],
]
const filter = ref<keyof typeof filters>('all')
const all = computed(() => docs.data?.documents ?? [])
const shown = computed(() => all.value.filter(filters[filter.value]))
const count = (key: keyof typeof filters) => all.value.filter(filters[key]).length

async function create() {
  let id = ''
  const ok = await run(async () => {
    id = (await api<{ id: string }>(`${w.base}/documents`, 'POST', { title: title.value, body: body.value })).id
  })
  if (ok) await router.push(`/w/${w.workspaceId}/documents/${id}`)
}
</script>

<template>
  <PageHeader
    title="Documents"
    :lede="
      w.manager
        ? 'Every document in this workspace, in any state.'
        : 'Your own documents, and approved documents shared with your groups.'
    "
  >
    <button type="button" class="btn btn-primary" :aria-expanded="adding" @click="adding = !adding">
      {{ adding ? 'Close' : 'New document' }}
    </button>
  </PageHeader>

  <form v-if="adding" class="sheet mb-8 grid gap-4 p-5" @submit.prevent="create">
    <div>
      <h2>Add a document</h2>
      <p class="text-sm text-graphite">New documents start as an unshared draft that an admin must approve.</p>
    </div>
    <label class="field">
      Title
      <input v-model="title" class="input" required />
    </label>
    <label class="field">
      Text
      <textarea v-model="body" class="input" required rows="10" />
    </label>
    <div class="flex gap-2">
      <button type="submit" class="btn btn-primary">Add document</button>
      <button type="button" class="btn" @click="adding = false">Cancel</button>
    </div>
  </form>
  <StatusMessage :text="message" />

  <div v-if="all.length" class="mb-3 flex flex-wrap gap-1" role="group" aria-label="Filter by state">
    <button
      v-for="[key, label] in filterLabels"
      :key="key"
      type="button"
      class="btn min-h-7 px-2.5 text-[0.8125rem]"
      :class="{ 'btn-primary': filter === key }"
      :aria-pressed="filter === key"
      @click="filter = key"
    >
      {{ label }} <span class="font-mono text-xs opacity-75">{{ count(key) }}</span>
    </button>
  </div>

  <LoadState :load="docs" :empty="!all.length" empty-text="No documents you can see yet." />
  <div v-if="all.length" class="sheet overflow-x-auto">
    <table class="register">
      <thead>
        <tr>
          <th>Document</th>
          <th>Status</th>
          <th class="hidden sm:table-cell">Author</th>
          <th class="hidden md:table-cell">ID</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="d in shown" :key="d.id">
          <td class="sm:min-w-56">
            <RouterLink :to="`/w/${w.workspaceId}/documents/${d.id}`" class="font-medium">
              {{ d.title || '(untitled)' }}
            </RouterLink>
            <span class="block text-sm text-muted sm:hidden">{{ d.author_id === w.userId ? 'You' : d.author_name }}</span>
          </td>
          <td>
            <span class="flex flex-wrap gap-1">
              <Stamp v-for="s in documentStamps(d)" :key="s.label" :kind="s.kind">{{ s.label }}</Stamp>
            </span>
          </td>
          <td class="hidden whitespace-nowrap sm:table-cell">{{ d.author_id === w.userId ? 'You' : d.author_name }}</td>
          <td class="hidden md:table-cell"><code class="text-muted">{{ shortId(d.id) }}</code></td>
        </tr>
        <tr v-if="!shown.length">
          <td colspan="4" class="py-6 text-muted">No documents in this state.</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
