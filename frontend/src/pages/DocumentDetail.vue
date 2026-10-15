<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError, errorText } from '../api'
import LoadState from '../components/LoadState.vue'
import Stamp from '../components/Stamp.vue'
import StatusMessage from '../components/StatusMessage.vue'
import {
  type Derivation,
  derivationStamp,
  type DocumentSummary,
  documentStamps,
  revLetter,
  shortId,
  type StampSpec,
  useAction,
  useLoad,
  useWorkspace,
  when,
} from '../session'

type Version = { id: string; title: string; created_at: string }
type Detail = DocumentSummary & {
  title: string
  body: string
  shown_version_id: string
  versions?: Version[]
  shared_with?: { id: string; name: string }[]
  last_decision?: { decision: string; reason: string | null; version_id: string } | null
  derivations?: Record<string, Derivation>
  intakes?: Record<string, { filename: string; detected_type: string | null; parser: string | null; version: string | null }>
}

const route = useRoute()
const w = useWorkspace()
const path = computed(() => `${w.base}/documents/${String(route.params.documentId)}`)
const load = useLoad<Detail>(() => path.value)
const doc = computed(() => load.data)
const { message, run } = useAction()

const author = computed(() => doc.value?.author_id === w.userId)
const showsDraft = computed(() => !!doc.value && doc.value.shown_version_id === doc.value.pending_version_id)
const versions = computed(() => doc.value?.versions ?? [])
const rev = (id: string | null | undefined) => {
  const i = versions.value.findIndex((v) => v.id === id)
  return i < 0 ? '' : revLetter(i)
}
const derivation = computed(() => doc.value?.derivations?.[doc.value.shown_version_id])
const intake = computed(() => doc.value?.intakes?.[doc.value.shown_version_id])
const shownVersion = computed(() => versions.value.find((v) => v.id === doc.value?.shown_version_id))

const serves = computed(() => {
  const d = doc.value
  if (!d) return ''
  if (d.withdrawn) return 'Nothing. Withdrawn documents are never served.'
  if (!d.serving_version_id) return 'Nothing yet. Drafts are never served.'
  const r = rev(d.serving_version_id)
  return r ? `REV ${r}, the approved version` : 'The approved version'
})

function versionStamp(v: Version, i: number): StampSpec {
  const d = doc.value!
  if (v.id === d.serving_version_id) return d.withdrawn ? { kind: 'withdrawn', label: 'Withdrawn' } : { kind: 'approved', label: 'Serving' }
  if (v.id === d.pending_version_id) return { kind: 'draft', label: 'Draft' }
  if (d.last_decision?.version_id === v.id && d.last_decision.decision === 'rejected') return { kind: 'rejected', label: 'Rejected' }
  if (versions.value.findIndex((x) => x.id === d.serving_version_id) > i) return { kind: 'superseded', label: 'Superseded' }
  return { kind: 'plain', label: 'Not approved' }
}

const act = async (action: () => Promise<unknown>, done: string, refused?: (e: unknown) => string) => {
  const ok = await run(action, done, refused)
  load.reload()
  return ok
}

// Editing
const editing = ref(false)
const editTitle = ref('')
const editBody = ref('')
function startEdit() {
  editTitle.value = doc.value!.title
  editBody.value = doc.value!.body
  editing.value = true
}
async function saveEdit() {
  if (await act(() => api(path.value, 'PUT', { title: editTitle.value, body: editBody.value }), 'New draft saved; it awaits approval.'))
    editing.value = false
}

const discard = () =>
  act(() => api(`${path.value}/discard`, 'POST', { version_id: doc.value!.pending_version_id }), 'Draft discarded.')
const withdraw = () =>
  window.confirm('Withdraw this document? Withdrawal is permanent in this version.') &&
  act(() => api(`${path.value}/withdraw`, 'POST'), 'Document withdrawn.')

// Uploading a new version (authors)
const newFile = ref<HTMLInputElement | null>(null)
async function uploadVersion() {
  const picked = newFile.value?.files?.[0]
  if (!picked) return
  const form = new FormData()
  form.append('file', picked)
  form.append('document_id', doc.value!.id)
  if (await act(() => api(`${w.base}/documents/upload`, 'POST', form), 'New version uploaded as a draft; it awaits approval.'))
    newFile.value!.value = ''
}

// Earlier versions
const viewing = ref<{ id: string; title: string; body: string } | null>(null)
const view = (v: Version) =>
  run(async () => {
    viewing.value = await api<{ id: string; title: string; body: string }>(`${path.value}/versions/${v.id}`)
  })

// Sharing
const groups = useLoad<{ groups: { id: string; name: string }[] }>(() => (doc.value?.shared_with ? `${w.base}/groups` : null))
const shareWith = ref('')
const unshared = computed(() => {
  const shared = new Set(doc.value?.shared_with?.map((g) => g.id))
  return groups.data?.groups.filter((g) => !shared.has(g.id)) ?? []
})
const shareRefused = (e: unknown) =>
  e instanceof ApiError && e.status === 404
    ? 'Sharing refused: the group does not exist, or you can only share with groups you belong to.'
    : errorText(e)
