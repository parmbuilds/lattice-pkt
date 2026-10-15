<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import GroupCard from '../components/GroupCard.vue'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { useAction, useLoad, useWorkspace } from '../session'

type Group = { id: string; name: string; members?: { user_id: string; name: string }[] }

const w = useWorkspace()
const groups = useLoad<{ groups: Group[] }>(() => `${w.base}/groups`)
const people = useLoad<{ members: { user_id: string; name: string }[] }>(() => (w.manager ? `${w.base}/members` : null))
const { message, run } = useAction()
const name = ref('')

async function act(action: () => Promise<unknown>, done: string) {
  const ok = await run(action, done)
  groups.reload()
  return ok
}
async function create() {
  if (await act(() => api(`${w.base}/groups`, 'POST', { name: name.value }), 'Group created.')) name.value = ''
}
</script>

<template>
  <PageHeader title="Groups" lede="Members read approved documents shared with their groups. Deleting a group removes its shares." />
  <StatusMessage :text="message" />
  <form v-if="w.manager" class="mt-4 mb-6 flex max-w-xl items-end gap-2" @submit.prevent="create">
    <label class="field flex-1">
      New group name
      <input v-model="name" class="input" required />
    </label>
    <button type="submit" class="btn btn-primary min-h-9">Create group</button>
  </form>
  <LoadState :load="groups" :empty="!groups.data?.groups.length" empty-text="No groups yet." />
  <div class="grid gap-6 xl:grid-cols-2">
    <template v-for="g in groups.data?.groups" :key="g.id">
      <GroupCard v-if="w.manager" :group="g" :people="people.data?.members ?? []" :base="w.base" :act="act" />
      <section v-else class="sheet px-5 py-3"><h2>{{ g.name }}</h2></section>
    </template>
  </div>
</template>
