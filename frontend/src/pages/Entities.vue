<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { useAction, useLoad, useWorkspace } from '../session'

type Entity = { id: string; name: string; aliases: string[] }
type Mention = { version_id: string; document_id: string; title: string; spans: { start: number; end: number; term: string; snippet: string }[] }
type Detail = Entity & { mentions: Mention[]; co_mentions: { id: string; name: string }[] }

// One page, two routes: the list at entities/ and one entity at entities/:entityId.
const route = useRoute()
const w = useWorkspace()
const entityId = computed(() => (route.params.entityId ? String(route.params.entityId) : null))
const list = useLoad<{ entities: Entity[] }>(() => (entityId.value ? null : `${w.base}/entities`))
const detail = useLoad<Detail>(() => (entityId.value ? `${w.base}/entities/${entityId.value}` : null))
const { message, run } = useAction()

const terms = (aliases: string) => aliases.split(',').map((a) => a.trim()).filter(Boolean)

// Adding and editing (managers; the API refuses members whatever this shows)
const name = ref('')
const aliases = ref('')
async function add() {
  if (await run(() => api(`${w.base}/entities`, 'POST', { name: name.value, aliases: terms(aliases.value) }), 'Entity added.')) {
    name.value = aliases.value = ''
    list.reload()
  }
}
const editing = ref<string | null>(null)
const editName = ref('')
const editAliases = ref('')
function startEdit(e: Entity) {
  editing.value = e.id
  editName.value = e.name
  editAliases.value = e.aliases.join(', ')
}
async function save(id: string) {
  const body = { name: editName.value, aliases: terms(editAliases.value) }
  if (await run(() => api(`${w.base}/entities/${id}`, 'PATCH', body), 'Entity saved.')) {
    editing.value = null
    list.reload()
  }
}
</script>

<template>
  <template v-if="!entityId">
    <PageHeader title="Entities" lede="People, systems and terms named in the approved documents you can read." />
    <form v-if="w.manager" class="sheet mb-8 grid gap-4 p-5" @submit.prevent="add">
      <h2>Add an entity</h2>
      <label class="field">
        Name
        <input v-model="name" class="input" required />
      </label>
      <label class="field">
        Aliases (comma separated)
        <input v-model="aliases" class="input" />
      </label>
      <div><button type="submit" class="btn btn-primary">Add entity</button></div>
    </form>
    <StatusMessage :text="message" />
    <LoadState :load="list" :empty="!list.data?.entities.length" empty-text="No entities are mentioned in documents you can read." />
    <ul v-if="list.data?.entities.length" class="sheet divide-y divide-rule">
      <li v-for="e in list.data.entities" :key="e.id" class="px-5 py-3">
        <form v-if="editing === e.id" class="grid gap-3" @submit.prevent="save(e.id)">
          <label class="field">
            Name
            <input v-model="editName" class="input" required />
          </label>
          <label class="field">
            Aliases (comma separated)
            <input v-model="editAliases" class="input" />
          </label>
          <div class="flex gap-2">
            <button type="submit" class="btn btn-primary">Save</button>
            <button type="button" class="btn" @click="editing = null">Cancel</button>
          </div>
        </form>
        <div v-else class="flex flex-wrap items-center justify-between gap-3">
          <span>
            <RouterLink :to="`/w/${w.workspaceId}/entities/${e.id}`" class="font-medium">{{ e.name }}</RouterLink>
            <span v-if="e.aliases.length" class="ml-2 text-sm text-muted">{{ e.aliases.join(', ') }}</span>
          </span>
          <button v-if="w.manager" type="button" class="btn btn-quiet" :aria-label="`Edit ${e.name}`" @click="startEdit(e)">Edit</button>
        </div>
      </li>
    </ul>
  </template>

  <template v-else>
    <nav aria-label="Breadcrumb" class="mb-3 text-sm">
      <RouterLink :to="`/w/${w.workspaceId}/entities`">Entities</RouterLink>
    </nav>
    <LoadState v-if="!detail.data" :load="detail" :empty="false" empty-text="" />
    <template v-else>
      <PageHeader :title="detail.data.name" :lede="detail.data.aliases.length ? `Also: ${detail.data.aliases.join(', ')}` : ''" />
      <section aria-labelledby="mentions" class="mb-8">
        <h2 id="mentions" class="mb-2">Mentions</h2>
        <p v-if="!detail.data.mentions.length" class="text-sm text-graphite">No mentions in documents you can read.</p>
        <ul v-else class="sheet divide-y divide-rule">
          <li v-for="m in detail.data.mentions" :key="m.version_id" class="px-5 py-3">
            <RouterLink :to="`/w/${w.workspaceId}/documents/${m.document_id}`" class="font-medium">{{ m.title }}</RouterLink>
            <p v-for="s in m.spans" :key="s.start" class="mt-1 max-w-[75ch] text-graphite">{{ s.snippet }}</p>
          </li>
        </ul>
      </section>
      <section aria-labelledby="co-mentions">
        <h2 id="co-mentions" class="mb-2">Mentioned alongside</h2>
        <p v-if="!detail.data.co_mentions.length" class="text-sm text-graphite">No other entities in these documents.</p>
        <ul v-else class="flex flex-wrap gap-3">
          <li v-for="c in detail.data.co_mentions" :key="c.id">
            <RouterLink :to="`/w/${w.workspaceId}/entities/${c.id}`">{{ c.name }}</RouterLink>
          </li>
        </ul>
      </section>
    </template>
  </template>
</template>
