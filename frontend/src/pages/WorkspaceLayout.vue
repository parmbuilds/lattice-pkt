<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NOT_FOUND } from '../api'
import Wordmark from '../components/Wordmark.vue'
import { me, rememberWorkspace, signOut, useWorkspace } from '../session'

const route = useRoute()
const router = useRouter()
const w = useWorkspace()

// Only a workspace the session confirms membership of becomes the next sign-in's landing page.
watchEffect(() => w.role && rememberWorkspace(w.workspaceId))

// Presentation only: the API refuses manager actions for members whatever the menu shows.
const sections = computed(() => [
  {
    label: 'Knowledge',
    links: [
      ['overview', 'Overview'],
      ['documents', 'Documents'],
      ['search', 'Search'],
      ['entities', 'Entities'],
    ],
  },
  { label: 'Agents', links: [['tokens', 'Tokens']] },
  ...(w.manager
    ? [
        {
          label: 'Governance',
          links: [
            ['approvals', 'Approvals'],
            ['members', 'Members'],
            ['groups', 'Groups'],
            ['audit', 'Audit trail'],
          ],
        },
      ]
    : []),
])

const isActive = (to: string) => route.path.startsWith(`/w/${w.workspaceId}/${to}`)

function switchWorkspace(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  void router.push(id ? `/w/${id}/documents` : '/')
}

async function leave() {
  await signOut()
  await router.push('/signin')
}
</script>

<template>
  <div v-if="!w.role" class="p-10">
    <p role="alert" class="alert inline-block">{{ NOT_FOUND }}</p>
    <p class="mt-4"><RouterLink to="/">Back to your workspaces</RouterLink></p>
  </div>
  <div v-else class="min-h-dvh md:grid md:grid-cols-[15rem_minmax(0,1fr)]">
    <div class="bg-ink">
    <aside class="flex flex-col bg-ink text-rail-text md:sticky md:top-0 md:h-dvh">
      <div class="flex items-center justify-between gap-3 px-4 pt-4 pb-3">
        <RouterLink to="/" class="text-rail-text no-underline"><Wordmark /></RouterLink>
        <button type="button" class="btn-quiet btn text-rail-muted hover:bg-ink-2 hover:text-rail-text md:hidden" @click="leave">
          Sign out
        </button>
      </div>

      <div class="border-y border-white/10 px-4 py-3">
        <label class="caps grid gap-1.5 text-rail-muted">
          Workspace
          <select
            class="input min-h-8 border-white/15 bg-ink-2 py-1 font-sans text-sm font-medium tracking-normal normal-case text-rail-text hover:border-white/30"
            :value="w.workspaceId"
            @change="switchWorkspace"
          >
            <option value="">All workspaces</option>
            <option v-for="m in me?.memberships" :key="m.workspace_id" :value="m.workspace_id">{{ m.workspace_name }}</option>
          </select>
        </label>
        <p class="mt-2 text-sm text-rail-muted">
          {{ me?.user.name }} · <span class="capitalize text-rail-text">{{ w.role }}</span>
        </p>
      </div>

      <nav aria-label="Workspace" class="flex flex-wrap gap-x-1 px-2 py-2 md:block md:flex-1 md:overflow-y-auto">
        <div v-for="section in sections" :key="section.label" class="contents md:block">
          <p class="caps mt-4 mb-1 hidden px-2 text-rail-muted md:block">{{ section.label }}</p>
          <ul class="contents md:block">
            <li v-for="[to, label] in section.links" :key="to">
              <RouterLink
                :to="`/w/${w.workspaceId}/${to}`"
                :aria-current="isActive(to) ? 'page' : undefined"
                class="block rounded-[3px] px-2 py-1.5 text-sm font-medium text-rail-text no-underline hover:bg-ink-2"
                :class="{ 'bg-film! text-ink!': isActive(to) }"
              >
                {{ label }}
              </RouterLink>
            </li>
          </ul>
        </div>
      </nav>

      <div class="hidden border-t border-white/10 px-4 py-3 md:block">
        <p class="truncate text-sm text-rail-muted">{{ me?.user.email }}</p>
        <button type="button" class="mt-2 text-sm text-rail-text underline underline-offset-3 hover:text-white" @click="leave">
          Sign out
        </button>
      </div>
    </aside>
    </div>

    <main class="min-w-0 px-5 py-6 md:px-10 md:py-8">
      <div class="mx-auto max-w-6xl">
        <!-- Keyed by path so per-document state (open version, edit form) never carries over. -->
        <RouterView v-slot="{ Component }">
          <component :is="Component" :key="route.path" />
        </RouterView>
      </div>
    </main>
  </div>
</template>
