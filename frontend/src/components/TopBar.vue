<script setup lang="ts">
import { useRouter } from 'vue-router'
import { me, signOut } from '../session'
import Wordmark from './Wordmark.vue'

const router = useRouter()
async function leave() {
  await signOut()
  await router.push('/signin')
}
</script>

<template>
  <header class="flex items-center gap-4 bg-ink px-5 py-3 text-rail-text md:px-10">
    <RouterLink to="/" class="mr-auto text-rail-text no-underline"><Wordmark /></RouterLink>
    <template v-if="me">
      <span class="text-sm text-rail-muted">{{ me.user.name }}</span>
      <button type="button" class="btn border-rail-muted/50 bg-transparent text-rail-text hover:bg-ink-2" @click="leave">
        Sign out
      </button>
    </template>
  </header>
</template>
