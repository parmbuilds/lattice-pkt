<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, ApiError, errorText } from '../api'
import LoadState from '../components/LoadState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { useAction, useLoad, useWorkspace } from '../session'

export type AiProvider = { id: string; label: string; models: { id: string; label: string }[] }
export type AiSettingsView = {
  providers: AiProvider[]
  enabled: boolean
  provider_id: string | null
  model: string | null
  retention_days: number
  key_status?: 'ok' | 'rejected' | null // owners and admins only (R30)
}

const w = useWorkspace()
const settings = useLoad<AiSettingsView>(() => `${w.base}/ai/settings`)
const { message, run } = useAction()
const providerId = ref('')
const model = ref('')
const apiKey = ref('')
const retention = ref(30)

const providers = computed(() => settings.data?.providers ?? [])
const models = computed(() => providers.value.find((p) => p.id === providerId.value)?.models ?? [])
const current = computed(() => {
  const s = settings.data
  const p = s?.providers.find((x) => x.id === s.provider_id)
  return { provider: p?.label ?? s?.provider_id, model: p?.models.find((m) => m.id === s?.model)?.label ?? s?.model }
})

watch(
  () => settings.data,
  (s) => {
    if (!s) return
    providerId.value = s.provider_id ?? s.providers[0]?.id ?? ''
    model.value = s.model ?? models.value[0]?.id ?? ''
    retention.value = s.retention_days
  },
  { immediate: true },
)
const pickProvider = () => (model.value = models.value[0]?.id ?? '')

async function save() {
  const body = {
    provider_id: providerId.value,
    model: model.value,
    retention_days: Number(retention.value),
    ...(apiKey.value ? { api_key: apiKey.value } : {}),
  }
  const put = (extra = {}) => api<AiSettingsView>(`${w.base}/ai/settings`, 'PUT', { ...body, ...extra })
  const ok = await run(async () => {
    try {
      settings.data = await put()
    } catch (e) {
      // A shorter retention deletes saved conversations, so the server asks for their count back (R20).
      const count = e instanceof ApiError && e.status === 409 ? (e.body as { conversations_to_delete?: number }).conversations_to_delete : undefined
      if (count === undefined) throw e
      if (!window.confirm(errorText(e))) throw new Error('Nothing was saved.')
      settings.data = await put({ confirm_deletions: count })
    }
  }, 'AI settings saved.')
  if (ok) apiKey.value = ''
}

const disconnect = () =>
  window.confirm('Turn off AI answers? Saved conversations stay readable until retention deletes them, but cannot be continued.') &&
  run(() => api(`${w.base}/ai/settings`, 'DELETE'), 'AI answers are off.').then((ok) => ok && settings.reload())
</script>

<template>
  <PageHeader
    title="AI answers"
    lede="Written answers in Ask, drawn only from approved passages the asker can read. Every member sees the provider and model; owners and admins change them."
  />
  <StatusMessage :text="message" />
  <LoadState :load="settings" :empty="false" empty-text="" />

  <div v-if="settings.data" class="mt-4 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
    <div class="grid min-w-0 gap-4">
      <p v-if="settings.data.key_status === 'rejected'" role="alert" class="alert">
        The provider rejected the saved key, so answers are unavailable. Save a new key to turn them back on.
      </p>
      <dl class="titleblock" aria-label="AI answers in this workspace">
        <div>
          <dt>Status</dt>
          <dd>{{ settings.data.enabled ? 'On' : 'Off' }}</dd>
        </div>
        <div>
          <dt>Conversations kept</dt>
          <dd>{{ settings.data.retention_days }} days after their last turn</dd>
        </div>
        <div>
          <dt>Provider</dt>
          <dd>{{ settings.data.enabled ? current.provider : '—' }}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{{ settings.data.enabled ? current.model : '—' }}</dd>
        </div>
      </dl>
      <p v-if="!w.manager" class="text-graphite">Owners and admins manage these settings.</p>
    </div>

    <template v-if="w.manager">
      <form v-if="providers.length" class="sheet grid gap-4 p-5" @submit.prevent="save">
        <h2>{{ settings.data.enabled ? 'Change settings' : 'Connect a provider' }}</h2>
        <label class="field">
          Provider
          <select v-model="providerId" class="input" @change="pickProvider">
            <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.label }}</option>
          </select>
        </label>
        <label class="field">
          Model
          <select v-model="model" class="input">
            <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label }}</option>
          </select>
        </label>
        <label class="field">
          API key
          <input v-model="apiKey" class="input" type="password" autocomplete="off" />
          <span v-if="settings.data.enabled" class="font-normal text-muted">Leave empty to keep the saved key.</span>
        </label>
        <label class="field">
          Keep conversations for (days)
          <input v-model.number="retention" class="input" type="number" min="1" max="365" required />
        </label>
        <button type="submit" class="btn btn-primary">Save settings</button>
        <button v-if="settings.data.enabled" type="button" class="btn btn-danger" @click="disconnect">Turn off AI answers</button>
      </form>
      <p v-else class="text-graphite">No AI providers are set up on this server.</p>
    </template>
  </div>
</template>
