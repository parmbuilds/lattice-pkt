<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatusMessage from '../components/StatusMessage.vue'
import TopBar from '../components/TopBar.vue'
import { me, refresh, useAction } from '../session'

const router = useRouter()
const { message, run } = useAction()
const name = ref('')

async function create() {
  let id = ''
  const ok = await run(async () => {
    id = (await api<{ id: string }>('/api/workspaces', 'POST', { name: name.value })).id
  })
  if (ok) {
    await refresh()
    await router.push(`/w/${id}/documents`)
  }
}
</script>

<template>
  <TopBar />
  <main class="mx-auto max-w-4xl px-5 py-8 md:px-10">
    <PageHeader title="Your workspaces" lede="Everything belongs to a workspace, and nothing crosses a workspace boundary." />
    <div class="grid gap-8 md:grid-cols-[minmax(0,1fr)_18rem]">
      <section aria-label="Workspaces">
        <table v-if="me?.memberships.length" class="register sheet">
          <thead>
            <tr>
              <th>Workspace</th>
              <th>Your role</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in me.memberships" :key="m.workspace_id">
              <td>
                <RouterLink :to="`/w/${m.workspace_id}/documents`" class="font-medium">{{ m.workspace_name }}</RouterLink>
              </td>
              <td class="capitalize">{{ m.role }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-graphite">You are not in any workspace yet. Create one, or open an invite link.</p>
      </section>
      <form class="sheet grid content-start gap-4 p-5" @submit.prevent="create">
        <h2>Create a workspace</h2>
        <label class="field">
          Workspace name
          <input v-model="name" class="input" required />
        </label>
        <button type="submit" class="btn btn-primary">Create workspace</button>
        <StatusMessage :text="message" />
      </form>
    </div>
  </main>
</template>
