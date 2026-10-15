<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Stamp from '../components/Stamp.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { useAction, useLoad, useWorkspace, when } from '../session'

type Token = {
  id: string
  name: string
  prefix: string
  created_at: string
  expires_at: string
  revoked: boolean
  last_used_at: string | null
}

const w = useWorkspace()
const tokens = useLoad<{ tokens: Token[] }>(() => `${w.base}/tokens`)
const { message, run } = useAction()
const name = ref('')
const password = ref('')
// The secret lives only here, from the create response; the list never carries it.
const secret = ref('')
const endpoint = `${window.location.origin}/mcp`
const config = JSON.stringify(
  { mcpServers: { lattice: { type: 'http', url: endpoint, headers: { Authorization: 'Bearer <token>' } } } },
  null,
  2,
)
const expired = (t: Token) => new Date(t.expires_at).getTime() < Date.now()

async function create() {
  secret.value = ''
  const ok = await run(async () => {
    secret.value = (await api<{ token: string }>(`${w.base}/tokens`, 'POST', { name: name.value, password: password.value })).token
  })
  password.value = ''
  if (ok) {
    name.value = ''
    tokens.reload()
  }
}
const copy = () =>
  navigator.clipboard?.writeText(secret.value).then(
    () => (message.value = 'Token copied.'),
    () => (message.value = 'Copy failed; select the token and copy it by hand.'),
  )
const revoke = (t: Token) => run(() => api(`${w.base}/tokens/${t.id}`, 'DELETE'), 'Token revoked.').then(tokens.reload)
</script>

<template>
  <PageHeader
    title="Tokens"
    lede="A personal access token lets your MCP agent search this workspace with your access. Agents can only search and read."
  />

  <div class="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
    <!-- A single minmax(0,1fr) track lets the token table scroll instead of widening the column. -->
    <div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
      <StatusMessage :text="message" />
      <div v-if="secret" class="sheet border-[1.5px] border-ink p-5" role="alert">
        <p class="font-semibold">Copy this token now. It will not be shown again.</p>
        <div class="mt-3 flex flex-wrap items-center gap-3">
          <code class="min-w-0 flex-1 rounded-[3px] border border-rule bg-white px-3 py-2 text-[0.9375rem] break-all">{{ secret }}</code>
          <button type="button" class="btn btn-primary" @click="copy">Copy token</button>
        </div>
      </div>

      <section aria-labelledby="your-tokens">
        <h2 id="your-tokens" class="mb-3">Your tokens</h2>
        <LoadState :load="tokens" :empty="!tokens.data?.tokens.length" empty-text="No tokens yet. Create one to connect an agent." />
        <div v-if="tokens.data?.tokens.length" class="sheet overflow-x-auto">
          <table class="register">
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Last used</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tokens.data.tokens" :key="t.id">
                <td class="font-medium">{{ t.name }}</td>
                <td><code>{{ t.prefix }}…</code></td>
                <td class="text-sm">{{ when(t.created_at) }}</td>
                <td class="text-sm">{{ when(t.expires_at) }}</td>
                <td class="text-sm">{{ when(t.last_used_at) }}</td>
                <td>
                  <Stamp v-if="t.revoked" kind="withdrawn">Revoked</Stamp>
                  <Stamp v-else-if="expired(t)" kind="plain">Expired</Stamp>
                  <button v-else type="button" class="btn btn-danger min-h-7" @click="revoke(t)">Revoke {{ t.name }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <aside class="grid gap-6">
      <form class="sheet grid gap-4 p-5" @submit.prevent="create">
        <h2>Create a token</h2>
        <label class="field">
          Token name
          <input v-model="name" class="input" required maxlength="100" placeholder="e.g. laptop agent" />
        </label>
        <label class="field">
          Your password
          <input v-model="password" class="input" type="password" autocomplete="current-password" required />
        </label>
        <button type="submit" class="btn btn-primary">Create token</button>
      </form>

      <section class="grid gap-3" aria-labelledby="connect">
        <h2 id="connect">Connect an agent</h2>
        <div>
          <p class="caps text-graphite">MCP endpoint</p>
          <code class="mt-1 block break-all">{{ endpoint }}</code>
        </div>
        <div>
          <p class="caps text-graphite">Client configuration</p>
          <pre class="mt-1 rounded-[3px] bg-ink px-3 py-2.5 text-rail-text">{{ config }}</pre>
        </div>
      </section>
    </aside>
  </div>
</template>
