<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import Stamp from '../components/Stamp.vue'
import { shortId, useLoad, useWorkspace, when } from '../session'

type AuditEvent = {
  id: number
  occurred_at: string
  actor_user_id: string | null
  channel: string
  token_id: string | null
  action: string
  outcome: 'allow' | 'deny'
  rule_id: string
  inputs: unknown
  document_id: string | null
  version_id: string | null
  returned_version_ids: string[]
  detail: unknown
}

const w = useWorkspace()
const people = useLoad<{ members: { user_id: string; name: string }[] }>(() => `${w.base}/members`)
const filters = reactive({ user_id: '', action: '', outcome: '', limit: '100' })
const query = ref('')
const events = useLoad<{ events: AuditEvent[] }>(() => `${w.base}/audit?${query.value}`)
const names = computed(() => new Map(people.data?.members.map((m) => [m.user_id, m.name])))

function submit() {
  const next = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString()
  if (next === query.value) events.reload()
  query.value = next
}
const json = (v: unknown) => JSON.stringify(v, null, 2)
</script>

<template>
  <PageHeader
    title="Audit trail"
    lede="Every decision, allowed or denied, from the web and from agents. Denials show here with their rule; to the requester they look like a missing document."
  />

  <form class="mb-5 flex flex-wrap items-end gap-3" @submit.prevent="submit">
    <label class="field">
      Person
      <select v-model="filters.user_id" class="input min-w-40">
        <option value="">Anyone</option>
        <option v-for="m in people.data?.members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option>
      </select>
    </label>
    <label class="field">
      Action
      <input v-model="filters.action" class="input w-44" />
    </label>
    <label class="field">
      Outcome
      <select v-model="filters.outcome" class="input">
        <option value="">Any</option>
        <option value="allow">allow</option>
        <option value="deny">deny</option>
      </select>
    </label>
    <label class="field">
      Limit
      <input v-model="filters.limit" class="input w-24" type="number" min="1" max="500" />
    </label>
    <button type="submit" class="btn btn-primary min-h-9">Filter</button>
  </form>

  <div aria-live="polite">
    <LoadState :load="events" :empty="!events.data?.events.length" empty-text="No audit events match." />
  </div>
  <div v-if="events.data?.events.length" class="sheet overflow-x-auto">
    <table class="register">
      <thead>
        <tr>
          <th>When</th>
          <th>Who</th>
          <th>Channel</th>
          <th>Action</th>
          <th>Outcome</th>
          <th>Rule</th>
          <th>Target</th>
          <th>Record</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in events.data.events" :key="e.id">
          <td class="text-sm whitespace-nowrap">{{ when(e.occurred_at) }}</td>
          <td class="whitespace-nowrap">{{ e.actor_user_id ? (names.get(e.actor_user_id) ?? e.actor_user_id) : '—' }}</td>
          <td class="text-sm">
            {{ e.channel }}
            <code v-if="e.token_id" class="block text-muted" :title="e.token_id">token {{ shortId(e.token_id) }}</code>
          </td>
          <td><code>{{ e.action }}</code></td>
          <td><Stamp :kind="e.outcome">{{ e.outcome }}</Stamp></td>
          <td><code class="text-graphite">{{ e.rule_id }}</code></td>
          <td class="text-sm">
            <code v-if="e.document_id" class="block" :title="e.document_id">doc {{ shortId(e.document_id) }}</code>
            <code v-if="e.version_id" class="block" :title="e.version_id">ver {{ shortId(e.version_id) }}</code>
            <span v-if="e.returned_version_ids.length" class="block">
              Returned {{ e.returned_version_ids.length }}:
              <code v-for="v in e.returned_version_ids" :key="v" class="mr-1" :title="v">{{ shortId(v) }}</code>
            </span>
          </td>
          <td class="min-w-48">
            <details>
              <summary class="cursor-pointer text-sm text-blueline">Inputs and detail</summary>
              <pre class="mt-2 text-xs text-graphite">{{ json(e.inputs) }}</pre>
              <pre class="mt-2 text-xs text-graphite">{{ json(e.detail) }}</pre>
            </details>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
