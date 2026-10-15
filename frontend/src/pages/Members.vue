<script setup lang="ts">
import { ref } from 'vue'
import { api, ApiError, errorText } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { refresh, type Role, useAction, useLoad, useWorkspace, when } from '../session'

type Member = { user_id: string; name: string; email: string; role: Role }
type Invite = { id: string; role: Role; prefix: string; inviter_id: string; expires_at: string }

const w = useWorkspace()
const members = useLoad<{ members: Member[] }>(() => `${w.base}/members`)
const invites = useLoad<{ invites: Invite[] }>(() => `${w.base}/invites`)
const { message, run } = useAction()
const inviteRole = ref<Role>('member')
const inviteUrl = ref('')

async function act(action: () => Promise<unknown>, done: string) {
  if (await run(action, done)) {
    members.reload()
    await refresh() // our own role may have changed (transfer)
  }
}
const remove = (m: Member) =>
  window.confirm(`Remove ${m.name} from this workspace? They lose access on their next request.`) &&
  act(() => api(`${w.base}/members/${m.user_id}`, 'DELETE'), `${m.name} was removed.`)
const transfer = (m: Member) =>
  window.confirm(`Make ${m.name} the owner? You become an admin and cannot undo this yourself.`) &&
  act(() => api(`${w.base}/transfer`, 'POST', { user_id: m.user_id }), `${m.name} is now the owner.`)
const changeRole = (m: Member, next: Role) =>
  act(() => api(`${w.base}/members/${m.user_id}`, 'PATCH', { role: next }), `${m.name} is now ${next}.`)

async function createInvite() {
  inviteUrl.value = ''
  await run(
    async () => {
      inviteUrl.value = (await api<{ invite_url: string }>(`${w.base}/invites`, 'POST', { role: inviteRole.value })).invite_url
      invites.reload()
    },
    'Invite created. Copy the link and send it to the person.',
    (err) =>
      err instanceof ApiError && err.status === 404 ? 'Invite refused: your role cannot invite with that role.' : errorText(err),
  )
}
const copyInvite = () => navigator.clipboard?.writeText(inviteUrl.value)
const cancelInvite = (i: Invite) => run(() => api(`${w.base}/invites/${i.id}`, 'DELETE'), 'Invite cancelled.').then(invites.reload)
</script>

<template>
  <PageHeader title="Members" lede="Roles apply on the next request; nothing caches permissions." />
  <StatusMessage :text="message" />

  <div class="mt-4 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
    <section aria-label="Members" class="min-w-0">
      <LoadState :load="members" :empty="!members.data?.members.length" empty-text="No members." />
      <div v-if="members.data?.members.length" class="sheet overflow-x-auto">
        <table class="register">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in members.data.members" :key="m.user_id">
              <td class="font-medium">{{ m.name }}<span v-if="m.user_id === w.userId" class="font-normal text-muted"> (you)</span></td>
              <td class="text-graphite">{{ m.email }}</td>
              <td>
                <select
                  v-if="w.role === 'owner' && m.role !== 'owner'"
                  class="input min-h-8 w-auto py-0.5"
                  :aria-label="`Role for ${m.name}`"
                  :value="m.role"
                  @change="changeRole(m, ($event.target as HTMLSelectElement).value as Role)"
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
                <span v-else class="capitalize">{{ m.role }}</span>
              </td>
              <td>
                <div class="flex justify-end gap-2">
                  <button
                    v-if="m.user_id !== w.userId && m.role !== 'owner' && (w.role === 'owner' || m.role === 'member')"
                    type="button"
                    class="btn btn-danger min-h-7"
                    @click="remove(m)"
                  >
                    Remove
                  </button>
                  <button v-if="w.role === 'owner' && m.role === 'admin'" type="button" class="btn min-h-7" @click="transfer(m)">
                    Transfer ownership
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <aside class="grid gap-6">
      <form class="sheet grid gap-4 p-5" @submit.prevent="createInvite">
        <h2>Invite someone</h2>
        <label class="field">
          Role for the invitee
          <select v-model="inviteRole" class="input">
            <option value="member">Member</option>
            <option v-if="w.role === 'owner'" value="admin">Admin</option>
          </select>
        </label>
        <button type="submit" class="btn btn-primary">Create invite link</button>
        <div v-if="inviteUrl" class="grid gap-2">
          <label class="field">
            Invite link (single use)
            <input readonly class="input font-mono text-xs" :value="inviteUrl" @focus="($event.target as HTMLInputElement).select()" />
          </label>
          <button type="button" class="btn" @click="copyInvite">Copy link</button>
        </div>
      </form>

      <section aria-labelledby="pending-invites" class="grid gap-3">
        <h2 id="pending-invites">Pending invites</h2>
        <LoadState :load="invites" :empty="!invites.data?.invites.length" empty-text="No pending invites." />
        <ul v-if="invites.data?.invites.length" class="sheet divide-y divide-rule">
          <li v-for="i in invites.data.invites" :key="i.id" class="flex items-center justify-between gap-3 px-3 py-2 text-sm">
            <span>
              <code>{{ i.prefix }}…</code> as {{ i.role }}
              <span class="block text-muted">Expires {{ when(i.expires_at) }}</span>
            </span>
            <button type="button" class="btn btn-quiet" @click="cancelInvite(i)">Cancel invite</button>
          </li>
        </ul>
      </section>
    </aside>
  </div>
</template>
