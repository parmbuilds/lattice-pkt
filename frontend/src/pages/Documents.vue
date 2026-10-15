<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError, errorText } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Stamp from '../components/Stamp.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { type DocumentSummary, documentStamps, shortId, useAction, useLoad, useWorkspace } from '../session'
import { ACCEPT, droppedFiles, hidden, type Picked, supported } from '../upload'

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

// Uploading a file
const uploading = ref(false)
const file = ref<File | null>(null)
const inFlight = ref(false)
const suggestion = ref<string | null>(null)
const uploadRefused = (e: unknown) => {
  const body = e instanceof ApiError ? (e.body as { suggest_document_id?: string } | undefined) : undefined
  suggestion.value = (e instanceof ApiError && e.status === 409 && body?.suggest_document_id) || null
  return suggestion.value ? '' : errorText(e)
}
// Several files or a folder: each goes to the same endpoint in turn, so each keeps its own receipt.
type Row = Picked & {
  state: 'waiting' | 'uploading' | 'created' | 'duplicate' | 'match' | 'refused'
  id?: string
  suggestion?: string
  reason?: string
}
const rows = ref<Row[]>([])
const skipped = ref<string[]>([])
const stopping = ref(false)
const dragging = ref(false)
const batch = computed(() => rows.value.length > 1)

function take(picked: Picked[]) {
  const visible = picked.filter((p) => !hidden(p.path))
  rows.value = visible.filter((p) => supported(p.path)).map((p) => ({ ...p, state: 'waiting' }))
  skipped.value = visible.filter((p) => !supported(p.path)).map((p) => p.path)
  file.value = rows.value.length === 1 ? rows.value[0].file : null
  suggestion.value = null
  uploading.value = true
}
function pick(event: Event) {
  const files = [...((event.target as HTMLInputElement).files ?? [])]
  take(files.map((f) => ({ file: f, path: f.webkitRelativePath || f.name })))
}

async function send(row: Row, extra: Record<string, string> = {}) {
  const form = new FormData()
  form.append('file', row.file)
  for (const [key, value] of Object.entries(extra)) form.append(key, value)
  row.state = 'uploading'
  try {
    const result = await api<{ id: string; created: boolean }>(`${w.base}/documents/upload`, 'POST', form)
    Object.assign(row, { state: result.created ? 'created' : 'duplicate', id: result.id })
  } catch (e) {
    const match = e instanceof ApiError && e.status === 409 && (e.body as { suggest_document_id?: string })?.suggest_document_id
    Object.assign(row, match ? { state: 'match', suggestion: match } : { state: 'refused', reason: errorText(e) })
  }
}
async function uploadAll() {
  inFlight.value = true
  stopping.value = false
  for (const row of rows.value) {
    if (stopping.value) break
    if (row.state === 'waiting') await send(row)
  }
  inFlight.value = false
  docs.reload()
}
async function resolve(row: Row, extra: Record<string, string>) {
  inFlight.value = true
  await send(row, extra)
  inFlight.value = false
  docs.reload()
}
const outcome = (row: Row) =>
  ({
    waiting: 'Waiting',
    uploading: 'Uploading…',
    created: 'New draft',
    duplicate: 'Already uploaded',
    match: 'Title matches an existing document',
    refused: `Refused: ${row.reason}`,
  })[row.state]
const summary = computed(() => {
  const n = (s: Row['state']) => rows.value.filter((r) => r.state === s).length
  const done = rows.value.length - n('waiting') - n('uploading')
  return `${done} of ${rows.value.length} uploaded: ${n('created')} new, ${n('duplicate')} already uploaded, ${n('match')} need a choice, ${n('refused')} refused.`
})

