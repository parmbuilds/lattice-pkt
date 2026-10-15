<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../api'

type Group = { id: string; name: string; members?: { user_id: string; name: string }[] }

const props = defineProps<{
  group: Group
  people: { user_id: string; name: string }[]
  base: string
  act: (action: () => Promise<unknown>, done: string) => Promise<boolean>
}>()

const path = computed(() => `${props.base}/groups/${props.group.id}`)
const rename = ref(props.group.name)
const adding = ref('')
const addable = computed(() => {
  const inGroup = new Set(props.group.members?.map((m) => m.user_id))
  return props.people.filter((p) => !inGroup.has(p.user_id))
})

const add = () => adding.value && props.act(() => api(`${path.value}/members/${adding.value}`, 'PUT'), 'Member added to the group.')
const removeMember = (m: { user_id: string; name: string }) =>
  props.act(() => api(`${path.value}/members/${m.user_id}`, 'DELETE'), `${m.name} removed from the group.`)
const save = () => props.act(() => api(path.value, 'PATCH', { name: rename.value }), 'Group renamed.')
const remove = () =>
  window.confirm(`Delete group "${props.group.name}"? Every document shared with it loses that share.`) &&
  props.act(() => api(path.value, 'DELETE'), 'Group deleted.')
</script>

<template>
  <section class="sheet" :aria-labelledby="`group-${group.id}`">
    <header class="flex items-center justify-between gap-3 border-b border-rule px-5 py-3">
      <h2 :id="`group-${group.id}`">{{ group.name }}</h2>
      <span class="text-sm text-muted">{{ group.members?.length ?? 0 }} {{ group.members?.length === 1 ? 'member' : 'members' }}</span>
    </header>
    <ul v-if="group.members?.length" class="divide-y divide-rule">
      <li v-for="m in group.members" :key="m.user_id" class="flex items-center justify-between gap-3 px-5 py-2">
        <span>{{ m.name }}</span>
        <button type="button" class="btn btn-quiet" :aria-label="`Remove ${m.name} from ${group.name}`" @click="removeMember(m)">
          Remove from group
        </button>
      </li>
    </ul>
    <p v-else class="px-5 py-3 text-sm text-graphite">No members.</p>
    <footer class="grid gap-3 border-t border-rule bg-film/60 px-5 py-4 sm:grid-cols-2">
      <form class="flex items-end gap-2" @submit.prevent="add">
        <label class="field flex-1">
          Add person
          <select v-model="adding" class="input">
            <option value="">Choose…</option>
            <option v-for="p in addable" :key="p.user_id" :value="p.user_id">{{ p.name }}</option>
          </select>
        </label>
        <button type="submit" class="btn">Add to group</button>
      </form>
      <form class="flex items-end gap-2" @submit.prevent="save">
        <label class="field flex-1">
          Rename group
          <input v-model="rename" class="input" required />
        </label>
        <button type="submit" class="btn">Rename</button>
      </form>
      <div class="sm:col-span-2">
        <button type="button" class="btn btn-danger" @click="remove">Delete group</button>
      </div>
    </footer>
  </section>
</template>
