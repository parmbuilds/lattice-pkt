<script setup lang="ts">
import { ref } from 'vue'
import { api, setCsrfToken } from '../api'
import { refresh, useAction } from '../session'
import StatusMessage from './StatusMessage.vue'

const emit = defineEmits<{ done: [] }>()
const { message, run } = useAction()
const email = ref('')
const password = ref('')

async function submit() {
  const ok = await run(async () => {
    const { csrf_token } = await api<{ csrf_token: string }>('/api/auth/signin', 'POST', {
      email: email.value,
      password: password.value,
    })
    setCsrfToken(csrf_token)
  })
  if (ok) {
    await refresh()
    emit('done')
  }
}
</script>

<template>
  <form class="grid gap-4" @submit.prevent="submit">
    <h2>Sign in</h2>
    <label class="field">
      Email
      <input v-model="email" class="input" type="email" autocomplete="username" required />
    </label>
    <label class="field">
      Password
      <input v-model="password" class="input" type="password" autocomplete="current-password" required />
    </label>
    <button class="btn btn-primary w-full" type="submit">Sign in</button>
    <StatusMessage :text="message" />
  </form>
</template>