// Files dropped anywhere on the page land here, instead of the browser opening them.
const carriesFiles = (e: DragEvent) => !!e.dataTransfer?.types.includes('Files')
function onDragOver(e: DragEvent) {
  if (!carriesFiles(e)) return
  e.preventDefault()
  dragging.value = true
}
function onDragLeave(e: DragEvent) {
  if (!e.relatedTarget) dragging.value = false
}
async function onDrop(e: DragEvent) {
  if (!carriesFiles(e)) return
  e.preventDefault()
  dragging.value = false
  if (!inFlight.value && e.dataTransfer) take(await droppedFiles(e.dataTransfer))
}
onMounted(() => {
  window.addEventListener('dragover', onDragOver)
  window.addEventListener('dragleave', onDragLeave)
  window.addEventListener('drop', onDrop)
})
onUnmounted(() => {
  window.removeEventListener('dragover', onDragOver)
  window.removeEventListener('dragleave', onDragLeave)
  window.removeEventListener('drop', onDrop)
})
async function upload(extra: Record<string, string> = {}) {
  if (!file.value) return
  const form = new FormData()
  form.append('file', file.value)
  for (const [key, value] of Object.entries(extra)) form.append(key, value)
  let result = { id: '', created: true }
  inFlight.value = true
  suggestion.value = null
  const ok = await run(async () => {
    result = await api<typeof result>(`${w.base}/documents/upload`, 'POST', form)
  }, '', uploadRefused)
  inFlight.value = false
  if (ok)
    await router.push({
      path: `/w/${w.workspaceId}/documents/${result.id}`,
      query: result.created ? {} : { uploaded: 'already' },
    })
}

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
    <button type="button" class="btn" :aria-expanded="uploading" @click="uploading = !uploading">
      {{ uploading ? 'Close upload' : 'Upload files' }}
    </button>
    <button type="button" class="btn btn-primary" :aria-expanded="adding" @click="adding = !adding">
      {{ adding ? 'Close' : 'New document' }}
    </button>
  </PageHeader>

  <form
    v-if="uploading"
    class="sheet mb-8 grid gap-4 p-5"
    :class="{ 'outline-2 outline-offset-4 outline-dashed': dragging }"
    @submit.prevent="batch ? uploadAll() : upload()"
  >
    <div>
      <h2>Upload files</h2>
      <p class="text-sm text-graphite">
        Markdown, text, HTML, PDF or Word. Choose files or a folder, or drop them anywhere on this page. Each file's
        extracted text becomes a draft an admin must approve.
      </p>
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <label class="field">
        Files
        <input class="input" type="file" :accept="ACCEPT" multiple :disabled="inFlight" @change="pick" />
      </label>
      <label class="field">
        Folder
        <input class="input" type="file" webkitdirectory :disabled="inFlight" @change="pick" />
      </label>
    </div>
    <p v-if="rows.length || skipped.length" class="text-sm">
      {{ rows.length }} {{ rows.length === 1 ? 'file' : 'files' }} to upload<template v-if="skipped.length">
        · {{ skipped.length }} skipped (not Markdown, text, HTML, PDF or Word)</template
      >
    </p>
    <details v-if="skipped.length" class="text-sm text-graphite">
      <summary>Skipped files</summary>
      <ul class="mt-1 list-disc pl-5">
        <li v-for="path in skipped" :key="path">{{ path }}</li>
      </ul>
    </details>
    <template v-if="batch">
      <ul class="divide-y divide-rule border border-rule" aria-label="Files">
        <li v-for="row in rows" :key="row.path" class="grid gap-2 px-3 py-2 sm:flex sm:items-center sm:justify-between">
          <span class="min-w-0 font-mono text-xs break-all">{{ row.path }}</span>
          <span class="flex flex-wrap items-center gap-2 text-sm">
            <span :class="{ 'text-redline-ink': row.state === 'refused' }">{{ outcome(row) }}</span>
            <RouterLink v-if="row.id" :to="`/w/${w.workspaceId}/documents/${row.id}`">Open</RouterLink>
            <template v-if="row.state === 'match'">
              <button
                type="button"
                class="btn min-h-7"
                :disabled="inFlight"
                :aria-label="`Add ${row.path} as a new version`"
                @click="resolve(row, { document_id: row.suggestion! })"
              >
                Add as new version
              </button>
              <button
                type="button"
                class="btn min-h-7"
                :disabled="inFlight"
                :aria-label="`Keep ${row.path} separate`"
                @click="resolve(row, { new_document: 'true' })"
              >
                Keep separate
              </button>
            </template>
          </span>
        </li>
      </ul>
      <p role="status" class="text-sm">{{ summary }}</p>
      <div class="flex gap-2">
        <button type="submit" class="btn btn-primary" :disabled="inFlight || !rows.some((r) => r.state === 'waiting')">
          Upload {{ rows.filter((r) => r.state === 'waiting').length }} files
        </button>
        <button v-if="inFlight" type="button" class="btn" :disabled="stopping" @click="stopping = true">
          {{ stopping ? 'Stopping after this file…' : 'Stop' }}
        </button>
      </div>
    </template>
    <div v-else-if="suggestion" class="grid gap-2" role="group" aria-label="Title match">
      <p class="text-sm">You already have a document with this title.</p>
      <div class="flex gap-2">
        <button type="button" class="btn btn-primary" :disabled="inFlight" @click="upload({ document_id: suggestion })">
          Upload as new version
        </button>
        <button type="button" class="btn" :disabled="inFlight" @click="upload({ new_document: 'true' })">Keep separate</button>
      </div>
    </div>
    <div v-else class="flex gap-2">
      <button type="submit" class="btn btn-primary" :disabled="inFlight || !file">Upload</button>
    </div>
  </form>

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