async function share() {
  if (shareWith.value && (await act(() => api(`${path.value}/shares/${shareWith.value}`, 'PUT'), 'Shared.', shareRefused)))
    shareWith.value = ''
}
const unshare = (id: string) => act(() => api(`${path.value}/shares/${id}`, 'DELETE'), 'Share removed.', shareRefused)

// Reassigning authorship (managers)
const people = useLoad<{ members: { user_id: string; name: string }[] }>(() => (w.manager ? `${w.base}/members` : null))
const newAuthor = ref('')
const reassign = () =>
  newAuthor.value && act(() => api(`${path.value}/author`, 'POST', { user_id: newAuthor.value }), 'Author changed.')
</script>

<template>
  <LoadState v-if="!doc" :load="load" :empty="false" empty-text="" />
  <template v-else>
    <nav aria-label="Breadcrumb" class="mb-3 text-sm">
      <RouterLink :to="`/w/${w.workspaceId}/documents`">Documents</RouterLink>
      <span class="px-1.5 text-muted">/</span>
      <span class="text-graphite">{{ shortId(doc.id) }}</span>
    </nav>

    <header class="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3 border-b border-rule pb-4">
      <div class="min-w-0">
        <h1>{{ doc.title }}</h1>
        <p class="mt-2 flex flex-wrap items-center gap-2 text-sm text-graphite">
          <Stamp v-for="s in documentStamps(doc)" :key="s.label" :kind="s.kind">{{ s.label }}</Stamp>
          <Stamp v-if="derivation" :kind="derivationStamp(derivation).kind">{{ derivationStamp(derivation).label }}</Stamp>
          <span>
            {{
              doc.withdrawn
                ? 'Kept for the record; never served again.'
                : showsDraft
                  ? 'Showing the draft awaiting approval.'
                  : 'Showing the approved version.'
            }}
          </span>
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button v-if="author && !doc.withdrawn && !editing" type="button" class="btn" @click="startEdit">
          Edit (creates a new draft)
        </button>
        <button v-if="author && doc.pending_version_id" type="button" class="btn" @click="discard">Discard draft</button>
        <RouterLink v-if="w.manager && doc.pending_version_id" :to="`/w/${w.workspaceId}/approvals`" class="btn btn-primary no-underline">
          Review in Approvals
        </RouterLink>
        <button v-if="(author || w.manager) && !doc.withdrawn" type="button" class="btn btn-danger" @click="withdraw">
          Withdraw
        </button>
      </div>
    </header>

    <StatusMessage :text="route.query.uploaded === 'already' && !message ? 'This file was already uploaded; here is that document.' : message" />

    <div class="mt-4 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
        <form v-if="editing" class="sheet grid gap-4 p-5" @submit.prevent="saveEdit">
          <div>
            <h2>Edit</h2>
            <p class="text-sm text-graphite">Saving creates a new draft. The approved version keeps serving until the draft is approved.</p>
          </div>
          <label class="field">
            Title
            <input v-model="editTitle" class="input" required />
          </label>
          <label class="field">
            Text
            <textarea v-model="editBody" class="input" required rows="16" />
          </label>
          <div class="flex gap-2">
            <button type="submit" class="btn btn-primary">Save draft</button>
            <button type="button" class="btn" @click="editing = false">Cancel edit</button>
          </div>
        </form>

        <article v-else class="sheet" aria-label="Document text">
          <div class="flex items-center justify-between gap-3 border-b border-rule px-5 py-2.5 md:px-8">
            <span class="caps text-graphite">
              Text{{ rev(doc.shown_version_id) ? ` · REV ${rev(doc.shown_version_id)}` : '' }}
            </span>
            <time v-if="shownVersion" class="text-sm text-muted" :datetime="shownVersion.created_at">{{ when(shownVersion.created_at) }}</time>
          </div>
          <pre class="max-w-[75ch] px-5 py-6 font-sans text-[0.9375rem] leading-relaxed md:px-8">{{ doc.body }}</pre>
        </article>

        <article v-if="viewing" class="sheet border-dashed" aria-label="Earlier version">
          <div class="flex items-center justify-between gap-3 border-b border-rule px-5 py-2.5 md:px-8">
            <span class="caps text-graphite">REV {{ rev(viewing.id) }} · {{ viewing.title }}</span>
            <button type="button" class="btn btn-quiet" @click="viewing = null">Close</button>
          </div>
          <pre class="max-w-[75ch] px-5 py-6 font-sans text-[0.9375rem] leading-relaxed md:px-8">{{ viewing.body }}</pre>
        </article>
      </div>

      <aside class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6" aria-label="Document record">
        <dl class="titleblock">
          <div>
            <dt>ID</dt>
            <dd><code :title="doc.id">{{ shortId(doc.id) }}</code></dd>
          </div>
          <div>
            <dt>Rev</dt>
            <dd class="font-cond text-xl leading-tight font-semibold">
              {{ rev(doc.shown_version_id) || '—' }}
              <span v-if="versions.length" class="text-sm font-normal text-muted">of {{ versions.length }}</span>
            </dd>
          </div>
          <div>
            <dt>Author</dt>
            <dd>{{ author ? 'You' : doc.author_name }}</dd>
          </div>
          <div>
            <dt>Date</dt>
            <dd>{{ shownVersion ? when(shownVersion.created_at) : '—' }}</dd>
          </div>
          <div class="span">
            <dt>Serves search and agents</dt>
            <dd>{{ serves }}</dd>
          </div>
          <div v-if="intake" class="span">
            <dt>Uploaded file</dt>
            <dd>{{ intake.filename }} <span class="text-sm text-muted">({{ intake.detected_type }}, read by {{ intake.parser }} {{ intake.version }})</span></dd>
          </div>
          <div v-if="doc.shared_with" class="span">
            <dt>Shared with</dt>
            <dd>{{ doc.shared_with.length ? doc.shared_with.map((g) => g.name).join(', ') : 'No groups' }}</dd>
          </div>
          <div v-if="doc.last_decision" class="span">
            <dt>Last decision</dt>
            <dd class="flex flex-wrap items-center gap-2">
              <Stamp :kind="doc.last_decision.decision === 'approved' ? 'approved' : 'rejected'">{{ doc.last_decision.decision }}</Stamp>
              <span v-if="rev(doc.last_decision.version_id)" class="text-sm">REV {{ rev(doc.last_decision.version_id) }}</span>
              <span v-if="doc.last_decision.reason" class="w-full text-sm font-normal text-graphite">{{ doc.last_decision.reason }}</span>
            </dd>
          </div>
        </dl>

        <section v-if="versions.length" aria-labelledby="revisions">
          <h2 id="revisions" class="mb-2">Revisions</h2>
          <table class="register sheet">
            <thead>
              <tr>
                <th>Rev</th>
                <th>Date</th>
                <th>State</th>
                <th><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(v, i) in [...versions].reverse()" :key="v.id">
                <td class="font-cond font-semibold">{{ revLetter(versions.length - 1 - i) }}</td>
                <td class="text-sm">{{ when(v.created_at) }}</td>
                <td>
                  <Stamp :kind="versionStamp(v, versions.length - 1 - i).kind">{{ versionStamp(v, versions.length - 1 - i).label }}</Stamp>
                </td>
                <td class="text-right">
                  <button type="button" class="btn btn-quiet" :aria-label="`View REV ${revLetter(versions.length - 1 - i)}`" @click="view(v)">
                    View
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="doc.shared_with" aria-labelledby="sharing" class="grid gap-3">
          <h2 id="sharing">Sharing</h2>
          <ul v-if="doc.shared_with.length" class="sheet divide-y divide-rule">
            <li v-for="g in doc.shared_with" :key="g.id" class="flex items-center justify-between gap-3 px-3 py-2">
              <span class="font-medium">{{ g.name }}</span>
              <button type="button" class="btn btn-quiet" :aria-label="`Unshare from ${g.name}`" @click="unshare(g.id)">Unshare</button>
            </li>
          </ul>
          <p v-else class="text-sm text-graphite">Not shared with any group.</p>
          <form class="flex items-end gap-2" @submit.prevent="share">
            <label class="field min-w-0 flex-1">
              Share with group
              <select v-model="shareWith" class="input">
                <option value="">Choose…</option>
                <option v-for="g in unshared" :key="g.id" :value="g.id">{{ g.name }}</option>
              </select>
            </label>
            <button type="submit" class="btn">Share</button>
          </form>
        </section>

        <form v-if="author && !doc.withdrawn" class="grid gap-3" @submit.prevent="uploadVersion">
          <h2>Upload new version</h2>
          <div class="flex items-end gap-2">
            <label class="field min-w-0 flex-1">
              File
              <input ref="newFile" class="input" type="file" accept=".md,.txt,.html,.pdf,.docx" required />
            </label>
            <button type="submit" class="btn">Upload</button>
          </div>
        </form>

        <form v-if="w.manager" class="grid gap-3" @submit.prevent="reassign">
          <h2>Authorship</h2>
          <div class="flex items-end gap-2">
            <label class="field min-w-0 flex-1">
              Reassign author to
              <select v-model="newAuthor" class="input">
                <option value="">Choose…</option>
                <option v-for="p in people.data?.members.filter((p) => p.user_id !== doc?.author_id)" :key="p.user_id" :value="p.user_id">
                  {{ p.name }}
                </option>
              </select>
            </label>
            <button type="submit" class="btn">Reassign</button>
          </div>
        </form>
      </aside>
    </div>
  </template>
</template>
