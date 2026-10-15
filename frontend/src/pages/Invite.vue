<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, errorText } from '../api'
import AuthFrame from '../components/AuthFrame.vue'
import SignInForm from '../components/SignInForm.vue'
import SignUpForm from '../components/SignUpForm.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { me, refresh, useAction } from '../session'

const router = useRouter()
const token = useRoute().hash.slice(1)
const { message, run } = useAction()

async function accept() {
  let workspaceId = ''
  const ok = await run(
    async () => {
      workspaceId = (await api<{ workspace_id: string }>('/api/invites/accept', 'POST', { token })).workspace_id
    },
    '',
    (e) =>
      e instanceof ApiError && e.status === 404
        ? 'This invite was refused: it is invalid, expired, already used or cancelled. Ask for a new one.'
        : errorText(e),
  )
  if (ok) {
    await refresh()
    await router.push(`/w/${workspaceId}/documents`)
  }
}
</script>

<template>
  <AuthFrame>
    <h1>Join a workspace</h1>
    <p v-if="!token" role="alert" class="alert mt-4">This invite link is incomplete. Ask for a new one.</p>
    <template v-else-if="!me">
      <p class="mt-1 text-graphite">Sign in or create an account to accept this invite.</p>
      <div class="mt-6 grid gap-10">
        <SignInForm />
        <div class="border-t border-rule pt-8"><SignUpForm /></div>
      </div>
    </template>
    <template v-else>
      <p class="mt-1 text-graphite">Signed in as {{ me.user.email }}.</p>
      <button type="button" class="btn btn-primary mt-6 w-full" @click="accept">Accept invite</button>
      <div class="mt-4"><StatusMessage :text="message" /></div>
    </template>
  </AuthFrame>
</template>
